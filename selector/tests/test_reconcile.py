"""The reconcile Run: bringing a conflicting Proposal up to date (issue #34).

A Proposal that `gh pr update-branch` cannot cleanly merge is left alone by
the freshness pass and flagged on the board. The reconcile Run is what meets
those conflicts inside the microVM boundary instead: the Selector detects
the conflicting Proposal, dispatches a reconcile Run for it on the box,
journals the outcome, and escalates what Harvey could not resolve.

Every test drives the real cycle.py through the scripted box from conftest:
the box's exit code is the Run's verdict, and the commands log is what was
dispatched - no model, no network, no GitHub.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import doing
from conftest import FAILED_RUN, events, issue, last, one


def conflicting(number, **over):
    proposal = {
        "number": number,
        "url": f"https://github.invalid/acme/widgets/pull/{number}",
        "state": "OPEN",
        "behind": True,
        "conflicting": True,
    }
    proposal.update(over)
    return proposal


def mergeable(number, **over):
    proposal = {
        "number": number,
        "url": f"https://github.invalid/acme/widgets/pull/{number}",
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "BEHIND",
    }
    proposal.update(over)
    return proposal


BODY_WITH_CHECK = """## Problem

Something is wrong.

## Acceptance criteria

- [ ] It is right

## Check

```
make verify
```
"""


# --- Dispatch ---------------------------------------------------------------


def test_a_leftover_failed_attempt_draft_is_not_reconciled(db, box, dispatch):
    """#102. Reconcile is for a conflicting review artifact, not for the
    leftover draft of a failed attempt the retry will continue. A reconcile
    failure would escalate to the human and spend the retry."""
    leftover = conflicting(13)
    dispatch(db, 630, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    result = box.run(db, [issue(630, proposals=[leftover])], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert "box reconcile" not in box.commands()
    assert events(db, "proposal.reconciled") == []
    assert events(db, "proposal.reconcile-failed") == []
    assert last(db, "run.dispatched")["issue"] == 630


def test_a_conflicting_proposal_dispatches_a_reconcile_run(db, box):
    """AC 1: the Selector detects a conflicting open Proposal and dispatches
    a reconcile Run for it on the box."""
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync\n")
    result = box.run(db, [issue(630, proposals=[conflicting(13)])])
    assert result.returncode == 0, result.stderr

    assert "box reconcile 13" in box.commands()

    reconciled = events(db, "proposal.reconciled")
    assert len(reconciled) == 1
    payload = reconciled[0]["payload"]
    assert payload["proposal"] == 13
    assert payload["number"] == 13
    assert payload["url"] == "https://github.invalid/acme/widgets/pull/13"
    assert payload["issue"] == 630
    assert payload["branch"] == "loop/630-the-nightly-sync"


def test_a_mergeable_behind_proposal_is_updated_not_reconciled(db, box):
    """The two halves of freshness stay on their own sides: a Proposal the
    forge can merge goes through update-branch, never through the box."""
    result = box.run(db, [issue(630, proposals=[mergeable(12)])])
    assert result.returncode == 0, result.stderr

    assert "issue acme/widgets update-branch 12" in box.commands()
    assert "box reconcile" not in box.commands()
    assert events(db, "proposal.reconciled") == []
    assert len(events(db, "proposal.updated")) == 1


def test_a_conflicting_proposal_in_review_is_reconciled_too(db, box):
    """Conflicts are found in the review queue as well as the Handover queue:
    the owning issue may already have been routed to awaiting-review."""
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync\n")
    result = box.run(
        db, [issue(640)], review_issues=[issue(630, proposals=[conflicting(13)])]
    )
    assert result.returncode == 0, result.stderr

    assert "box reconcile 13" in box.commands()
    assert one(db, "proposal.reconciled")["issue"] == 630


def test_the_owning_issues_check_section_reaches_the_reconcile(db, box):
    """The Check section is the suite the merged branch must pass before the
    box pushes it, so it travels with the dispatch like it does with Seeding."""
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-x\n")
    result = box.run(
        db, [issue(630, body=BODY_WITH_CHECK, proposals=[conflicting(13)])]
    )
    assert result.returncode == 0, result.stderr

    assert "box reconcile 13 --check make verify" in box.commands()


def test_a_reconcile_without_a_check_dispatches_without_one(db, box):
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-x\n")
    result = box.run(db, [issue(630, proposals=[conflicting(13)])])
    assert result.returncode == 0, result.stderr

    assert "box reconcile 13 --check" not in box.commands()


def test_one_reconcile_per_proposal_per_drain(db, box):
    """A multi-dispatch drain re-fetches the queue every pass. The Proposal
    reconciled on the first pass must not be reconciled again on the second."""
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-x\n")
    result = box.run(
        db,
        [issue(630, proposals=[conflicting(13)]), issue(640), issue(645)],
    )
    assert result.returncode == 0, result.stderr

    assert box.commands().count("box reconcile 13") == 1
    assert len(events(db, "proposal.reconciled")) == 1
    assert [e["payload"]["issue"] for e in events(db, "run.dispatched")] == [640, 645]


# --- Escalation --------------------------------------------------------------


def test_a_failed_reconcile_escalates_the_issue_to_ready_for_human(db, box):
    """AC 5: conflicts the Run cannot resolve - or a suite that goes red -
    leave the Proposal un-merged and move the owning issue to ready-for-human,
    with the reason said on the issue and journaled."""
    result = box.run(db, [issue(630, proposals=[conflicting(13)])], RECONCILE_EXIT=1)
    assert result.returncode == 0, result.stderr

    commands = box.commands()
    assert "box reconcile 13" in commands
    # Commented first, then swapped - a swap with no comment is the silence
    # the loud skip exists to avoid.
    assert commands.index("issue acme/widgets comment 630") < commands.index(
        "issue acme/widgets relabel 630 ready-for-human ready-for-agent"
    )
    assert "could not be brought up to date" in commands
    assert "Posted by the Selector" in commands

    assert events(db, "proposal.reconciled") == []
    failed = events(db, "proposal.reconcile-failed")
    assert len(failed) == 1
    payload = failed[0]["payload"]
    assert payload["proposal"] == 13
    assert payload["issue"] == 630
    assert "could not reconcile proposal 13" in payload["error"]
    assert payload["added_label"] == "ready-for-human"
    assert payload["removed_label"] == "ready-for-agent"

    # And no ordinary Run was dispatched for the escalated issue.
    assert events(db, "run.dispatched") == []


def test_a_reconcile_failing_after_the_merge_names_the_branch(db, box):
    """The box reports the branch once the merge stands, so an escalation
    after a failed verification or push says where the work got to rather
    than that none exists."""
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-x\n")
    result = box.run(
        db, [issue(630, proposals=[conflicting(13)])], RECONCILE_EXIT=1
    )
    assert result.returncode == 0, result.stderr

    payload = one(db, "proposal.reconcile-failed")
    assert payload["branch"] == "loop/630-x"
    assert "The reconcile Run worked on branch `loop/630-x`." in box.commands()


def test_a_review_queue_conflict_escalates_from_the_review_label(db, box):
    """An owning issue already in awaiting-review is swapped from there, not
    from the Handover label it no longer carries."""
    result = box.run(
        db,
        [issue(640)],
        review_issues=[issue(630, proposals=[conflicting(13)])],
        RECONCILE_EXIT=1,
    )
    assert result.returncode == 0, result.stderr

    assert (
        "issue acme/widgets relabel 630 ready-for-human awaiting-review"
        in box.commands()
    )
    assert last(db, "proposal.reconcile-failed")["removed_label"] == "awaiting-review"


def test_an_escalation_the_tracker_refuses_is_journaled_and_pages(db, box):
    """A comment or swap GitHub refuses leaves the issue wrongly queued with
    nothing on it saying why - paged like any other bookkeeping refusal."""
    result = box.run(
        db, [issue(630, proposals=[conflicting(13)])], RECONCILE_EXIT=1, ISSUE_EXIT=1
    )
    assert result.returncode == 1, result.stderr

    payload = one(db, "proposal.reconcile-failed")
    assert "could not reconcile proposal 13" in payload["error"]
    assert "escalation refused" in payload["error"]
    assert "added_label" not in payload


# --- Gates -------------------------------------------------------------------


def test_a_paused_cycle_starts_no_reconcile(db, box):
    """Paused suspends new dispatches, and a reconcile Run is a dispatch -
    unlike the forge-side fast-forward, it spends an agent Run on the box."""
    import psycopg

    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE selector.control SET paused = true")

    result = box.run(db, [issue(630, proposals=[conflicting(13)])])
    assert result.returncode == 0, result.stderr

    assert "box reconcile" not in box.commands()
    assert events(db, "proposal.reconciled") == []
    assert events(db, "proposal.reconcile-failed") == []
    assert last(db, "cycle.finished")["halted"] == "paused"


def test_a_run_in_flight_holds_the_reconcile_for_its_target(db, box, dispatch):
    """One Run at a time per Target, and a reconcile Run is a Run: while the
    Target holds a Run, conflicting Proposals wait for the next cycle."""
    dispatch(db, 640)
    box.reconcile_summary("LOOP_RECONCILE_BRANCH=loop/630-x\n")
    result = box.run(db, [issue(630, proposals=[conflicting(13)])])
    assert result.returncode == 0, result.stderr

    assert "box reconcile" not in box.commands()
    assert events(db, "proposal.reconciled") == []


def test_a_dry_run_reconciles_nothing(db, fakes):
    """A dry run reaches the tracker and nothing else: no box Run, no forge
    mutation, no reconcile row."""
    result = fakes.run(db, [issue(630, proposals=[conflicting(13)])])
    assert result.returncode == 0, result.stderr

    assert fakes.tripped() == ""
    assert events(db, "proposal.reconciled") == []
    assert events(db, "proposal.reconcile-failed") == []


# --- The detector ------------------------------------------------------------


def test_conflicting_proposals_carry_their_owning_issue():
    record = issue(630, proposals=[conflicting(13), mergeable(12)])
    found = doing.conflicting_proposals([record, issue(640)])
    assert [(r["number"], p["number"]) for r, p in found] == [(630, 13)]
