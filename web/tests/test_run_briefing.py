"""The stored Iteration briefing, where Runs are watched and reviewed (#83).

The Selector journals the first Iteration's rendered briefing at Run start
(#80); this window shows that stored copy - live on the in-flight run card,
and on Run History after the Run ends - verbatim and unredacted to both
roles. It never reconstructs the text from current scripts: what the card
shows is what the agent read, even after the scripts changed.
"""
import pytest
from fastapi.testclient import TestClient

import journal
from app import app

client = TestClient(app)

BRIEFING = (
    "You are Iteration 1 of Run 645 (attempt 1).\n"
    "Discipline skills: /tdd for code work, /code-review before every commit.\n"
    "Completion Promise: recorded, never terminal."
)


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


def _briefing_row(conn, cycle, briefing=BRIEFING, **over):
    payload = {
        "cycle": cycle,
        "issue": 645,
        "attempt": 1,
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "iteration": 1,
        "briefing": briefing,
    }
    payload.update(over)
    return journal.append(conn, "run.briefing", payload)


def _ended(conn, cycle, **over):
    payload = {
        "cycle": cycle, "issue": 645, "attempt": 1,
        "outcome": "iteration-cap", "ended_by": "iteration-cap",
        "exit": 0, "iterations": 5, "faults": "none", "notified": "sent",
        "proposal": "https://github.invalid/acme/widgets/pull/12",
    }
    payload.update(over)
    return journal.append(conn, "run.outcome", payload)


def runs(body):
    """The run cards alone: the page ends with an event table dumping every
    payload verbatim, so a whole-body `in` check would pass for a card that
    rendered nothing at all."""
    return body.split("<h2>Cycles</h2>")[0]


def history(body):
    """The history's live region, cut out of the shell."""
    return body[body.index('<div id="live-region"'):]


def test_the_in_flight_card_shows_the_journaled_briefing(db):
    """First acceptance half: the Run in flight shows the stored briefing
    live, while it runs."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _briefing_row(conn, cycle)
    card = runs(client.get("/").text)
    assert "Iteration briefing" in card
    assert "/tdd for code work" in card
    assert "Completion Promise: recorded, never terminal" in card


def test_the_history_shows_the_briefing_after_the_run_ends(db):
    """Second acceptance half: the ended Run keeps its briefing on History,
    read from the Journal alone."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _briefing_row(conn, cycle)
        _ended(conn, cycle)
    body = history(client.get("/loop/history").text)
    assert "Iteration briefing" in body
    assert "/tdd for code work" in body
    assert "Completion Promise: recorded, never terminal" in body


@pytest.mark.anonymous
def test_both_roles_read_the_briefing_unredacted(db):
    """Admin and Reader see the same stored text: it carries no credentials,
    only paths, the checklist and the promise, so there is nothing to redact."""
    from test_roles import signed_in_admin, signed_in_reader

    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _briefing_row(conn, cycle)
        _ended(conn, cycle)
    for signed_in in (signed_in_admin, signed_in_reader):
        visitor = signed_in(db)
        assert BRIEFING.splitlines()[1] in runs(visitor.get("/").text)
        assert BRIEFING.splitlines()[1] in history(visitor.get("/history").text)


def test_a_run_predating_the_journaling_shows_no_briefing(db):
    """A box from before the emission journals nothing rather than failing -
    and the card invents nothing in its place."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
    assert "Iteration briefing" not in runs(client.get("/").text)
    assert "Iteration briefing" not in history(client.get("/loop/history").text)


def test_a_briefing_is_shown_on_the_run_it_was_journaled_for(db):
    """Paired by issue and attempt like every other row on a card: a retry's
    briefing is its own stored fact, not the attempt before it's."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        _briefing_row(conn, cycle, attempt=1, briefing="briefing for attempt one")
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "agent-failed", "exit": 4, "iterations": 1,
             "faults": "agent-failed"},
        )
        _dispatch_row(conn, cycle, attempt=2)
        _briefing_row(conn, cycle, attempt=2, briefing="briefing for attempt two")
    cards = runs(client.get("/").text).split('<section class="run')[1:]
    assert len(cards) == 2
    newest, oldest = cards
    assert "briefing for attempt two" in newest
    assert "briefing for attempt one" not in newest
    assert "briefing for attempt one" in oldest
    assert "briefing for attempt two" not in oldest


def test_the_stored_copy_is_rendered_verbatim_not_reconstructed(db):
    """The card shows exactly what was journaled - including markup, which is
    escaped as text rather than interpreted, and a marker no current script
    renders - so what you read is what the agent read."""
    stored = "Stored wording v1 <b>not-a-tag</b> & \"quoted\" - scripts now say v2"
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _briefing_row(conn, cycle, briefing=stored)
    card = runs(client.get("/").text)
    assert "Stored wording v1" in card
    assert "&lt;b&gt;not-a-tag&lt;/b&gt;" in card
    assert "<b>not-a-tag</b>" not in card
