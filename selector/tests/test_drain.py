"""The Cycle, called in-process: a canned queue, a recording doing, a real Journal.

No forked interpreter, no fake command on disk, no environment variables.
The Cycle is handed a tracker and a doing; these tests assert on what it
decided and what it journaled, not on private helpers.
"""
import pytest

import drain
import journal
from conftest import (
    CannedTracker,
    RecordingDoing,
    a_config,
    a_dispatch_config,
    events,
    issue,
)


def _run(dsn, tracker, doing, *, dry_run=False):
    with journal.connect(dsn) as conn:
        return drain.run_cycle(
            conn,
            a_config(),
            a_dispatch_config(),
            tracker=tracker,
            doing=doing,
            dry_run=dry_run,
        )


def _cycle_kinds(dsn):
    return [e["kind"] for e in events(dsn) if e["kind"].startswith("cycle.")]


def test_lowest_eligible_issue_is_picked(db):
    doing = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(651), issue(645), issue(648)]),
        doing,
    )
    assert summary.picked == 645
    assert doing.picks[0] == 645
    assert events(db, "cycle.picked")[0]["payload"]["number"] == 645
    assert _cycle_kinds(db)[0] == "cycle.started"
    assert "cycle.picked" in _cycle_kinds(db)
    assert _cycle_kinds(db)[-1] == "cycle.finished"


def test_an_empty_queue_halts_queue_empty(db):
    summary = _run(db, CannedTracker([]), RecordingDoing())
    assert summary.halted == "queue-empty"
    assert summary.picked is None
    assert summary.considered == 0
    assert _cycle_kinds(db) == ["cycle.started", "cycle.finished"]


def test_a_dispatched_issue_is_not_picked_again(db):
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(640), issue(645)]), doing)
    assert doing.picks == [640, 645]
    assert summary.dispatches == [640, 645]
    assert [e["payload"]["number"] for e in events(db, "cycle.picked")] == [640, 645]


def test_a_doing_that_fails_journals_one_cycle_failed(db):
    doing = RecordingDoing(error="the box started no Run")
    with pytest.raises(drain.CycleFailed, match="the box started no Run"):
        _run(db, CannedTracker([issue(645)]), doing)
    assert doing.picks == [645]
    failed = events(db, "cycle.failed")
    assert len(failed) == 1
    assert failed[0]["payload"]["error"] == "the box started no Run"
    assert not events(db, "cycle.finished")
    assert _cycle_kinds(db) == ["cycle.started", "cycle.picked", "cycle.failed"]


def test_tracker_reads_that_fail_journals_one_cycle_failed(db):
    with pytest.raises(drain.CycleFailed, match="tracker exploded"):
        _run(db, CannedTracker([], error="tracker exploded"), RecordingDoing())
    failed = events(db, "cycle.failed")
    assert len(failed) == 1
    assert failed[0]["payload"]["error"] == "tracker exploded"
    assert not events(db, "cycle.finished")
    assert _cycle_kinds(db) == ["cycle.started", "cycle.failed"]
