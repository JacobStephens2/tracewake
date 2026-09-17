"""The page stops needing refresh (#159): NOTIFY -> SSE -> a re-rendered region.

Asserted at HTTP level against the real local Postgres, because the whole
mechanism is the database's: `schema.sql`'s insert trigger fires NOTIFY, the
endpoint holds a LISTEN, and a mock of either end would be a test of this
file's opinion about Postgres rather than of the thing that has to work. The
throwaway-database harness is the same one the Selector's suite uses.

The reads run on a pump thread with a queue between it and the assertions.
Not decoration: every assertion here is "a row lands, and the stream says so",
so a test that read the stream on the main thread would hang forever - not
fail - the day the stream stopped saying so, and a hung suite reports nothing.
"""
import contextlib
import json
import queue
import re
import threading
import time

import httpx
import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

# Long enough that a slow socket is not a red test, short enough that a broken
# stream reports rather than stalls the suite.
WAIT_SECONDS = 15


@pytest.fixture(scope="module")
def server():
    """A real uvicorn on a loopback port, for the streaming assertions only.

    Not the TestClient the rest of this suite uses: its transport runs the ASGI
    app to completion and buffers the body before returning a response, so a
    stream that never ends never returns at all. That is a property of the test
    transport rather than of the endpoint - but it means the only way to assert
    "a row lands and the browser hears about it" is to be a browser, over a
    socket, against a server that flushes.
    """
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not running.started:
        assert time.monotonic() < deadline, "the test server did not start"
        assert thread.is_alive(), "the test server died while starting"
        time.sleep(0.05)
    port = running.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        thread.join(timeout=10)


class Stream:
    """An open SSE response, read frame by frame off a pump thread."""

    def __init__(self, response):
        self.response = response
        self._frames = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        fields = {}
        try:
            for line in self.response.iter_lines():
                line = line.rstrip("\r\n")
                if line == "":
                    if fields:
                        self._frames.put(fields)
                        fields = {}
                    continue
                if line.startswith(":"):
                    fields["comment"] = line[1:].strip()
                    continue
                key, _, value = line.partition(":")
                fields[key] = value.lstrip()
        except Exception:  # the response was closed under us; that is the end
            pass
        finally:
            self._frames.put(None)

    def frame(self, timeout=WAIT_SECONDS) -> dict:
        try:
            frame = self._frames.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(
                f"no SSE frame arrived within {timeout}s"
            ) from None
        assert frame is not None, "the stream closed before the expected frame"
        return frame

    def event(self, timeout=WAIT_SECONDS) -> dict:
        """The next frame that is an event, keepalive comments skipped."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self.frame(timeout=max(0.1, deadline - time.monotonic()))
            if "event" in frame:
                return frame

    def data(self, timeout=WAIT_SECONDS) -> dict:
        return json.loads(self.event(timeout)["data"])


@contextlib.contextmanager
def open_stream(server, headers=None):
    with httpx.Client(timeout=WAIT_SECONDS + 5) as browser:
        with browser.stream(
            "GET", server + "/loop/events", headers=headers or {}
        ) as response:
            yield response


def append(dsn, kind, payload=None):
    """A row, written the way anything else writes one: an INSERT.

    Through psycopg rather than through `journal.append` so that what the
    stream is proved to carry is any append at all - the schema's trigger is
    what fires NOTIFY, so a hand `psql` insert has to reach the page too.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO journal.events (kind, payload) VALUES (%s, %s::jsonb)"
            " RETURNING id",
            (kind, json.dumps(payload or {})),
        ).fetchone()[0]


# --- The stream -------------------------------------------------------------


def test_the_stream_is_an_event_stream(db, server):
    with open_stream(server) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # For an nginx-family proxy, which buffers by default and would hold
        # every event until the stream ended - that is, forever. Caddy, which
        # fronts this app today, flushes an event stream without being asked.
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-store"


def test_the_stream_says_it_is_listening_before_anything_lands(db, server):
    """The frame every other assertion here depends on.

    A LISTEN is established after the request is handled, so a client that
    inserted a row the moment it got its response headers could beat it and
    wait forever for an event that was never going to come. `ready` is
    written after the LISTEN, so it is the client's - and this suite's -
    signal that the next row will be seen.
    """
    with open_stream(server) as response:
        stream = Stream(response)
        frame = stream.event()
        assert frame["event"] == "ready"
        assert json.loads(frame["data"])["newest"] is None


def test_a_row_inserted_reaches_the_stream(db, server):
    with open_stream(server) as response:
        stream = Stream(response)
        stream.event()  # ready: the LISTEN is up
        row = append(db, "cycle.started", {"repo": "acme/widgets"})
        frame = stream.event()
        assert frame["event"] == "journal"
        assert json.loads(frame["data"]) == {"id": row, "kind": "cycle.started"}
        # The SSE id is the row id, so a reconnect can name what it last saw.
        assert frame["id"] == str(row)


def test_a_cycle_an_iteration_and_an_outcome_all_arrive(db, server):
    """The viewing criterion, at HTTP level: the three kinds the operator
    watches for during a Run each reach an open page, in order, with no
    request in between."""
    with open_stream(server) as response:
        stream = Stream(response)
        stream.event()
        kinds = ["cycle.started", "run.iteration", "run.outcome"]
        for kind in kinds:
            append(db, kind, {"issue": 646})
        assert [stream.data()["kind"] for _ in kinds] == kinds


def test_a_burst_of_rows_arrives_whole(db, server):
    """A cycle writes its skips in one go. Every one is its own frame: the
    NOTIFY backlog is drained, not sampled."""
    with open_stream(server) as response:
        stream = Stream(response)
        stream.event()
        ids = [append(db, "issue.skipped", {"number": n}) for n in range(646, 656)]
        assert [stream.data()["id"] for _ in ids] == ids


def test_a_reconnecting_client_is_told_what_it_missed(db, server):
    """EventSource reconnects on its own, and the rows written while it was
    away fired their NOTIFY into a closed connection.

    Without this the page would sit on stale content until the next row
    happened to land - and after the last row of a Run, none ever does. So a
    reconnect replays by id rather than trusting that something else will
    come along.
    """
    missed = append(db, "run.outcome", {"issue": 646, "outcome": "complete"})
    with open_stream(server, headers={"Last-Event-ID": str(missed - 1)}) as response:
        stream = Stream(response)
        assert stream.event()["event"] == "ready"
        frame = stream.event()
        assert frame["event"] == "journal"
        assert json.loads(frame["data"])["id"] == missed


def test_a_reconnecting_client_that_missed_nothing_is_told_nothing(db, server):
    seen = append(db, "run.outcome", {"issue": 646})
    with open_stream(server, headers={"Last-Event-ID": str(seen)}) as response:
        stream = Stream(response)
        assert json.loads(stream.event()["data"])["newest"] == seen
        fresh = append(db, "cycle.started", {})
        # The next event is the new row, not a replay of the one it had.
        assert stream.data()["id"] == fresh


def test_a_long_absence_is_replayed_whole(db, server):
    """A replay reads in batches, and a batch-sized one is the case where a
    cap that was mistaken for a limit would truncate.

    Truncation here is not a slow page but a permanently stale one: the client
    would be left holding an id it had already passed, with no later NOTIFY
    coming to correct it.
    """
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO journal.events (kind, payload)"
            " SELECT 'run.iteration', jsonb_build_object('iteration', n)"
            " FROM generate_series(1, %s) AS n",
            (journal_batch() + 20,),
        )
        newest = conn.execute("SELECT max(id) FROM journal.events").fetchone()[0]
    with open_stream(server, headers={"Last-Event-ID": "0"}) as response:
        stream = Stream(response)
        assert stream.event()["event"] == "ready"
        seen = []
        while len(seen) < journal_batch() + 20:
            seen.append(stream.data()["id"])
    assert seen == sorted(seen), "replayed out of order"
    assert seen[-1] == newest


def journal_batch() -> int:
    """The Journal's own page size, so this test cannot drift from it."""
    import journal

    return journal._BATCH


def test_a_nonsense_last_event_id_is_not_a_500(db, server):
    """`Last-Event-ID` is whatever the client sent."""
    with open_stream(server, headers={"Last-Event-ID": "not-a-number"}) as response:
        assert response.status_code == 200
        stream = Stream(response)
        assert stream.event()["event"] == "ready"


def test_the_stream_keeps_itself_alive(db, server, monkeypatch):
    """A stream that says nothing for minutes is indistinguishable from a
    dead one, and an idle proxy connection is reaped. The comment frames say
    the connection is still there without inventing a Journal row."""
    monkeypatch.setenv("SELECTOR_SSE_KEEPALIVE_SECONDS", "0.2")
    with open_stream(server) as response:
        stream = Stream(response)
        stream.event()
        assert stream.frame(timeout=5)["comment"] == "keepalive"


def test_an_unreachable_journal_closes_the_stream_rather_than_hanging(
    db, server, monkeypatch
):
    """The page's other panels already say the Journal is unavailable. The
    stream must agree and end, so EventSource retries, instead of holding a
    connection that will never carry an event."""
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_no_such_db")
    with open_stream(server) as response:
        # The stream is a window page: no session store, no stream.
        assert response.status_code == 303


# --- The live region --------------------------------------------------------


LIVE_REGION = re.compile(r'<div id="live-region"[^>]*>')


def test_the_page_carries_a_live_region_that_asks_for_itself(db):
    body = client.get("/").text
    match = LIVE_REGION.search(body)
    assert match, "no live region on /"
    opening = match.group(0)
    assert 'hx-get="/loop/live"' in opening
    assert 'hx-swap="outerHTML"' in opening
    # Coalesced: a cycle writes a dozen rows in a second and the board behind
    # this fragment is a tracker call, not a database read.
    assert re.search(r'hx-trigger="[^"]*delay:', opening), opening


def test_the_page_opens_the_stream(db):
    body = client.get("/").text
    assert "/loop/events" in body
    assert "EventSource" in body
    # HTMX does the swapping, so the page has to actually load it. Every
    # page gets it from terminal_base.html, the window's single shell (#84).
    assert "/static/htmx.min.js" in body


def test_the_fragment_is_the_region_alone(db, dispatch):
    dispatch(db, 646, outcome="complete")
    body = client.get("/loop/live").text
    assert body.lstrip().startswith('<div id="live-region"')
    assert "<html" not in body
    # It re-arms itself: the swap replaces the element carrying the trigger,
    # so a fragment that dropped the attributes would update exactly once.
    assert 'hx-get="/loop/live"' in body


def test_the_fragment_and_the_page_render_the_same_panels(db, dispatch):
    dispatch(db, 646, outcome="complete")
    append(db, "run.iteration", {"issue": 646, "iteration": 1})
    page = client.get("/").text
    fragment = client.get("/loop/live").text
    for panel in ("The queue", "The host", "Runs", "Every event", "review capacity"):
        assert panel in page, panel
        assert panel in fragment, panel


def test_the_fragment_is_what_the_page_updates_to(db):
    """The AC's viewing criterion, minus the browser: whatever lands in the
    Journal is in the region the SSE event makes the page re-fetch."""
    before = client.get("/loop/live").text
    assert "#646" not in before
    append(db, "run.dispatched",
           {"issue": 646, "title": "a thing", "url": "https://x/646"})
    after = client.get("/loop/live").text
    assert "#646" in after
    assert "in flight" in after


def test_a_prefixed_mount_asks_itself_for_the_fragment_and_the_stream(db):
    """Same property `_static_base` exists for: under a path prefix the
    browser must ask this instance, not the root's."""
    prefixed = TestClient(app, root_path="/loop-staging")
    body = prefixed.get("/").text
    assert 'hx-get="/loop-staging/loop/live"' in body
    assert "/loop-staging/loop/events" in body


@pytest.mark.parametrize("path", ["/", "/loop/live"])
def test_the_journal_being_down_does_not_take_the_region_with_it(
    path, monkeypatch
):
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_no_such_db")
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert "/sign-in" in response.headers["location"]
