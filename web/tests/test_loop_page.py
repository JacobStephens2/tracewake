"""The /loop window at HTTP level: FastAPI test client over a seeded Journal."""
from fastapi.testclient import TestClient

import journal
from app import app

client = TestClient(app)


def test_loop_page_renders_journal_newest_first(db):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.summary", {"eligible": 3})
        journal.append(conn, "run.dispatched", {"issue": 646})
    resp = client.get("/loop")
    assert resp.status_code == 200
    assert "terminal.css" in resp.text
    body = resp.text
    assert body.index("run.dispatched") < body.index("cycle.summary")
    assert "646" in body


def test_loop_page_serves_when_journal_is_unreachable(monkeypatch):
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_test_no_such_db")
    resp = client.get("/loop")
    assert resp.status_code == 200
    assert "unavailable" in resp.text.lower()


def test_terminal_css_is_served():
    resp = client.get("/static/terminal.css")
    assert resp.status_code == 200
    assert "--global-font-size" in resp.text


def test_home_links_to_loop():
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/loop"' in resp.text


def test_the_cycle_card_shows_the_pick_and_every_skip_with_its_reason(db):
    """Issue #153's viewing criterion, at HTTP level over a seeded Journal."""
    with journal.connect(db) as conn:
        cycle = journal.append(
            conn,
            "cycle.started",
            {"repo": "acme/widgets", "label": "ready-for-agent", "dry_run": True},
        )
        journal.append(
            conn,
            "issue.skipped",
            {
                "cycle": cycle,
                "number": 646,
                "url": "https://example.invalid/646",
                "reason": "blocked-by-open-dependency",
                "detail": "1 open blocking edge(s) on the tracker",
            },
        )
        journal.append(
            conn,
            "cycle.picked",
            {
                "cycle": cycle,
                "number": 645,
                "title": "Widen the sync window",
                "url": "https://example.invalid/645",
                "area": "The nightly sync script",
                "check": "scripts/check.sh",
            },
        )
        journal.append(
            conn,
            "cycle.finished",
            {
                "cycle": cycle,
                "considered": 2,
                "eligible": [645],
                "skipped": {"blocked-by-open-dependency": 1},
                "picked": 645,
                "halted": None,
                "dispatched_in_window": 0,
                "daily_cap": 4,
                "dry_run": True,
            },
        )
    body = client.get("/loop").text
    assert "Widen the sync window" in body
    assert "The nightly sync script" in body
    assert "blocked-by-open-dependency" in body
    assert "1 open blocking edge(s) on the tracker" in body
    assert "dry run" in body
    assert "0/4" in body, "the daily-cap budget is shown"


def test_a_cycle_that_picked_nothing_says_why(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": True})
        journal.append(
            conn,
            "cycle.finished",
            {
                "cycle": cycle,
                "considered": 1,
                "eligible": [],
                "skipped": {},
                "picked": None,
                "halted": "run-in-flight",
                "dispatched_in_window": 1,
                "daily_cap": 4,
                "dry_run": True,
            },
        )
    body = client.get("/loop").text
    assert "picked nothing" in body
    assert "run-in-flight" in body


def test_events_outside_a_cycle_still_reach_the_page(db):
    """The raw event table stays the Journal's unabridged view: a hand
    `psql` INSERT with no cycle id must not vanish behind the cards."""
    with journal.connect(db) as conn:
        journal.append(conn, "test.hand", {"note": "appended by hand"})
    body = client.get("/loop").text
    assert "test.hand" in body
    assert "appended by hand" in body


def test_a_cycle_cut_in_half_by_the_read_limit_is_not_rendered(db):
    """The Journal read is capped, so the oldest cycle on a busy page has
    lost its `cycle.started` row. A card built from what survived would
    render as a nameless, timeless cycle instead of as the absence it is."""
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "issue.skipped",
            {
                "cycle": 999999,
                "number": 646,
                "reason": "blocked-by-open-dependency",
                "detail": "from a cycle whose start scrolled off the page",
            },
        )
    body = client.get("/loop").text
    assert "from a cycle whose start scrolled off" not in body.split("Every event")[0]
    assert "from a cycle whose start scrolled off" in body, (
        "the raw event table still shows it"
    )


# --- The Run card (#154) ----------------------------------------------------


def _dispatch_row(conn, cycle, **over):
    payload = {
        "cycle": cycle,
        "issue": 645,
        "title": "Widen the sync window",
        "url": "https://example.invalid/645",
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "area": "The nightly sync script",
        "attempt": 1,
    }
    payload.update(over)
    return journal.append(conn, "run.dispatched", payload)


def test_a_run_in_flight_has_a_card_of_its_own(db):
    """Issue #154's viewing criterion, first half: the in-flight card appears
    while the Run is running, which is the whole of what the page can show
    before the box says anything."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
    body = client.get("/loop").text
    assert "in flight" in body
    assert "loop/645-the-nightly-sync-script" in body
    assert "Widen the sync window" in body


def test_the_card_ends_showing_its_proposal_link(db):
    """The second half: when the outcome lands, the same Run reads as ended
    and carries the Proposal a reviewer opens."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        journal.append(
            conn,
            "run.outcome",
            {
                "cycle": cycle,
                "issue": 645,
                "attempt": 1,
                "outcome": "iteration-cap",
                "ended_by": "iteration-cap",
                "exit": 0,
                "iterations": 5,
                "faults": "none",
                "notified": "sent",
                "proposal": "https://github.invalid/acme/widgets/pull/12",
            },
        )
    body = client.get("/loop").text
    runs = body.split("<h2>Cycles</h2>")[0]
    assert 'class="run run-in-flight"' not in runs
    assert "iteration-cap" in runs
    assert "https://github.invalid/acme/widgets/pull/12" in runs


def test_a_retry_is_its_own_card(db):
    """Paired by issue AND attempt. A retry of an issue that already has an
    outcome is its own Run, and folding the two together would show the first
    Run's Proposal beside the second Run's state."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "agent-failed", "exit": 4, "iterations": 1,
             "faults": "agent-failed", "notified": "sent",
             "proposal": "https://github.invalid/acme/widgets/pull/12"},
        )
        _dispatch_row(conn, cycle, attempt=2)
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
    assert runs.count('<section class="run') == 2
    assert 'class="run run-in-flight"' in runs
    assert "agent-failed" in runs
    assert "attempt 2" in runs


def test_a_dispatch_that_started_no_run_says_so(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "dispatch-failed", "error": "ssh: no route to host"},
        )
    body = client.get("/loop").text
    assert "no Run was started" in body
    assert "ssh: no route to host" in body


def test_a_returned_issue_is_marked_on_the_skip_that_returned_it(db):
    """The loud skip, seen: the comment and the label swap are one fact about
    one issue, so the page shows them on the skip row rather than in a list
    of their own."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        journal.append(
            conn, "issue.skipped",
            {"cycle": cycle, "number": 596, "url": "https://example.invalid/596",
             "reason": "missing-section", "detail": "no `Owning area` section"},
        )
        journal.append(
            conn, "issue.returned",
            {"cycle": cycle, "number": 596, "reason": "missing-section",
             "added_label": "needs-info", "removed_label": "ready-for-agent"},
        )
    body = client.get("/loop").text
    assert "commented, swapped to needs-info" in body


def test_a_return_that_failed_is_not_shown_as_a_return(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        journal.append(
            conn, "issue.skipped",
            {"cycle": cycle, "number": 596, "url": "https://example.invalid/596",
             "reason": "missing-section", "detail": "no `Owning area` section"},
        )
        journal.append(
            conn, "issue.return-failed",
            {"cycle": cycle, "number": 596, "error": "GitHub refused the label swap"},
        )
    body = client.get("/loop").text
    assert "could not hand it back" in body
    assert "GitHub refused the label swap" in body
    assert "commented, swapped to needs-info" not in body


def test_a_run_whose_dispatch_scrolled_off_the_page_is_not_rendered(db):
    """Same rule as the cycle cards: an outcome with no dispatch above it is
    an absence, not a nameless Run."""
    with journal.connect(db) as conn:
        journal.append(
            conn, "run.outcome",
            {"cycle": 999999, "issue": 111, "attempt": 1,
             "outcome": "iteration-cap", "proposal": "https://example.invalid/pull/1"},
        )
    body = client.get("/loop").text
    assert "<h2>Runs</h2>" not in body
    assert "111" in body, "the raw event table still shows it"
