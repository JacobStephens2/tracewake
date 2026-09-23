"""The doing a Cycle asks of the world.

Hand-back, Proposal upkeep, Dispatch of a pick, routing, and observation of
the box and the Guardrail. The Cycle in drain.py decides; this module acts.

A Cycle is handed a Doing: production wraps these functions, a dry run does
nothing and journals nothing, and a recorder sits at the same four
operations. The four per-drain Proposal sets are this module's state.
"""
from __future__ import annotations

import abc
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control  # noqa: E402
import dispatch  # noqa: E402
import events  # noqa: E402
import journal  # noqa: E402
import targets  # noqa: E402
import watcher  # noqa: E402
from drain import CycleFailed, spend, sections, check_command  # noqa: E402
from events import (  # noqa: E402
    MAX_ATTEMPTS,
    NO_PROPOSAL,
    is_failure,
    outcome_name,
)
from targets import Config  # noqa: E402

# Every comment the Selector posts ends with this. One copy, because four
# comments that each carried their own would drift, and the line is a claim
# about what wrote the comment - the thing a reader is entitled to see said
# the same way every time.
SIGNATURE = (
    "\n\n*Posted by the Selector. Deterministic code, not an agent - no model"
    " wrote this and none read the issue.*"
)


# What the box card on /loop is built from (#156, #260): four facts read off
# the box, over the box surface, once per cycle. Keys are the box's own
# `LOOP_BOX_*` names, mapped here to the Journal's.
#
# `credential_expires_at` is the odd one and is meant to be: the other three
# say what the box IS, and this one says whether it can currently do anything.
# It is journaled as the absolute instant the box gave, and NOT as a remaining
# time - the read happens once a cycle and the page is viewed whenever, so a
# duration recorded here would be stale by however long the page sat open. The
# page does that arithmetic against its own clock.
BOX_FACT_KEYS = {
    "LOOP_BOX_SCRIPTS_HASH": "scripts_hash",
    "LOOP_BOX_GUEST_TEMPLATE": "guest_template",
    "LOOP_BOX_AGENT": "agent",
    "LOOP_BOX_AGENT_VERSION": "agent_version",
    "LOOP_BOX_CREDENTIAL_EXPIRES_AT": "credential_expires_at",
}

# What the guardrail chip on /loop is built from (#165): the write protection
# standing over the paths that run unattended. Keys are the guardrail
# command's, mapped here to the Journal's, and the values are lists in every
# case but the two that name one thing.
GUARDRAIL_FACT_KEYS = {
    "SELECTOR_GUARDRAIL_REF": "ref",
    "SELECTOR_GUARDRAIL_REF_HEAD": "ref_head",
    "SELECTOR_GUARDRAIL_RULES": "rules",
    "SELECTOR_GUARDRAIL_PATHS": "paths",
    "SELECTOR_GUARDRAIL_UNREVIEWED": "unreviewed",
}
GUARDRAIL_LIST_FACTS = ("rules", "paths", "unreviewed")

# What has to be in force on the ref the executed paths are deployed from
# before the Selector is no easier to change than the repository it makes
# Proposals against (story 34).
#
#   pull_request       nothing lands without a review. The rule the whole
#                      guardrail is about.
#   non_fast_forward   the reviewed history cannot be replaced afterwards. A
#                      review a force-push can overwrite is not a review.
#   deletion           the branch cannot be deleted and re-created, which is
#                      the other way round the first two.
#
# Named rather than counted, so the chip can say which one went missing.
REQUIRED_RULES = ("pull_request", "non_fast_forward", "deletion")


# --- Proposal freshness ----------------------------------------------------


def is_conflicting(proposal: dict) -> bool:
    """True when an open Proposal cannot be cleanly merged into its base."""
    if proposal.get("state", "OPEN") != "OPEN":
        return False
    if proposal.get("conflicting") is True:
        return True
    mergeable = proposal.get("mergeable")
    if mergeable is False:
        return True
    if mergeable is not None and str(mergeable).upper() in ("CONFLICTING", "FALSE"):
        return True
    status = proposal.get("mergeStateStatus")
    if status is not None and str(status).upper() in ("DIRTY",):
        return True
    return False


def is_behind_and_mergeable(proposal: dict) -> bool:
    """True when an open Proposal is behind its base branch and mergeable."""
    if proposal.get("state", "OPEN") != "OPEN":
        return False
    if is_conflicting(proposal):
        return False
    status = str(proposal.get("mergeStateStatus") or "").upper()
    behind = proposal.get("behind") is True or status == "BEHIND"
    if not behind:
        return False
    mergeable = proposal.get("mergeable")
    if mergeable is not None:
        m_str = str(mergeable).upper()
        if m_str not in ("MERGEABLE", "TRUE"):
            return False
    return True


def update_proposals_freshness(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    issues: list[dict],
    *,
    updated: set,
    failed: set,
) -> None:
    """Update all open proposals behind their base and mergeable during a drain.

    An update is journaled once (`proposal.updated`). A forge refusal is
    journaled (`proposal.update-failed`) and fails no dispatch.
    """
    for issue_record in issues:
        issue_number = int(issue_record.get("number"))
        for p in issue_record.get("proposals") or []:
            proposal_target = p.get("number") if p.get("number") is not None else p.get("url")
            if not proposal_target:
                continue
            keys = [proposal_target]
            if p.get("number") is not None:
                keys.append(p.get("number"))
            if p.get("url"):
                keys.append(p.get("url"))
            if any(k in updated or k in failed for k in keys):
                continue
            if not is_behind_and_mergeable(p):
                continue
            try:
                dispatch.update_branch(dispatch_config, config.task_repo, proposal_target)
                journal.append(
                    conn,
                    *events.proposal_updated(
                        cycle=cycle_id,
                        proposal=proposal_target,
                        url=p.get("url"),
                        number=p.get("number"),
                        issue=issue_number,
                    ),
                )
                for k in keys:
                    updated.add(k)
            except dispatch.DispatchFailed as exc:
                journal.append(
                    conn,
                    *events.proposal_update_failed(
                        cycle=cycle_id,
                        proposal=proposal_target,
                        url=p.get("url"),
                        number=p.get("number"),
                        issue=issue_number,
                        error=str(exc),
                    ),
                )
                for k in keys:
                    failed.add(k)


def conflicting_proposals(
    issues: list[dict],
) -> list[tuple[dict, dict]]:
    """Every open conflicting Proposal, each with its owning issue record.

    The owning record is what names the escalation target and carries the
    Check section the reconcile Run verifies against - a Proposal without an
    owning issue in the queues is a Proposal this pass cannot act on.
    """
    found: list[tuple[dict, dict]] = []
    for issue_record in issues:
        for p in issue_record.get("proposals") or []:
            if is_conflicting(p):
                found.append((issue_record, p))
    return found


def _reconcile_failed_comment(config: Config, proposal_ref: object,
                              branch: str | None, error: str) -> str:
    """What the operator reads on the owning issue when a reconcile Run fails.

    The Proposal, the reason, and where the issue went: `ready-for-human`,
    from which no further Run is dispatched under this Handover. A reconcile
    that cannot be resolved or verified is operator work, not a retry - a
    second Run would meet the same conflicts, so the budget it would spend is
    the operator's review queue staying honest about what is in it.
    """
    where = (
        f"\n\nThe reconcile Run worked on branch `{branch}`."
        if branch else
        "\n\nThe reconcile Run started no branch work it could report."
    )
    return f"""The Selector dispatched a reconcile Run for the conflicting Proposal {proposal_ref}, and it could not be brought up to date with the base branch: {error}.

The Proposal remains un-merged. The label has been swapped to `{config.human_label}`: no further Run will be started for this issue under this Handover, and the conflicts are yours to resolve.{where}

The `proposal.reconcile-failed` row in the Selector Journal is the same ending in the Selector's own terms."""


def _escalate_reconcile(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    record: dict,
    proposal_target: object,
    url: str | None,
    number: int | None,
    branch: str | None,
    reconcile_error: str,
    remove_label: str,
) -> None:
    """Comment, swap the owning issue to `ready-for-human`, journal the failure.

    The comment goes first, for the loud skip's reason: a swap that landed
    with no comment would take the issue out of its queue with nothing on it
    saying why. A tracker that refuses the bookkeeping is paged like any
    other: an issue still sitting in its queue with a conflicting Proposal
    and nothing saying why is exactly the silence story 31 asks to be loud
    about.
    """
    issue_number = int(record["number"])
    try:
        dispatch.comment(
            dispatch_config,
            config.task_repo,
            issue_number,
            _reconcile_failed_comment(
                config, proposal_target, branch, reconcile_error) + SIGNATURE,
        )
        dispatch.relabel(
            dispatch_config,
            config.task_repo,
            issue_number,
            add=config.human_label,
            remove=remove_label,
        )
    except dispatch.DispatchFailed as exc:
        journal.append(
            conn,
            *events.proposal_reconcile_failed(
                cycle=cycle_id,
                proposal=proposal_target,
                url=url,
                number=number,
                issue=issue_number,
                branch=branch,
                error=f"{reconcile_error}; escalation refused: {exc}",
            ),
        )
        raise CycleFailed(str(exc)) from exc
    journal.append(
        conn,
        *events.proposal_reconcile_failed(
            cycle=cycle_id,
            proposal=proposal_target,
            url=url,
            number=number,
            issue=issue_number,
            branch=branch,
            error=reconcile_error,
            added_label=config.human_label,
            removed_label=remove_label,
        ),
    )


def reconcile_conflicting_proposals(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    handover: list[dict],
    review: list[dict],
    *,
    reconciled: set,
    failed: set,
) -> None:
    """Dispatch a reconcile Run for every conflicting open Proposal.

    The reconcile half of Proposal freshness (ADR 0023): `gh pr update-branch`
    refuses a Proposal it cannot cleanly merge, so a conflicting one rots
    unless something meets the conflicts inside the microVM boundary and
    resolves them. That something is a reconcile Run on the box, verifying the
    merged branch against the owning issue's Check before it pushes.

    One reconcile per Proposal per drain: `reconciled` and `failed` persist
    across the drain's passes like the freshness sets, so a Proposal is never
    reconciled twice in one Cycle. An escalation removes the owning issue
    from its queue, so the next pass - which re-fetches - no longer sees it.

    Paused means started nothing: unlike a forge-side fast-forward, a
    reconcile spends an agent Run on the box, and the pause flag suspends new
    dispatches. Likewise a Target already holding a Run stays serial: the box
    executes one Run at a time per Target, and a reconcile Run is a Run.
    Neither gate journals; the `cycle.finished` row already says why nothing
    was dispatched.
    """
    if control.is_paused(conn):
        return
    flight = spend(conn)
    if config.drain_concurrency == 1 and flight.in_flight:
        return
    if config.drain_concurrency > 1 and flight.in_flight_on(config.task_repo):
        return
    handover_numbers = {int(r["number"]) for r in handover}
    for issue_record, p in conflicting_proposals(handover + (review or [])):
        proposal_target = p.get("number") if p.get("number") is not None else p.get("url")
        if not proposal_target:
            continue
        keys = [proposal_target]
        if p.get("number") is not None:
            keys.append(p.get("number"))
        if p.get("url"):
            keys.append(p.get("url"))
        if any(k in reconciled or k in failed for k in keys):
            continue
        issue_number = int(issue_record["number"])
        # A leftover draft from a failed attempt still inside the retry
        # budget is the retry's head, not a review artifact to reconcile
        # (#102). Reconcile failure would escalate and spend the retry.
        if issue_number in handover_numbers:
            labeled_at = issue_record.get("labeledAt")
            if (
                flight.last_attempt_failed(
                    issue_number, labeled_at, repo=config.task_repo
                )
                and flight.attempts(
                    issue_number, labeled_at, repo=config.task_repo
                )
                < MAX_ATTEMPTS
            ):
                continue
        body_sections = sections(issue_record.get("body") or "")
        check = check_command(body_sections.get("check", "")) or None
        # The queue the owning issue came from is the label the escalation
        # removes: a Handover issue goes from `ready`, a review issue from
        # the review label, and either way it lands on `ready-for-human`.
        remove_label = (
            config.label
            if issue_number in handover_numbers
            else config.review_label
        )
        try:
            result = dispatch.reconcile(
                dispatch_config, config.task_repo, proposal_target, check)
        except dispatch.DispatchFailed as exc:
            _escalate_reconcile(
                conn, cycle_id, config, dispatch_config, issue_record,
                proposal_target, p.get("url"), p.get("number"),
                getattr(exc, "branch", None),
                str(exc), remove_label,
            )
            for k in keys:
                failed.add(k)
            continue
        journal.append(
            conn,
            *events.proposal_reconciled(
                cycle=cycle_id,
                proposal=proposal_target,
                url=p.get("url"),
                number=p.get("number"),
                issue=issue_number,
                branch=result.get("branch"),
            ),
        )
        for k in keys:
            reconciled.add(k)


# --- The box, as the page shows it -----------------------------------------


def observe_box(config: Config) -> tuple[dict | None, str | None]:
    """Read the box's own facts through the box surface: the hash of the Loop
    scripts it is holding, the guest template an Iteration is built from, the
    agent version installed on it, and when its model credential stops
    working.

    Returns `(facts, None)` or `(None, error)`. It never raises, and a failure
    never ends the cycle: an unreachable box is not the Selector failing, and
    the dispatch behind this read fails on its own and pages on its own. One
    outage should page once.

    Read once per cycle rather than at request time on /loop, for the reason
    the Journal exists (ADR 0015): the page is a window, and a window that
    opened an SSH session per view would make an unreachable box look like a
    broken dashboard.
    """
    # Every fact is optional. The box's copy of the Loop is updated by an
    # ansible apply rather than by a merge, so it can be older than this
    # repository - a key it does not report is the ordinary case, and the card
    # says so rather than filling it in from the controller's copy, which
    # would be the page asserting something it did not observe.
    return read_facts(
        config.box_facts_command,
        config.box_facts_timeout_seconds,
        BOX_FACT_KEYS,
        subject="the box",
        overlay=config.target.environ(),
    )


def read_facts(
    command: str,
    timeout_seconds: int,
    keys: dict[str, str],
    *,
    subject: str,
    list_keys: tuple[str, ...] = (),
    overlay: dict | None = None,
) -> tuple[dict | None, str | None]:
    """Run one status command and read its `KEY=value` lines back.

    The shape both status reads share (#156, #165): a substitutable command,
    a bound on how long it may hold the cycle open, and lines in the shape the
    box already answers a Run in. `keys` maps the command's names to the
    Journal's; `list_keys` names the ones whose value is a comma-separated
    list.

    Returns `(facts, None)` or `(None, error)` and never raises. Every key in
    `keys` is present in `facts`, as `None` when the command did not answer
    it - an absent key and an empty value are different answers, and the
    caller decides what each means.
    """
    try:
        done = subprocess.run(
            [command], capture_output=True, text=True, timeout=timeout_seconds,
            env=targets.overlaid(overlay),
        )
    except subprocess.TimeoutExpired:
        return None, f"{subject} did not answer within {timeout_seconds}s"
    except OSError as exc:
        return None, f"{command}: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return None, (detail[-1] if detail else f"exit {done.returncode}")
    facts: dict = {name: None for name in keys.values()}
    for line in done.stdout.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip() not in keys:
            continue
        name = keys[key.strip()]
        if name in list_keys:
            facts[name] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            facts[name] = value.strip() or None
    return facts, None


# --- The write protection over what runs unattended -------------------------


def observe_guardrail(config: Config) -> tuple[dict | None, str | None]:
    """Read the protection standing over the executed paths, and grade it.

    Two halves, because either one alone can be satisfied while the executed
    code is unreviewed: the rules GitHub holds over the ref those paths are
    deployed from, and whether the deployed tree's copy of them still matches
    that ref. A ruleset says nothing about the bytes systemd is about to exec
    from a shared working tree; a clean tree says nothing about what may be
    pushed to it tomorrow.

    With multiple declared trees (issue #14), every tree is read and the chip
    is green only when all of them are.

    Returns `(facts, None)` or `(None, error)`, and never raises. Like the box
    read, a guardrail that cannot be read does not end the cycle: it is a
    status read, and a Selector that stopped working because GitHub would not
    answer a question about its own rules would be a queue stopped by a
    dashboard. What it must not do is report green - see `guardrail_verdict`.
    """
    trees = config.guardrail_trees or targets.load_guardrail_trees()
    if not trees:
        return None, "no guardrail trees declared"

    tree_results: list[dict] = []
    errors: list[str] = []

    for tree in trees:
        overlay = {
            **config.target.environ(),
            **tree.environ(),
        }
        facts, error = read_facts(
            config.guardrail_command,
            config.guardrail_timeout_seconds,
            GUARDRAIL_FACT_KEYS,
            subject=f"the guardrail for {tree.repo}",
            list_keys=GUARDRAIL_LIST_FACTS,
            overlay=overlay,
        )
        if facts is None:
            err_msg = error or "command failed"
            errors.append(f"{tree.repo}: {err_msg}")
            tree_results.append({
                "repo": tree.repo,
                "ref": tree.ref,
                "ref_head": None,
                "rules": None,
                "paths": None,
                "unreviewed": None,
                "protected": False,
                "detail": f"{tree.repo}: could not be read ({err_msg})",
                "error": err_msg,
            })
        else:
            tree_facts = {**facts, "repo": tree.repo}
            protected, detail = guardrail_verdict(tree_facts)
            tree_results.append({
                **tree_facts,
                "protected": protected,
                "detail": detail,
                "error": None,
            })

    if all(t.get("error") is not None for t in tree_results):
        return None, ("; ".join(errors) or "the guardrail could not be read")

    all_protected = all(t["protected"] for t in tree_results)
    details = [t["detail"] for t in tree_results if not t["protected"] and t.get("detail")]
    overall_detail = "; ".join(details) or None

    all_paths = [p for t in tree_results if t.get("paths") for p in t["paths"]]
    all_unreviewed = [u for t in tree_results if t.get("unreviewed") for u in t["unreviewed"]]

    if len(tree_results) == 1:
        first = tree_results[0]
        payload = {
            "ref": first.get("ref"),
            "ref_head": first.get("ref_head"),
            "rules": first.get("rules"),
            "paths": first.get("paths"),
            "unreviewed": first.get("unreviewed"),
            "protected": all_protected,
            "detail": overall_detail,
            "trees": tree_results,
        }
    else:
        payload = {
            "ref": ", ".join(t.get("ref") or "" for t in tree_results),
            "ref_head": ", ".join(t.get("ref_head") or "unknown" for t in tree_results),
            "rules": list(dict.fromkeys(r for t in tree_results if t.get("rules") for r in t["rules"])),
            "paths": all_paths,
            "unreviewed": all_unreviewed,
            "protected": all_protected,
            "detail": overall_detail,
            "trees": tree_results,
        }
    return payload, None


def guardrail_verdict(facts: dict) -> tuple[bool, str | None]:
    """Green or not, and - when not - the sentence the chip carries.

    Unknown is not green. Every half the command did not answer counts
    against, because the failure this is for is a protection that quietly
    stopped applying, and a chip that stayed green through a command that had
    stopped reporting would be the reassurance rather than the check.
    """
    faults = []
    rules = facts.get("rules")
    prefix = f"{facts['repo']}: " if facts.get("repo") else ""
    if rules is None:
        faults.append(f"{prefix}the rules on {facts.get('ref') or 'the ref'} could not be read")
    else:
        missing = [rule for rule in REQUIRED_RULES if rule not in rules]
        if missing:
            faults.append(
                f"{prefix}{facts.get('ref') or 'the ref'} is missing "
                + ", ".join(missing)
            )
    unreviewed = facts.get("unreviewed")
    if unreviewed is None:
        faults.append(f"{prefix}the unreviewed-path comparison did not run")
    elif unreviewed:
        faults.append(
            f"{prefix}{len(unreviewed)} executed path(s) differ from "
            f"{facts.get('ref') or 'the protected ref'}: "
            + ", ".join(unreviewed)
        )
    if not facts.get("paths"):
        faults.append(f"{prefix}no executed paths were declared")
    return (not faults), ("; ".join(faults) or None)


# --- Returning an underspecified issue to the operator ----------------------


def _missing_section_comment(config: Config, detail: str) -> str:
    """What the operator reads on the issue when the Selector will not guess.

    Named gap first, then the whole contract, then what to do about it. It
    says how to put the issue back in the queue because the swap to
    `needs-info` takes it out of one, and a return with no way back is a
    queue that only ever drains into a siding.
    """
    return f"""The Selector picked this up from `{config.label}` and stopped before seeding a Run: {detail}.

`{config.label}` promises one section a Run is built from (ADR 0014):

- `## Acceptance criteria` - one list item per criterion. They are carried into the Run's Plan verbatim and are its only definition of done. Nothing else in the issue grades the work, so a Run cannot be started without them.

Two sections are optional:

- `## Owning area` - one phrase naming the part of this issue a single Run is scoped to. Without it the Run is scoped to this issue's title, which is the right answer unless the issue is bigger than one Run.
- `## Check` - a command that grades the work, run by the Run as it goes and by you afterwards.

The label has been swapped to `{config.needs_info_label}`. Add what is missing and re-apply `{config.label}`: that is a fresh Handover, with a fresh retry budget, and the next cycle picks it up."""


def _return_to_operator(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    record: dict,
    detail: str,
) -> bool:
    """The loud half of the loud skip: comment, swap the label, journal it.

    The comment goes first. A swap that landed with no comment would take the
    issue out of the queue with nothing on it saying why, which is the silent
    failure this whole path exists to avoid; a comment with no swap leaves the
    issue in the queue and it is simply commented on twice next cycle, which
    is noisy rather than lossy.
    """
    number = int(record["number"])
    returned = dict(
        cycle=cycle_id,
        number=number,
        title=record.get("title"),
        url=record.get("url"),
        reason="missing-section",
        detail=detail,
        added_label=config.needs_info_label,
        removed_label=config.label,
    )
    try:
        dispatch.comment(
            dispatch_config,
            config.task_repo,
            number,
            _missing_section_comment(config, detail) + SIGNATURE,
        )
        dispatch.relabel(
            dispatch_config,
            config.task_repo,
            number,
            add=config.needs_info_label,
            remove=config.label,
        )
    except dispatch.DispatchFailed as exc:
        journal.append(
            conn, *events.issue_return_failed(**returned, error=str(exc))
        )
        return False
    journal.append(conn, *events.issue_returned(**returned))
    return True


# --- Dispatch ---------------------------------------------------------------


def _dispatch_pick(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    pick: dict,
    attempt: int,
) -> dict:
    """Start the Run, and journal both ends of it.

    `run.dispatched` is written BEFORE the box is reached and `run.outcome`
    after, and the gap between them is the in-flight lock every later cycle
    reads (see Spend). So the ordering is the concurrency control: a dispatch
    that is journaled only on success would leave the ninety minutes it takes
    unguarded, which is the window a second cycle would start a second Run in.

    A Run that ended on a bound is an outcome, not a failure. A box that
    started no Run is a failure, and both are journaled as `run.outcome` so
    that the Journal has exactly one row per dispatch to pair with.
    """
    number = pick["number"]
    task_ref = f"{config.task_repo}#{number}"
    context = dict(
        cycle=cycle_id,
        issue=number,
        title=pick.get("title"),
        url=pick.get("url"),
        task_ref=task_ref,
        attempt=attempt,
    )
    try:
        branch = dispatch.prepare_branch(dispatch_config, number, pick["area"])
        # Both before the dispatch is journaled, because neither has reached
        # anything outside this VM yet: the branch is a checkout and the
        # keeping is a commit that is pushed with the Plan. A failure here is
        # a cycle that failed with nothing dispatched, not a Run that broke.
        kept = dispatch.keep_previous_progress(dispatch_config, branch)
    except dispatch.DispatchFailed as exc:
        # Nothing has been dispatched, so nothing holds the lock and nothing
        # is journaled as a dispatch. The Cycle journals cycle.failed.
        raise CycleFailed(str(exc)) from exc

    context["branch"] = branch
    journal.append(
        conn,
        *events.run_dispatched(
            **context,
            area=pick.get("area"),
            check=pick.get("check"),
            # Null on a first dispatch, and the path on a retry that had an
            # earlier attempt's Progress Log to move aside. A file that moved
            # on the Run's branch is not something to do silently.
            kept_progress=kept,
        ),
    )
    try:
        seeded = dispatch.seed(
            dispatch_config, config.task_repo, number, pick["area"], pick.get("check")
        )
        dispatch.push(dispatch_config, branch)
        # The watcher (#157) reads the box's Progress Log while the Run runs,
        # journaling each Iteration record as it appears. It is a window and
        # not a step: it holds nothing up, it cannot fail the dispatch, and
        # what it records is the only sign of life a Run gives off before it
        # ends. Scoped to exactly this call, because outside it there is no
        # Run to watch.
        with watcher.watching(
            {
                "cycle": cycle_id,
                "issue": number,
                "attempt": attempt,
                "branch": branch,
                "task_ref": task_ref,
            },
            watcher.WatchConfig.for_target(config.target),
        ):
            summary = dispatch.start_run(dispatch_config, branch, task_ref)
    except dispatch.DispatchFailed as exc:
        journal.append(
            conn, *events.run_dispatch_failed(**context, error=str(exc))
        )
        raise CycleFailed(str(exc)) from exc

    # What the agent read, journaled before the outcome so the rows read in
    # the order the Run happened them: the briefing rendered at Run start,
    # then how the Run ended (#80). A box that predates the emission reports
    # no briefing, which journals nothing rather than failing the dispatch.
    briefing = summary.pop("briefing", None)
    if briefing:
        journal.append(
            conn,
            *events.run_briefing(
                cycle=cycle_id,
                issue=number,
                attempt=attempt,
                branch=branch,
                task_ref=task_ref,
                iteration=1,
                briefing=briefing,
            ),
        )
    # `**summary` is a checked unpacking: the box report's fields and the
    # constructor's parameters are the same seven names, and a field one side
    # grows that the other does not know is a TypeError here rather than a
    # silently journaled extra.
    kind, outcome = events.run_outcome(
        **context,
        **summary,
        seed=seeded.get("LOOP_SEED_RESULT"),
        criteria=seeded.get("LOOP_SEED_CRITERIA"),
    )
    journal.append(conn, kind, outcome)
    return outcome


# --- Routing the outcome ----------------------------------------------------
#
# The Run has ended and the Journal has its outcome row. What is left is the
# bookkeeping the box deliberately cannot do (ADR 0010): move the issue into
# the queue its result puts it in, and say why on the issue itself.
#
# Four routes, from spec #151's stories 14-17. Which one is taken is decided
# from two facts only - the Run's ending bound, and the Proposal's checks -
# so that the reasoning is readable in one function rather than spread
# through the dispatch.


def _evidence_lines(branch: str | None) -> list[str]:
    """Where the Run's record lives, shared by every route comment.

    One copy, for the reason SIGNATURE is one copy: four comments that each
    spelled this differently would drift, and a pointer is only useful while
    it agrees with what the Run actually left behind.

    The branch's history, not its tip: since #79 the Run removes the Plan,
    the Progress Log and any kept-earlier log in a cleanup commit before
    proposing, so the tip is merge-clean and the record reads from the
    commits underneath it. The `run.outcome` row in the Selector Journal is
    the same ending in the Selector's own terms.
    """
    if not branch:
        return [
            "The Run's record is the `run.outcome` row the Selector",
            "journaled for this attempt.",
        ]
    return [
        f"The Run's record - the Plan, the Progress Log and any kept-earlier",
        f"log - is on branch `{branch}`. The tip carries none of that",
        "scaffolding: the Run removes it in a cleanup commit before proposing",
        "(#79), so read the record from the branch's history rather than from",
        "the tip. The `run.outcome` row in the Selector Journal is the same",
        "ending in the Selector's own terms.",
    ]


def _failure_comment(config: Config, outcome: str, attempt: int,
                     proposal: str | None, branch: str | None) -> str:
    """What the operator reads when the Selector has stopped trying.

    The bound that ended this attempt, then what that means, then where the
    work got to. It names the branch's Proposal when there is one: a Run that
    failed can still have left a partial diff worth reading, and a give-up
    that mentioned no artifact would send the operator looking for one.
    """
    # Only ever what this cycle actually saw. The earlier attempt ended in its
    # own cycle and may have ended differently; a comment that spoke for it
    # would be the Selector asserting something it did not observe, and the
    # Journal is where the whole sequence can be read back.
    ran = "ended with no Proposal" if outcome == NO_PROPOSAL else \
        f"was cut short by `{outcome}`"
    where = (
        f"\n\nWhat this attempt left behind: {proposal}"
        if proposal else
        "\n\nThis attempt left no Proposal."
    )
    evidence = "\n".join(_evidence_lines(branch))
    return f"""The Selector has now dispatched this issue {attempt} times. The last Run {ran}.

The retry budget is {MAX_ATTEMPTS} attempts per Handover, and it is now spent, so the label has been swapped to `{config.human_label}`. No further Run will be started for this issue: a third dispatch is refused whatever the label says.{where}

{evidence}

If the failure was transient, or you have changed something that fixes it, re-apply `{config.label}`: that is a fresh Handover with a fresh retry budget."""


def _red_checks_comment(config: Config, proposal: str,
                        failing: list[str], branch: str | None) -> str:
    """The failing check names, and why no repair Run is coming.

    Deliberately not a repair loop (spec #151, out of scope): CI output
    reaches the operator and the fix is a human's. Saying so on the issue is
    what stops the silence being read as "the Selector will handle it".
    """
    named = "\n".join(f"- `{name}`" for name in failing)
    evidence = "\n".join(_evidence_lines(branch))
    return f"""The Run ended cleanly and left a Proposal, but its checks are red.

{proposal}

Failing:

{named}

{evidence}

No repair Run will be started - committing CI output back into the branch for a fix-up Run is deliberately out of the Selector's scope. The label has been swapped to `{config.human_label}`."""


def _unsettled_checks_comment(config: Config, proposal: str,
                              waited: int, branch: str | None) -> str:
    """CI had not decided by the time the Selector stopped waiting.

    The surprising half of this path, so it says the wait out loud: pending is
    not green, and a Proposal whose checks are merely slow is handed over
    rather than held. It also says the checks may have finished since, because
    they usually will have - the operator is being asked to look, not told the
    work is broken.
    """
    evidence = "\n".join(_evidence_lines(branch))
    return f"""The Run ended cleanly and left a Proposal, but its checks did not finish within the {waited}s the Selector waits.

{proposal}

{evidence}

Unverified work is not put in the review queue, so the label has been swapped to `{config.human_label}` rather than `{config.review_label}`. The checks may well have gone green since; read them on the Proposal."""


def _no_checks_comment(config: Config, proposal: str,
                       branch: str | None) -> str:
    """A Proposal that no check ran against at all.

    Deliberately not green. "Every check passed" and "no check ran" are
    opposite facts about how far a Proposal has been verified, and on a
    repository that does have CI - which the task repository does - this
    answer usually means something went wrong upstream: a workflow file that
    will not parse, Actions disabled, a run that never triggered. Sending that
    to review as though it had passed would be the same false pass as a
    permission error read as an all-clear, reached by a different road.
    """
    evidence = "\n".join(_evidence_lines(branch))
    return f"""The Run ended cleanly and left a Proposal, but no check ran against it at all.

{proposal}

{evidence}

That is not the same as passing. On a repository with CI it usually means a workflow did not trigger - a file that will not parse, Actions disabled, or a run that never started - so the Proposal is unverified rather than clean. The label has been swapped to `{config.human_label}` rather than `{config.review_label}`."""


@dataclass(frozen=True)
class Route:
    """Where an outcome puts the issue: one name, one label, one event kind.

    The three travelled together as separate arguments and the label was
    named twice at every call site - once to relabel with, and once inside the
    journaled payload. Two spellings of one fact is two chances for the label
    the Journal records to differ from the label GitHub actually carries, and
    nothing downstream could have told which was true. Here they are one
    value, the payload's `label` is derived from it, and the page's CSS class
    comes from `name` rather than from taking the event kind apart again.
    """

    name: str
    label: str


def AWAITING_REVIEW(config: Config) -> Route:
    return Route("awaiting-review", config.review_label)


def GIVEN_UP(config: Config) -> Route:
    return Route("given-up", config.human_label)


def HANDED_TO_HUMAN(config: Config) -> Route:
    return Route("handed-to-human", config.human_label)


def _hand_over(
    conn: psycopg.Connection,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    number: int,
    body: str | None,
    route: Route,
    row: tuple,
    failed,
) -> str:
    """Comment (when there is something to say), swap the label, journal it.

    The comment goes first, for the same reason it does in the loud skip: a
    swap that landed with no comment takes the issue out of the agent queue
    with nothing on it saying why. The green route passes no body - the
    Proposal is the artifact and a comment restating that it exists is noise
    on an issue the operator is about to open anyway.

    `row` is the route's Journal Event, already constructed, and `failed`
    builds the `issue.route-failed` row for a tracker that refused - so both
    spellings of the bookkeeping come from the vocabulary rather than from
    this function editing payloads.
    """
    try:
        if body is not None:
            dispatch.comment(
                dispatch_config, config.task_repo, number, body + SIGNATURE
            )
        dispatch.relabel(
            dispatch_config, config.task_repo, number,
            add=route.label, remove=config.label,
        )
    except dispatch.DispatchFailed as exc:
        journal.append(conn, *failed(str(exc)))
        raise CycleFailed(str(exc)) from exc
    journal.append(conn, *row)
    return route.name


def _route(
    conn: psycopg.Connection,
    cycle_id: int,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    pick: dict,
    outcome: dict,
    attempt: int,
) -> str:
    """Decide the route, act on it, journal it. Returns the route's name.

    Raises CycleFailed when the tracker refused the bookkeeping: a Run whose
    result could not be recorded on the issue leaves the operator with a
    Proposal nothing points at, which is exactly the silent failure story 31
    asks to be paged for.
    """
    number = pick["number"]
    proposal = outcome.get("proposal")
    branch = outcome.get("branch")
    ended_by = outcome["ended_by"]
    failure = is_failure(ended_by, proposal)
    journaled_as = outcome_name(ended_by, proposal)

    base = dict(
        cycle=cycle_id,
        issue=number,
        title=pick.get("title"),
        url=pick.get("url"),
        attempt=attempt,
        outcome=journaled_as,
        proposal=proposal,
    )

    if failure and attempt < MAX_ATTEMPTS:
        # No label swap and no comment. The issue keeps `ready-for-agent`, so
        # the next cycle picks it up again by the ordinary route - the retry
        # is the queue working rather than a second dispatch path - and
        # prepare_branch continues the branch this attempt left behind.
        journal.append(conn, *events.issue_retrying(**base))
        return "retrying"

    if failure:
        route = GIVEN_UP(config)
        return _hand_over(
            conn, config, dispatch_config, number,
            _failure_comment(config, journaled_as, attempt, proposal, branch),
            route,
            events.issue_given_up(**base, label=route.label),
            lambda error: events.issue_route_failed(
                **base, label=route.label, error=error
            ),
        )

    try:
        answer = dispatch.settled_checks(
            dispatch_config, config.task_repo, proposal
        )
    except dispatch.DispatchFailed as exc:
        # A tracker that will not say whether CI passed is not a green light,
        # and guessing either way would be the Selector inventing a fact. It
        # is journaled and paged like any other refusal.
        journal.append(
            conn, *events.issue_route_failed(**base, label=None, error=str(exc))
        )
        raise CycleFailed(str(exc)) from exc

    state = answer["state"]

    if state == "green":
        # The only route into the review queue, and the only one that posts no
        # comment. Everything else lands on the operator.
        route = AWAITING_REVIEW(config)
        return _hand_over(
            conn, config, dispatch_config, number, None, route,
            events.issue_awaiting_review(
                **base, label=route.label, checks=state
            ),
            lambda error: events.issue_route_failed(
                **base, label=route.label, error=error, checks=state
            ),
        )

    # Everything below hands the issue to the operator with the same label,
    # and differs only in what the comment says - because "CI failed", "CI
    # never answered" and "no CI ran" call for three different next actions
    # from the person reading the issue.
    if state == "pending":
        body = _unsettled_checks_comment(
            config, proposal, dispatch_config.checks_timeout_seconds, branch
        )
        failing = []
    elif state == "none":
        body = _no_checks_comment(config, proposal, branch)
        failing = []
    else:
        # Red, and anything a future check state adds: not green is not
        # reviewable, and an unrecognised state must never reach the review
        # queue as though it had passed.
        body = _red_checks_comment(config, proposal, answer["failing"], branch)
        failing = answer["failing"]

    route = HANDED_TO_HUMAN(config)
    return _hand_over(
        conn, config, dispatch_config, number, body, route,
        events.issue_handed_to_human(
            **base, label=route.label, checks=state, failing=failing
        ),
        lambda error: events.issue_route_failed(
            **base, label=route.label, error=error, checks=state,
            failing=failing
        ),
    )


# --- The seam a Cycle is handed --------------------------------------------


@dataclass(frozen=True)
class ProposalUpkeep:
    """What keeping Proposals current has done this drain, cumulatively."""

    updated: set
    failed: set
    reconciled: set
    reconcile_failed: set


@dataclass(frozen=True)
class PickResult:
    """The Run's outcome and its Route, or that no Dispatch happened.

    `dispatched` is False when the doing did not start a Run - a dry run's
    answer, and the Cycle's reason to stop looking. Production always
    dispatches when asked, or raises.
    """

    dispatched: bool
    outcome: dict | None = None
    route: str | None = None


class Doing(abc.ABC):
    """The four operations a Cycle needs back from the world.

    Grouped by what the Cycle uses, not one per existing function. The Cycle
    never uses box facts and never acts between Dispatch and Route, so those
    orderings belong here. The Cycle journals its decisions; a Doing journals
    its acts.
    """

    @abc.abstractmethod
    def observe_guardrail(
        self, conn: psycopg.Connection, cycle_id: int, config: Config,
    ) -> None:
        """Read the Guardrail once, when the Cycle starts, and journal it."""

    @abc.abstractmethod
    def keep_proposals_current(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        handover: list[dict],
        review: list[dict],
    ) -> ProposalUpkeep:
        """Proposal Freshness and Reconcile Runs.

        Dedup across drain passes is this doing's own state. The answer is
        what was updated, reconciled, and failed.
        """

    @abc.abstractmethod
    def return_to_operator(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        record: dict,
        detail: str,
    ) -> bool | None:
        """The Loud Skip act: comment, relabel, journal.

        True if the tracker accepted, False if it refused, None if this
        doing did not try.
        """

    @abc.abstractmethod
    def work_pick(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        pick: dict,
        attempt: int,
    ) -> PickResult:
        """Read the box, Dispatch, Route. The Cycle never acts between them."""


class ProductionDoing(Doing):
    """Today's acts, including the journal rows that belong next to them."""

    def __init__(self) -> None:
        self.updated_proposals: set = set()
        self.failed_proposals: set = set()
        self.reconciled_proposals: set = set()
        self.reconcile_failed_proposals: set = set()

    def observe_guardrail(
        self, conn: psycopg.Connection, cycle_id: int, config: Config,
    ) -> None:
        guardrail, guardrail_error = observe_guardrail(config)
        journal.append(
            conn,
            *(
                events.guardrail_observed(cycle=cycle_id, **guardrail)
                if guardrail
                else events.guardrail_unreadable(
                    cycle=cycle_id, error=guardrail_error
                )
            ),
        )

    def keep_proposals_current(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        handover: list[dict],
        review: list[dict],
    ) -> ProposalUpkeep:
        update_proposals_freshness(
            conn,
            cycle_id,
            config,
            dispatch_config,
            handover + review,
            updated=self.updated_proposals,
            failed=self.failed_proposals,
        )
        reconcile_conflicting_proposals(
            conn,
            cycle_id,
            config,
            dispatch_config,
            handover,
            review,
            reconciled=self.reconciled_proposals,
            failed=self.reconcile_failed_proposals,
        )
        return ProposalUpkeep(
            updated=self.updated_proposals,
            failed=self.failed_proposals,
            reconciled=self.reconciled_proposals,
            reconcile_failed=self.reconcile_failed_proposals,
        )

    def return_to_operator(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        record: dict,
        detail: str,
    ) -> bool | None:
        return _return_to_operator(
            conn, cycle_id, config, dispatch_config, record, detail
        )

    def work_pick(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        pick: dict,
        attempt: int,
    ) -> PickResult:
        facts, box_error = observe_box(config)
        journal.append(
            conn,
            *(
                events.box_observed(cycle=cycle_id, **facts)
                if facts
                else events.box_unreachable(cycle=cycle_id, error=box_error)
            ),
        )
        outcome = _dispatch_pick(
            conn, cycle_id, config, dispatch_config, pick, attempt,
        )
        # Routing is separate from dispatching, and after it, because the two
        # answer different questions: `_dispatch_pick` records what the Run
        # did, and this decides what that means for the issue. Keeping the
        # outcome row unconditional is what stops a label swap GitHub refused
        # from erasing the Journal's record that a Run ever ran.
        route = _route(
            conn, cycle_id, config, dispatch_config, pick,
            outcome, attempt,
        )
        return PickResult(dispatched=True, outcome=outcome, route=route)


class DryRunDoing(Doing):
    """A doing that does nothing and journals nothing.

    `--dry-run` reaches the tracker and nothing else. The Cycle still
    journals its decisions; this adapter is how a new act cannot be added
    to the Cycle and forgotten in the dry-run path.
    """

    def observe_guardrail(
        self, conn: psycopg.Connection, cycle_id: int, config: Config,
    ) -> None:
        return None

    def keep_proposals_current(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        handover: list[dict],
        review: list[dict],
    ) -> ProposalUpkeep:
        return ProposalUpkeep(
            updated=set(), failed=set(), reconciled=set(), reconcile_failed=set(),
        )

    def return_to_operator(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        record: dict,
        detail: str,
    ) -> bool | None:
        return None

    def work_pick(
        self,
        conn: psycopg.Connection,
        cycle_id: int,
        config: Config,
        dispatch_config: dispatch.DispatchConfig,
        pick: dict,
        attempt: int,
    ) -> PickResult:
        return PickResult(dispatched=False)
