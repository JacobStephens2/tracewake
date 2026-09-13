"""Run history (#160), at HTTP level over a seeded Journal.

The history is the half of /loop that owes nothing to GitHub. Every Run it
shows is read back from rows the Selector wrote, which is what makes it
outlive the branch the Run worked on: a merged-and-deleted branch takes its
tree with it, and the Journal keeps the title, the issue, the bound the Run
ended on and the Proposal that came out of it.
"""
import re

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import app as app_module
import journal
from app import app

client = TestClient(app)


def _at(conn, ago, kind, payload):
    """Append one row, backdated.

    An explicit `at` is the only way to write history into an append-only
    table, and the durations this page reports are the gap between two rows -
    so a Run written entirely at `now()` would render every duration as zero
    and prove nothing.
    """
    conn.execute(
        "INSERT INTO journal.events (at, kind, payload)"
        " VALUES (now() - %s::interval, %s, %s)",
        (ago, kind, Jsonb(payload)),
    )


def _run(conn, *, issue=645, attempt=1, outcome="iteration-cap",
         proposal="https://github.invalid/acme/widgets/pull/12",
         started="5 hours", ended="4 hours", **over):
    """One whole Run in the Journal: dispatched, then ended."""
    dispatched = {
        "issue": issue,
        "attempt": attempt,
        "title": "Widen the sync window",
        "url": "https://example.invalid/645",
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "area": "The nightly sync script",
    }
    dispatched.update(over)
    _at(conn, started, "run.dispatched", dispatched)
    if ended is not None:
        _at(conn, ended, "run.outcome", {
            "issue": issue, "attempt": attempt, "outcome": outcome,
            "ended_by": outcome, "exit": 0, "iterations": 5,
            "faults": "none", "notified": "sent", "proposal": proposal,
        })


def history(body):
    """The live region of the history page, cut out of the shell."""
    start = body.index('<div id="live-region"')
    return body[start:]


def test_the_history_renders_from_the_journal_alone(db, tracker):
    """The first acceptance criterion: a seeded Journal is the whole input.

    The board on /loop reads the tracker at request time, so /loop is only as
    available as GitHub. The history is not - and what proves it is that no
    tracker command was asked anything, not that the page happened to render.
    """
    with journal.connect(db) as conn:
        _run(conn)
    resp = client.get("/loop/history")
    assert resp.status_code == 200
    assert tracker.labels_asked() == []
    body = history(resp.text)
    assert "Widen the sync window" in body
    assert "iteration-cap" in body


def test_the_history_still_serves_when_the_tracker_is_down(db, tracker):
    tracker.fail("gh: could not resolve host github.com")
    with journal.connect(db) as conn:
        _run(conn)
    body = history(client.get("/loop/history").text)
    assert "Widen the sync window" in body
    assert "could not resolve host" not in body


def test_a_run_outlives_the_branch_it_worked_on(db):
    """The second criterion. Nothing on the card is fetched from the forge, so
    a branch merged and deleted an hour ago changes nothing here: the Proposal
    link is the URL the Run reported, and it still opens the merged pull
    request."""
    with journal.connect(db) as conn:
        _run(conn, proposal="https://github.invalid/acme/widgets/pull/701")
    body = history(client.get("/loop/history").text)
    assert 'href="https://github.invalid/acme/widgets/pull/701"' in body
    # The branch is named, but as text. A link to a deleted branch is a 404
    # dressed up as a working link.
    assert "loop/645-the-nightly-sync-script" in body
    assert 'href="loop/645' not in body


def test_a_run_that_proposed_nothing_says_so_rather_than_linking_nowhere(db):
    with journal.connect(db) as conn:
        _run(conn, outcome="agent-failed", proposal=None)
    body = history(client.get("/loop/history").text)
    assert "none - the Run produced nothing to review" in body


def test_the_history_reports_the_bound_and_how_long_the_run_took(db):
    """Outcome, ending bound and duration - which is the two rows' own gap,
    not something the box reported."""
    with journal.connect(db) as conn:
        _run(conn, started="5 hours", ended="3 hours 20 minutes")
    body = history(client.get("/loop/history").text)
    assert "iteration-cap" in body
    assert "1h 40m" in body


def test_a_run_shorter_than_a_minute_is_reported_in_seconds(db):
    with journal.connect(db) as conn:
        _run(conn, started="5 hours", ended="4 hours 59 minutes 18 seconds")
    assert "42s" in history(client.get("/loop/history").text)


def test_the_run_in_flight_is_not_history_yet(db):
    """History is what has ended. A Run still going has no bound, no duration
    and no Proposal, so a card for it here would be three empty cells - and
    /loop already shows it, live, on the panel built for it."""
    with journal.connect(db) as conn:
        _run(conn, issue=645, ended="4 hours")
        _run(conn, issue=652, started="1 hour", ended=None)
    body = history(client.get("/loop/history").text)
    assert "#645" in body
    assert "#652" not in body
    assert "in flight" not in body


def test_an_empty_journal_says_so_rather_than_rendering_nothing(db):
    body = history(client.get("/loop/history").text)
    assert "No Run has ended yet" in body


def test_the_history_serves_when_the_journal_is_unreachable(monkeypatch):
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_test_no_such_db")
    resp = client.get("/loop/history", follow_redirects=False)
    assert resp.status_code == 303
    assert "/sign-in" in resp.headers["location"]


def test_the_history_is_reachable_from_the_loop_and_back(db):
    assert 'href="/history"' in client.get("/").text
    assert 'href="/"' in client.get("/history").text


# --- How far back it reaches ------------------------------------------------
#
# The history counts in Runs, not in Journal rows. A row cap is a Run cap of
# no fixed size: a Run's rows are interleaved with every cycle summary and
# skip written since, so on the page whose whole purpose is that past Runs
# stay inspectable, a busy fortnight would quietly delete the oldest of them.


def test_a_run_buried_under_later_journal_rows_is_still_listed(db):
    """The failure this guards against is silent and total: the Run renders
    today, and one busy week later the same page renders without it and says
    nothing. Three hundred rows is past `journal.events`'s 200-row default,
    which is what /loop reads and what this page must not."""
    with journal.connect(db) as conn:
        _run(conn, issue=600, started="30 hours", ended="29 hours")
        for n in range(300):
            _at(conn, "10 hours", "cycle.started", {"repo": "acme/widgets"})
        _run(conn, issue=700, started="5 hours", ended="4 hours")
    body = history(client.get("/loop/history").text)
    assert "#600" in body
    assert "#700" in body


def test_a_runs_own_rows_are_read_whole_however_far_back_it_is(db):
    """Not just the dispatch. A card built from a dispatch whose outcome was
    left behind reads as a Run still in flight, which is how a finished Run
    disappears from a history that filters the in-flight ones out."""
    with journal.connect(db) as conn:
        _run(conn, issue=600, started="30 hours", ended="29 hours",
             proposal="https://github.invalid/acme/widgets/pull/600")
        for n in range(300):
            _at(conn, "10 hours", "cycle.started", {"repo": "acme/widgets"})
    body = history(client.get("/loop/history").text)
    assert "/pull/600" in body
    assert "in flight" not in body


def test_the_runs_below_the_window_are_counted_rather_than_dropped(db,
                                                                  monkeypatch):
    """A page that simply ended would look identical to a Journal holding
    nothing older - on the page an operator opens precisely when he is looking
    for something old."""
    monkeypatch.setattr(app_module, "HISTORY_RUNS", 2)
    with journal.connect(db) as conn:
        for issue in (600, 601, 602, 603):
            _run(conn, issue=issue)
    body = history(client.get("/loop/history").text)
    assert "#603" in body and "#602" in body
    assert "#601" not in body
    assert "2 older Run(s) are in the Journal and not shown" in body


def test_a_history_inside_the_window_says_nothing_about_older_runs(db):
    with journal.connect(db) as conn:
        _run(conn)
    assert "not shown" not in history(client.get("/loop/history").text)


# --- Liveness ---------------------------------------------------------------
#
# The history is a terminal page like /loop, so it is live like /loop (#159):
# a Run that ends while it is open appears on it without a reload. The only
# thing that differs is which region gets re-fetched.


def test_the_history_swaps_its_own_region_not_the_loops(db):
    """The failure this guards against is quiet: a history page pointed at
    /loop/live would swap in a queue board it never rendered - and reach the
    tracker to build it, which is the one thing this page does not do."""
    body = client.get("/loop/history").text
    assert 'hx-get="/loop/history/live"' in body
    assert 'hx-get="/loop/live"' not in body


def test_the_history_fragment_is_the_region_alone(db):
    with journal.connect(db) as conn:
        _run(conn)
    body = client.get("/loop/history/live").text
    assert body.lstrip().startswith('<div id="live-region"')
    assert "<html" not in body
    # It re-arms itself: the swap replaces the element carrying the trigger,
    # so a fragment that dropped the attributes would update exactly once.
    assert 'hx-get="/loop/history/live"' in body


def test_the_history_fragment_and_page_render_the_same_panels(db):
    with journal.connect(db) as conn:
        _run(conn)
    page = client.get("/loop/history").text
    fragment = client.get("/loop/history/live").text
    for panel in ("Runs that have ended", "1h 0m"):
        assert panel in page, panel
        assert panel in fragment, panel


def test_the_history_opens_the_stream(db):
    body = client.get("/loop/history").text
    assert "/loop/events" in body
    assert "EventSource" in body
    assert "/static/htmx.min.js" in body


def test_a_prefixed_mount_asks_itself_for_the_history_fragment(db):
    """Same property `_static_base` exists for: under a path prefix the
    browser must ask this instance, not the root's."""
    prefixed = TestClient(app, root_path="/loop-staging")
    body = prefixed.get("/loop/history").text
    assert 'hx-get="/loop-staging/loop/history/live"' in body
    assert "/loop-staging/loop/events" in body


def test_the_journal_being_down_does_not_take_the_history_region_with_it(
    monkeypatch,
):
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_no_such_db")
    for path in ("/loop/history", "/loop/history/live"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert "/sign-in" in response.headers["location"]


# --- The budget -------------------------------------------------------------
#
# The third criterion. "2 of 4 dispatched" and "2 remaining" are the same
# arithmetic, but the operator's question is the second one, and a tile that
# leaves him to do the subtraction is a tile that gets read wrong the day the
# cap changes.


def budget(body):
    """The budget cell, cut out of whichever page it is on."""
    match = re.search(
        r'<div class="cell cell-budget">(.*?)</div>', body, re.DOTALL
    )
    assert match, "no budget cell on the page"
    return match.group(1)


def test_the_budget_tile_shows_review_capacity_remaining(tracker):
    tracker.queue("awaiting-review", [tracker.issue(640), tracker.issue(641)])
    cell = budget(client.get("/loop").text)
    assert "18 remaining" in cell
    assert "2 of 20" in cell
    assert "awaiting review" in cell


def test_a_spent_budget_reads_as_zero_remaining(tracker):
    tracker.queue("awaiting-review", [tracker.issue(i) for i in range(20)])
    cell = budget(client.get("/loop").text)
    assert "0 remaining" in cell
    assert "20 of 20" in cell


def test_the_budget_is_unknown_rather_than_full_when_the_tracker_is_down(tracker):
    """The direction that matters. An unreadable tracker defaulting to '20
    remaining' would be the page inventing capacity it cannot see."""
    tracker.fail("gh: could not resolve host github.com")
    cell = budget(client.get("/loop").text)
    assert "unknown" in cell
    assert "the tracker could not be read." in cell
