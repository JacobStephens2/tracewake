"""The Cycle, called in-process: a canned queue, a recording doing, a real Journal.

No forked interpreter, no fake command on disk, no environment variables.
The Cycle is handed a tracker and a doing; these tests assert on what it
decided and what it journaled, not on private helpers.

Pick ordering and queue-empty live in the specialized files. This file keeps
the tracers those files do not: two-dispatch re-pick, a doing that fails,
and a tracker that fails.
"""
import pytest

import drain
from conftest import (
    CannedTracker,
    RecordingDoing,
    _cycle_kinds,
    _run,
    events,
    issue,
)


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
