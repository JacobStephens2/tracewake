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
