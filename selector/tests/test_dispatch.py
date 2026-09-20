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
    RUN_BRIEFING_TEXT,
    SELECTOR,
    _script,
    events,
    hours_ago_iso,
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


def test_the_unenrolled_warning_does_not_write_to_the_tracker(db, box):
    """Issue #39: the warning is a Journal row and a notice. It must not
    comment, relabel, or otherwise mutate a repository the instance has not
    been enrolled to work."""
    result = box.run(
        db, [],
        owner_issues=[{
            "repo": "acme/other",
            "number": 7,
            "title": "Do the thing",
            "url": "https://github.invalid/acme/other/issues/7",
        }],
    )
    assert result.returncode == 0, result.stderr
    log = box.commands()
    assert "search " in log
    assert "issue " not in log
    warned = events(db, "target.unenrolled")
    assert warned
    assert warned[-1]["payload"]["new"] == ["acme/other"]


def test_the_plan_is_on_the_remote_before_the_run_starts(db, box):
    """The Plan reaches the box as a commit, like everything else (ADR 0010).
    The box command reports what it would have fetched, so a push that
    happened after the Run started would be visible as an absence here."""
    box.run(db, [issue(645)])
    assert "remote-tree PLAN.md PROGRESS.md README.md" in box.commands()


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
    assert outcome["ended_by"] == "iteration-cap"
    assert outcome["exit"] == 0
    assert outcome["iterations"] == 5
    assert outcome["faults"] == "none"
    assert outcome["notified"] == "sent"
    assert outcome["proposal"] == "https://github.invalid/acme/widgets/pull/12"
    assert outcome["issue"] == 645
    assert outcome["branch"] == "loop/645-the-nightly-sync-script"
    assert outcome["attempt"] == 1


def test_the_runs_first_briefing_is_journaled(db, box):
    """Issue #80. The box emits the first Iteration's rendered briefing and
    the Selector journals it as a vocabulary-owned payload - the stored fact
    of what the agent read, provable after the scripts change."""
    box.run(db, [issue(645)])
    briefing = one(db, "run.briefing")
    assert briefing["issue"] == 645
    assert briefing["attempt"] == 1
    assert briefing["branch"] == "loop/645-the-nightly-sync-script"
    assert briefing["task_ref"] == "acme/widgets#645"
    assert briefing["iteration"] == 1
    assert briefing["briefing"] == RUN_BRIEFING_TEXT


def test_the_briefing_row_precedes_the_outcome_and_carries_no_credentials(
        db, box):
    """Journaled before the outcome, so the rows read in the order the Run
    happened them - and safe for the window to show both roles."""
    box.run(db, [issue(645)])
    kinds = [row["kind"] for row in events(db)]
    assert kinds.index("run.briefing") < kinds.index("run.outcome")
    payload = one(db, "run.briefing")
    assert set(payload) == {"cycle", "issue", "attempt", "branch",
                            "task_ref", "iteration", "briefing"}
    text = payload["briefing"]
    assert "PLAN.md" in text
    assert "LOOP: WORK COMPLETE" in text
    for secret in ("ghp_", "github_pat_", "TOKEN", "PRIVATE KEY"):
        assert secret not in text


def test_a_box_that_predates_the_briefing_journals_no_briefing_row(db, box):
    """An old box emits no block: the dispatch still succeeds, journaling
    nothing for it rather than failing."""
    box.run_summary(CLEAN_RUN.split("LOOP_BRIEFING_BEGIN")[0])
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert events(db, "run.briefing") == []
    assert one(db, "run.outcome")["ended_by"] == "iteration-cap"


def test_a_dispatch_that_started_no_run_journals_no_briefing(db, box):
    """No Run, no briefing: a box that never connected leaves no stored
    fact behind."""
    box.run_summary("ssh: no route\n")
    box.run(db, [issue(645)], BOX_EXIT=255)
    assert events(db, "run.briefing") == []


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
    assert outcome["ended_by"] == "agent-failed"
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
    assert outcome["ended_by"] == "dispatch-failed"
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
    assert one(db, "run.outcome")["ended_by"] == "dispatch-failed"


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


BRANCH_645 = "loop/645-the-nightly-sync-script"


def _on_branch(box, path, branch=BRANCH_645):
    """One file as it stands on a remote branch, or "" when it is not there."""
    return subprocess.run(
        ["git", "--git-dir", str(box.bare), "show", f"{branch}:{path}"],
        capture_output=True, text=True,
    ).stdout


def test_a_second_dispatch_within_one_handover_starts_a_run(db, box):
    """#301. The first attempt's Run committed a Progress Log to the branch,
    and Seeding refuses to overwrite one. With MAX_ATTEMPTS = 2 that made the
    retry unreachable: the second dispatch of any issue whose first attempt
    ran died at Seeding, and the whole retry budget was spent on two
    dispatches that never started anything."""
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)])
    assert last(db, "issue.retrying")["attempt"] == 1

    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    outcome = last(db, "run.outcome")
    assert outcome["attempt"] == 2
    assert outcome["ended_by"] == "agent-failed", "the second dispatch reached a Run"


def test_a_fresh_handover_of_a_worked_issue_starts_a_run(db, box):
    """The other shape #301 reaches, and the one tourbot#646 hit: an issue
    labeled again long after its Run, whose branch is still on the remote.
    Attempt 1 as far as the budget is concerned, and the same refusal."""
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)])

    # Labeled again just now, so nothing has been dispatched since the
    # Handover: the budget is untouched and only the branch remembers.
    result = box.run(db, [issue(645, labeledAt=hours_ago_iso(0))])
    assert result.returncode == 0, result.stderr
    assert last(db, "run.outcome")["ended_by"] == "agent-failed"


def test_a_first_dispatch_off_a_base_that_carries_a_progress_log_starts_a_run(db, box):
    """The shape tourbot#646 actually hit, and the widest of the three: a Run
    proposes its Progress Log along with its work, so master carries the last
    merged Run's log. Every dispatch cut from that base - first attempt, brand
    new issue, branch that never existed - then met the refusal."""
    (box.work / "PROGRESS.md").write_text(
        "# Progress Log\n\n## Run started 2026-08-29 16:51:30\n\n"
        "Task: acme/widgets#640\n\n### Iteration 1\n\nA Run that was merged.\n"
    )
    box.git("add", "PROGRESS.md")
    box.git("commit", "--quiet", "-m", "Merge a Run's bookkeeping into master")
    box.git("push", "--quiet", "origin", "master")

    result = box.run(db, [issue(648)])
    assert result.returncode == 0, result.stderr
    assert one(db, "run.outcome")["ended_by"] == "iteration-cap"
    assert "A Run that was merged" in _on_branch(
        box, "PROGRESS-earlier.md", "loop/648-the-nightly-sync-script"
    )


def test_the_earlier_attempts_progress_log_is_kept_on_the_branch(db, box):
    """The record survives the re-seed. seed-run.sh's refusal exists because
    the Progress Log is the only account of what a Run did; moving it aside
    honours that where passing --reseed would have discarded it."""
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)])
    box.run(db, [issue(645)])

    kept = _on_branch(box, "PROGRESS-earlier.md")
    assert "What the first attempt tried" in kept, (
        "the retry discarded the first attempt's Progress Log"
    )
    assert kept.count("## Run started") == 1
    assert _on_branch(box, "PROGRESS.md").count("## Run started") == 1, (
        "the live Progress Log is the second attempt's alone, seeded fresh"
    )


def test_a_third_attempts_keeping_appends_rather_than_overwrites(db, box):
    """One file that accumulates. A keeping that wrote whole would preserve
    the newest attempt by discarding the one before it, which is the loss the
    move is there to avoid."""
    branch = BRANCH_645
    box.git("checkout", "--quiet", "-b", branch)
    (box.work / "PROGRESS.md").write_text(
        "# Progress Log\n\n## Run started earlier\n\nWhat attempt one tried.\n"
    )
    (box.work / "PROGRESS-earlier.md").write_text(
        "# Earlier Progress Logs\n\n## Run started long ago\n\nWhat attempt nought tried.\n"
    )
    box.git("add", "PROGRESS.md", "PROGRESS-earlier.md")
    box.git("commit", "--quiet", "-m", "An attempt that ran")
    box.git("push", "--quiet", "-u", "origin", branch)
    box.git("checkout", "--quiet", "master")

    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)])

    kept = _on_branch(box, "PROGRESS-earlier.md")
    assert "What attempt nought tried" in kept
    assert "What attempt one tried" in kept


def test_a_new_branch_starts_its_kept_log_rather_than_inheriting_one(db, box):
    """Per branch, not for ever. The kept file is proposed and merged like any
    other file, so a branch cut from a base that carries one would inherit an
    unrelated issue's record and append to it - and one file would grow for
    the life of the repository. What the base carries was merged, so its
    history is on the base branch either way."""
    (box.work / "PROGRESS.md").write_text(
        "# Progress Log\n\n## Run started 2026-08-29 16:51:30\n\nThis issue's Run.\n"
    )
    (box.work / "PROGRESS-earlier.md").write_text(
        "# Earlier Progress Logs\n\n## Run started 2026-08-01 09:00:00\n\n"
        "Some other issue's attempt, merged long ago.\n"
    )
    box.git("add", "PROGRESS.md", "PROGRESS-earlier.md")
    box.git("commit", "--quiet", "-m", "Two merged Runs' bookkeeping")
    box.git("push", "--quiet", "origin", "master")

    box.run(db, [issue(645)])

    kept = _on_branch(box, "PROGRESS-earlier.md")
    assert "This issue's Run" in kept
    assert "Some other issue's attempt" not in kept, (
        "the kept log accumulates unrelated issues and never stops growing"
    )


def test_keeping_a_progress_log_is_journaled(db, box):
    """A file that moved on the Run's branch is not something to do silently:
    the row is what a reader follows from `dispatch-failed` to why it stopped
    being one."""
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)])
    assert last(db, "run.dispatched")["kept_progress"] is None

    box.run(db, [issue(645)])
    assert last(db, "run.dispatched")["kept_progress"] == "PROGRESS-earlier.md"


# --- The loud skip ----------------------------------------------------------


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


def test_a_dry_run_hands_nothing_back(db, box):
    body = "## Owning area\n\nThe nightly sync script\n"
    box.run(db, [issue(645, body=body)], dry_run=True)
    log = box.commands()
    assert "issue " not in log, "a dry run wrote to the tracker"
    assert "seed " not in log
    assert "box " not in log
    assert not events(db, "issue.returned")
    assert events(db, "issue.skipped"), "it still reasoned and journaled"


def test_a_dry_run_dispatches_nothing(db, box):
    box.run(db, [issue(645)], dry_run=True)
    log = box.commands()
    assert "seed " not in log
    assert "box " not in log
    assert box.remote_branches() == ["master"]
    assert not events(db, "run.dispatched")


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


# --- The target's own settings reach the dispatch (issue #3) ----------------


def test_the_box_command_is_handed_the_targets_checkout_token_and_image(
    db, box
):
    """Three values that differ per target and are consumed on the box: which
    checkout a Run works in, which repository token it pushes with, and which
    guest image its Iterations are built from.

    They travel as environment because what reads them is a script (ADR 0004),
    and they are overlaid at the call site rather than exported into the
    process, so a drain that worked two targets cannot hand the second one the
    first's token.
    """
    result = box.run(db, [issue(645)], targets=[{
        "repo": "acme/gadgets",
        "box_repo": "/home/loop/gadgets",
        "token_file": "/home/loop/.config/loop/gadgets-token",
        "guest_template": "gadgets-python:1",
    }])

    assert result.returncode == 0, result.stderr
    assert (
        "box-env repo=acme/gadgets box_repo=/home/loop/gadgets"
        " token=/home/loop/.config/loop/gadgets-token"
        " guest=gadgets-python:1"
    ) in box.commands()


def test_seeding_happens_in_the_targets_own_work_checkout(db, box):
    """The Selector seeds off the box, as the operator (ADR 0010), so each
    target needs its own clone on the controller. The Plan has to be committed
    in that clone and pushed from it - a dispatch that seeded somewhere else
    would push somebody else's branch."""
    result = box.run(db, [issue(645)])

    assert result.returncode == 0, result.stderr
    seeded = [line for line in box.commands().splitlines()
              if line.startswith("seed ")]
    assert seeded, box.commands()
    assert f"--repo {box.work_repo}" in seeded[0]
