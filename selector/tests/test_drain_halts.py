"""Halt reasons, the Review Cap, pause and the archived flag, in-process.

Each test calls the Cycle with a canned queue, a recording doing, and a real
Journal. Forked twins that only reached these decisions through cycle.py are
gone: the Cycle decides here.
"""
from dataclasses import replace

import control
import drain
import events as journal_events
import journal
import testdb
from conftest import (
    CannedTracker,
    RecordingDoing,
    TARGET_REPO,
    a_config,
    a_dispatch_config,
    a_target,
    events,
    issue,
)


def _run(dsn, tracker, doing, *, config=None, dry_run=False):
    with journal.connect(dsn) as conn:
        return drain.run_cycle(
            conn,
            config or a_config(),
            a_dispatch_config(),
            tracker=tracker,
            doing=doing,
            dry_run=dry_run,
        )


def _cycle_kinds(dsn):
    return [e["kind"] for e in events(dsn) if e["kind"].startswith("cycle.")]


def _ops(doing):
    return [c[0] if isinstance(c, tuple) else c for c in doing.calls]


def _k2(repo=None):
    target = a_target() if repo is None else replace(a_target(), repo=repo)
    return replace(a_config(), target=target, drain_concurrency=2)


# --- Pause -------------------------------------------------------------------


def test_a_paused_cycle_dispatches_nothing_and_journals_why(db):
    """The flag stops the next Run, but not the cycle's explanation of what
    it found. The timer keeps running while paused, so the Journal must say
    why an otherwise Eligible issue stayed put — including skip rows."""
    doing = RecordingDoing()
    with journal.connect(db) as conn:
        control.set_paused(conn, True)
    summary = _run(
        db,
        CannedTracker([issue(645), issue(646, blockedBy=1)]),
        doing,
    )
    assert summary.halted == "paused"
    assert summary.picked is None
    assert doing.picks == []
    assert summary.eligible == [645]
    assert summary.considered == 2
    skipped = events(db, "issue.skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["number"] == 646
    assert skipped[0]["payload"]["reason"] == "blocked-by-open-dependency"
    assert events(db, "cycle.finished")[0]["payload"]["halted"] == "paused"


def test_resuming_allows_the_next_cycle_to_dispatch_again(db):
    with journal.connect(db) as conn:
        control.set_paused(conn, True)
    paused = RecordingDoing()
    _run(db, CannedTracker([issue(645)]), paused)
    assert paused.picks == []

    with journal.connect(db) as conn:
        control.set_paused(conn, False)
    resumed = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)]), resumed)
    assert resumed.picks == [645]
    assert summary.picked == 645


def test_a_paused_selector_with_an_empty_queue_reports_paused(db):
    """Paused is tried before queue-empty. Swapping the two `if`s reports
    queue-empty instead."""
    with journal.connect(db) as conn:
        control.set_paused(conn, True)
    summary = _run(db, CannedTracker([]), RecordingDoing())
    assert summary.halted == "paused"
    assert summary.picked is None
    assert summary.considered == 0


# --- Queue-empty and none-eligible -------------------------------------------


def test_an_empty_queue_halts_queue_empty(db):
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([]), doing)
    assert summary.halted == "queue-empty"
    assert summary.picked is None
    assert summary.considered == 0
    assert doing.picks == []
    assert _cycle_kinds(db) == ["cycle.started", "cycle.finished"]
    assert events(db, "cycle.finished")[0]["payload"]["halted"] == "queue-empty"


def test_a_queue_with_nothing_eligible_journals_why(db):
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(646, blockedBy=1)]), doing)
    assert summary.halted == "none-eligible"
    assert summary.picked is None
    assert doing.picks == []
    assert summary.skipped == {"blocked-by-open-dependency": 1}
    skipped = events(db, "issue.skipped")
    assert skipped[0]["payload"]["number"] == 646
    assert skipped[0]["payload"]["reason"] == "blocked-by-open-dependency"


# --- Run in flight -----------------------------------------------------------


def test_a_run_in_flight_stops_the_pick(db):
    testdb.append_run(db, 640, outcome=None)
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)]), doing)
    assert summary.halted == "run-in-flight"
    assert summary.picked is None
    assert doing.picks == []
    assert summary.eligible == [645], "eligibility is still reasoned and journaled"


def test_an_issue_dispatched_again_after_an_outcome_is_in_flight(db):
    """In flight is counted per attempt. An issue with a finished Run and a
    running retry has an outcome on record and is still in flight."""
    testdb.append_run(db, 640, outcome="agent-failed")
    testdb.append_run(db, 640, outcome=None)
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)]), doing)
    assert summary.halted == "run-in-flight"
    assert doing.picks == []


def test_a_dispatch_with_no_outcome_stops_holding_the_lock_once_stale(db):
    """A Run cannot outlive the 90-minute run clock, so a dispatch this old
    with no outcome is a cycle that died before recording one. Without an
    expiry that single death wedges every later cycle forever."""
    testdb.append_run(db, 640, outcome=None, hours_ago=9)
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)]), doing)
    assert summary.picked == 645
    assert doing.picks == [645]
    assert summary.halted != "run-in-flight"


def test_a_recent_dispatch_with_no_outcome_still_holds_the_lock(db):
    testdb.append_run(db, 640, outcome=None, hours_ago=1)
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)]), doing)
    assert summary.halted == "run-in-flight"
    assert doing.picks == []


def test_a_leftover_in_flight_on_one_target_does_not_block_another(db):
    """K>1: a Run a dead Cycle left journaled on widgets does not halt
    gadgets; widgets itself still refuses a second Dispatch."""
    with journal.connect(db) as conn:
        journal.append(
            conn,
            *journal_events.run_dispatched(
                cycle=None, issue=639, title=None, url=None,
                task_ref=f"{TARGET_REPO}#639", attempt=1, branch=None,
                area=None, check=None, kept_progress=None,
            ),
        )
    widgets = RecordingDoing()
    gadgets = RecordingDoing()
    widgets_summary = _run(
        db, CannedTracker([issue(640)]), widgets, config=_k2(),
    )
    gadgets_summary = _run(
        db, CannedTracker([issue(700)]), gadgets, config=_k2("acme/gadgets"),
    )
    assert widgets_summary.halted == "run-in-flight"
    assert widgets.picks == []
    assert gadgets.picks == [700]
    assert gadgets_summary.picked == 700


# --- Review cap --------------------------------------------------------------


def test_the_review_cap_stops_the_pick(db):
    review = [issue(600 + i) for i in range(20)]
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)], review=review), doing)
    assert summary.halted == "review-cap-reached"
    assert summary.picked is None
    assert doing.picks == []
    assert summary.awaiting_review == 20
    assert summary.review_cap == 20
    assert summary.eligible == [645]


def test_the_review_cap_is_configurable(db):
    config = replace(a_config(), target=replace(a_target(), review_cap=1))
    doing = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(645)], review=[issue(640)]),
        doing,
        config=config,
    )
    assert summary.halted == "review-cap-reached"
    assert doing.picks == []
    assert summary.awaiting_review == 1
    assert summary.review_cap == 1


def test_dispatch_resumes_when_awaiting_review_drops_below_cap(db):
    review = [issue(600 + i) for i in range(19)]
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(645)], review=review), doing)
    assert summary.picked == 645
    assert doing.picks == [645]


# --- Archived ----------------------------------------------------------------


def test_an_archived_repository_is_not_operated_on(db):
    """An archived Target is read-only. The Cycle must not consider its
    issues, refresh Proposals, or dispatch, even if the tracker payload
    still lists them."""
    doing = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(645), issue(646, blockedBy=1)], archived=True),
        doing,
    )
    assert summary.halted == "repository-archived"
    assert summary.considered == 0
    assert summary.eligible == []
    assert summary.picked is None
    assert doing.picks == []
    assert "keep_proposals_current" not in _ops(doing)
    assert "work_pick" not in _ops(doing)
    assert not events(db, "issue.skipped")
    assert not events(db, "cycle.picked")
    assert events(db, "cycle.finished")[0]["payload"]["halted"] == "repository-archived"
    assert _cycle_kinds(db) == ["cycle.started", "cycle.finished"]
