"""The drain and K greater than one, called in-process.

A canned queue, a recording doing, a real Journal, and a real semaphore.
No forked interpreter. The two orderings a forked Cycle could not see:
the slot is taken before the pick is journaled, and pause is checked
again after the wait for a slot.
"""
from dataclasses import replace
import threading
import time

import pytest

import control
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


def _run(dsn, tracker, doing, *, dry_run=False, slots=None, config=None):
    with journal.connect(dsn) as conn:
        return drain.run_cycle(
            conn,
            config or a_config(),
            a_dispatch_config(),
            tracker=tracker,
            doing=doing,
            dry_run=dry_run,
            slots=slots,
        )


def _parallel_config():
    return replace(a_config(), drain_concurrency=2)


def _thread_run(dsn, tracker, doing, *, slots=None, config=None):
    box = {}

    def target():
        try:
            box["summary"] = _run(
                dsn, tracker, doing, slots=slots, config=config,
            )
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


class BlockingDoing(RecordingDoing):
    """work_pick blocks on `release` after signalling `started`."""

    def __init__(self, started: threading.Event, release: threading.Event):
        super().__init__()
        self._started = started
        self._release = release

    def work_pick(self, conn, cycle_id, config, dispatch_config, pick, attempt):
        self._started.set()
        if not self._release.wait(timeout=10):
            raise drain.CycleFailed("blocked doing was not released")
        return super().work_pick(
            conn, cycle_id, config, dispatch_config, pick, attempt,
        )


class _WaitingSemaphore:
    """Signals when a second acquire has started, then forwards to a real cap."""

    def __init__(self, k: int):
        self._inner = threading.BoundedSemaphore(k)
        self._acquires = 0
        self._lock = threading.Lock()
        self.second_wait = threading.Event()

    def acquire(self, blocking=True, timeout=None):
        with self._lock:
            self._acquires += 1
            n = self._acquires
        if n >= 2:
            self.second_wait.set()
        return self._inner.acquire(blocking, timeout)

    def release(self):
        return self._inner.release()


class _ReviewFollowingTracker(CannedTracker):
    """Review column grows with each worked pick, as a live tracker would."""

    def __init__(self, handover, review, doing):
        super().__init__(handover, review)
        self._doing = doing

    def read(self, config):
        tracked = super().read(config)
        picked = set(self._doing.picks)
        handover = [
            record for record in tracked.handover
            if int(record["number"]) not in picked
        ]
        review = list(tracked.review)
        seen = {int(record["number"]) for record in review}
        for number in self._doing.picks:
            if number not in seen:
                review.append(issue(number))
                seen.add(number)
        return drain.TrackerReads(
            handover=handover,
            review=review,
            archived=tracked.archived,
        )


def test_one_cycle_dispatches_several_runs_and_looks_again(db):
    doing = RecordingDoing()
    summary = _run(db, CannedTracker([issue(640), issue(645)]), doing)
    assert doing.picks == [640, 645]
    assert doing.calls.count("keep_proposals_current") >= 2
    assert summary.dispatches == [640, 645]
    assert summary.picked == 640
    assert summary.considered == 2
    assert summary.eligible == []
    finished = events(db, "cycle.finished")[0]["payload"]
    assert finished["dispatches"] == [640, 645]
    assert finished["picked"] == 640
    assert [e["payload"]["number"] for e in events(db, "cycle.picked")] == [
        640, 645,
    ]


def test_considered_is_the_first_read_and_eligible_is_the_last(db):
    doing = RecordingDoing()
    summary = _run(
        db, CannedTracker([issue(640), issue(645, blockedBy=1)]), doing,
    )
    assert doing.picks == [640]
    assert summary.dispatches == [640]
    assert summary.considered == 2
    assert summary.eligible == []
    assert summary.halted == "none-eligible"


def test_cycle_ends_when_review_cap_is_reached_during_drain(db):
    doing = RecordingDoing()
    config = replace(a_config(), target=replace(a_config().target, review_cap=2))
    summary = _run(
        db,
        _ReviewFollowingTracker(
            [issue(640), issue(645)], [issue(630)], doing,
        ),
        doing,
        config=config,
    )
    assert doing.picks == [640]
    assert summary.dispatches == [640]
    assert summary.halted == "review-cap-reached"
    assert summary.eligible == [645]
    assert summary.awaiting_review == 2
    assert summary.review_cap == 2


def test_two_eligible_tasks_on_one_target_never_overlap_at_any_k(db):
    class OverlapWatchingDoing(RecordingDoing):
        def __init__(self):
            super().__init__()
            self.max_active = 0
            self._active = 0
            self._lock = threading.Lock()

        def work_pick(self, conn, cycle_id, config, dispatch_config, pick, attempt):
            with self._lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                time.sleep(0.05)
                return super().work_pick(
                    conn, cycle_id, config, dispatch_config, pick, attempt,
                )
            finally:
                with self._lock:
                    self._active -= 1

    doing = OverlapWatchingDoing()
    summary = _run(
        db,
        CannedTracker([issue(640), issue(645)]),
        doing,
        slots=threading.BoundedSemaphore(2),
        config=_parallel_config(),
    )
    assert doing.picks == [640, 645]
    assert doing.max_active == 1
    assert summary.dispatches == [640, 645]


def test_a_third_cycle_waits_when_k_is_2(db):
    hold = threading.Event()
    inflight = []
    lock = threading.Lock()
    two_working = threading.Event()

    class HoldingDoing(RecordingDoing):
        def work_pick(self, conn, cycle_id, config, dispatch_config, pick, attempt):
            with lock:
                inflight.append(pick["number"])
                if len(inflight) >= 2:
                    two_working.set()
            if not hold.wait(timeout=10):
                raise drain.CycleFailed("holding doing was not released")
            return super().work_pick(
                conn, cycle_id, config, dispatch_config, pick, attempt,
            )

    slots = threading.BoundedSemaphore(2)
    config = _parallel_config()
    threads = []
    boxes = []
    try:
        for number in (640, 700, 800):
            thread, box = _thread_run(
                db,
                CannedTracker([issue(number)]),
                HoldingDoing(),
                slots=slots,
                config=config,
            )
            threads.append(thread)
            boxes.append(box)
        if not two_working.wait(timeout=5):
            pytest.fail("two Cycles never entered work_pick")
        time.sleep(0.3)
        assert len(inflight) == 2
    finally:
        hold.set()
        for thread in threads:
            thread.join(timeout=5)
    assert all("error" not in box for box in boxes)
    dispatched = sorted(
        n for box in boxes for n in box["summary"].dispatches
    )
    assert dispatched == [640, 700, 800]


def test_pause_during_the_wait_for_a_slot_stops_the_pick_and_releases_it(db):
    """Pause flipped while waiting for a slot: no pick, slot released.

    Fails if the re-check after slots.acquire() is removed: the waiter
    would pick once the holder finishes.
    """
    started = threading.Event()
    release = threading.Event()
    slots = _WaitingSemaphore(1)
    config = _parallel_config()
    holder = BlockingDoing(started, release)
    waiter = RecordingDoing()
    t1, box1 = _thread_run(
        db, CannedTracker([issue(640)]), holder, slots=slots, config=config,
    )
    t2 = None
    box2 = None
    try:
        if not started.wait(timeout=5):
            pytest.fail("first Cycle never entered work_pick")
        t2, box2 = _thread_run(
            db, CannedTracker([issue(645)]), waiter, slots=slots, config=config,
        )
        if not slots.second_wait.wait(timeout=5):
            pytest.fail("second Cycle never waited for a slot")
        with journal.connect(db) as conn:
            control.set_paused(conn, True)
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)
    finally:
        release.set()
        t1.join(timeout=5)
        if t2 is not None:
            t2.join(timeout=5)
    assert waiter.picks == []
    assert [e["payload"]["number"] for e in events(db, "cycle.picked")] == [640]
    assert box2 is not None and "error" not in box2
    assert box2["summary"].halted == "paused"
    assert box2["summary"].picked is None
    assert "error" not in box1
    assert slots.acquire(blocking=False)


def test_a_slot_is_taken_before_the_pick_is_journaled(db):
    """cycle.picked is not written while this Cycle is still waiting for a slot.

    Fails if slots.acquire() moves after the journal append.
    """
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    thread, box = _thread_run(
        db, CannedTracker([issue(640)]), RecordingDoing(), slots=slots,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if events(db, "cycle.started"):
                break
            time.sleep(0.01)
        else:
            pytest.fail("cycle never started")
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            assert events(db, "cycle.picked") == [], (
                "cycle.picked was journaled before the slot was taken"
            )
            time.sleep(0.02)
    finally:
        slots.release()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert "error" not in box
    assert [e["payload"]["number"] for e in events(db, "cycle.picked")] == [640]


def test_a_slot_is_released_when_working_a_pick_raises(db):
    slots = threading.BoundedSemaphore(1)
    doing = RecordingDoing(error="the box started no Run")
    with pytest.raises(drain.CycleFailed, match="the box started no Run"):
        _run(db, CannedTracker([issue(645)]), doing, slots=slots)
    assert doing.picks == [645]
    assert slots.acquire(blocking=False)
