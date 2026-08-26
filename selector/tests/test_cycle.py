"""The Selector cycle at its boundary: real cycle.py, faked external commands.

Nothing here reads Selector internals. Each test scripts a canned tracker
response, runs the real `cycle.py --dry-run` as a subprocess, and observes only
what a cycle can be seen to do from outside: which commands it issued, and
which rows it appended to the Journal.

Two boundaries are watched:

  Seam 1 - the tracker command. SELECTOR_TRACKER_COMMAND is the whole of the
  cycle's outward reach in dry-run, so every scenario is a different canned
  queue through that one seam.

  Seam 2 - the Journal. A throwaway database per test (testdb.py), read back
  through journal.events.

A third thing is asserted everywhere: the tripwire. `gh`, `git`, `ssh` and
`seed-run.sh` are shimmed on PATH to log and fail, so "dry-run stops before
writing to anything but the Journal" is a checked property of every scenario
rather than a claim in a docstring.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import journal

SELECTOR = Path(__file__).resolve().parents[1]
CYCLE = SELECTOR / "cycle.py"

BODY = """## Problem

Something is wrong.

## Acceptance criteria

- [ ] It is right

## Owning area

The nightly sync script
"""


def _hours_ago_iso(hours):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(number, **over):
    """One tracker record, eligible unless a field is overridden."""
    record = {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.invalid/{number}",
        "state": "OPEN",
        "body": BODY,
        "labeledBy": "JacobStephens2",
        "labeledAt": None,
        "blockedBy": 0,
        "openSubIssues": 0,
        "proposals": [],
    }
    record.update(over)
    return record


@pytest.fixture
def fakes(tmp_path):
    """A fake tracker command plus a tripwire PATH; returns a runner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tripped = tmp_path / "tripped.log"

    for name in ("gh", "git", "ssh", "seed-run.sh"):
        shim = bin_dir / name
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s %s\\n" "{name}" "$*" >> "{tripped}"\n'
            "exit 1\n"
        )
        shim.chmod(0o755)

    queue_file = tmp_path / "queue.json"
    tracker = tmp_path / "tracker.sh"
    tracker.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s %s\\n" "$1" "$2" > "{tmp_path}/tracker.args"\n'
        f'exec cat "{queue_file}"\n'
    )
    tracker.chmod(0o755)

    class Runner:
        args_file = tmp_path / "tracker.args"

        def run(self, dsn, issues=(), *, tracker_command=None,
                dry_run=True, **env):
            queue_file.write_text(json.dumps({"issues": list(issues)}))
            environ = dict(os.environ)
            environ.update(
                {
                    "PATH": f"{bin_dir}:{environ['PATH']}",
                    "SELECTOR_JOURNAL_DSN": dsn,
                    "SELECTOR_TRACKER_COMMAND": str(tracker_command or tracker),
                    "SELECTOR_TASK_REPO": "acme/widgets",
                    "SELECTOR_LABELER_ALLOWLIST": "JacobStephens2",
                }
            )
            environ.update({k: str(v) for k, v in env.items()})
            argv = ["--dry-run"] if dry_run else []
            return subprocess.run(
                [sys.executable, str(CYCLE), *argv],
                capture_output=True,
                text=True,
                env=environ,
            )

        def tripped(self):
            return tripped.read_text() if tripped.exists() else ""

    return Runner()


def events(dsn, kind=None):
    """Journal rows oldest first - the order a cycle wrote them."""
    with journal.connect(dsn) as conn:
        rows = list(reversed(journal.events(conn)))
    return [r for r in rows if kind is None or r["kind"] == kind]


def skips(dsn):
    """Skipped issue number -> the reason journaled for it."""
    return {e["payload"]["number"]: e["payload"]["reason"]
            for e in events(dsn, "issue.skipped")}


def picked(dsn):
    got = events(dsn, "cycle.picked")
    return got[0]["payload"] if got else None


def finished(dsn):
    got = events(dsn, "cycle.finished")
    assert got, "every cycle journals how it finished"
    return got[0]["payload"]


# --- Ordering and the pick --------------------------------------------------


def test_lowest_eligible_issue_is_picked(db, fakes):
    result = fakes.run(db, [issue(651), issue(645), issue(648)])
    assert result.returncode == 0, result.stderr
    assert picked(db)["number"] == 645
    assert finished(db)["eligible"] == [645, 648, 651]


def test_the_pick_carries_the_owning_area_and_check_it_would_seed_with(db, fakes):
    body = BODY + "\n## Check\n\n```\nscripts/check.sh --strict\n```\n"
    fakes.run(db, [issue(645, body=body)])
    assert picked(db)["area"] == "The nightly sync script"
    assert picked(db)["check"] == "scripts/check.sh --strict"


def test_a_pick_without_a_check_section_is_still_a_pick(db, fakes):
    fakes.run(db, [issue(645)])
    assert picked(db)["check"] is None


# --- Eligibility, one scenario per predicate --------------------------------


def test_blocked_issue_is_skipped(db, fakes):
    fakes.run(db, [issue(646, blockedBy=1), issue(648)])
    assert skips(db)[646] == "blocked-by-open-dependency"
    assert picked(db)["number"] == 648


def test_parent_spec_is_skipped(db, fakes):
    fakes.run(db, [issue(635, openSubIssues=10), issue(648)])
    assert skips(db)[635] == "has-open-sub-issues"
    assert picked(db)["number"] == 648


def test_non_allowlisted_labeler_is_skipped(db, fakes):
    fakes.run(db, [issue(645, labeledBy="someone-else"), issue(648)])
    assert skips(db)[645] == "labeler-not-allowlisted"
    assert picked(db)["number"] == 648


def test_unlabeled_by_anyone_is_skipped(db, fakes):
    """A null labeler is on no allowlist - the safe direction."""
    fakes.run(db, [issue(645, labeledBy=None)])
    assert skips(db)[645] == "labeler-not-allowlisted"


def test_issue_with_an_open_proposal_is_skipped_as_in_flight(db, fakes):
    proposal = {"number": 12, "url": "https://example.invalid/pull/12",
                "state": "OPEN", "isDraft": True}
    fakes.run(db, [issue(645, proposals=[proposal]), issue(648)])
    assert skips(db)[645] == "proposal-open"
    assert picked(db)["number"] == 648


def test_missing_owning_area_is_skipped(db, fakes):
    body = "## Acceptance criteria\n\n- [ ] It is right\n"
    fakes.run(db, [issue(645, body=body), issue(648)])
    assert skips(db)[645] == "missing-section"
    assert "Owning area" in events(db, "issue.skipped")[0]["payload"]["detail"]
    assert picked(db)["number"] == 648


def test_missing_acceptance_criteria_is_skipped(db, fakes):
    body = "## Owning area\n\nThe nightly sync script\n"
    fakes.run(db, [issue(645, body=body)])
    assert skips(db)[645] == "missing-section"
    assert "Acceptance criteria" in events(db, "issue.skipped")[0]["payload"]["detail"]


def test_an_empty_owning_area_heading_counts_as_missing(db, fakes):
    body = "## Acceptance criteria\n\n- [ ] It is right\n\n## Owning area\n\n"
    fakes.run(db, [issue(645, body=body)])
    assert skips(db)[645] == "missing-section"


def test_a_heading_inside_a_fenced_block_is_not_a_section(db, fakes):
    """An issue quoting the template it was written from is quoting it. The
    quoted section has content under it, so a reader that ignored fences would
    seed a Run scoped to somebody else's example."""
    body = (
        "## Acceptance criteria\n\n- [ ] It is right\n\n"
        "Fill this in, like so:\n\n"
        "```\n## Owning area\n\nThe billing module\n```\n"
    )
    fakes.run(db, [issue(645, body=body)])
    assert skips(db)[645] == "missing-section"


def test_a_blocked_issue_is_not_also_shouted_at_for_a_missing_section(db, fakes):
    """The missing-section skip is the loud one (#154 comments and swaps the
    label for it), so a cheaper reason must win."""
    fakes.run(db, [issue(646, blockedBy=1, body="no sections at all")])
    assert skips(db)[646] == "blocked-by-open-dependency"


def test_attempts_exhausted_issue_is_skipped(db, fakes, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    fakes.run(db, [issue(645), issue(648)])
    assert skips(db)[645] == "attempts-exhausted"
    assert picked(db)["number"] == 648


def test_one_failed_attempt_leaves_the_retry_budget_unspent(db, fakes, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    fakes.run(db, [issue(645)])
    assert picked(db)["number"] == 645


def test_relabeling_after_a_give_up_restores_the_retry_budget(db, fakes, dispatch):
    """Re-applying the label is a fresh Handover: the operator saying "try
    that again" must not meet a budget spent on the previous one."""
    dispatch(db, 645, outcome="agent-failed", hours_ago=40)
    dispatch(db, 645, outcome="agent-failed", hours_ago=39)
    fakes.run(db, [issue(645, labeledAt=_hours_ago_iso(2))])
    assert picked(db)["number"] == 645


def test_attempts_before_the_current_handover_are_not_the_only_ones_counted(
    db, fakes, dispatch
):
    """The reset is by the labeling time, not by age: two dispatches since
    the label still spend the budget however recent the label is."""
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    fakes.run(db, [issue(645, labeledAt=_hours_ago_iso(3))])
    assert skips(db)[645] == "attempts-exhausted"


# --- Caps -------------------------------------------------------------------


def test_an_issue_dispatched_again_after_an_outcome_is_in_flight(db, fakes, dispatch):
    """In flight is counted per attempt. An issue with a finished Run and a
    running retry has an outcome on record and is still in flight."""
    dispatch(db, 640, outcome="agent-failed")
    dispatch(db, 640, outcome=None)
    fakes.run(db, [issue(645)])
    assert finished(db)["halted"] == "run-in-flight"


def test_a_run_in_flight_stops_the_pick(db, fakes, dispatch):
    dispatch(db, 640, outcome=None)
    fakes.run(db, [issue(645)])
    assert picked(db) is None
    summary = finished(db)
    assert summary["halted"] == "run-in-flight"
    assert summary["eligible"] == [645], "eligibility is still reasoned and journaled"


def test_a_dispatch_with_no_outcome_stops_holding_the_lock_once_stale(
    db, fakes, dispatch
):
    """A Run cannot outlive the 90-minute run clock, so a dispatch this old
    with no outcome is a cycle that died before recording one. Without an
    expiry that single death wedges every later cycle forever."""
    dispatch(db, 640, outcome=None, hours_ago=9)
    fakes.run(db, [issue(645)])
    assert picked(db)["number"] == 645


def test_a_recent_dispatch_with_no_outcome_still_holds_the_lock(db, fakes, dispatch):
    dispatch(db, 640, outcome=None, hours_ago=1)
    fakes.run(db, [issue(645)])
    assert finished(db)["halted"] == "run-in-flight"


def test_the_daily_cap_stops_the_pick(db, fakes, dispatch):
    for number in (640, 641, 642, 643):
        dispatch(db, number, outcome="clean")
    fakes.run(db, [issue(645)])
    assert picked(db) is None
    assert finished(db)["halted"] == "daily-cap-reached"


def test_dispatches_older_than_the_window_do_not_count_against_the_cap(
    db, fakes, dispatch
):
    for number in (640, 641, 642, 643):
        dispatch(db, number, outcome="clean", hours_ago=30)
    fakes.run(db, [issue(645)])
    assert picked(db)["number"] == 645


def test_the_cap_is_configurable(db, fakes, dispatch):
    dispatch(db, 640, outcome="clean")
    fakes.run(db, [issue(645)], SELECTOR_DAILY_CAP=1)
    assert finished(db)["halted"] == "daily-cap-reached"


# --- The cycle as a whole ---------------------------------------------------


def test_dry_run_touches_nothing_but_the_journal(db, fakes):
    result = fakes.run(db, [issue(645), issue(646, blockedBy=1)])
    assert result.returncode == 0
    assert fakes.tripped() == "", "dry-run reached outside the Journal"
    assert events(db), "the Journal is the one thing it does write"


def test_the_tracker_is_asked_for_the_configured_repo_and_label(db, fakes):
    fakes.run(db, [issue(645)], SELECTOR_LABEL="ready-for-agent")
    assert fakes.args_file.read_text().strip() == "acme/widgets ready-for-agent"


def test_every_event_of_one_cycle_shares_its_cycle_id(db, fakes):
    fakes.run(db, [issue(645), issue(646, blockedBy=1)])
    rows = events(db)
    start = [r for r in rows if r["kind"] == "cycle.started"]
    assert len(start) == 1
    cycle_id = start[0]["id"]
    assert {r["payload"].get("cycle") for r in rows[1:]} == {cycle_id}


def test_an_empty_queue_still_journals_a_finished_cycle(db, fakes):
    result = fakes.run(db, [])
    assert result.returncode == 0
    summary = finished(db)
    assert summary["considered"] == 0
    assert summary["picked"] is None
    assert summary["halted"] == "queue-empty"


def test_a_queue_with_nothing_eligible_journals_why(db, fakes):
    fakes.run(db, [issue(646, blockedBy=1)])
    summary = finished(db)
    assert summary["halted"] == "none-eligible"
    assert summary["skipped"] == {"blocked-by-open-dependency": 1}


def test_a_dead_tracker_fails_the_cycle_loudly(db, fakes, tmp_path):
    broken = tmp_path / "broken.sh"
    broken.write_text("#!/usr/bin/env bash\necho 'tracker exploded' >&2\nexit 4\n")
    broken.chmod(0o755)
    result = fakes.run(db, [issue(645)], tracker_command=broken)
    assert result.returncode != 0, "a dead Selector must not look like a quiet queue"
    assert "tracker" in result.stderr.lower()
    assert events(db, "cycle.failed"), "the failure is journaled, not only printed"


def test_a_tracker_that_exits_nonzero_after_printing_still_fails(db, fakes, tmp_path):
    """The dangerous shape: a paginated read that fetched one page and then
    failed prints well-formed JSON on the way out. Trusting the exit code is
    the only thing between that and a short queue read as the whole queue -
    an issue skipped for not being there at all."""
    truncated = tmp_path / "truncated.sh"
    truncated.write_text(
        "#!/usr/bin/env bash\n"
        "echo '{\"issues\": []}'\n"
        "echo 'gh: API rate limit exceeded' >&2\n"
        "exit 3\n"
    )
    truncated.chmod(0o755)
    result = fakes.run(db, [issue(645)], tracker_command=truncated)
    assert result.returncode != 0
    assert events(db, "cycle.failed")
    assert not events(db, "cycle.finished"), "a failed read is not a finished cycle"


def test_a_queue_record_with_no_number_fails_the_cycle(db, fakes):
    """Malformed on the inside, not just at the envelope: the ordering step
    is the first thing to touch a record, and it must fail the same journaled
    way as an unparseable response."""
    result = fakes.run(db, [{"title": "no number here"}])
    assert result.returncode != 0
    assert events(db, "cycle.failed")


def test_a_tracker_that_returns_nonsense_fails_the_cycle(db, fakes, tmp_path):
    junk = tmp_path / "junk.sh"
    junk.write_text("#!/usr/bin/env bash\necho 'not json'\n")
    junk.chmod(0o755)
    result = fakes.run(db, [], tracker_command=junk)
    assert result.returncode != 0
    assert events(db, "cycle.failed")


def test_dispatch_is_refused_until_the_dispatch_ticket_lands(db, fakes):
    """--dry-run is the only mode #153 built. A cycle asked to dispatch must
    refuse and say so, rather than quietly journal a pick and stop."""
    result = fakes.run(db, [issue(645)], dry_run=False)
    assert result.returncode != 0
    assert "--dry-run" in result.stderr
    assert not events(db), "a refused cycle journals nothing"
