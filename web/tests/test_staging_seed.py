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
    / "selector" / "seed.sql"
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
    # The guardrail chip (#165), journaled by the same part of the cycle as
    # the box card and invisible in a preview without a row of its own.
    "the executed paths are review-gated",
    # The watcher's rows (#157): the in-flight card's Iteration table (a
    # no-op Iteration is its most distinctive cell), and the one-row failure
    # a Run whose Progress Log could not be read carries instead.
    "no-op",
    "The Progress Log could not be read",
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


def _constructor_shapes():
    """Every payload shape the shipping writer can produce, per kind, built
    by calling every constructor once with stand-in values. The expected key
    sets come from the vocabulary itself, so the fixture is graded against
    the writer it impersonates and not against a hand-kept list."""
    import events
    samples = [
        events.cycle_started(repo="r", label="l", allowlist=["a"],
                             daily_cap=4, dry_run=False),
        events.cycle_skipped(reason="cycle-in-progress"),
        events.cycle_picked(cycle=1, number=2, title="t", url="u", area="a",
                            check="c"),
        events.cycle_finished(cycle=1, considered=0, eligible=[], skipped={},
                              picked=None, halted=None, in_flight=None,
                              dispatched_in_window=0, daily_cap=4,
                              returned=[], dry_run=False),
        events.cycle_failed(cycle=1, error="e"),
        events.issue_skipped(cycle=1, number=2, title="t", url="u",
                             reason="r", detail="d"),
        events.issue_returned(cycle=1, number=2, title="t", url="u",
                              reason="r", detail="d", added_label="a",
                              removed_label="b"),
        events.issue_return_failed(cycle=1, number=2, title="t", url="u",
                                   reason="r", detail="d", added_label="a",
                                   removed_label="b", error="e"),
        events.issue_retrying(cycle=1, issue=2, title="t", url="u", attempt=1,
                              outcome="agent-failed", proposal=None),
        events.issue_awaiting_review(cycle=1, issue=2, title="t", url="u",
                                     attempt=1, outcome="iteration-cap",
                                     proposal="p", label="l", checks="green"),
        events.issue_given_up(cycle=1, issue=2, title="t", url="u", attempt=2,
                              outcome="agent-failed", proposal=None,
                              label="l"),
        events.issue_handed_to_human(cycle=1, issue=2, title="t", url="u",
                                     attempt=1, outcome="iteration-cap",
                                     proposal="p", label="l", checks="red",
                                     failing=[]),
        events.issue_route_failed(cycle=1, issue=2, title="t", url="u",
                                  attempt=1, outcome="iteration-cap",
                                  proposal="p", label=None, error="e"),
        events.issue_route_failed(cycle=1, issue=2, title="t", url="u",
                                  attempt=1, outcome="iteration-cap",
                                  proposal="p", label="l", error="e",
                                  checks="green"),
        events.issue_route_failed(cycle=1, issue=2, title="t", url="u",
                                  attempt=1, outcome="iteration-cap",
                                  proposal="p", label="l", error="e",
                                  checks="red", failing=["x"]),
        events.run_dispatched(cycle=1, issue=2, title="t", url="u",
                              task_ref="tr", attempt=1, branch="b", area="a",
                              check=None, kept_progress=None),
        events.run_outcome(cycle=1, issue=2, title="t", url="u",
                           task_ref="tr", attempt=1, branch="b",
                           ended_by="iteration-cap", exit=0, iterations=1,
                           faults="none", proposal="p", proposed="proposed",
                           notified="sent", seed="s", criteria="1"),
        events.run_dispatch_failed(cycle=1, issue=2, title="t", url="u",
                                   task_ref="tr", attempt=1, branch="b",
                                   error="e"),
        events.run_iteration(cycle=1, issue=2, attempt=1, branch="b",
                             task_ref="tr", iteration=1, started="s",
                             run_started="s", agent_exit=0, exit_note=None,
                             turn_bound=40, noop=False, head_before="h",
                             head_after="h", promise=None, dirty=False),
        events.run_contract(cycle=1, issue=2, attempt=1, branch="b",
                            task_ref="tr", run_started="s", contract=[]),
        events.run_watch_failed(cycle=1, issue=2, attempt=1, branch="b",
                                task_ref="tr", error="e"),
        events.box_observed(cycle=1, scripts_hash="h", guest_template="g",
                            agent="a", agent_version="v",
                            credential_expires_at="t"),
        events.box_unreachable(cycle=1, error="e"),
        events.guardrail_observed(cycle=1, ref="r", ref_head="h", rules=[],
                                  paths=[], unreviewed=[], protected=True,
                                  detail=None),
        events.guardrail_unreadable(cycle=1, error="e"),
    ]
    shapes = {}
    for kind, payload in samples:
        shapes.setdefault(kind, set()).add(frozenset(payload))
    return shapes


def test_every_seeded_row_wears_a_shape_the_writer_writes(seeded):
    """Writer-strict, where the page's readers are deliberately tolerant: a
    reader must take rows from any era, but the fixture impersonates today's
    writer, and a fixture that drifts from the writer is reader-side folklore
    with a database behind it - `box.observed` seeded without
    `credential_expires_at` meant no preview could ever render the credential
    headline, and nothing said so.
    """
    shapes = _constructor_shapes()
    with psycopg.connect(seeded, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT kind, payload FROM journal.events"
        ).fetchall()
    strays = []
    for kind, payload in rows:
        allowed = shapes.get(kind)
        if allowed is None:
            strays.append(f"{kind}: no constructor produces this kind")
        elif frozenset(payload) not in allowed:
            strays.append(f"{kind}: keys {sorted(payload)}")
    assert not strays, (
        "seeded rows the writer would not write:\n" + "\n".join(strays)
    )
    # And the whole vocabulary, not most of it: the header's promise is that
    # every state appears, and five kinds were quietly absent for a year of
    # the file saying so.
    assert {kind for kind, _ in rows} == set(shapes), (
        "the fixture no longer covers every kind the Selector writes"
    )


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
    / "selector" / "preview-sources" / "tracker.sh"
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
