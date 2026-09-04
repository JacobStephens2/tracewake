"""Unattended operation at the cycle's boundary (issue #156).

Three properties a timer-driven Selector needs that a hand-run one did not,
each asserted from outside the cycle - the commands it issued and the rows it
wrote, never its internals:

  * only one cycle at a time, whoever started it. The timer fires every thirty
    minutes and a Run holds the process for ninety, so overlap is the normal
    case rather than the exceptional one.
  * the daily cap holding across cycles, read back from the Journal, so that
    the fifth Run of a day is refused by a process that has no memory of the
    other four.
  * the box's own facts - scripts hash, guest template, agent version - read
    over the box command and journaled, which is what the box card on /loop
    is rendered from.
"""
import subprocess
import sys
from pathlib import Path

import psycopg

from conftest import BOX_FACTS, CYCLE, events, issue, last, one


def _nested_cycle(tmp_path, *args):
    """A script that runs a second, real cycle.py with whatever environment it
    is called in - which inside the fake box is the first cycle's own."""
    script = tmp_path / "nested-cycle.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'exec {sys.executable} {CYCLE} {" ".join(args)}\n'
    )
    script.chmod(0o755)
    return script


# --- One cycle at a time ----------------------------------------------------


def test_a_second_cycle_refuses_while_the_first_is_still_running(db, box, tmp_path):
    """The timer's next firing lands in the middle of a Run. It must not
    reason about a queue the cycle holding the process is already acting on."""
    result = box.run(
        db, [issue(645)], NESTED_CYCLE=str(_nested_cycle(tmp_path))
    )
    assert result.returncode == 0, result.stderr
    log = box.commands()
    assert "nested-begin" in log, "the nested cycle never ran"
    assert "nested-exit 0" in log, (
        "a cycle that finds another one running is not a failure - the timer's"
        " OnFailure must not page for the ordinary overlap"
    )
    assert [e["payload"]["reason"] for e in events(db, "cycle.skipped")] == [
        "cycle-in-progress"
    ]


def test_a_refused_cycle_starts_nothing_it_cannot_finish(db, box, tmp_path):
    """It journals that it stood down and nothing else: a `cycle.started` from
    a cycle that never reasoned would put a card on /loop for a cycle that
    never happened."""
    box.run(db, [issue(645)], NESTED_CYCLE=str(_nested_cycle(tmp_path)))
    # One dispatching cycle ran, so exactly one of each of its rows exists.
    assert len(events(db, "cycle.started")) == 1
    assert len(events(db, "cycle.finished")) == 1
    assert len(events(db, "run.dispatched")) == 1


def test_a_dry_run_is_refused_by_a_running_cycle_too(db, box, tmp_path):
    """A dry run writes to the Journal and reads the spend a live cycle is in
    the middle of changing, so it queues behind one like any other."""
    box.run(
        db, [issue(645)],
        NESTED_CYCLE=str(_nested_cycle(tmp_path, "--dry-run")),
    )
    assert "nested-exit 0" in box.commands()
    assert len(events(db, "cycle.skipped")) == 1


def test_the_lock_is_released_when_the_cycle_ends(db, box, tmp_path):
    """Two cycles one after the other are two cycles, not one and a refusal."""
    box.run(db, [issue(645)])
    box.run(db, [issue(645)], dry_run=True)
    assert events(db, "cycle.skipped") == []
    assert len(events(db, "cycle.started")) == 2


# --- The daily cap, across cycles -------------------------------------------


def test_a_paused_cycle_dispatches_nothing_and_journals_why(db, box):
    """The flag stops the next Run, but not the cycle's explanation of what
    it found. The timer keeps running while paused, so the Journal must say
    why an otherwise Eligible issue stayed put."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE selector.control SET paused = true")

    result = box.run(db, [issue(645)])

    assert result.returncode == 0, result.stderr
    assert events(db, "run.dispatched") == []
    assert "seed " not in box.commands()
    assert "box " not in box.commands()
    finished = last(db, "cycle.finished")
    assert finished["halted"] == "paused"
    assert finished["eligible"] == [645]


def test_resuming_allows_the_next_cycle_to_dispatch_again(db, box):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE selector.control SET paused = true")
    box.run(db, [issue(645)])

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE selector.control SET paused = false")
    result = box.run(db, [issue(645)])

    assert result.returncode == 0, result.stderr
    assert len(events(db, "run.dispatched")) == 1
    assert "box loop/645-the-nightly-sync-script" in box.commands()


def test_the_fifth_dispatch_of_a_day_is_refused_and_journaled(db, box, dispatch):
    """Story 9's bound, held by a process that remembers nothing: the four
    earlier Runs are in the Journal and nowhere else."""
    for number in (640, 641, 642, 643):
        dispatch(db, number, outcome="clean")
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    # The four seeded dispatches and no fifth: the cap refused this one before
    # the box was reached at all.
    assert len(events(db, "run.dispatched")) == 4, "a fifth Run was started"
    assert "box " not in box.commands()
    finished = last(db, "cycle.finished")
    assert finished["halted"] == "daily-cap-reached"
    assert finished["dispatched_in_window"] == 4
    assert finished["daily_cap"] == 4
    # Refused, not skipped over silently: the issue is still eligible, and the
    # Journal says which one the budget cost.
    assert finished["eligible"] == [645]


# --- The box card's facts ---------------------------------------------------


def test_the_box_facts_are_read_and_journaled(db, box):
    box.run(db, [issue(645)])
    observed = one(db, "box.observed")
    assert observed["scripts_hash"] == "8c1f3a90d2"
    assert observed["guest_template"] == "loop-php:1"
    assert observed["agent_version"] == "2.1.221 (Claude Code)"


def test_the_credential_expiry_is_journaled_as_the_instant_the_box_gave(db, box):
    """The fourth fact (#260). Stored as the absolute instant and not as a
    remaining time: the read happens once a cycle and the page is viewed
    whenever, so a duration recorded here would be stale by however long the
    page sat open. The page does that arithmetic against its own clock."""
    box.run(db, [issue(645)])
    assert one(db, "box.observed")["credential_expires_at"] == "2026-08-30T01:13:44Z"


def test_a_box_that_cannot_say_when_its_credential_expires_says_nothing(db, box):
    """The adapter answers nothing when there is no credential or no expiry in
    it, and a fact the box did not report must not be filled in from
    somewhere else - least of all this one, where the invented value would be
    a claim that the box can still start a Run."""
    box.facts("LOOP_BOX_SCRIPTS_HASH=deadbeef01\n")
    box.run(db, [issue(645)])
    assert one(db, "box.observed")["credential_expires_at"] is None


def test_the_box_facts_are_read_before_the_run_holds_the_process(db, box):
    """A Run blocks for ninety minutes. Facts read after it would be a box
    card that goes stale for exactly as long as the page is most worth
    looking at."""
    box.run(db, [issue(645)])
    kinds = [e["kind"] for e in events(db)]
    assert kinds.index("box.observed") < kinds.index("run.dispatched")


def test_a_fact_the_box_does_not_report_is_absent_rather_than_guessed(db, box):
    """The box's copy of the Loop can be older than this repository's - it is
    updated by an ansible apply, not by a merge - so a key that is not there
    is the ordinary case and must not be filled in from somewhere else."""
    box.facts("LOOP_BOX_SCRIPTS_HASH=deadbeef01\n")
    box.run(db, [issue(645)])
    observed = one(db, "box.observed")
    assert observed["scripts_hash"] == "deadbeef01"
    assert observed["guest_template"] is None
    assert observed["agent_version"] is None


def test_a_box_that_cannot_be_read_is_journaled_and_does_not_fail_the_cycle(db, box):
    """An unreachable box is not the Selector failing. The dispatch that
    follows fails on its own and pages on its own; paging twice for one
    outage, once from a status card, is what makes an alert unreadable."""
    box.facts_command('printf "no route to host\\n" >&2\nexit 255\n')
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert "no route to host" in one(db, "box.unreachable")["error"]
    assert events(db, "box.observed") == []
    assert len(events(db, "run.dispatched")) == 1, "the Run still went out"


def test_a_dry_run_does_not_reach_the_box(db, fakes):
    """`ssh` is on the tripwire PATH, so a dry run that read the box would be
    caught by it. A dry run reaches the tracker and nothing else.

    Both outcomes of the read are asserted absent, not just the successful
    one. A box read that was attempted and failed journals
    `box.unreachable`, and a test that only checked for `box.observed` would
    pass for a dry run that reached the box and was refused - which is the
    property broken, not preserved. The same holds for the guardrail.
    """
    result = fakes.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert fakes.tripped() == ""
    assert events(db, "box.observed") == []
    assert events(db, "box.unreachable") == []
    assert events(db, "guardrail.observed") == []
    assert events(db, "guardrail.unreadable") == []
