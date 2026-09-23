"""Eligibility, skips, and the Loud Skip decision, called in-process.

A canned queue, a recording doing, a real Journal. No forked interpreter.
These tests assert on what the Cycle decided and what it journaled, not on
private helpers.
"""
import doing
from conftest import (
    BODY,
    CannedTracker,
    RecordingDoing,
    _run,
    events,
    hours_ago_iso,
    issue,
)


def skips(dsn):
    return {
        e["payload"]["number"]: e["payload"]["reason"]
        for e in events(dsn, "issue.skipped")
    }


def picked(dsn):
    got = events(dsn, "cycle.picked")
    return got[0]["payload"] if got else None


def _handed_back(adapter):
    return [
        c[1]
        for c in adapter.calls
        if isinstance(c, tuple) and c[0] == "return_to_operator"
    ]


class ReturningDoing(RecordingDoing):
    """The harness recorder returns None. Loud Skip tests need True or False."""

    def __init__(self, handed_back, **kwargs):
        super().__init__(**kwargs)
        self._handed_back = handed_back

    def return_to_operator(self, conn, cycle_id, config, dispatch_config, record, detail):
        super().return_to_operator(
            conn, cycle_id, config, dispatch_config, record, detail
        )
        return self._handed_back


# --- Ordering and the pick --------------------------------------------------


def test_lowest_eligible_issue_is_picked(db):
    doing_adapter = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(651), issue(645), issue(648)]),
        doing_adapter,
    )
    assert summary.picked == 645
    assert doing_adapter.picks == [645, 648, 651]
    assert summary.dispatches == [645, 648, 651]
    assert [e["payload"]["number"] for e in events(db, "cycle.picked")] == [
        645, 648, 651,
    ]
    kinds = [e["kind"] for e in events(db) if e["kind"].startswith("cycle.")]
    assert kinds[0] == "cycle.started"
    assert kinds[-1] == "cycle.finished"


def test_the_pick_carries_the_owning_area_and_check_it_would_seed_with(db):
    body = BODY + "\n## Check\n\n```\nscripts/check.sh --strict\n```\n"
    summary = _run(db, CannedTracker([issue(645, body=body)]), RecordingDoing())
    payload = picked(db)
    assert payload["area"] == "The nightly sync script"
    assert payload["check"] == "scripts/check.sh --strict"
    assert summary.picked == 645


def test_a_check_section_without_a_fence_is_its_first_line(db):
    body = BODY + "\n## Check\n\nscripts/check.sh --strict\n"
    summary = _run(db, CannedTracker([issue(645, body=body)]), RecordingDoing())
    assert picked(db)["check"] == "scripts/check.sh --strict"
    assert summary.picked == 645


def test_a_pick_without_a_check_section_is_still_a_pick(db):
    summary = _run(db, CannedTracker([issue(645)]), RecordingDoing())
    assert picked(db)["check"] is None
    assert summary.picked == 645


# --- Eligibility, one scenario per predicate --------------------------------


def test_blocked_issue_is_skipped(db):
    summary = _run(
        db, CannedTracker([issue(646, blockedBy=1), issue(648)]), RecordingDoing()
    )
    assert skips(db)[646] == "blocked-by-open-dependency"
    assert events(db, "issue.skipped")[0]["payload"]["number"] == 646
    assert summary.picked == 648
    assert summary.skipped == {"blocked-by-open-dependency": 1}


def test_parent_spec_is_skipped(db):
    summary = _run(
        db,
        CannedTracker([issue(635, openSubIssues=10), issue(648)]),
        RecordingDoing(),
    )
    assert skips(db)[635] == "has-open-sub-issues"
    assert summary.picked == 648
    assert summary.skipped == {"has-open-sub-issues": 1}


def test_non_allowlisted_labeler_is_skipped(db):
    summary = _run(
        db,
        CannedTracker([issue(645, labeledBy="someone-else"), issue(648)]),
        RecordingDoing(),
    )
    assert skips(db)[645] == "labeler-not-allowlisted"
    assert summary.picked == 648


def test_unlabeled_by_anyone_is_skipped(db):
    """A null labeler is on no allowlist - the safe direction."""
    summary = _run(
        db, CannedTracker([issue(645, labeledBy=None)]), RecordingDoing()
    )
    assert skips(db)[645] == "labeler-not-allowlisted"
    assert "nobody on the timeline" in events(db, "issue.skipped")[0]["payload"]["detail"]
    assert summary.picked is None
    assert summary.halted == "none-eligible"


def test_issue_with_an_open_proposal_is_skipped_as_in_flight(db):
    proposal = {
        "number": 12,
        "url": "https://example.invalid/pull/12",
        "state": "OPEN",
        "isDraft": True,
    }
    summary = _run(
        db,
        CannedTracker([issue(645, proposals=[proposal]), issue(648)]),
        RecordingDoing(),
    )
    assert skips(db)[645] == "proposal-open"
    assert summary.picked == 648


def test_a_leftover_failed_attempt_draft_is_not_in_flight(db, dispatch):
    """#102. An open Proposal left by a failed attempt is the retry's
    branch, not a lock. A draft with no failed attempt under this Handover
    still skips, in the test above."""
    leftover = {
        "number": 13,
        "url": "https://example.invalid/pull/13",
        "state": "OPEN",
        "isDraft": True,
    }
    dispatch(db, 645, outcome="agent-failed")
    summary = _run(
        db, CannedTracker([issue(645, proposals=[leftover])]), RecordingDoing()
    )
    assert skips(db) == {}
    assert summary.picked == 645


def test_a_spent_budget_with_a_leftover_draft_is_attempts_exhausted(db, dispatch):
    """#102. The leftover-draft exception is only for a retry that is still
    inside the budget. A spent Handover with the draft still open is
    attempts-exhausted, not in flight."""
    leftover = {
        "number": 13,
        "url": "https://example.invalid/pull/13",
        "state": "OPEN",
        "isDraft": True,
    }
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    summary = _run(
        db, CannedTracker([issue(645, proposals=[leftover])]), RecordingDoing()
    )
    assert skips(db)[645] == "attempts-exhausted"
    assert summary.picked is None
    assert summary.halted == "none-eligible"


def test_an_issue_with_no_owning_area_is_worked_anyway(db):
    """Amendment to #151, 2026-08-27: the section was required and is not."""
    body = "## Acceptance criteria\n\n- [ ] It is right\n"
    summary = _run(db, CannedTracker([issue(645, body=body)]), RecordingDoing())
    assert skips(db) == {}
    assert summary.picked == 645


def test_an_issue_with_no_owning_area_is_scoped_to_its_own_title(db):
    """A default rather than a guess. `seed-run.sh --area` is required and is
    the Plan's scope fence, so the Run always has one; without a section
    naming it, "work this issue" is the honest reading."""
    body = "## Acceptance criteria\n\n- [ ] It is right\n"
    summary = _run(
        db,
        CannedTracker([issue(645, title="Widen the sync window", body=body)]),
        RecordingDoing(),
    )
    assert picked(db)["area"] == "Widen the sync window"
    assert summary.picked == 645


def test_an_empty_owning_area_heading_falls_back_to_the_title_too(db):
    """The area must never reach Seeding empty: a blank `--area` is a refusal,
    which would put the dropped requirement back in through the dispatch."""
    body = "## Acceptance criteria\n\n- [ ] It is right\n\n## Owning area\n\n"
    summary = _run(
        db,
        CannedTracker([issue(645, title="Widen the sync window", body=body)]),
        RecordingDoing(),
    )
    assert picked(db)["area"] == "Widen the sync window"
    assert summary.picked == 645


def test_an_owning_area_section_still_scopes_the_run_when_it_is_there(db):
    """An issue bigger than one Run still says so, and the fence still holds."""
    summary = _run(
        db,
        CannedTracker([issue(645, title="Widen the sync window")]),
        RecordingDoing(),
    )
    assert picked(db)["area"] == "The nightly sync script"
    assert summary.picked == 645


def test_missing_acceptance_criteria_is_skipped(db):
    body = "## Owning area\n\nThe nightly sync script\n"
    summary = _run(
        db, CannedTracker([issue(645, body=body), issue(648)]), RecordingDoing()
    )
    assert skips(db)[645] == "missing-section"
    assert "Acceptance criteria" in events(db, "issue.skipped")[0]["payload"]["detail"]
    assert summary.picked == 648
    assert summary.skipped == {"missing-section": 1}


def test_an_empty_acceptance_criteria_heading_counts_as_missing(db):
    body = "## Acceptance criteria\n\n## Owning area\n\nThe nightly sync script\n"
    summary = _run(db, CannedTracker([issue(645, body=body)]), RecordingDoing())
    assert skips(db)[645] == "missing-section"
    assert summary.halted == "none-eligible"


def test_a_heading_inside_a_fenced_block_is_not_a_section(db):
    """An issue quoting the template it was written from is quoting it. The
    quoted section has content under it, so a reader that ignored fences would
    read somebody else's example as this issue's definition of done."""
    body = (
        "Fill this in, like so:\n\n"
        "```\n## Acceptance criteria\n\n- [ ] It is right\n```\n"
    )
    summary = _run(db, CannedTracker([issue(645, body=body)]), RecordingDoing())
    assert skips(db)[645] == "missing-section"
    assert summary.halted == "none-eligible"


def test_a_blocked_issue_is_not_also_shouted_at_for_a_missing_section(db):
    """The missing-section skip is the loud one, so a cheaper reason must win."""
    doing_adapter = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(646, blockedBy=1, body="no sections at all")]),
        doing_adapter,
    )
    assert skips(db)[646] == "blocked-by-open-dependency"
    assert _handed_back(doing_adapter) == []
    assert summary.returned == []


def test_attempts_exhausted_issue_is_skipped(db, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    summary = _run(
        db, CannedTracker([issue(645), issue(648)]), RecordingDoing()
    )
    assert skips(db)[645] == "attempts-exhausted"
    assert summary.picked == 648


def test_one_failed_attempt_leaves_the_retry_budget_unspent(db, dispatch):
    dispatch(db, 645, outcome="agent-failed")
    summary = _run(db, CannedTracker([issue(645)]), RecordingDoing())
    assert skips(db) == {}
    assert summary.picked == 645


def test_relabeling_after_a_give_up_restores_the_retry_budget(db, dispatch):
    """Re-applying the label is a fresh Handover: the operator saying "try
    that again" must not meet a budget spent on the previous one."""
    dispatch(db, 645, outcome="agent-failed", hours_ago=40)
    dispatch(db, 645, outcome="agent-failed", hours_ago=39)
    summary = _run(
        db,
        CannedTracker([issue(645, labeledAt=hours_ago_iso(2))]),
        RecordingDoing(),
    )
    assert summary.picked == 645
    assert skips(db) == {}


def test_attempts_before_the_current_handover_are_not_the_only_ones_counted(
    db, dispatch
):
    """The reset is by the labeling time, not by age: two dispatches since
    the label still spend the budget however recent the label is."""
    dispatch(db, 645, outcome="agent-failed")
    dispatch(db, 645, outcome="agent-failed")
    summary = _run(
        db,
        CannedTracker([issue(645, labeledAt=hours_ago_iso(3))]),
        RecordingDoing(),
    )
    assert skips(db)[645] == "attempts-exhausted"
    assert summary.picked is None


def test_a_skip_is_journaled_once_per_cycle(db):
    """A blocked issue stays in the canned queue after another issue is
    dispatched. The drain re-reads it; the skip is still one Journal row."""
    doing_adapter = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(646, blockedBy=1), issue(648), issue(651)]),
        doing_adapter,
    )
    assert doing_adapter.picks == [648, 651]
    skipped = events(db, "issue.skipped")
    assert [e["payload"]["number"] for e in skipped] == [646]
    assert skipped[0]["payload"]["reason"] == "blocked-by-open-dependency"
    assert summary.skipped == {"blocked-by-open-dependency": 1}


# --- The Loud Skip decision -------------------------------------------------


def test_a_missing_section_asks_the_doing_to_return(db):
    body = "## Owning area\n\nThe nightly sync script\n"
    doing_adapter = ReturningDoing(True)
    summary = _run(
        db, CannedTracker([issue(645, body=body), issue(648)]), doing_adapter
    )
    assert _handed_back(doing_adapter) == [645]
    assert summary.returned == [645]
    assert summary.return_failures == 0
    assert skips(db)[645] == "missing-section"
    assert "Acceptance criteria" in events(db, "issue.skipped")[0]["payload"]["detail"]
    assert summary.picked == 648
    finished = events(db, "cycle.finished")[0]["payload"]
    assert finished["returned"] == [645]


def test_an_issue_is_returned_even_when_a_cap_halts_the_cycle(db, dispatch):
    """Handing work back is not spending a Run. An issue the Selector will
    never seed should not wait for a free budget to be told so."""
    dispatch(db, 640, outcome=None)
    body = "## Owning area\n\nThe nightly sync script\n"
    doing_adapter = ReturningDoing(True)
    summary = _run(db, CannedTracker([issue(645, body=body)]), doing_adapter)
    assert _handed_back(doing_adapter) == [645]
    assert summary.returned == [645]
    assert summary.halted == "run-in-flight"
    assert summary.picked is None
    assert events(db, "cycle.finished")[0]["payload"]["halted"] == "run-in-flight"


def test_only_a_missing_section_is_shouted_at(db):
    """Every other skip is quiet. A blocked issue commented on every half hour
    is a queue nobody reads."""
    doing_adapter = RecordingDoing()
    summary = _run(
        db,
        CannedTracker([issue(646, blockedBy=1), issue(635, openSubIssues=2)]),
        doing_adapter,
    )
    assert _handed_back(doing_adapter) == []
    assert summary.returned == []
    assert skips(db) == {
        646: "blocked-by-open-dependency",
        635: "has-open-sub-issues",
    }


def test_a_return_the_doing_refused_is_counted(db):
    body = "## Owning area\n\nThe nightly sync script\n"
    doing_adapter = ReturningDoing(False)
    summary = _run(db, CannedTracker([issue(645, body=body)]), doing_adapter)
    assert _handed_back(doing_adapter) == [645]
    assert summary.return_failures == 1
    assert summary.returned == []
    assert not events(db, "issue.returned")
    assert skips(db)[645] == "missing-section"
    finished = events(db, "cycle.finished")[0]["payload"]
    assert finished["returned"] == []


def test_a_dry_run_asks_nothing_of_the_doing(db):
    body = "## Owning area\n\nThe nightly sync script\n"
    doing_adapter = doing.DryRunDoing()
    summary = _run(
        db,
        CannedTracker([issue(645, body=body)]),
        doing_adapter,
        dry_run=True,
    )
    assert skips(db)[645] == "missing-section"
    assert summary.returned == []
    assert summary.return_failures == 0
    assert summary.dry_run is True
    assert not events(db, "issue.returned")
    assert not events(db, "run.dispatched")
    assert events(db, "cycle.finished")[0]["payload"]["dry_run"] is True
