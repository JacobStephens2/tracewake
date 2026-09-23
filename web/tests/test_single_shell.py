"""One shell across the window (#84).

Every human-viewed page - board, history, accounts, decisions, and the auth
flows - wears the terminal identity, and the legacy editorial shell
(`base.html` + `app.css`) is deleted so there is no second shell to drift
back to. Clarity of information governs the move: the route badges keep
their meaning and the prompt block stays readable.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import journal
from app import app

client = TestClient(app)

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
STATIC = Path(__file__).resolve().parents[1] / "static"


def _adr_slug():
    """A real decision to open, read off the index rather than hardcoded."""
    match = re.search(r'/adr/([a-z0-9][a-z0-9\-]*)', client.get("/adr").text)
    assert match, "the decisions index names no decision"
    return match.group(1)


def test_the_legacy_shell_is_deleted():
    assert not (TEMPLATES / "base.html").exists(), (
        "base.html must be removed: one shell remains"
    )
    assert not (STATIC / "app.css").exists(), (
        "app.css must be removed: one shell remains"
    )


def test_no_template_references_the_deleted_shell():
    """Quoted names, so `terminal_base.html` - the shell that remains - is
    not mistaken for `base.html`, the one that is gone."""
    offenders = [
        p.name
        for p in TEMPLATES.glob("*.html")
        if re.search(r'''["']base\.html["']''', p.read_text())
        or "app.css" in p.read_text()
    ]
    assert offenders == [], f"templates still reach for the deleted shell: {offenders}"


def test_the_deleted_stylesheet_is_gone_over_http():
    assert client.get("/static/app.css").status_code == 404
    assert client.get("/static/terminal.css").status_code == 200
    assert client.get("/static/loop.css").status_code == 200


# --- Every page wears the single shell -------------------------------------


def _assert_terminal_shell(body, page):
    assert "terminal.css" in body, f"{page} does not wear the terminal shell"
    assert "app.css" not in body, f"{page} still wears the deleted shell"
    assert 'id="theme-toggle"' in body, f"{page} has no terminal header"
    assert 'rel="icon"' in body, f"{page} has no favicon"


def test_board_history_accounts_and_decisions_wear_the_single_shell(db):
    slug = _adr_slug()
    pages = {
        "/": client.get("/").text,
        "/history": client.get("/history").text,
        "/accounts": client.get("/accounts").text,
        "/adr": client.get("/adr").text,
        f"/adr/{slug}": client.get(f"/adr/{slug}").text,
    }
    missing = client.get("/adr/no-such-decision")
    assert missing.status_code == 404
    pages["/adr/no-such-decision"] = missing.text
    for path, body in pages.items():
        assert body, f"{path} rendered nothing"
        _assert_terminal_shell(body, path)


AUTH_PAGES = ["/sign-in", "/forgot", "/reset/no-such-token", "/invite/no-such-token"]


@pytest.mark.anonymous
def test_auth_pages_wear_the_single_shell(db):
    for path in AUTH_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        _assert_terminal_shell(resp.text, path)


def test_every_page_carries_the_decisions_link(db):
    """One site, one nav: wherever you stand you can reach the decisions."""
    bodies = [
        client.get("/").text,
        client.get("/history").text,
        client.get("/accounts").text,
        client.get("/adr").text,
    ]
    for body in bodies:
        assert 'href="/adr"' in body


@pytest.mark.anonymous
def test_auth_pages_carry_no_live_stream(db):
    """Only the live pages open the Journal stream: a page with no live
    region must not open one it never reads."""
    for path in AUTH_PAGES:
        body = client.get(path).text
        assert "EventSource" not in body, path
        assert "/loop/events" not in body, path


def test_live_pages_still_open_the_stream(db):
    for path in ["/", "/history"]:
        body = client.get(path).text
        assert "EventSource" in body, path
        assert "/loop/events" in body, path


# --- Meaning survives the move ----------------------------------------------


def _seed_dispatched_run(conn):
    """One finished-iteration-cap Run's opening rows, shared by the badge
    and briefing tests so the seeded story cannot drift between them."""
    cycle = journal.append(conn, "cycle.started", {"dry_run": False})
    journal.append(conn, "run.dispatched", {
        "cycle": cycle, "issue": 645, "attempt": 1,
        "title": "Widen the sync window",
        "url": "https://example.invalid/645",
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "area": "The nightly sync script",
    })
    return cycle


def _record_iteration_cap_outcome(conn, cycle):
    journal.append(conn, "run.outcome", {
        "cycle": cycle, "issue": 645, "attempt": 1,
        "outcome": "iteration-cap", "ended_by": "iteration-cap",
        "exit": 0, "iterations": 5, "faults": "none", "notified": "sent",
        "proposal": "https://github.invalid/acme/widgets/pull/12",
    })


def test_route_badges_keep_their_colours(db):
    """Green still means reviewable and red still means needs a human: the
    route badge rules are untouched, and a green Run still renders its own
    badge rather than a neutral one."""
    css = (STATIC / "loop.css").read_text()
    for name in ("awaiting-review", "handed-to-human", "given-up", "retrying"):
        assert f".badge-{name}" in css, f"loop.css lost the {name} badge rule"
    with journal.connect(db) as conn:
        cycle = _seed_dispatched_run(conn)
        _record_iteration_cap_outcome(conn, cycle)
        journal.append(conn, "issue.awaiting-review", {
            "cycle": cycle, "issue": 645, "attempt": 1,
            "label": "awaiting-review", "checks": "green",
            "proposal": "https://github.invalid/acme/widgets/pull/12",
        })
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-awaiting-review">awaiting-review</span>' in runs


def test_account_and_decision_badges_borrow_no_route_colour(db):
    """The status words elsewhere are read as words, not glanced as colours:
    no account or decision badge reuses a route badge's class."""
    bodies = [
        client.get("/accounts").text,
        client.get("/adr").text,
        client.get(f"/adr/{_adr_slug()}").text,
    ]
    for body in bodies:
        for name in ("awaiting-review", "handed-to-human", "given-up", "retrying"):
            assert f"badge-{name}" not in body, f"a non-run badge borrows {name}"


def test_the_prompt_block_stays_readable(db):
    """The journaled briefing renders as a prompt block (whitespace kept),
    on the live card and in history - the #83 block, unmoved by the move."""
    briefing = (
        "You are Iteration 1 of Run 645 (attempt 1).\n"
        "Discipline skills: /tdd for code work, /code-review before every commit.\n"
        "Completion Promise: recorded, never terminal."
    )
    with journal.connect(db) as conn:
        cycle = _seed_dispatched_run(conn)
        journal.append(conn, "run.briefing", {
            "cycle": cycle, "issue": 645, "attempt": 1,
            "branch": "loop/645-the-nightly-sync-script",
            "task_ref": "acme/widgets#645",
            "iteration": 1, "briefing": briefing,
        })
        _record_iteration_cap_outcome(conn, cycle)
    for path in ["/", "/history"]:
        body = client.get(path).text
        assert "Iteration briefing" in body, path
        assert "<pre>" in body, f"{path} lost the prompt block"
        assert "/tdd for code work" in body, path
