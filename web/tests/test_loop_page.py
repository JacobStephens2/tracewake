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
