"""The staging Journal fixture (`selector/seed.sql`, ADR 0016).

The live Journal has never held a `run.dispatched` or a `run.outcome` row, so
a preview pointed at real data shows a page with no Run cards and proves
nothing about a branch that changed how Run cards look. This fixture is what
makes every state visible - which makes these tests the ones that fail when a
branch breaks a state, and when the fixture stops covering one.
"""
import re
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import column

client = TestClient(app)

SEED = (
    Path(__file__).resolve().parents[2]
    / "single-user-factory" / "selector" / "seed.sql"
)

# Every state /loop can render. A state on the page and not in this list is a
# state nothing checks; a state here and not on the page is a broken branch.
STATES = [
    "in flight",
    "awaiting-review",
    "ready-for-human",
    "retrying",
    "no Run was started",
    "picked nothing",
    "commented, swapped to needs-info",
    # The three the fixture missed on the first pass, each rendered by
    # loop.html and each previously invisible in a preview.
    "cycle failed:",
    "could not hand it back",
    "did not finish",
    # The status strip's box card (#156). The strip's other three cells are
    # not seedable - the timer comes from systemd and the budget is counted
    # from the dispatch rows already above - but the box card is Journal data
    # like everything else here, and without a row it renders `not read yet`.
    "loop scripts",
    "not reported",
]


@pytest.fixture
def seeded(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(SEED.read_text())
    return db


def test_the_fixture_renders_every_state_the_page_has(seeded):
    body = client.get("/loop").text
    assert "unavailable" not in body.lower()
    for state in STATES:
        assert state in body, f"the fixture no longer shows: {state}"


# The Run states the history page shows (#160). A subset of STATES rather than
# all of it: the history is the ended Runs, so "in flight" is deliberately
# absent, and the cycle cards it does not render take their states with them.
HISTORY_STATES = [
    "awaiting-review",
    "ready-for-human",
    "retrying",
    "no Run was started",
    "did not finish",
]


def test_the_fixture_renders_every_state_the_history_has(seeded):
    body = client.get("/loop/history").text
    assert "unavailable" not in body.lower()
    for state in HISTORY_STATES:
        assert state in body, f"the fixture no longer shows: {state}"
    # The two facts the history adds to a card, so a preview of a branch that
    # broke either one shows it: how long the Run took, and the Proposal that
    # outlives its branch.
    assert re.search(r"after \d+[hms]", body), "no Run duration on the history"
    assert "/pull/701" in body
    # And not the Run still going, which belongs on /loop. The badge rather
    # than the words: the page's own prose says where the in-flight Run is.
    assert '<span class="badge">in flight</span>' not in body


def test_both_ends_of_the_retry_are_visible(seeded):
    """A retried attempt and the give-up that followed it are two cards, and
    the fixture has to hold both or the pairing rule goes untested."""
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
    assert "badge-retrying" in runs
    assert "badge-given-up" in runs
    assert "attempt 2" in runs


def test_a_red_proposal_names_its_failing_checks(seeded):
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
    assert "badge-handed-to-human" in runs
    assert "phpunit" in runs and "lint" in runs


def test_every_skip_reason_the_selector_can_journal_is_shown(seeded):
    body = client.get("/loop").text
    for reason in ("blocked-by-open-dependency", "proposal-open", "missing-section"):
        assert reason in body


def test_seeding_twice_changes_nothing(seeded):
    """The table is append-only, so a fixture that appended on every apply
    would have no way back. Re-applying has to be a no-op."""
    with psycopg.connect(seeded, autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM journal.events").fetchone()[0]
        conn.execute(SEED.read_text())
        after = conn.execute("SELECT count(*) FROM journal.events").fetchone()[0]
    assert before == after


def test_the_fixture_never_names_the_live_journal(seeded):
    """A preview writes into a database it is granted; the fixture must not
    smuggle the live one's name into anything that could be copy-pasted."""
    assert "dbname=selector\n" not in SEED.read_text()


# The board is the one panel a preview cannot show from `seed.sql`: it is read
# from the tracker, which in a preview is `preview-sources/tracker.sh` (ADR
# 0016). Same rule as the fixture above, applied to the other source - a
# column the preview fixture cannot fill is a column no preview can show you.

PREVIEW_TRACKER = (
    Path(__file__).resolve().parents[2]
    / "single-user-factory" / "selector" / "preview-sources" / "tracker.sh"
)

BOARD_COLUMNS = [
    "eligible", "blocked", "in-flight", "awaiting-review", "ready-for-human",
]


def test_the_preview_tracker_fills_every_column_of_the_board(db, monkeypatch):
    monkeypatch.setenv("SELECTOR_TRACKER_COMMAND", str(PREVIEW_TRACKER))
    body = client.get("/loop").text
    for name in BOARD_COLUMNS:
        cards = column(body, name)
        assert "nothing here" not in cards, (
            f"the preview fixture leaves the {name} column empty"
        )
