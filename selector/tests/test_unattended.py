"""Unattended operation at the cycle's boundary (issue #156).

Cycle drain decisions are in-process. This file still forks because the
advisory lock, the per-Target fan-out, box-facts and proposal freshness are
the entry point or production doing (ADR 0004), not Cycle decisions.

Two of the forked Cycle wiring tests live here: the advisory-lock stand-down
(`test_a_second_cycle_refuses_while_the_first_is_still_running`) and K>1
across two Targets
(`test_two_eligible_tasks_on_different_targets_overlap_when_k_is_2`).

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
    """Forked Cycle wiring: the advisory-lock stand-down.

    The timer's next firing lands in the middle of a Run. It must not
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
    """Still forks: the entry point's lock, not a Cycle decision.

    It journals that it stood down and nothing else: a `cycle.started` from
    a cycle that never reasoned would put a card on /loop for a cycle that
    never happened."""
    box.run(db, [issue(645)], NESTED_CYCLE=str(_nested_cycle(tmp_path)))
    # One dispatching cycle ran, so exactly one of each of its rows exists.
    assert len(events(db, "cycle.started")) == 1
    assert len(events(db, "cycle.finished")) == 1
    assert len(events(db, "run.dispatched")) == 1


def test_a_dry_run_is_refused_by_a_running_cycle_too(db, box, tmp_path):
    """Still forks: the entry point's lock covers dry-run too.

    A dry run writes to the Journal and reads the spend a live cycle is in
    the middle of changing, so it queues behind one like any other."""
    box.run(
        db, [issue(645)],
        NESTED_CYCLE=str(_nested_cycle(tmp_path, "--dry-run")),
    )
    assert "nested-exit 0" in box.commands()
    assert len(events(db, "cycle.skipped")) == 1


def test_the_lock_is_released_when_the_cycle_ends(db, box, tmp_path):
    """Still forks: the entry point's lock lifetime, not a Cycle decision.

    Two cycles one after the other are two cycles, not one and a refusal."""
    box.run(db, [issue(645)])
    box.run(db, [issue(645)], dry_run=True)
    assert events(db, "cycle.skipped") == []
    assert len(events(db, "cycle.started")) == 2


# --- The box card's facts ---------------------------------------------------
#
# Still forks: production doing (ADR 0004) observes the box.


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
    """Still forks: `--dry-run` through the entry point must not reach the box.

    `ssh` is on the tripwire PATH, so a dry run that read the box would be
    caught by it. A dry run reaches the tracker and nothing else.

    Both outcomes of the read are asserted absent, not just the successful
    one. A box read that was attempted and failed journals
    `box.unreachable`, and a test that only checked for `box.observed` would
    pass for a dry run that reached the box and was refused - which is the
    property broken, not preserved. The same holds for the guardrail.
    """
    result = fakes.run(db, [issue(645)], dry_run=True)
    assert result.returncode == 0, result.stderr
    assert fakes.tripped() == ""
    assert events(db, "box.observed") == []
    assert events(db, "box.unreachable") == []
    assert events(db, "guardrail.observed") == []
    assert events(db, "guardrail.unreadable") == []


# --- A Cycle drains the queue (issue #8) ------------------------------------
#
# Multi-pass drain, considered vs eligible, slot ordering, and K>1 slot
# waits live in tests/test_drain_slots.py. What remains here still forks:
# production doing (box facts, pause between live Runs) and the entry-point
# fan-out.


def test_the_box_facts_are_read_before_each_dispatch_and_guardrail_once_per_cycle(db, box):
    """Still forks: production doing observes the box and Guardrail (ADR 0004).

    The box's facts are read before each dispatch, and the Guardrail is
    journaled once per Cycle (#14 AC 4)."""
    result = box.run(db, [issue(640), issue(645)])
    assert result.returncode == 0, result.stderr

    assert len(events(db, "box.observed")) == 2
    assert len(events(db, "guardrail.observed")) == 1

    all_kinds = [e["kind"] for e in events(db)]
    seq = [k for k in all_kinds if k in ("box.observed", "guardrail.observed", "run.dispatched")]
    assert seq == [
        "guardrail.observed",
        "box.observed", "run.dispatched",
        "box.observed", "run.dispatched",
    ]


def test_pause_is_honoured_between_runs_and_does_not_cancel_run_in_flight(db, box, tmp_path):
    """Still forks: pause between live Runs is production doing holding the
    process, not a Cycle decision tested in-process.

    Criterion 3: Pause is honoured before each pick and never cancels a Run
    already in flight."""
    pause_script = tmp_path / "pause-mid-run.sh"
    pause_script.write_text(
        f"#!/usr/bin/env bash\n"
        f"psql \"{db}\" -c 'UPDATE selector.control SET paused = true'\n"
    )
    pause_script.chmod(0o755)

    result = box.run(db, [issue(640), issue(645)], NESTED_CYCLE=str(pause_script))
    assert result.returncode == 0, result.stderr

    # Run 640 finished and was routed cleanly (not canceled)
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [640]
    assert len(events(db, "issue.awaiting-review")) == 1

    # Cycle halted before picking 645
    finished = last(db, "cycle.finished")
    assert finished["dispatches"] == [640]
    assert finished["halted"] == "paused"
    assert finished["eligible"] == [645]


# --- Proposal freshness during drains (Issue #10) ---------------------------
#
# Still forks: production doing (ADR 0004) keeps Proposals current.

def test_open_proposals_behind_base_and_mergeable_are_updated_during_drain(db, box):
    """AC 1: Each open Proposal that is behind its base and mergeable is updated
    during a drain, and journaled once."""
    review_issues = [
        issue(
            630,
            proposals=[
                {
                    "number": 12,
                    "url": "https://github.invalid/acme/widgets/pull/12",
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "BEHIND",
                }
            ],
        ),
    ]
    result = box.run(db, [issue(640)], review_issues=review_issues)
    assert result.returncode == 0, result.stderr

    # Issue 640 was dispatched
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [640]

    # Proposal 12 was updated via issue_command
    assert "issue acme/widgets update-branch 12" in box.commands()

    # And journaled once as proposal.updated
    updated = events(db, "proposal.updated")
    assert len(updated) == 1
    assert updated[0]["payload"]["proposal"] == 12
    assert updated[0]["payload"]["issue"] == 630
    assert updated[0]["payload"]["url"] == "https://github.invalid/acme/widgets/pull/12"


def test_conflicting_proposals_are_not_updated_during_drain(db, box):
    """AC 2: A conflicting Proposal is not updated."""
    review_issues = [
        issue(
            630,
            proposals=[
                {
                    "number": 13,
                    "url": "https://github.invalid/acme/widgets/pull/13",
                    "state": "OPEN",
                    "behind": True,
                    "conflicting": True,
                },
                {
                    "number": 14,
                    "url": "https://github.invalid/acme/widgets/pull/14",
                    "state": "OPEN",
                    "behind": True,
                    "mergeable": "CONFLICTING",
                    "mergeStateStatus": "DIRTY",
                },
            ],
        ),
    ]
    result = box.run(db, [issue(640)], review_issues=review_issues)
    assert result.returncode == 0, result.stderr

    # Proposals 13 and 14 were not updated
    assert "update-branch 13" not in box.commands()
    assert "update-branch 14" not in box.commands()
    assert events(db, "proposal.updated") == []


def test_proposal_updated_only_once_during_multi_dispatch_drain(db, box):
    """AC 1: An open Proposal is journaled once even across multiple dispatches."""
    review_issues = [
        issue(
            630,
            proposals=[
                {
                    "number": 12,
                    "url": "https://github.invalid/acme/widgets/pull/12",
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "BEHIND",
                }
            ],
        ),
    ]
    result = box.run(db, [issue(640), issue(645)], review_issues=review_issues)
    assert result.returncode == 0, result.stderr

    # Both 640 and 645 dispatched
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [640, 645]

    # But proposal 12 was updated and journaled only once
    updated = events(db, "proposal.updated")
    assert len(updated) == 1
    assert updated[0]["payload"]["proposal"] == 12


def test_refused_proposal_update_is_journaled_and_fails_no_dispatch(db, box):
    """AC 3: An update the forge refuses is journaled and fails no dispatch."""
    review_issues = [
        issue(
            630,
            proposals=[
                {
                    "number": 14,
                    "url": "https://github.invalid/acme/widgets/pull/14",
                    "state": "OPEN",
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "BEHIND",
                }
            ],
        ),
    ]
    result = box.run(
        db, [issue(640)], review_issues=review_issues, UPDATE_BRANCH_EXIT="1"
    )
    assert result.returncode == 0, result.stderr

    # Dispatch still happened despite update failure
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [640]

    # The refusal was journaled as proposal.update-failed
    failed = events(db, "proposal.update-failed")
    assert len(failed) == 1
    assert failed[0]["payload"]["proposal"] == 14
    assert failed[0]["payload"]["issue"] == 630
    assert "could not update branch" in failed[0]["payload"]["error"]


# --- The parallel drain (issue #37) -----------------------------------------
#
# Seam: the real cycle.py, a scripted box that can hold the process, and the
# Journal. Overlap is a fact about timestamps and row order, not about
# threads.


def _gadget_target(work):
    return {
        "repo": "acme/gadgets",
        "work_repo": str(work),
        "box_repo": "/nonexistent/box-gadgets",
        "token_file": "/nonexistent/token-gadgets",
        "guest_template": "gadgets-guest:1",
    }


def _run_kinds(dsn):
    return [
        e["kind"]
        for e in events(dsn)
        if e["kind"] in ("run.dispatched", "run.outcome")
    ]


def test_two_eligible_tasks_on_different_targets_overlap_when_k_is_2(db, box):
    """Forked Cycle wiring: K>1 across two Targets.

    K=2, two Targets, one Eligible each: both Runs are in flight at once,
    and the Journal's account proves the overlap."""
    gadgets_work = box.extra_work("work-gadgets")
    box.queue_for("acme/widgets", "ready-for-agent", [issue(640)])
    box.queue_for("acme/gadgets", "ready-for-agent", [issue(700)])

    result = box.run(
        db, [],
        targets=[{}, _gadget_target(gadgets_work)],
        SELECTOR_DRAIN_CONCURRENCY="2",
        BOX_SLEEP="0.4",
    )
    assert result.returncode == 0, result.stderr

    dispatched = [e["payload"]["issue"] for e in events(db, "run.dispatched")]
    assert sorted(dispatched) == [640, 700]
    assert len(events(db, "cycle.started")) == 2
    assert len(events(db, "cycle.finished")) == 2
    # Both dispatches land before either outcome: the second Run started
    # while the first was still holding the process.
    assert _run_kinds(db)[:2] == ["run.dispatched", "run.dispatched"]
    for row in events(db, "cycle.finished"):
        assert row["payload"]["dispatches"] in ([640], [700])
        assert row["payload"]["halted"] == "queue-empty"


def test_k_unset_keeps_the_serial_drain_across_targets(db, box):
    """Still forks: the entry point's default K, not a Cycle decision.

    K unset defaults to 1: two Targets still drain one after the other,
    and each still has its own cycle.started / cycle.finished pair."""
    gadgets_work = box.extra_work("work-gadgets")
    box.queue_for("acme/widgets", "ready-for-agent", [issue(640)])
    box.queue_for("acme/gadgets", "ready-for-agent", [issue(700)])

    result = box.run(
        db, [],
        targets=[{}, _gadget_target(gadgets_work)],
        BOX_SLEEP="0.2",
    )
    assert result.returncode == 0, result.stderr
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [
        640, 700,
    ]
    assert _run_kinds(db) == [
        "run.dispatched", "run.outcome",
        "run.dispatched", "run.outcome",
    ]
    assert [
        e["payload"]["repo"] for e in events(db, "cycle.started")
    ] == ["acme/widgets", "acme/gadgets"]
    finished = events(db, "cycle.finished")
    assert [row["payload"]["dispatches"] for row in finished] == [
        [640], [700],
    ]

