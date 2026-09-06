"""The Journal Event vocabulary, driven directly.

events.py owns which kinds exist and what each payload holds: constructors on
the write side (closed - a writer cannot misspell a key or invent a kind) and
readers on the read side (total - a row from any era normalizes into one
record). Pure on purpose: no database, no clock, importable without dragging
the dispatcher's import graph, which is what lets this suite drive every shape
with plain dicts.

The expected shapes come from the write-site inventory of 2026-09-01 (every
`journal.append` in cycle.py and watcher.py), not from re-running the code
under test.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import events  # noqa: E402

PROPOSAL = "https://github.invalid/acme/widgets/pull/12"


# --- run.outcome: the Run's summary row --------------------------------------

def test_run_outcome_carries_the_bound_under_one_key():
    kind, payload = events.run_outcome(
        cycle=7,
        issue=312,
        title="Give the guest a PHP toolchain",
        url="https://github.invalid/acme/widgets/issues/312",
        task_ref="acme/widgets#312",
        attempt=1,
        branch="loop/312-php-guest",
        ended_by="iteration-cap",
        exit=0,
        iterations=5,
        faults="none",
        proposal="https://github.invalid/acme/widgets/pull/12",
        proposed="proposed",
        notified="sent",
        seed="seeded",
        criteria="3",
    )
    assert kind == "run.outcome"
    assert payload["ended_by"] == "iteration-cap"
    # The clean break: the raw bound is spelled once. `outcome` is the route
    # rows' derived name and never appears on a run.outcome row again.
    assert "outcome" not in payload
    assert set(payload) == {
        "cycle", "issue", "title", "url", "task_ref", "attempt", "branch",
        "ended_by", "exit", "iterations", "faults", "proposal", "proposed",
        "notified", "seed", "criteria",
    }


def test_a_dispatch_that_started_no_run_is_the_same_kind_with_the_sentinel():
    kind, payload = events.run_dispatch_failed(
        cycle=7,
        issue=312,
        title="Give the guest a PHP toolchain",
        url="https://github.invalid/acme/widgets/issues/312",
        task_ref="acme/widgets#312",
        attempt=1,
        branch="loop/312-php-guest",
        error="the box started no Run (exit 255): ssh: connect refused",
    )
    assert kind == "run.outcome"
    # "How the dispatch ended" is one axis: a Bound when a Run reported one,
    # and this sentinel when the box never started one.
    assert payload["ended_by"] == events.DISPATCH_FAILED
    assert "outcome" not in payload
    assert set(payload) == {
        "cycle", "issue", "title", "url", "task_ref", "attempt", "branch",
        "ended_by", "error",
    }


# --- The naming rules: what a Run's result is CALLED -------------------------

def test_a_capped_run_with_a_proposal_keeps_its_bounds_name():
    assert events.outcome_name("iteration-cap", PROPOSAL) == "iteration-cap"


def test_a_capped_run_that_proposed_nothing_gets_its_own_name():
    assert events.outcome_name("iteration-cap", None) == events.NO_PROPOSAL


def test_a_failure_bound_keeps_its_name_with_or_without_a_proposal():
    assert events.outcome_name("run-clock", None) == "run-clock"
    assert events.outcome_name("run-clock", PROPOSAL) == "run-clock"


def test_the_failure_bounds_are_the_contracts_cut_short_bounds():
    assert events.RUN_FAILURE_BOUNDS == ("run-clock", "consecutive-noops",
                                         "agent-failed")


def test_the_retry_budget_is_one_automatic_retry():
    assert events.MAX_ATTEMPTS == 2


def test_the_route_names_and_the_kinds_derived_from_them():
    assert events.ROUTE_NAMES == ("retrying", "awaiting-review", "given-up",
                                  "handed-to-human")
    assert events.route_kind("awaiting-review") == "issue.awaiting-review"
    # The kind -> name map is the same derivation, made once: the reader and
    # the page both import it rather than re-deriving their own copies.
    assert events.ROUTE_KIND_NAMES["issue.given-up"] == "given-up"
    assert set(events.ROUTE_KIND_NAMES.values()) == set(events.ROUTE_NAMES)


def test_a_failed_dispatch_keeps_its_own_name():
    # The naming rule is total over the whole ended_by axis: the sentinel is
    # not a Run that ran, so it must never come back as "no-proposal".
    assert events.outcome_name(events.DISPATCH_FAILED, None) == \
        events.DISPATCH_FAILED


def test_the_failure_predicate_is_spelled_once_and_total():
    assert events.is_failure("run-clock", PROPOSAL)
    assert events.is_failure("iteration-cap", None)
    assert not events.is_failure("iteration-cap", PROPOSAL)
    assert events.is_failure(events.NO_PROPOSAL, None)


def test_the_checks_vocabulary_is_closed_and_none_is_not_red():
    assert events.CHECK_STATES == ("green", "red", "pending", "none")


# --- The sweep: shipping code spells kinds only here -------------------------

def test_shipping_code_spells_kinds_only_in_the_vocabulary():
    """Convention alone produced seven files spelling `run.outcome`; this is
    what stops the eighth. Every kind literal in shipping code lives in
    events.py - other modules import the constant or call the constructor.

    Scope: the Selector's modules and the window's. Not the tests (fixtures
    legitimately spell rows of any era) and not events.py, which is the one
    place the spelling is allowed to exist.
    """
    import re
    selector = Path(__file__).resolve().parents[1]
    web = selector.parent / "web"
    shipping = sorted(p for p in selector.glob("*.py") if p.name != "events.py")
    shipping += [web / "app.py", web / "preview.py"]
    pattern = re.compile(
        r"""["'](cycle|run|issue|box|guardrail|proposal)\.([a-z][a-z-]*)["']"""
    )

    strays = []
    for path in shipping:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for match in pattern.finditer(line):
                # "cycle.py" in a usage string and "run.sh" in a command name
                # are files, not kinds.
                if match.group(2) in ("py", "sh"):
                    continue
                strays.append(f"{path.name}:{n}: {match.group(0)}")
    assert not strays, "kind literals outside events.py:\n" + "\n".join(strays)


# --- Readers: total over every era of row ------------------------------------

def row(kind, payload, *, id=41, at="2026-08-31T12:00:00+00:00"):
    return {"id": id, "at": at, "kind": kind, "payload": payload}


def test_a_new_shape_outcome_row_reads_back_whole():
    kind, payload = events.run_outcome(
        cycle=7, issue=312, title="t", url="u", task_ref="acme/widgets#312",
        attempt=1, branch="loop/312-x", ended_by="iteration-cap", exit=0,
        iterations=5, faults="none", proposal=PROPOSAL, proposed="proposed",
        notified="sent", seed="seeded", criteria="3",
    )
    record = events.run_outcome_record(row(kind, payload))
    assert record.ended_by == "iteration-cap"
    assert record.proposal == PROPOSAL
    assert record.issue == 312
    assert record.at == "2026-08-31T12:00:00+00:00"
    assert record.error is None


def test_a_legacy_row_spelling_the_bound_twice_normalizes_to_one():
    # Rows written before the clean break carry the raw bound under both
    # `outcome` and `ended_by`. The Journal is append-only, so they are
    # forever; the reader is where they become today's shape.
    legacy = {"cycle": 7, "issue": 312, "attempt": 1, "branch": "loop/312-x",
              "ended_by": "run-clock", "outcome": "run-clock", "exit": 124,
              "iterations": 3, "faults": "none", "proposal": None,
              "proposed": None, "notified": None, "seed": "seeded",
              "criteria": "3", "title": "t", "url": "u",
              "task_ref": "acme/widgets#312"}
    record = events.run_outcome_record(row("run.outcome", legacy))
    assert record.ended_by == "run-clock"


def test_a_legacy_dispatch_failure_row_normalizes_to_the_sentinel():
    # The old dispatch-failed shape had no ended_by at all: the sentinel sat
    # in `outcome` alone.
    legacy = {"cycle": 7, "issue": 312, "title": "t", "url": "u",
              "task_ref": "acme/widgets#312", "attempt": 2,
              "branch": "loop/312-x", "outcome": "dispatch-failed",
              "error": "the box started no Run (exit 255)"}
    record = events.run_outcome_record(row("run.outcome", legacy))
    assert record.ended_by == events.DISPATCH_FAILED
    assert record.error == "the box started no Run (exit 255)"


def test_a_sparse_row_reads_as_a_record_with_gaps_not_a_crash():
    # Seeded and hand-appended rows can be thinner than the writer's. The
    # reader is total over them: absent keys are None, never a KeyError.
    record = events.run_outcome_record(
        row("run.outcome", {"issue": 9001, "ended_by": "iteration-cap"})
    )
    assert record.issue == 9001
    assert record.title is None
    assert record.exit is None


def test_a_reader_refuses_a_row_of_the_wrong_kind():
    import pytest
    with pytest.raises(ValueError):
        events.run_outcome_record(row("run.dispatched", {"issue": 1}))


# --- The issue rows an outcome produces --------------------------------------

ROUTE_BASE = dict(cycle=7, issue=312, title="t", url="u", attempt=1,
                  outcome="iteration-cap", proposal=PROPOSAL)


def test_a_retry_names_the_budget_it_is_spending():
    kind, payload = events.issue_retrying(**ROUTE_BASE)
    assert kind == "issue.retrying"
    assert payload["of"] == events.MAX_ATTEMPTS
    # A retry swaps no label and reads no checks; its row says neither.
    assert set(payload) == {"cycle", "issue", "title", "url", "attempt",
                            "outcome", "proposal", "of"}


def test_the_three_label_swaps_carry_exactly_their_own_facts():
    kind, payload = events.issue_awaiting_review(
        **ROUTE_BASE, label="awaiting-review", checks="green")
    assert (kind, payload["label"], payload["checks"]) == (
        "issue.awaiting-review", "awaiting-review", "green")

    kind, payload = events.issue_given_up(**ROUTE_BASE, label="ready-for-human")
    assert kind == "issue.given-up"
    assert "checks" not in payload and "failing" not in payload

    kind, payload = events.issue_handed_to_human(
        **ROUTE_BASE, label="ready-for-human", checks="none", failing=[])
    assert kind == "issue.handed-to-human"
    assert payload["checks"] == "none" and payload["failing"] == []


def test_a_check_state_outside_the_vocabulary_is_refused():
    import pytest
    with pytest.raises(ValueError):
        events.issue_handed_to_human(
            **ROUTE_BASE, label="ready-for-human", checks="amber", failing=[])


def test_route_failed_carries_its_two_callers_shapes():
    # The checks-read refusal: no label was ever chosen.
    kind, payload = events.issue_route_failed(
        **ROUTE_BASE, label=None, error="gh: boom")
    assert kind == "issue.route-failed"
    assert payload["label"] is None and payload["error"] == "gh: boom"
    assert "checks" not in payload
    # A route GitHub refused to apply: the label and what the checks said.
    kind, payload = events.issue_route_failed(
        **ROUTE_BASE, label="ready-for-human", error="gh: boom",
        checks="red", failing=["suite"])
    assert payload["checks"] == "red" and payload["failing"] == ["suite"]


def test_a_route_row_reads_back_with_its_name():
    kind, payload = events.issue_handed_to_human(
        **ROUTE_BASE, label="ready-for-human", checks="none", failing=[])
    record = events.route_record(row(kind, payload))
    assert record.route == "handed-to-human"
    assert record.checks == "none"
    assert record.label == "ready-for-human"
    assert record.outcome == "iteration-cap"


# --- The dispatch row and the watcher's rows ---------------------------------

RUN_CONTEXT = dict(cycle=7, issue=312, attempt=1, branch="loop/312-x",
                   task_ref="acme/widgets#312")


def test_a_dispatch_row_is_the_in_flight_lock_and_says_what_was_started():
    kind, payload = events.run_dispatched(
        **RUN_CONTEXT, title="t", url="u", area="the guest image",
        check="php tests/run.php", kept_progress=False)
    assert kind == "run.dispatched"
    assert set(payload) == {"cycle", "issue", "title", "url", "task_ref",
                            "attempt", "branch", "area", "check",
                            "kept_progress"}
    record = events.run_dispatched_record(row(kind, payload))
    assert record.area == "the guest image"
    assert record.branch == "loop/312-x"


def test_an_iteration_row_takes_the_parsers_record_whole():
    # watcher.py's parser yields exactly these keys; the constructor being
    # keyword-only is what makes `**record` a checked unpacking - a parser
    # key this vocabulary does not know is a TypeError, not a silent extra.
    parsed = {"iteration": 3, "started": "2026-08-31T12:04:00+00:00",
              "run_started": "2026-08-31T11:30:00+00:00", "agent_exit": 0,
              "exit_note": None, "turn_bound": 40, "noop": False,
              "head_before": "abc123def456", "head_after": "def456abc123",
              "promise": None, "dirty": False}
    kind, payload = events.run_iteration(**RUN_CONTEXT, **parsed)
    assert kind == "run.iteration"
    assert set(payload) == set(RUN_CONTEXT) | set(parsed)
    record = events.run_iteration_record(row(kind, payload))
    assert record.iteration == 3
    assert record.noop is False


def test_the_contract_row_carries_the_boxs_own_terms():
    kind, payload = events.run_contract(
        **RUN_CONTEXT, run_started="2026-08-31T11:30:00+00:00",
        contract=["Iterations: 10", "Run clock: 90m"])
    assert kind == "run.contract"
    assert set(payload) == set(RUN_CONTEXT) | {"run_started", "contract"}
    record = events.run_contract_record(row(kind, payload))
    assert record.contract == ["Iterations: 10", "Run clock: 90m"]


def test_a_watch_failure_is_one_row_naming_the_error():
    kind, payload = events.run_watch_failed(
        **RUN_CONTEXT, error="ssh: connect refused")
    assert kind == "run.watch-failed"
    assert set(payload) == set(RUN_CONTEXT) | {"error"}
    record = events.run_watch_failed_record(row(kind, payload))
    assert record.error == "ssh: connect refused"


# --- The cycle's own rows ----------------------------------------------------

def test_the_cycle_rows_spell_their_settled_shapes():
    kind, payload = events.cycle_started(
        repo="acme/widgets", label="ready-for-agent",
        allowlist=("an-operator",), review_cap=20,
        landing="propose", dry_run=False)
    # No `cycle` key: the row's own id IS the cycle id every later row names.
    assert (kind, "cycle" in payload) == ("cycle.started", False)
    # The target's settings travel with the cycle that ran under them (issue
    # #3): a Journal holding the reasoning but not the configuration would
    # leave "why that repository, under whose Handover?" answerable only from
    # a file that has since changed.
    assert set(payload) == {"repo", "label", "allowlist",
                            "review_cap", "landing", "dry_run"}

    kind, payload = events.cycle_skipped(reason="cycle-in-progress")
    assert (kind, set(payload)) == ("cycle.skipped", {"reason"})

    kind, payload = events.cycle_picked(
        cycle=7, number=312, title="t", url="u", area="a", check="c")
    assert (kind, set(payload)) == (
        "cycle.picked", {"cycle", "number", "title", "url", "area", "check"})

    kind, payload = events.cycle_finished(
        cycle=7, considered=5, eligible=2, skipped=3, picked=312,
        halted=None, in_flight=None, awaiting_review=1, review_cap=20,
        returned=0, dry_run=False)
    assert kind == "cycle.finished"
    assert set(payload) == {"cycle", "considered", "eligible", "skipped",
                            "picked", "dispatches", "halted", "in_flight",
                            "awaiting_review", "review_cap", "returned",
                            "dry_run"}

    kind, payload = events.cycle_failed(cycle=7, error="boom")
    assert (kind, set(payload)) == ("cycle.failed", {"cycle", "error"})
    record = events.cycle_failed_record(row(kind, payload))
    assert record.error == "boom"


def test_the_skip_and_the_loud_skip_rows():
    kind, payload = events.issue_skipped(
        cycle=7, number=312, title="t", url="u",
        reason="blocked-by-open-dependency", detail="blocked by #311")
    assert (kind, set(payload)) == (
        "issue.skipped",
        {"cycle", "number", "title", "url", "reason", "detail"})

    kind, payload = events.issue_returned(
        cycle=7, number=313, title="t", url="u", reason="missing-section",
        detail="no Acceptance criteria", added_label="needs-info",
        removed_label="ready-for-agent")
    assert kind == "issue.returned"
    returned = events.issue_returned_record(row(kind, payload))
    assert (returned.failed, returned.number) == (False, 313)

    kind, payload = events.issue_return_failed(
        cycle=7, number=313, title="t", url="u", reason="missing-section",
        detail="no Acceptance criteria", added_label="needs-info",
        removed_label="ready-for-agent", error="gh: boom")
    assert kind == "issue.return-failed"
    failed = events.issue_returned_record(row(kind, payload))
    assert (failed.failed, failed.error) == (True, "gh: boom")


def test_the_cycle_card_rows_read_back_as_records():
    kind, payload = events.cycle_started(
        repo="acme/widgets", label="ready-for-agent",
        allowlist=("an-operator",), review_cap=20,
        landing="propose", dry_run=True)
    started = events.cycle_started_record(row(kind, payload, id=51))
    assert (started.id, started.repo, started.review_cap, started.dry_run) == (
        51, "acme/widgets", 20, True)

    kind, payload = events.cycle_picked(
        cycle=51, number=312, title="t", url="u", area="a", check="c")
    pick = events.cycle_picked_record(row(kind, payload))
    assert (pick.number, pick.area) == (312, "a")

    kind, payload = events.cycle_finished(
        cycle=51, considered=5, eligible=[312], skipped={"proposal-open": 1},
        picked=312, halted=None, in_flight=None, awaiting_review=1,
        review_cap=20, returned=[313], dry_run=False)
    summary = events.cycle_finished_record(row(kind, payload))
    assert (summary.halted, summary.review_cap, summary.dispatches) == (None, 20, [312])

    kind, payload = events.issue_skipped(
        cycle=51, number=314, title="t", url="u",
        reason="attempts-exhausted", detail="2 of 2")
    skip = events.issue_skipped_record(row(kind, payload))
    assert (skip.number, skip.reason) == (314, "attempts-exhausted")


# --- The box and guardrail readings ------------------------------------------

def test_a_box_reading_is_one_record_whichever_kind_wrote_it():
    kind, payload = events.box_observed(
        cycle=7, scripts_hash="abc123", guest_template="loop-php:1",
        agent="claude", agent_version="2.1.14",
        credential_expires_at="2026-08-31T20:00:00+00:00")
    reading = events.box_record(row(kind, payload))
    assert reading.reachable is True
    assert reading.credential_expires_at == "2026-08-31T20:00:00+00:00"

    kind, payload = events.box_unreachable(cycle=7, error="ssh: refused")
    reading = events.box_record(row(kind, payload))
    assert (reading.reachable, reading.error) == (False, "ssh: refused")
    # The record's flag is an attribute, so no payload key - present or
    # future - can overwrite what the reader worked out. That guarantee is
    # the reason these are records and not splatted dicts.
    assert events.box_record(
        row("box.observed", {"reachable": False})).reachable is True


def test_a_guardrail_reading_is_one_record_whichever_kind_wrote_it():
    kind, payload = events.guardrail_observed(
        cycle=7, ref="master", ref_head="abc123", rules=["pull_request"],
        paths=["loop"], unreviewed=[],
        protected=True, detail=None)
    reading = events.guardrail_record(row(kind, payload))
    assert (reading.readable, reading.protected) == (True, True)

    kind, payload = events.guardrail_unreadable(cycle=7, error="gh: boom")
    reading = events.guardrail_record(row(kind, payload))
    assert (reading.readable, reading.error) == (False, "gh: boom")


# --- Proposal freshness rows ------------------------------------------------

def test_proposal_updated_and_failed_events_and_records():
    kind, payload = events.proposal_updated(
        cycle=7, proposal=12, url="https://github.invalid/acme/widgets/pull/12",
        issue=630)
    assert kind == "proposal.updated"
    assert payload["cycle"] == 7
    assert payload["proposal"] == 12
    assert payload["number"] == 12
    assert payload["url"] == "https://github.invalid/acme/widgets/pull/12"
    assert payload["issue"] == 630

    rec = events.proposal_updated_record(row(kind, payload, id=99))
    assert rec.id == 99
    assert rec.cycle == 7
    assert rec.proposal == 12
    assert rec.url == "https://github.invalid/acme/widgets/pull/12"
    assert rec.issue == 630

    kind_f, payload_f = events.proposal_update_failed(
        cycle=7, proposal=13, error="GitHub refused update-branch",
        url="https://github.invalid/acme/widgets/pull/13", issue=631)
    assert kind_f == "proposal.update-failed"
    assert payload_f["cycle"] == 7
    assert payload_f["proposal"] == 13
    assert payload_f["number"] == 13
    assert payload_f["error"] == "GitHub refused update-branch"
    assert payload_f["url"] == "https://github.invalid/acme/widgets/pull/13"
    assert payload_f["issue"] == 631

    rec_f = events.proposal_update_failed_record(row(kind_f, payload_f, id=100))
    assert rec_f.id == 100
    assert rec_f.cycle == 7
    assert rec_f.proposal == 13
    assert rec_f.error == "GitHub refused update-branch"
    assert rec_f.url == "https://github.invalid/acme/widgets/pull/13"
    assert rec_f.issue == 631

