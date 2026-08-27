"""Dispatch at its boundary: real cycle.py and real git, faked everything else.

Same rule as the cycle suite - nothing here reads Selector internals. Each
test runs the real `cycle.py` as a subprocess against a canned queue and
observes only what a dispatch can be seen to do from outside: which commands
it issued and with which arguments, what ended up on the remote, and which
rows it appended to the Journal.

Four seams, and one of them deliberately is not faked:

  the tracker command  - a canned queue, as in the cycle suite.
  the Seeding command  - scripted. What is asserted is the arguments the
                         Selector hands it; the real seed-run.sh has its own
                         suite (loop/tests/seed-run.bats). One test below
                         drives the real one all the same, because an
                         argument name that drifted would be invisible to
                         every fake here.
  the box command      - scripted, standing in for the SSH hop and the Run.
                         It answers with a canned LOOP_RUN_* block, which is
                         how "the outcome is captured" becomes an assertion
                         rather than a claim: the box persists nothing, so
                         what the Journal ends up holding can only have come
                         from what this printed.
  the issue command    - scripted. The loud skip's comment arrives on its
                         stdin and is logged verbatim.

  git                  - REAL, against a real bare repository in a tmpdir.
                         Faking it would leave the branch preparation - the
                         one step with a retry case in it - asserted only
                         against a script that agreed with the implementation.
                         Same choice, for the same reason, as the Loop's
                         propose.bats pushing to a real bare repo.

The box command also reads the Journal while it is "running", which is how
the in-flight lock being held FOR THE LENGTH OF THE RUN is checked rather
than assumed: a dispatch journaled after the box returned would leave the
ninety minutes it takes unguarded.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    BODY,
    CLEAN_RUN,
    CYCLE,
    FAILED_RUN,
    SELECTOR,
    _script,
    events,
    issue,
    last,
    one,
)


# --- The dispatch sequence --------------------------------------------------


def test_the_whole_dispatch_sequence_is_issued(db, box):
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    log = box.commands()

    assert "seed --repo" in log
    assert "--task 645" in log
    assert "--task-repo acme/widgets" in log
    assert "--area The nightly sync script" in log
    assert "box loop/645-the-nightly-sync-script acme/widgets#645" in log
    assert log.index("seed ") < log.index("box "), "seeded before the Run started"


def test_the_run_branch_is_created_and_pushed(db, box):
    box.run(db, [issue(645)])
    assert "loop/645-the-nightly-sync-script" in box.remote_branches()


def test_the_plan_is_on_the_remote_before_the_run_starts(db, box):
    """The Plan reaches the box as a commit, like everything else (ADR 0010).
    The box command reports what it would have fetched, so a push that
    happened after the Run started would be visible as an absence here."""
    box.run(db, [issue(645)])
    assert "remote-tree PLAN.md README.md" in box.commands()


def test_the_in_flight_lock_is_held_while_the_run_is_running(db, box):
    """Journaled before the box is reached, not after it returned. A dispatch
    recorded afterwards would leave the length of the Run unguarded, which is
    the window a second cycle would start a second Run in."""
    box.run(db, [issue(645)])
    assert "dispatched-rows 1" in box.commands()


def test_the_branch_names_the_issue_and_the_owning_area(db, box):
    body = BODY.replace("The nightly sync script", "Dashboards & Reports!!")
    box.run(db, [issue(648, body=body)])
    assert "loop/648-dashboards-reports" in box.remote_branches()


def test_the_check_section_is_passed_to_seeding(db, box):
    body = BODY + "\n## Check\n\n```\nscripts/check.sh --strict\n```\n"
    box.run(db, [issue(645, body=body)])
    assert "--check scripts/check.sh --strict" in box.commands()


def test_a_pick_with_no_check_seeds_without_one(db, box):
    box.run(db, [issue(645)])
    assert "--check" not in box.commands()


# --- Capturing the outcome --------------------------------------------------


def test_the_runs_stdout_summary_is_captured_into_the_journal(db, box):
    """Story 13. The box persists none of this - run.sh prints it and exits -
    so everything asserted here can only have come from what was captured."""
    box.run(db, [issue(645)])
    outcome = one(db, "run.outcome")
    assert outcome["outcome"] == "iteration-cap"
    assert outcome["ended_by"] == "iteration-cap"
    assert outcome["exit"] == 0
    assert outcome["iterations"] == 5
    assert outcome["faults"] == "none"
    assert outcome["notified"] == "sent"
    assert outcome["proposal"] == "https://github.invalid/acme/widgets/pull/12"
    assert outcome["issue"] == 645
    assert outcome["branch"] == "loop/645-the-nightly-sync-script"
    assert outcome["attempt"] == 1


def test_the_dispatch_row_carries_what_the_run_was_seeded_from(db, box):
    box.run(db, [issue(645)])
    dispatched = one(db, "run.dispatched")
    assert dispatched["issue"] == 645
    assert dispatched["area"] == "The nightly sync script"
    assert dispatched["task_ref"] == "acme/widgets#645"
    assert dispatched["branch"] == "loop/645-the-nightly-sync-script"


def test_a_run_that_ended_on_a_bound_is_not_a_selector_failure(db, box):
    """The Termination Contract working is not the Selector failing. If a Run
    ending on agent-failed exited this non-zero, the timer's OnFailure would
    page the operator for every Run that did what it was designed to do."""
    box.run_summary(FAILED_RUN)
    result = box.run(db, [issue(645)], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    outcome = one(db, "run.outcome")
    assert outcome["outcome"] == "agent-failed"
    assert outcome["exit"] == 4
    assert outcome["proposal"].endswith("/13")


def test_a_box_that_starts_no_run_fails_the_cycle_loudly(db, box):
    """An SSH that never connected prints no LOOP_RUN_ENDED_BY. That absence
    is the discriminator: a Run that ended badly reported a bound, and a
    dispatch that never happened cannot."""
    box.run_summary("ssh: connect to host loop.etadventures.com port 22: No route\n")
    result = box.run(db, [issue(645)], BOX_EXIT=255)
    assert result.returncode != 0
    outcome = one(db, "run.outcome")
    assert outcome["outcome"] == "dispatch-failed"
    assert "no route" in outcome["error"].lower()


def test_a_failed_dispatch_still_releases_the_in_flight_lock(db, box):
    """One row per dispatch, always. A dispatch journaled with no outcome to
    pair with is what wedges every later cycle at run-in-flight."""
    box.run_summary("ssh: no route\n")
    box.run(db, [issue(645)], BOX_EXIT=255)
    assert len(events(db, "run.dispatched")) == 1
    assert len(events(db, "run.outcome")) == 1


def test_a_seeding_refusal_stops_the_dispatch_before_the_box(db, box):
    """seed-run.sh refuses a task whose acceptance criteria it cannot read.
    That refusal is the component that would otherwise have to guess what
    done means, so it ends the dispatch rather than being worked around."""
    result = box.run(db, [issue(645)], SEED_EXIT=1)
    assert result.returncode != 0
    assert "box " not in box.commands(), "no Run was started"
    assert one(db, "run.outcome")["outcome"] == "dispatch-failed"


# --- Retries and attempts ---------------------------------------------------


def test_a_second_dispatch_is_recorded_as_the_second_attempt(db, box, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    box.run(db, [issue(645)])
    assert last(db, "run.dispatched")["attempt"] == 2


def test_a_retry_continues_the_branch_the_first_attempt_left_behind(db, box):
    """#155 retries on the same branch. A preparation that reset the branch to
    the base would silently discard whatever the first attempt committed and
    propose an empty diff."""
    branch = "loop/645-the-nightly-sync-script"
    box.git("checkout", "--quiet", "-b", branch)
    (box.work / "ITERATION.txt").write_text("work from the first attempt\n")
    box.git("add", "ITERATION.txt")
    box.git("commit", "--quiet", "-m", "Iteration 1")
    box.git("push", "--quiet", "-u", "origin", branch)
    box.git("checkout", "--quiet", "master")

    box.run(db, [issue(645)])
    assert "ITERATION.txt" in box.commands(), (
        "the retry reset the branch and lost the first attempt's commit"
    )


# --- The loud skip ----------------------------------------------------------


def test_a_missing_section_issue_is_commented_on_and_swapped_to_needs_info(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    result = box.run(db, [issue(645, body=body)])
    assert result.returncode == 0, result.stderr
    log = box.commands()
    assert "issue acme/widgets comment 645" in log
    assert "issue acme/widgets relabel 645 needs-info ready-for-agent" in log
    assert log.index("comment 645") < log.index("relabel 645"), (
        "a swap with no comment takes the issue out of the queue silently"
    )


def test_the_comment_names_the_gap_and_the_way_back(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body)])
    log = box.commands()
    comment = log.split("--- body ---")[1].split("--- end ---")[0]
    assert "Acceptance criteria" in comment
    assert "needs-info" in comment
    assert "re-apply `ready-for-agent`" in comment


def test_the_return_is_journaled(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body)])
    returned = one(db, "issue.returned")
    assert returned["number"] == 645
    assert returned["added_label"] == "needs-info"
    assert returned["removed_label"] == "ready-for-agent"
    assert "Acceptance criteria" in returned["detail"]
    assert one(db, "cycle.finished")["returned"] == [645]


def test_a_returned_issue_does_not_stop_a_later_one_being_dispatched(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body), issue(648)])
    assert one(db, "issue.returned")["number"] == 645
    assert one(db, "run.dispatched")["issue"] == 648


def test_an_issue_is_returned_even_when_a_cap_halts_the_cycle(db, box, dispatch):
    """Handing work back is not spending a Run. An issue the Selector will
    never seed should not wait for a free budget to be told so."""
    dispatch(db, 640, outcome=None)
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body)])
    assert one(db, "issue.returned")["number"] == 645
    assert one(db, "cycle.finished")["halted"] == "run-in-flight"


def test_a_dry_run_hands_nothing_back(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body)], dry_run=True)
    assert box.commands() == "", "a dry run wrote to the tracker"
    assert not events(db, "issue.returned")
    assert events(db, "issue.skipped"), "it still reasoned and journaled"


def test_a_dry_run_dispatches_nothing(db, box):
    box.run(db, [issue(645)], dry_run=True)
    assert box.commands() == ""
    assert box.remote_branches() == ["master"]
    assert not events(db, "run.dispatched")


def test_a_return_github_refuses_is_journaled_and_pages(db, box):
    """Story 31: the Selector's own failures reach the operator. An issue
    still sitting in the queue with nothing on it saying why is exactly the
    silence the loud skip exists to prevent."""
    body = "## Owning area\n\nThe nightly sync script\n"
    result = box.run(db, [issue(645, body=body)], ISSUE_EXIT=1)
    assert result.returncode != 0
    assert not events(db, "issue.returned")
    assert one(db, "issue.return-failed")["number"] == 645


def test_only_a_missing_section_is_shouted_at(db, box):
    """Every other skip is quiet. A blocked issue commented on every half hour
    is a queue nobody reads."""
    box.run(db, [issue(646, blockedBy=1), issue(635, openSubIssues=2)])
    assert "issue " not in box.commands()
    assert not events(db, "issue.returned")


# --- The Seeding command, unfaked -------------------------------------------


def test_the_real_seed_step_accepts_what_the_selector_passes_it(db, box, tmp_path):
    """Every other test here scripts the Seeding command, so an argument name
    that drifted would be invisible to all of them. This one drives the real
    seed-run.sh - through its own task-source seam, so it reaches no network -
    and asserts what the Selector handed it produced a Plan."""
    if not (SELECTOR.parent / "loop" / "seed-run.sh").exists():
        pytest.skip("the Loop is not checked out beside the Selector")
    task_source = _script(tmp_path / "task-source.sh", '''
        cat <<JSON
        {"number": $2, "title": "Widen the sync window",
         "url": "https://example.invalid/$2", "state": "OPEN",
         "body": "## Acceptance criteria\\\\n\\\\n- [ ] It is right\\\\n"}
JSON
    ''')
    result = box.run(
        db,
        [issue(645)],
        SELECTOR_SEED_COMMAND=str(SELECTOR.parent / "loop" / "seed-run.sh"),
        LOOP_TASK_SOURCE_COMMAND=str(task_source),
    )
    assert result.returncode == 0, result.stderr
    plan = subprocess.run(
        ["git", "--git-dir", str(box.bare), "show",
         "loop/645-the-nightly-sync-script:PLAN.md"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "acme/widgets#645 - Widen the sync window" in plan
    assert "The nightly sync script" in plan
