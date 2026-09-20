"""What the Selector does to the issue once the Run has ended (issue #155).

Dispatch (#154) ends the moment the box hands back a summary. This is the half
after that: the Run's ending bound and the Proposal's checks decide which
queue the issue lands in, and the operator finds out by reading the issue
rather than by reading a log on a box.

Same rule as the suites either side of it - the real `cycle.py` as a
subprocess, nothing reading Selector internals. Every assertion is either a
command the Selector issued (which label swap, which comment body) or a row it
appended to the Journal. The two new scripted answers are the Run's summary
(the box fake already printed one) and the Proposal's CI state, which the
issue command's `checks` action answers from a file each test writes.

The routes, from spec #151's stories 14-17:

    Run-level failure, budget left    retry - no label swap, journaled
    Run-level failure, budget spent   comment + `ready-for-human`
    clean Run, red checks             comment naming them + `ready-for-human`
    clean Run, green checks           `awaiting-review`

and the invariant underneath all four: an issue is never dispatched a third
time, whichever way the second attempt ended and whether or not GitHub
accepted the label swap that was supposed to take it out of the queue.
"""
import json

from conftest import (
    NO_CHECKS,
    CLEAN_RUN,
    FAILED_RUN,
    GREEN_CHECKS,
    PENDING_CHECKS,
    RED_CHECKS,
    events,
    issue,
    last,
    one,
)

NO_PROPOSAL_RUN = """LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_PROPOSAL=none
LOOP_RUN_NOTIFIED=sent
"""


def relabels(box):
    """Every label swap the Selector issued, as (number, add, remove)."""
    out = []
    for line in box.commands().splitlines():
        parts = line.split()
        if parts[:1] == ["issue"] and len(parts) >= 6 and parts[2] == "relabel":
            out.append((parts[3], parts[4], parts[5]))
    return out


def checks_calls(box):
    """How many times the checks command was invoked.

    Counted from the command lines rather than by searching the whole log:
    the word appears in the comment bodies too, and a count that included
    those would pass whatever the polling did.
    """
    return sum(
        1 for line in box.commands().splitlines()
        if line.split()[:1] == ["issue"] and line.split()[2:3] == ["checks"]
    )


def comment_bodies(box):
    """The comment bodies, in the order they were posted."""
    bodies, collecting, current = [], False, []
    for line in box.commands().splitlines():
        if line == "--- body ---":
            collecting, current = True, []
        elif line == "--- end ---":
            collecting = False
            bodies.append("\n".join(current))
        elif collecting:
            current.append(line)
    return bodies


# --- Green: the Proposal is ready for the operator ---------------------------


def test_a_green_proposal_swaps_the_issue_to_awaiting_review(db, box):
    """Story 16. The operator's issue list is the kanban, so a Run that ended
    clean with passing checks has to move the card itself."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "awaiting-review", "ready-for-agent")]


def test_a_green_proposal_is_not_commented_on(db, box):
    """Nothing to say. The Proposal is the artifact and a comment repeating
    that it exists is noise on an issue the operator is about to read."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)])
    assert comment_bodies(box) == []


def test_the_green_route_is_journaled_with_the_proposal(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)])
    routed = one(db, "issue.awaiting-review")
    assert routed["issue"] == 645
    assert routed["attempt"] == 1
    assert routed["label"] == "awaiting-review"
    assert routed["proposal"].endswith("/12")


def test_the_checks_are_read_for_the_proposal_the_run_left(db, box):
    """The Proposal URL the box reported is the one whose checks are read.
    Reading the branch's instead would be a second way of naming the same
    thing, and one that goes wrong the moment a Run proposes nothing."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)])
    assert "issue acme/widgets checks https://github.invalid/acme/widgets/pull/12" \
        in box.commands()


# --- Red: CI failed, and the operator is told which ------------------------


def test_red_checks_route_to_a_human_with_the_failing_names(db, box):
    """Story 17. No repair Run: the failing check names reach the operator and
    the issue leaves the agent queue."""
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]
    body = comment_bodies(box)[0]
    assert "phpunit" in body and "lint" in body


def test_the_red_comment_links_the_proposal_it_is_about(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    assert "https://github.invalid/acme/widgets/pull/12" in comment_bodies(box)[0]


def test_the_red_comment_points_at_the_branch_history(db, box):
    """The failing names say what happened; the branch history says where the
    record is - the tip is merge-clean since #79."""
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    body = comment_bodies(box)[0]
    assert one(db, "run.outcome")["branch"] in body
    assert "history" in body


def test_a_red_proposal_never_reaches_the_review_queue(db, box):
    """Unverified work is not reviewable, with or without a comment saying
    so: the route and the comment travel together."""
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    assert events(db, "issue.awaiting-review") == []
    assert len(comment_bodies(box)) == 1


def test_the_red_route_is_journaled_with_the_failing_checks(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    routed = one(db, "issue.handed-to-human")
    assert routed["issue"] == 645
    assert routed["label"] == "ready-for-human"
    assert routed["failing"] == ["phpunit", "lint"]


def test_a_red_proposal_is_commented_before_it_is_relabeled(db, box):
    """Same ordering rule as the loud skip: a swap that landed with no comment
    takes the issue out of the queue with nothing on it saying why."""
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    log = box.commands()
    assert log.index("issue acme/widgets comment 645") < \
        log.index("issue acme/widgets relabel 645")


# --- A Run-level failure, retried once --------------------------------------


def test_a_first_failure_is_retried_rather_than_handed_over(db, box):
    """Story 14. The issue keeps `ready-for-agent`, so the next cycle picks it
    up again - the retry is the queue working, not a second code path."""
    box.run_summary(FAILED_RUN)
    result = box.run(db, [issue(645)], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert relabels(box) == []
    assert one(db, "issue.retrying")["issue"] == 645


def test_a_first_failure_reads_no_checks(db, box):
    """A Run that failed has nothing to review whatever CI thinks of the
    branch it left behind, and asking would spend a tracker call to reach a
    decision already made."""
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    assert checks_calls(box) == 0


def test_the_retry_row_says_which_attempt_is_left(db, box):
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    retry = one(db, "issue.retrying")
    assert retry["attempt"] == 1
    assert retry["of"] == 2
    assert retry["outcome"] == "agent-failed"


# --- The second failure gives up, visibly ------------------------------------


def test_the_second_failure_swaps_to_ready_for_human(db, box, dispatch):
    """Story 15. Giving up is visible, and the issue never re-enters the queue
    silently."""
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    result = box.run(db, [issue(645)], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]


def test_the_give_up_comment_says_what_happened(db, box, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    body = comment_bodies(box)[0]
    assert "agent-failed" in body
    assert "2" in body
    assert "ready-for-human" in body


def test_the_give_up_comment_names_the_draft_proposal(db, box, dispatch):
    """A failed Run is learnable from the issue alone: the draft Proposal the
    failed attempt left behind is named on the issue, so the operator never
    opens a Proposal just to learn a Run failed."""
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    body = comment_bodies(box)[0]
    assert "https://github.invalid/acme/widgets/pull/13" in body


def test_the_give_up_comment_points_at_the_branch_history(db, box, dispatch):
    """Since #79 the tip is merge-clean, so the comment points at the branch's
    history rather than at files the tip no longer carries."""
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    body = comment_bodies(box)[0]
    branch = last(db, "run.outcome")["branch"]
    assert branch in body
    assert "history" in body


def test_a_give_up_with_no_proposal_still_names_the_branch(db, box, dispatch):
    """Nothing to link, but still somewhere to look: the branch history holds
    the Run's record even when no Proposal exists."""
    dispatch(db, 645, outcome="iteration-cap")
    box.run_summary(NO_PROPOSAL_RUN)
    box.run(db, [issue(645)])
    bodies = comment_bodies(box)
    assert len(bodies) == 1
    assert "left no Proposal" in bodies[0]
    assert last(db, "run.outcome")["branch"] in bodies[0]
    assert "history" in bodies[0]


def test_a_failed_run_with_a_proposal_is_never_routed_to_review(
    db, box, dispatch
):
    """Routing judgment, pinned: a Run the Contract cut short goes to the
    human even when it left a draft Proposal and CI would call it green. The
    checks are not even read - there is nothing to review whatever they say."""
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    box.checks(GREEN_CHECKS)
    result = box.run(db, [issue(645)], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert checks_calls(box) == 0
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]
    assert events(db, "issue.awaiting-review") == []
    assert one(db, "issue.given-up")["outcome"] == "agent-failed"


def test_a_first_failure_with_a_proposal_is_retried_without_review(
    db, box
):
    """The same judgment on the first attempt: retry, no comment, no review
    queue - even holding a draft Proposal with green checks scripted."""
    box.run_summary(FAILED_RUN)
    box.checks(GREEN_CHECKS)
    result = box.run(db, [issue(645)], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert relabels(box) == []
    assert comment_bodies(box) == []
    assert events(db, "issue.awaiting-review") == []
    assert one(db, "issue.retrying")["outcome"] == "agent-failed"


def test_a_failed_runs_open_draft_does_not_block_its_retry(db, box):
    """#102. A failed Run still proposes. The leftover draft is the branch
    the retry continues, not an in-flight lock: the next Cycle dispatches
    without anyone closing that draft."""
    leftover = {
        "number": 13,
        "url": "https://github.invalid/acme/widgets/pull/13",
        "state": "OPEN",
        "isDraft": True,
    }
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    result = box.run(db, [issue(645, proposals=[leftover])], BOX_EXIT=4)
    assert result.returncode == 0, result.stderr
    assert last(db, "run.outcome")["attempt"] == 2
    assert last(db, "run.outcome")["ended_by"] == "agent-failed"
    skipped = [
        e["payload"] for e in events(db, "issue.skipped")
        if e["payload"]["number"] == 645
    ]
    assert skipped == []


def test_the_give_up_is_journaled(db, box, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(FAILED_RUN)
    box.run(db, [issue(645)], BOX_EXIT=4)
    given = one(db, "issue.given-up")
    assert given["issue"] == 645
    assert given["attempt"] == 2
    assert given["label"] == "ready-for-human"
    assert given["outcome"] == "agent-failed"


def test_a_retry_that_succeeds_goes_to_review_not_to_a_human(db, box, dispatch):
    """The budget is spent on failures, not on attempts that worked. A second
    attempt that ended clean is a Proposal like any other."""
    dispatch(db, 645, outcome="agent-failed")
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)])
    assert relabels(box) == [("645", "awaiting-review", "ready-for-agent")]
    assert events(db, "issue.given-up") == []


# --- Never a third dispatch --------------------------------------------------


def test_no_issue_is_dispatched_a_third_time(db, box, dispatch):
    """The acceptance criterion, asserted where it can actually be seen: after
    two dispatches the box is never reached again."""
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    box.run(db, [issue(645)])
    # The seeded history is itself two `run.dispatched` rows, so the
    # assertion is that the box was never reached - not that the Journal is
    # empty of dispatches.
    assert "box " not in box.commands()
    assert "seed " not in box.commands()


def test_a_third_dispatch_is_refused_even_if_the_give_up_swap_failed(
    db, box, dispatch
):
    """Defence in depth. The swap to `ready-for-human` is what takes the issue
    out of the queue, but a swap GitHub refused leaves it in one - and the
    retry budget, which is read from the Journal rather than from the label,
    still has to stop the third Run."""
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    box.run(db, [issue(645)], ISSUE_EXIT=1)
    assert "box " not in box.commands()


def test_the_exhausted_issue_is_skipped_with_its_reason(db, box, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    box.run(db, [issue(645)])
    assert one(db, "issue.skipped")["reason"] == "attempts-exhausted"


# --- A Run that proposed nothing ---------------------------------------------


def test_a_clean_run_with_no_proposal_is_treated_as_a_failed_attempt(db, box):
    """There is nothing to review and nothing to read checks for. Retrying is
    the honest reading: the Run ended within its bounds and left no artifact,
    which a second attempt may well fix."""
    box.run_summary(NO_PROPOSAL_RUN)
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert relabels(box) == []
    assert one(db, "issue.retrying")["outcome"] == "no-proposal"


def test_a_second_run_with_no_proposal_gives_up(db, box, dispatch):
    dispatch(db, 645, outcome="iteration-cap")
    box.run_summary(NO_PROPOSAL_RUN)
    box.run(db, [issue(645)])
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]
    assert one(db, "issue.given-up")["outcome"] == "no-proposal"


# --- Checks that never settle ------------------------------------------------


def test_checks_that_never_settle_reach_the_operator(db, box):
    """A Proposal whose CI is still pending when the wait is spent is not
    green, and calling it green would put unverified work in the review queue.
    It goes to the operator with the wait named, rather than being retried:
    the Run did its job and a second one would not change CI."""
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS)
    result = box.run(db, [issue(645)], SELECTOR_CHECKS_TIMEOUT_SECONDS=0)
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]
    assert one(db, "issue.handed-to-human")["checks"] == "pending"


def test_the_unsettled_comment_says_the_checks_did_not_finish(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS)
    box.run(db, [issue(645)], SELECTOR_CHECKS_TIMEOUT_SECONDS=0)
    assert "did not finish" in comment_bodies(box)[0]


def test_the_unsettled_comment_points_at_the_branch_history(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS)
    box.run(db, [issue(645)], SELECTOR_CHECKS_TIMEOUT_SECONDS=0)
    body = comment_bodies(box)[0]
    assert "https://github.invalid/acme/widgets/pull/12" in body
    assert one(db, "run.outcome")["branch"] in body
    assert "history" in body


def test_a_pending_proposal_never_reaches_the_review_queue(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS)
    box.run(db, [issue(645)], SELECTOR_CHECKS_TIMEOUT_SECONDS=0)
    assert events(db, "issue.awaiting-review") == []
    assert len(comment_bodies(box)) == 1


# --- Bookkeeping the tracker refused -----------------------------------------


def test_a_route_github_refuses_is_journaled_and_pages(db, box):
    """Story 31's rule, applied to this half: a Run whose outcome could not be
    recorded on the issue leaves the operator with a Proposal nothing points
    at, so the cycle exits non-zero and the timer's OnFailure pages."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    result = box.run(db, [issue(645)], ISSUE_EXIT=1)
    assert result.returncode == 1
    failed = one(db, "issue.route-failed")
    assert failed["issue"] == 645
    assert failed["label"] == "awaiting-review"
    assert "error" in failed


def test_a_successful_proposal_left_on_the_handover_queue_is_not_retried(
    db, box
):
    """#102. A leftover draft is only the retry's branch when the attempt
    failed. A Proposal that finished and whose label swap failed is still
    in flight: retrying it would dispatch a second Run on work already
    proposed for review."""
    leftover = {
        "number": 12,
        "url": "https://github.invalid/acme/widgets/pull/12",
        "state": "OPEN",
        "isDraft": True,
    }
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)], ISSUE_EXIT=1)
    result = box.run(db, [issue(645, proposals=[leftover])])
    assert result.returncode == 0, result.stderr
    assert last(db, "run.outcome")["attempt"] == 1
    skipped = {
        e["payload"]["number"]: e["payload"]["reason"]
        for e in events(db, "issue.skipped")
    }
    assert skipped[645] == "proposal-open"


def test_a_route_that_failed_still_recorded_the_run(db, box):
    """The Run happened whatever GitHub said afterwards. Losing the outcome
    row because the label swap failed would make the Journal disagree with the
    box about whether a Run ever ran."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)], ISSUE_EXIT=1)
    assert one(db, "run.outcome")["ended_by"] == "iteration-cap"


def test_a_checks_read_that_fails_reaches_the_operator(db, box):
    """A tracker that will not say whether CI passed is not a green light."""
    box.run_summary(CLEAN_RUN)
    result = box.run(db, [issue(645)], CHECKS_EXIT=1)
    assert result.returncode == 1
    assert events(db, "issue.awaiting-review") == []


# --- The dry run still does nothing ------------------------------------------


def test_a_dry_run_routes_nothing(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)], dry_run=True)
    assert relabels(box) == []
    assert events(db, "issue.awaiting-review") == []
    assert events(db, "issue.retrying") == []


# --- The checks answer the real script has to produce -------------------------


def test_the_checks_contract_is_the_shape_the_selector_parses(db, box):
    """The one assertion about the JSON itself. github.sh translates `gh pr
    checks` into this shape, and a drift between the two would otherwise show
    up only against the live tracker."""
    for text in (GREEN_CHECKS, RED_CHECKS, PENDING_CHECKS):
        payload = json.loads(text)
        assert payload["state"] in ("green", "red", "pending")
        assert isinstance(payload["failing"], list)


# --- Waiting for CI to decide -------------------------------------------------


def test_checks_are_re_read_until_ci_decides(db, box):
    """The wait is a poll, and this is what drives it: CI answers `pending`
    twice and then green, and the issue ends up in the review queue rather
    than on the operator. Without this the polling loop would be asserted only
    by tests that never let it go round."""
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS + PENDING_CHECKS + GREEN_CHECKS)
    result = box.run(
        db, [issue(645)],
        SELECTOR_CHECKS_TIMEOUT_SECONDS=60,
        SELECTOR_CHECKS_POLL_SECONDS=1,
    )
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "awaiting-review", "ready-for-agent")]
    assert checks_calls(box) == 3


def test_a_wait_that_is_spent_stops_re_reading(db, box):
    """The bound is real: CI that stays pending is read once at a zero wait
    and the Selector moves on rather than polling forever."""
    box.run_summary(CLEAN_RUN)
    box.checks(PENDING_CHECKS)
    box.run(db, [issue(645)], SELECTOR_CHECKS_TIMEOUT_SECONDS=0)
    assert checks_calls(box) == 1


# --- A Proposal nothing ran against -------------------------------------------


def test_a_proposal_with_no_checks_does_not_reach_the_review_queue(db, box):
    """"Every check passed" and "no check ran" are opposite facts about how far
    a Proposal has been verified. On a repository that has CI - which the task
    repository does - the second usually means a workflow did not trigger, and
    routing it to review as though it had passed is the same false pass as a
    permission error read as an all-clear."""
    box.run_summary(CLEAN_RUN)
    box.checks(NO_CHECKS)
    result = box.run(db, [issue(645)])
    assert result.returncode == 0, result.stderr
    assert relabels(box) == [("645", "ready-for-human", "ready-for-agent")]
    assert one(db, "issue.handed-to-human")["checks"] == "none"


def test_the_no_checks_comment_says_it_is_not_the_same_as_passing(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(NO_CHECKS)
    box.run(db, [issue(645)])
    body = comment_bodies(box)[0]
    assert "no check ran against it" in body
    assert "not the same as passing" in body


def test_the_no_checks_comment_points_at_the_branch_history(db, box):
    box.run_summary(CLEAN_RUN)
    box.checks(NO_CHECKS)
    box.run(db, [issue(645)])
    body = comment_bodies(box)[0]
    assert "https://github.invalid/acme/widgets/pull/12" in body
    assert one(db, "run.outcome")["branch"] in body
    assert "history" in body


def test_a_proposal_with_no_checks_is_commented_not_just_relabeled(db, box):
    """Every terminal route off an unverified Run leaves a comment: the swap
    alone would take the issue out of the queue with nothing saying why."""
    box.run_summary(CLEAN_RUN)
    box.checks(NO_CHECKS)
    box.run(db, [issue(645)])
    assert events(db, "issue.awaiting-review") == []
    assert len(comment_bodies(box)) == 1


def test_every_comment_the_selector_posts_is_signed(db, box):
    """One signature, appended where the comment is posted rather than written
    into each body - so a route added later cannot forget it."""
    box.run_summary(CLEAN_RUN)
    box.checks(RED_CHECKS)
    box.run(db, [issue(645)])
    assert comment_bodies(box)[0].rstrip().endswith(
        "*Posted by the Selector. Deterministic code, not an agent - no model"
        " wrote this and none read the issue.*"
    )


def test_the_journaled_label_is_the_label_that_was_applied(db, box):
    """One spelling of one fact. The label reached the tracker and the label
    the Journal recorded came from the same value, so a reader can trust the
    row to say which queue the issue is actually in."""
    box.run_summary(CLEAN_RUN)
    box.checks(GREEN_CHECKS)
    box.run(db, [issue(645)])
    applied = relabels(box)[0][1]
    assert one(db, "issue.awaiting-review")["label"] == applied
