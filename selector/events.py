"""The Journal Event vocabulary: every kind, and what each payload holds.

One module owns what a Journal Event is (CONTEXT.md): closed for writers -
shipping code builds every row through the constructors here, so a kind
cannot be invented and a key cannot be misspelled - and open for readers,
which normalize a row from any era and render a kind they do not know
generically rather than failing, so a hand `psql` append stays legitimate.

Pure on purpose: no psycopg, no clock, no import from cycle. A constructor
returns the `(kind, payload)` pair `journal.append` takes, and deciding to
append is still the caller's; this module only spells.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- The naming rules -------------------------------------------------------
#
# What a Run's result is called, what counts as failure, and the closed value
# sets the payloads draw from. These are statements about what Journal rows
# MEAN, which is why they live here rather than with the code that decides
# what to do about them.

# One automatic retry, then the give-up swap (#155). Two dispatches for an
# issue is the budget spent - counted since the issue was last labeled, so
# re-applying the label after a give-up is a fresh Handover with a fresh
# budget, which is how an operator says "try that again". It is also the
# number the `of` key on an `issue.retrying` row means.
MAX_ATTEMPTS = 2

# The ending bounds that mean the Run failed rather than finished (#155,
# story 14). `iteration-cap` is the Run doing what it was designed to do -
# with faults or without them - and everything else is the Termination
# Contract cutting a Run short. A failure is retried once; a Run that reached
# its cap is judged on the Proposal it left instead.
RUN_FAILURE_BOUNDS = ("run-clock", "consecutive-noops", "agent-failed")

# A Run that ended within its bounds and proposed nothing is recorded under
# this instead of an ending bound. It is treated as a failed attempt: there is
# no Proposal to read checks for and nothing for the operator to review, and a
# second attempt on the same branch may well produce one.
NO_PROPOSAL = "no-proposal"

# Where an outcome can put the issue. The names double as the route rows'
# event kinds (`route_kind`) and as the page's badge CSS classes, which is
# why they are declared rather than derived: a rename here is a rename of
# all three, and the suites that pin the other two go red with it.
ROUTE_NAMES = ("retrying", "awaiting-review", "given-up", "handed-to-human")

# What a Proposal's checks can be. Four states, not three: `none` (no check
# ran at all) and `red` are opposite facts about how far a Proposal has been
# verified, and a reader that folds one into the other reports the false pass
# this vocabulary exists to prevent.
CHECK_STATES = ("green", "red", "pending", "none")

# The sentinel on the ended_by axis for a box that never started a Run. A Run
# that ended names its Bound; a dispatch that failed names this instead, and
# the two are told apart exactly as dispatch.py tells them apart - by whether
# the box reported LOOP_RUN_ENDED_BY at all.
DISPATCH_FAILED = "dispatch-failed"


def route_kind(name: str) -> str:
    """The event kind a route's row is journaled under."""
    return f"issue.{name}"


# The kind -> name map, derived once: `route_record` reads it per row and the
# page imports it rather than deriving its own copy, so there is exactly one
# spelling of which kinds are routes.
ROUTE_KIND_NAMES = {route_kind(name): name for name in ROUTE_NAMES}


def is_failure(ended_by, proposal) -> bool:
    """The failure predicate, spelled once (#280): the Contract cut the Run
    short, or it left nothing to review. Total over both the raw bound and
    the derived name, so the router (which holds the raw bound) and the
    notifier (which holds the derived name) read the same rule."""
    return ended_by in RUN_FAILURE_BOUNDS or not proposal


def outcome_name(ended_by: str, proposal: str | None) -> str:
    """What a Run's result is CALLED, which is not always the bound that ended
    it: a Run that reached its cap and proposed nothing gets its own name,
    because "iteration-cap with a Proposal" and "iteration-cap with nothing to
    show" are opposite results and a record that called them the same thing
    could not be read back.

    One spelling of the rule, because the router and the notifier both need
    the same answer (#280): the `run.outcome` row carries the raw bound - it
    is written before this distinction is drawn - and every reader deciding
    what to call a Run draws it again through this function.

    Total over the whole ended_by axis: a dispatch that started no Run keeps
    its sentinel rather than being misnamed "no-proposal", which is a claim
    about a Run that ran.
    """
    if ended_by == DISPATCH_FAILED or ended_by in RUN_FAILURE_BOUNDS:
        return ended_by
    return ended_by if proposal else NO_PROPOSAL


# --- The kinds and their constructors ---------------------------------------

RUN_OUTCOME = "run.outcome"


def run_outcome(*, cycle, issue, title, url, task_ref, attempt, branch,
                ended_by, exit, iterations, faults, proposal, proposed,
                notified, seed, criteria):
    """A Run's summary row: how the dispatch ended, in the box's own terms.

    `ended_by` is the one spelling of that fact - the raw Bound for a Run
    the box reported. The derived outcome name (`no-proposal` and friends)
    belongs to the issue's route row, never to this one.
    """
    return RUN_OUTCOME, {
        "cycle": cycle, "issue": issue, "title": title, "url": url,
        "task_ref": task_ref, "attempt": attempt, "branch": branch,
        "ended_by": ended_by, "exit": exit, "iterations": iterations,
        "faults": faults, "proposal": proposal, "proposed": proposed,
        "notified": notified, "seed": seed, "criteria": criteria,
    }


def run_dispatch_failed(*, cycle, issue, title, url, task_ref, attempt,
                        branch, error):
    """The summary row for a dispatch whose box started no Run at all."""
    return RUN_OUTCOME, {
        "cycle": cycle, "issue": issue, "title": title, "url": url,
        "task_ref": task_ref, "attempt": attempt, "branch": branch,
        "ended_by": DISPATCH_FAILED, "error": error,
    }


ISSUE_RETRYING = "issue.retrying"
ISSUE_ROUTE_FAILED = "issue.route-failed"
RUN_DISPATCHED = "run.dispatched"
RUN_ITERATION = "run.iteration"
RUN_CONTRACT = "run.contract"
RUN_WATCH_FAILED = "run.watch-failed"


def _run_context(cycle, issue, attempt, branch, task_ref):
    return {"cycle": cycle, "issue": issue, "attempt": attempt,
            "branch": branch, "task_ref": task_ref}


def run_dispatched(*, cycle, issue, title, url, task_ref, attempt, branch,
                   area, check, kept_progress):
    """The in-flight lock: journaled before the box is reached, and the row
    every later cycle reads to know a Run is holding the slot."""
    payload = _run_context(cycle, issue, attempt, branch, task_ref)
    payload.update({"title": title, "url": url, "area": area, "check": check,
                    "kept_progress": kept_progress})
    return RUN_DISPATCHED, payload


def run_iteration(*, cycle, issue, attempt, branch, task_ref, iteration,
                  started, run_started, agent_exit, exit_note, turn_bound,
                  noop, head_before, head_after, promise, dirty):
    """One Iteration, as the watcher read it off the box's Progress Log.

    Keyword-only so the watcher's `**record` unpacking is checked: a parser
    key this vocabulary does not know is a TypeError at the call site, not a
    silently journaled extra.
    """
    payload = _run_context(cycle, issue, attempt, branch, task_ref)
    payload.update({
        "iteration": iteration, "started": started,
        "run_started": run_started, "agent_exit": agent_exit,
        "exit_note": exit_note, "turn_bound": turn_bound, "noop": noop,
        "head_before": head_before, "head_after": head_after,
        "promise": promise, "dirty": dirty,
    })
    return RUN_ITERATION, payload


def run_contract(*, cycle, issue, attempt, branch, task_ref, run_started,
                 contract):
    """The Run's own terms, read once off the box: the bounds THAT Run was
    given rather than this side's configuration read back at the operator."""
    payload = _run_context(cycle, issue, attempt, branch, task_ref)
    payload.update({"run_started": run_started, "contract": contract})
    return RUN_CONTRACT, payload


RUN_BRIEFING = "run.briefing"


def run_briefing(*, cycle, issue, attempt, branch, task_ref, iteration,
                 briefing):
    """The first Iteration's rendered briefing, as the box emitted it.

    Persisted once per Run (#80): later Iterations differ only in number, so
    this one rendering is the stored fact of what the agent read, provable
    after the scripts change instead of vanishing with the scratch files. It
    carries no credentials - the task reference in the context, and file
    paths, the checklist and the completion promise in the text - which is
    what lets the window show it to both roles with no redaction.
    """
    payload = _run_context(cycle, issue, attempt, branch, task_ref)
    payload.update({"iteration": iteration, "briefing": briefing})
    return RUN_BRIEFING, payload


def run_watch_failed(*, cycle, issue, attempt, branch, task_ref, error):
    """A box that would not hand over its log - journaled once, not once a
    minute, and never a failure of the dispatch it was watching."""
    payload = _run_context(cycle, issue, attempt, branch, task_ref)
    payload["error"] = error
    return RUN_WATCH_FAILED, payload


def _checked(checks):
    if checks not in CHECK_STATES:
        raise ValueError(
            f"{checks!r} is not a check state - one of {CHECK_STATES}"
        )
    return checks


def _issue_base(cycle, issue, title, url, attempt, outcome, proposal):
    return {
        "cycle": cycle, "issue": issue, "title": title, "url": url,
        "attempt": attempt, "outcome": outcome, "proposal": proposal,
    }


def issue_retrying(*, cycle, issue, title, url, attempt, outcome, proposal):
    """A first failure kept in the queue: no label swap, no checks read."""
    payload = _issue_base(cycle, issue, title, url, attempt, outcome, proposal)
    payload["of"] = MAX_ATTEMPTS
    return ISSUE_RETRYING, payload


def issue_awaiting_review(*, cycle, issue, title, url, attempt, outcome,
                          proposal, label, checks):
    """The green route: a Proposal with green checks, waiting on the operator."""
    payload = _issue_base(cycle, issue, title, url, attempt, outcome, proposal)
    payload.update({"label": label, "checks": _checked(checks)})
    return route_kind("awaiting-review"), payload


def issue_given_up(*, cycle, issue, title, url, attempt, outcome, proposal,
                   label):
    """The budget-spent swap. No checks: a give-up never read any."""
    payload = _issue_base(cycle, issue, title, url, attempt, outcome, proposal)
    payload["label"] = label
    return route_kind("given-up"), payload


def issue_handed_to_human(*, cycle, issue, title, url, attempt, outcome,
                          proposal, label, checks, failing):
    """The not-green swap, carrying what the checks said - `red`, `pending`
    or `none`, which are three different facts and keep their own names."""
    payload = _issue_base(cycle, issue, title, url, attempt, outcome, proposal)
    payload.update({"label": label, "checks": _checked(checks),
                    "failing": failing})
    return route_kind("handed-to-human"), payload


def issue_route_failed(*, cycle, issue, title, url, attempt, outcome,
                       proposal, label, error, checks=None, failing=None):
    """The tracker refused the bookkeeping. Two callers, two shapes: the
    checks read that failed before any route was chosen (`label=None`, no
    checks), and a chosen route GitHub would not apply (the route's label,
    plus whatever the checks had said). One kind for both today - splitting
    them is a vocabulary change this module makes cheap but does not make.
    """
    payload = _issue_base(cycle, issue, title, url, attempt, outcome, proposal)
    payload.update({"label": label, "error": error})
    if checks is not None:
        payload["checks"] = _checked(checks)
    if failing is not None:
        payload["failing"] = failing
    return ISSUE_ROUTE_FAILED, payload


CYCLE_STARTED = "cycle.started"
CYCLE_SKIPPED = "cycle.skipped"
CYCLE_PICKED = "cycle.picked"
CYCLE_FINISHED = "cycle.finished"
CYCLE_FAILED = "cycle.failed"
ISSUE_SKIPPED = "issue.skipped"
ISSUE_RETURNED = "issue.returned"
ISSUE_RETURN_FAILED = "issue.return-failed"
BOX_OBSERVED = "box.observed"
BOX_UNREACHABLE = "box.unreachable"
GUARDRAIL_OBSERVED = "guardrail.observed"
GUARDRAIL_UNREADABLE = "guardrail.unreadable"
PROPOSAL_UPDATED = "proposal.updated"
PROPOSAL_UPDATE_FAILED = "proposal.update-failed"
PROPOSAL_RECONCILED = "proposal.reconciled"
PROPOSAL_RECONCILE_FAILED = "proposal.reconcile-failed"
TARGET_UNENROLLED = "target.unenrolled"



def cycle_started(*, repo, label, allowlist, review_cap, landing,
                  dry_run):
    """The cycle's opening row. It carries no `cycle` key on purpose: the id
    this row is appended under IS the cycle id every later row names.

    It carries the target's own settings - which repository, which Handover
    label, whose labelling counts, the caps, and what a finished Run does
    with its work - because they are configuration rather than code (issue
    #3), and a Journal that recorded the reasoning without the settings the
    reasoning ran under would leave "why did it pick that?" answerable only
    by reading a file that has since changed.
    """
    return CYCLE_STARTED, {
        "repo": repo, "label": label, "allowlist": allowlist,
        "review_cap": review_cap, "landing": landing,
        "dry_run": dry_run,
    }


def cycle_skipped(*, reason):
    """A cycle that stood down before reading anything - the advisory lock's
    row, and the one row with no cycle to belong to."""
    return CYCLE_SKIPPED, {"reason": reason}


def cycle_picked(*, cycle, number, title, url, area, check):
    return CYCLE_PICKED, {
        "cycle": cycle, "number": number, "title": title, "url": url,
        "area": area, "check": check,
    }


def cycle_finished(*, cycle, considered, eligible, skipped, picked=None,
                   dispatches=None, halted, in_flight, awaiting_review,
                   review_cap, returned, dry_run):
    """The cycle summary: what was read, what survived, every dispatch made,
    and - when `halted` names a cap or the pause - why nothing more was
    dispatched."""
    if dispatches is None:
        dispatches = [picked] if picked is not None else []
    elif picked is None and dispatches:
        picked = dispatches[0]
    return CYCLE_FINISHED, {
        "cycle": cycle, "considered": considered, "eligible": eligible,
        "skipped": skipped, "picked": picked, "dispatches": dispatches,
        "halted": halted, "in_flight": in_flight,
        "awaiting_review": awaiting_review,
        "review_cap": review_cap, "returned": returned, "dry_run": dry_run,
    }


def cycle_failed(*, cycle, error):
    return CYCLE_FAILED, {"cycle": cycle, "error": error}


def issue_skipped(*, cycle, number, title, url, reason, detail):
    """A quiet skip: the Eligibility clause that held, journaled and nothing
    else."""
    return ISSUE_SKIPPED, {
        "cycle": cycle, "number": number, "title": title, "url": url,
        "reason": reason, "detail": detail,
    }


def _returned_payload(cycle, number, title, url, reason, detail, added_label,
                      removed_label):
    return {
        "cycle": cycle, "number": number, "title": title, "url": url,
        "reason": reason, "detail": detail, "added_label": added_label,
        "removed_label": removed_label,
    }


def issue_returned(*, cycle, number, title, url, reason, detail, added_label,
                   removed_label):
    """The loud skip: handed back with a comment and a swap to needs-info."""
    return ISSUE_RETURNED, _returned_payload(
        cycle, number, title, url, reason, detail, added_label, removed_label)


def issue_return_failed(*, cycle, number, title, url, reason, detail,
                        added_label, removed_label, error):
    """A hand-back GitHub refused - the cycle exits non-zero for it."""
    payload = _returned_payload(
        cycle, number, title, url, reason, detail, added_label, removed_label)
    payload["error"] = error
    return ISSUE_RETURN_FAILED, payload


def box_observed(*, cycle, scripts_hash, guest_template, agent, agent_version,
                 credential_expires_at):
    """The box card's facts. `credential_expires_at` is an absolute instant,
    never a remaining time - the page does that arithmetic on its own clock."""
    return BOX_OBSERVED, {
        "cycle": cycle, "scripts_hash": scripts_hash,
        "guest_template": guest_template, "agent": agent,
        "agent_version": agent_version,
        "credential_expires_at": credential_expires_at,
    }


def box_unreachable(*, cycle, error):
    return BOX_UNREACHABLE, {"cycle": cycle, "error": error}


def guardrail_observed(*, cycle, ref, ref_head, rules, paths, unreviewed,
                       protected, detail, trees=None):
    """The write-protection reading, verdict included - `protected` is
    decided in reviewed Python before the row is written."""
    payload = {
        "cycle": cycle, "ref": ref, "ref_head": ref_head, "rules": rules,
        "paths": paths, "unreviewed": unreviewed, "protected": protected,
        "detail": detail,
    }
    if trees is not None:
        payload["trees"] = trees
    return GUARDRAIL_OBSERVED, payload


def guardrail_unreadable(*, cycle, error):
    return GUARDRAIL_UNREADABLE, {"cycle": cycle, "error": error}


def proposal_updated(*, cycle, proposal, url=None, number=None, issue=None):
    """An open Proposal behind base was brought up to date."""
    num = number if number is not None else (proposal if isinstance(proposal, int) else None)
    prop = proposal if proposal is not None else number
    payload = {"cycle": cycle, "proposal": prop}
    if num is not None:
        payload["number"] = num
    if url is not None:
        payload["url"] = url
    if issue is not None:
        payload["issue"] = issue
    return PROPOSAL_UPDATED, payload


def proposal_update_failed(*, cycle, proposal, error, url=None, number=None, issue=None):
    """An update the forge refused - journaled and fails no dispatch."""
    num = number if number is not None else (proposal if isinstance(proposal, int) else None)
    prop = proposal if proposal is not None else number
    payload = {"cycle": cycle, "proposal": prop, "error": error}
    if num is not None:
        payload["number"] = num
    if url is not None:
        payload["url"] = url
    if issue is not None:
        payload["issue"] = issue
    return PROPOSAL_UPDATE_FAILED, payload


def proposal_reconciled(*, cycle, proposal, url=None, number=None, issue=None,
                        branch=None):
    """A conflicting Proposal a reconcile Run brought up to date.

    Journaled only after the Run verified the merged branch and pushed it to
    the Proposal: a reconcile that resolved the conflicts but reds the suite
    is not reconciled, and the row must never read as though it were.
    `branch` is the Proposal's working branch as the box reported it.
    """
    num = number if number is not None else (proposal if isinstance(proposal, int) else None)
    prop = proposal if proposal is not None else number
    payload = {"cycle": cycle, "proposal": prop}
    if num is not None:
        payload["number"] = num
    if url is not None:
        payload["url"] = url
    if issue is not None:
        payload["issue"] = issue
    if branch is not None:
        payload["branch"] = branch
    return PROPOSAL_RECONCILED, payload


def proposal_reconcile_failed(*, cycle, proposal, error, url=None, number=None,
                              issue=None, branch=None, added_label=None,
                              removed_label=None):
    """A reconcile Run that left the Proposal conflicting, or a Proposal the
    Selector could not reconcile without inventing the resolution.

    The Proposal remains un-merged; the owning issue is escalated to
    `ready-for-human` alongside this row, and `added_label`/`removed_label`
    name the swap that escalation performed. A tracker that refused the
    escalation is the same kind with the refusal in `error` and no labels.
    """
    num = number if number is not None else (proposal if isinstance(proposal, int) else None)
    prop = proposal if proposal is not None else number
    payload = {"cycle": cycle, "proposal": prop, "error": error}
    if num is not None:
        payload["number"] = num
    if url is not None:
        payload["url"] = url
    if issue is not None:
        payload["issue"] = issue
    if branch is not None:
        payload["branch"] = branch
    if added_label is not None:
        payload["added_label"] = added_label
    if removed_label is not None:
        payload["removed_label"] = removed_label
    return PROPOSAL_RECONCILE_FAILED, payload


def target_unenrolled(*, owner, label, repos, new, dry_run=False):
    """The Handover label sits on a repository with no Target stanza.

    Instance-level, not per-target: one owner-wide search per Cycle, the gap
    against the declared Targets. `new` is the repos that were not in the
    last live warning - the standing gap journals with `new=[]` so the
    notifier can stay silent. Warn-only: nothing here enrolls a Target.
    """
    return TARGET_UNENROLLED, {
        "owner": owner,
        "label": label,
        "repos": repos,
        "new": new,
        "dry_run": dry_run,
    }



# --- Readers ----------------------------------------------------------------
#
# Total on purpose, in both directions the append-only table demands: a row
# written before the clean break normalizes into today's shape here and
# nowhere else, and a row thinner than the writer's - seeded, or appended by
# hand - reads as a record with gaps rather than a KeyError. What a reader is
# NOT total over is the kind: handing a reader the wrong kind is a caller bug
# and raises.


def _payload_of(row, expected_kind):
    if row.get("kind") != expected_kind:
        raise ValueError(
            f"a {expected_kind} reader was handed a {row.get('kind')!r} row"
        )
    return row.get("payload") or {}


@dataclass(frozen=True)
class CycleStarted:
    """The opening row; its `id` is the cycle id every later row names."""

    id: int | None
    at: object
    repo: str | None
    label: str | None
    allowlist: object
    review_cap: int | None
    dry_run: object


def cycle_started_record(row) -> CycleStarted:
    payload = _payload_of(row, CYCLE_STARTED)
    return CycleStarted(
        id=row.get("id"), at=row.get("at"), repo=payload.get("repo"),
        label=payload.get("label"), allowlist=payload.get("allowlist"),
        review_cap=payload.get("review_cap"), dry_run=payload.get("dry_run"),
    )


@dataclass(frozen=True)
class CyclePick:
    """What the cycle chose to dispatch."""

    id: int | None
    at: object
    cycle: int | None
    number: int | None
    title: str | None
    url: str | None
    area: str | None
    check: str | None


def cycle_picked_record(row) -> CyclePick:
    payload = _payload_of(row, CYCLE_PICKED)
    return CyclePick(
        id=row.get("id"), at=row.get("at"), cycle=payload.get("cycle"),
        number=payload.get("number"), title=payload.get("title"),
        url=payload.get("url"), area=payload.get("area"),
        check=payload.get("check"),
    )


@dataclass(frozen=True)
class CycleSummary:
    """The cycle.finished row: what was read, what survived, why it halted."""

    id: int | None
    at: object
    cycle: int | None
    considered: int | None
    eligible: object
    skipped: object
    picked: int | None
    halted: str | None
    in_flight: object
    awaiting_review: int | None
    review_cap: int | None
    returned: object
    dry_run: object
    dispatches: list[int] | None = None


def cycle_finished_record(row) -> CycleSummary:
    payload = _payload_of(row, CYCLE_FINISHED)
    dispatches = payload.get("dispatches")
    if dispatches is None:
        picked = payload.get("picked")
        dispatches = [picked] if picked is not None else []
    return CycleSummary(
        id=row.get("id"), at=row.get("at"), cycle=payload.get("cycle"),
        considered=payload.get("considered"), eligible=payload.get("eligible"),
        skipped=payload.get("skipped"), picked=payload.get("picked"),
        halted=payload.get("halted"), in_flight=payload.get("in_flight"),
        awaiting_review=payload.get("awaiting_review"),
        review_cap=payload.get("review_cap"), returned=payload.get("returned"),
        dry_run=payload.get("dry_run"),
        dispatches=dispatches,
    )


@dataclass(frozen=True)
class IssueSkip:
    """One quiet skip: the Eligibility clause that held."""

    id: int | None
    at: object
    cycle: int | None
    number: int | None
    title: str | None
    url: str | None
    reason: str | None
    detail: str | None


def issue_skipped_record(row) -> IssueSkip:
    payload = _payload_of(row, ISSUE_SKIPPED)
    return IssueSkip(
        id=row.get("id"), at=row.get("at"), cycle=payload.get("cycle"),
        number=payload.get("number"), title=payload.get("title"),
        url=payload.get("url"), reason=payload.get("reason"),
        detail=payload.get("detail"),
    )


@dataclass(frozen=True)
class CycleFailure:
    """One cycle.failed row: which cycle, and what refused."""

    id: int | None
    at: object
    cycle: int | None
    error: str | None


def cycle_failed_record(row) -> CycleFailure:
    payload = _payload_of(row, CYCLE_FAILED)
    return CycleFailure(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), error=payload.get("error"),
    )


@dataclass(frozen=True)
class IssueReturned:
    """A hand-back, succeeded or refused - `failed` says which."""

    id: int | None
    at: object
    failed: bool
    cycle: int | None
    number: int | None
    title: str | None
    url: str | None
    reason: str | None
    detail: str | None
    added_label: str | None
    removed_label: str | None
    error: str | None


def issue_returned_record(row) -> IssueReturned:
    kind = row.get("kind")
    if kind not in (ISSUE_RETURNED, ISSUE_RETURN_FAILED):
        raise ValueError(f"a hand-back reader was handed a {kind!r} row")
    payload = row.get("payload") or {}
    return IssueReturned(
        id=row.get("id"), at=row.get("at"),
        failed=kind == ISSUE_RETURN_FAILED,
        cycle=payload.get("cycle"), number=payload.get("number"),
        title=payload.get("title"), url=payload.get("url"),
        reason=payload.get("reason"), detail=payload.get("detail"),
        added_label=payload.get("added_label"),
        removed_label=payload.get("removed_label"),
        error=payload.get("error"),
    )


@dataclass(frozen=True)
class BoxReading:
    """One reading of the box, whichever kind took it.

    `reachable` is worked out from the kind and is an attribute, not a
    payload key - so no key, present or future, can overwrite it. The same
    holds for `readable` on GuardrailReading; the two flags are the stated
    guarantee that used to be a call-site convention.
    """

    id: int | None
    at: object
    reachable: bool
    cycle: int | None
    scripts_hash: str | None
    guest_template: str | None
    agent: str | None
    agent_version: str | None
    credential_expires_at: str | None
    error: str | None


def box_record(row) -> BoxReading:
    kind = row.get("kind")
    if kind not in (BOX_OBSERVED, BOX_UNREACHABLE):
        raise ValueError(f"a box reader was handed a {kind!r} row")
    payload = row.get("payload") or {}
    return BoxReading(
        id=row.get("id"), at=row.get("at"), reachable=kind == BOX_OBSERVED,
        cycle=payload.get("cycle"), scripts_hash=payload.get("scripts_hash"),
        guest_template=payload.get("guest_template"),
        agent=payload.get("agent"),
        agent_version=payload.get("agent_version"),
        credential_expires_at=payload.get("credential_expires_at"),
        error=payload.get("error"),
    )


@dataclass(frozen=True)
class GuardrailReading:
    """One reading of the write protection, whichever kind took it."""

    id: int | None
    at: object
    readable: bool
    cycle: int | None
    ref: str | None
    ref_head: str | None
    rules: object
    paths: object
    unreviewed: object
    protected: object
    detail: str | None
    error: str | None
    trees: object = None


def guardrail_record(row) -> GuardrailReading:
    kind = row.get("kind")
    if kind not in (GUARDRAIL_OBSERVED, GUARDRAIL_UNREADABLE):
        raise ValueError(f"a guardrail reader was handed a {kind!r} row")
    payload = row.get("payload") or {}
    return GuardrailReading(
        id=row.get("id"), at=row.get("at"),
        readable=kind == GUARDRAIL_OBSERVED,
        cycle=payload.get("cycle"), ref=payload.get("ref"),
        ref_head=payload.get("ref_head"), rules=payload.get("rules"),
        paths=payload.get("paths"), unreviewed=payload.get("unreviewed"),
        protected=payload.get("protected"), detail=payload.get("detail"),
        error=payload.get("error"),
        trees=payload.get("trees"),
    )


@dataclass(frozen=True)
class RunDispatched:
    """The dispatch row: what was started, where, and on which attempt."""

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    title: str | None
    url: str | None
    task_ref: str | None
    attempt: int | None
    branch: str | None
    area: str | None
    check: str | None
    kept_progress: object


def run_dispatched_record(row) -> RunDispatched:
    payload = _payload_of(row, RUN_DISPATCHED)
    return RunDispatched(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        title=payload.get("title"), url=payload.get("url"),
        task_ref=payload.get("task_ref"), attempt=payload.get("attempt"),
        branch=payload.get("branch"), area=payload.get("area"),
        check=payload.get("check"), kept_progress=payload.get("kept_progress"),
    )


@dataclass(frozen=True)
class IterationRow:
    """One Iteration as journaled by the watcher."""

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    attempt: int | None
    branch: str | None
    task_ref: str | None
    iteration: int | None
    started: object
    run_started: object
    agent_exit: int | None
    exit_note: str | None
    turn_bound: int | None
    noop: object
    head_before: str | None
    head_after: str | None
    promise: str | None
    dirty: object


def run_iteration_record(row) -> IterationRow:
    payload = _payload_of(row, RUN_ITERATION)
    return IterationRow(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        attempt=payload.get("attempt"), branch=payload.get("branch"),
        task_ref=payload.get("task_ref"), iteration=payload.get("iteration"),
        started=payload.get("started"), run_started=payload.get("run_started"),
        agent_exit=payload.get("agent_exit"),
        exit_note=payload.get("exit_note"),
        turn_bound=payload.get("turn_bound"), noop=payload.get("noop"),
        head_before=payload.get("head_before"),
        head_after=payload.get("head_after"), promise=payload.get("promise"),
        dirty=payload.get("dirty"),
    )


@dataclass(frozen=True)
class ContractRow:
    """The Run's own terms, one row per Run."""

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    attempt: int | None
    branch: str | None
    task_ref: str | None
    run_started: object
    contract: object


def run_contract_record(row) -> ContractRow:
    payload = _payload_of(row, RUN_CONTRACT)
    return ContractRow(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        attempt=payload.get("attempt"), branch=payload.get("branch"),
        task_ref=payload.get("task_ref"),
        run_started=payload.get("run_started"),
        contract=payload.get("contract"),
    )


@dataclass(frozen=True)
class BriefingRow:
    """The Run's first Iteration briefing, one row per Run."""

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    attempt: int | None
    branch: str | None
    task_ref: str | None
    iteration: int | None
    briefing: str | None


def run_briefing_record(row) -> BriefingRow:
    payload = _payload_of(row, RUN_BRIEFING)
    return BriefingRow(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        attempt=payload.get("attempt"), branch=payload.get("branch"),
        task_ref=payload.get("task_ref"),
        iteration=payload.get("iteration"),
        briefing=payload.get("briefing"),
    )


@dataclass(frozen=True)
class WatchFailure:
    """The one row a watcher that could not read the box leaves behind."""

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    attempt: int | None
    branch: str | None
    task_ref: str | None
    error: str | None


def run_watch_failed_record(row) -> WatchFailure:
    payload = _payload_of(row, RUN_WATCH_FAILED)
    return WatchFailure(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        attempt=payload.get("attempt"), branch=payload.get("branch"),
        task_ref=payload.get("task_ref"), error=payload.get("error"),
    )


@dataclass(frozen=True)
class RunOutcome:
    """One `run.outcome` row, whatever era wrote it.

    `ended_by` is the one axis of "how the dispatch ended": a Bound, or the
    `DISPATCH_FAILED` sentinel. Legacy rows spelled it twice (`outcome` held
    the same raw bound) or - for a failed dispatch - only as `outcome`; both
    normalize here.
    """

    id: int | None
    at: object
    cycle: int | None
    issue: int | None
    title: str | None
    url: str | None
    task_ref: str | None
    attempt: int | None
    branch: str | None
    ended_by: str | None
    exit: int | None
    iterations: int | None
    faults: str | None
    proposal: str | None
    proposed: str | None
    notified: str | None
    seed: str | None
    criteria: str | None
    error: str | None


@dataclass(frozen=True)
class RouteRow:
    """One route row - the badge, the label, and what the checks said."""

    id: int | None
    at: object
    route: str
    cycle: int | None
    issue: int | None
    title: str | None
    url: str | None
    attempt: int | None
    outcome: str | None
    proposal: str | None
    label: str | None
    checks: str | None
    failing: object


def route_record(row) -> RouteRow:
    name = ROUTE_KIND_NAMES.get(row.get("kind"))
    if name is None:
        raise ValueError(
            f"a route reader was handed a {row.get('kind')!r} row"
        )
    payload = row.get("payload") or {}
    return RouteRow(
        id=row.get("id"), at=row.get("at"), route=name,
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        title=payload.get("title"), url=payload.get("url"),
        attempt=payload.get("attempt"), outcome=payload.get("outcome"),
        proposal=payload.get("proposal"), label=payload.get("label"),
        checks=payload.get("checks"), failing=payload.get("failing"),
    )


def run_outcome_record(row) -> RunOutcome:
    payload = _payload_of(row, RUN_OUTCOME)
    return RunOutcome(
        id=row.get("id"), at=row.get("at"),
        cycle=payload.get("cycle"), issue=payload.get("issue"),
        title=payload.get("title"), url=payload.get("url"),
        task_ref=payload.get("task_ref"), attempt=payload.get("attempt"),
        branch=payload.get("branch"),
        ended_by=payload.get("ended_by") or payload.get("outcome"),
        exit=payload.get("exit"), iterations=payload.get("iterations"),
        faults=payload.get("faults"), proposal=payload.get("proposal"),
        proposed=payload.get("proposed"), notified=payload.get("notified"),
        seed=payload.get("seed"), criteria=payload.get("criteria"),
        error=payload.get("error"),
    )


@dataclass(frozen=True)
class ProposalUpdated:
    id: int | None
    at: object
    cycle: int | None
    proposal: int | str | None
    url: str | None
    issue: int | None


@dataclass(frozen=True)
class ProposalUpdateFailed:
    id: int | None
    at: object
    cycle: int | None
    proposal: int | str | None
    error: str | None
    url: str | None
    issue: int | None


def proposal_updated_record(row) -> ProposalUpdated:
    payload = _payload_of(row, PROPOSAL_UPDATED)
    return ProposalUpdated(
        id=row.get("id"),
        at=row.get("at"),
        cycle=payload.get("cycle"),
        proposal=payload.get("proposal"),
        url=payload.get("url"),
        issue=payload.get("issue"),
    )


def proposal_update_failed_record(row) -> ProposalUpdateFailed:
    payload = _payload_of(row, PROPOSAL_UPDATE_FAILED)
    return ProposalUpdateFailed(
        id=row.get("id"),
        at=row.get("at"),
        cycle=payload.get("cycle"),
        proposal=payload.get("proposal"),
        error=payload.get("error"),
        url=payload.get("url"),
        issue=payload.get("issue"),
    )


@dataclass(frozen=True)
class ProposalReconciled:
    id: int | None
    at: object
    cycle: int | None
    proposal: int | str | None
    url: str | None
    issue: int | None
    branch: str | None


@dataclass(frozen=True)
class ProposalReconcileFailed:
    id: int | None
    at: object
    cycle: int | None
    proposal: int | str | None
    error: str | None
    url: str | None
    issue: int | None
    branch: str | None
    added_label: str | None
    removed_label: str | None


def proposal_reconciled_record(row) -> ProposalReconciled:
    payload = _payload_of(row, PROPOSAL_RECONCILED)
    return ProposalReconciled(
        id=row.get("id"),
        at=row.get("at"),
        cycle=payload.get("cycle"),
        proposal=payload.get("proposal"),
        url=payload.get("url"),
        issue=payload.get("issue"),
        branch=payload.get("branch"),
    )


def proposal_reconcile_failed_record(row) -> ProposalReconcileFailed:
    payload = _payload_of(row, PROPOSAL_RECONCILE_FAILED)
    return ProposalReconcileFailed(
        id=row.get("id"),
        at=row.get("at"),
        cycle=payload.get("cycle"),
        proposal=payload.get("proposal"),
        error=payload.get("error"),
        url=payload.get("url"),
        issue=payload.get("issue"),
        branch=payload.get("branch"),
        added_label=payload.get("added_label"),
        removed_label=payload.get("removed_label"),
    )


@dataclass(frozen=True)
class UnenrolledTargets:
    """The gap: Handover-labeled issues on repositories with no Target."""

    id: int | None
    at: object
    owner: str | None
    label: str | None
    repos: object
    new: object
    dry_run: object


def target_unenrolled_record(row) -> UnenrolledTargets:
    payload = _payload_of(row, TARGET_UNENROLLED)
    return UnenrolledTargets(
        id=row.get("id"),
        at=row.get("at"),
        owner=payload.get("owner"),
        label=payload.get("label"),
        repos=payload.get("repos"),
        new=payload.get("new"),
        dry_run=payload.get("dry_run"),
    )

