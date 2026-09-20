"""The Cycle: Eligibility, Spend, the review budget, and draining the queue.

A Cycle drains the queue (ADR 0021). This module is the deciding half: which
issues are Eligible, what has already been spent, how much review capacity
remains, and the loop that applies those. The doing lives in doing.py; the
process that starts a Cycle lives in cycle.py.
"""
from __future__ import annotations

import abc
import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control  # noqa: E402
import dispatch  # noqa: E402
import events  # noqa: E402
import journal  # noqa: E402
import targets  # noqa: E402
from events import MAX_ATTEMPTS, is_failure  # noqa: E402
from targets import Config  # noqa: E402

if TYPE_CHECKING:
    from doing import Doing, ProposalUpkeep

# The section `ready-for-agent` promises (ADR 0014). One, not two: Acceptance
# criteria is what seed-run.sh already refuses without, and it is the Run's
# only definition of done, so an issue without it is one the Selector could
# not seed and could not grade.
#
# `Owning area` was the second, and was dropped on 2026-08-27 as a deliberate
# amendment to spec #151's Issue contract. The requirement was defensible -
# `--area` is the Loop's scope fence, and deciding how much of a large issue
# one Run is for is a judgement - but it made the operator's Handover two
# steps instead of one, which is the exact friction the Selector exists to
# remove. Measured before it was dropped: of the 28 tourbot issues carrying
# `ready-for-agent` on 2026-08-26, 10 were otherwise ready and all 10 lacked
# the section - so the requirement's practical effect was to hand the whole
# queue back rather than to work it.
#
# What replaces it is a default rather than a guess: an issue with no
# `## Owning area` is scoped to its own title (see `_area`), which is the
# honest reading of "work this issue". An issue that IS too big for one Run
# still says so by carrying the section, and the fence still holds for it.
REQUIRED_SECTIONS = ("Acceptance criteria",)

# How long a dispatch with no outcome still counts as a Run in flight. The
# Termination Contract's run clock is 6 hours (loop/contract.sh,
# LOOP_RUN_TIMEOUT_SECONDS), so nothing legitimate is still running after
# this; what is, is a cycle that died between dispatching and recording the
# outcome. Without the bound that one death wedges every later cycle at
# `run-in-flight` forever, which is the failure the rolling cap window exists
# to prevent and this lock needs just as much.
IN_FLIGHT_STALE_HOURS = 8


# --- Reading the issue body -------------------------------------------------


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def sections(body: str) -> dict[str, str]:
    """Markdown sections of `body`, keyed by lowercased heading text.

    Fence-aware for the same reason seed-run.sh's reader is: an issue that
    quotes another issue's headings inside a code block is quoting them, and a
    shell comment in an example is not a heading.
    """
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    fence: str | None = None
    for line in body.splitlines():
        opening = _FENCE.match(line)
        if opening:
            token = opening.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            if current is not None:
                current.append(line)
            continue
        heading = None if fence else _HEADING.match(line)
        if heading:
            current = found.setdefault(heading.group(2).strip().lower(), [])
            continue
        if current is not None:
            current.append(line)
    return {name: "\n".join(lines).strip() for name, lines in found.items()}


def _first_line(text: str) -> str:
    """The section's value: its first non-empty line, undecorated.

    An `Owning area` section is a phrase, and operators write phrases as
    "**The nightly sync**" or "- the nightly sync" as readily as bare prose.
    """
    for line in text.splitlines():
        stripped = re.sub(r"^[ \t]*([-*+]|[0-9]+[.)])[ \t]+", "", line).strip()
        stripped = stripped.strip("*_` ").strip()
        if stripped:
            return stripped
    return ""


def _area(record: dict, body_sections: dict[str, str]) -> str:
    """The one owning area this Run is scoped to.

    `seed-run.sh --area` is required and is written into the Plan as the fence
    everything else in the issue is outside of, so a Run always has one. The
    question is only who names it.

    An `## Owning area` section names it: that is the operator saying this
    issue is bigger than one Run and here is the part to work. Without one the
    issue's own title is the area, which is the honest reading of "work this
    issue" - and is why `ready-for-agent` is a single step again rather than a
    label plus a section (amendment to #151, 2026-08-27).

    Never empty: a blank area is a Seeding refusal, and refusing a Run because
    a section the label no longer promises is missing would put the dropped
    requirement straight back in through the dispatch.
    """
    named = _first_line(body_sections.get("owning area", ""))
    return named or str(record.get("title") or "").strip() or f"issue #{record['number']}"


def _check_command(text: str) -> str:
    """The `Check` section's command: a fenced block if it has one, else the
    first line. Both spellings appear in the tracker."""
    fenced = re.search(r"^ {0,3}(?:`{3,}|~{3,}).*?\n(.*?)^ {0,3}(?:`{3,}|~{3,})",
                       text + "\n", re.DOTALL | re.MULTILINE)
    if fenced:
        return fenced.group(1).strip()
    return _first_line(text)


# --- Eligibility ------------------------------------------------------------


def eligibility(
    record: dict,
    config: Config,
    attempts: int,
    last_failed: bool = False,
) -> tuple[str, str] | None:
    """None when the issue is Eligible, else (reason, detail).

    The order is deliberate: the cheap, quiet reasons are tested before
    `missing-section`, which is the loud one - it comments on the issue and
    swaps its label (#154). An issue that is blocked anyway should not be
    shouted at for a gap the operator will fill when it is its turn.

    An open Proposal is in flight, except when it is the leftover draft of a
    failed attempt that still has retry budget (#102). That draft is the
    branch the retry continues, not a lock.
    """
    if record.get("labeledBy") not in config.allowlist:
        return (
            "labeler-not-allowlisted",
            f"labeled by {record.get('labeledBy') or 'nobody on the timeline'};"
            f" the Handover counts only from {', '.join(config.allowlist)}",
        )
    blocked = int(record.get("blockedBy") or 0)
    if blocked:
        return (
            "blocked-by-open-dependency",
            f"{blocked} open blocking edge(s) on the tracker",
        )
    sub_issues = int(record.get("openSubIssues") or 0)
    if sub_issues:
        return (
            "has-open-sub-issues",
            f"{sub_issues} open sub-issue(s); a parent spec is not a unit of work",
        )
    if attempts >= MAX_ATTEMPTS:
        return (
            "attempts-exhausted",
            f"{attempts} dispatches already; the retry budget is {MAX_ATTEMPTS}",
        )
    open_proposals = [
        p for p in record.get("proposals") or [] if p.get("state", "OPEN") == "OPEN"
    ]
    if open_proposals and not last_failed:
        urls = ", ".join(str(p.get("url") or p.get("number")) for p in open_proposals)
        return ("proposal-open", f"in flight: {urls}")
    present = sections(record.get("body") or "")
    missing = [
        name
        for name in REQUIRED_SECTIONS
        if not _first_line(present.get(name.lower(), ""))
    ]
    if missing:
        return (
            "missing-section",
            "the label promises "
            + " and ".join(f"an `{name}` section" for name in missing)
            + "; this issue has no such heading, or nothing under it",
        )
    return None


# --- Journal state ----------------------------------------------------------
#
# The caps and the retry budget are the Selector's own history, and the
# Journal is where its history lives. This is not the Journal deciding work:
# which issues exist and which are labeled comes from the tracker and nowhere
# else (ADR 0015). What is read back here is only how much the Selector has
# already spent.


@dataclass(frozen=True)
class Dispatch:
    """One `run.dispatched` row, with the age question already answered
    by the database that knows what "now" is."""

    issue: int
    at: datetime
    stale: bool
    repo: str | None = None


@dataclass(frozen=True)
class Outcome:
    """One `run.outcome` row, enough for in-flight counts and for whether
    the last attempt under this Handover was a failure (#102)."""

    repo: str | None
    issue: int
    at: datetime
    ended_by: str | None
    proposal: str | None


class Spend:
    """What the Selector has already spent, read back from the Journal."""

    def __init__(
        self,
        dispatches: list[Dispatch],
        outcomes: list[Outcome],
    ):
        self._dispatches = dispatches
        self._outcomes = outcomes
        started: dict[tuple[str | None, int], int] = {}
        for d in dispatches:
            if d.stale:
                continue
            key = (d.repo, d.issue)
            started[key] = started.get(key, 0) + 1
        ended: dict[tuple[str | None, int], int] = {}
        for o in outcomes:
            key = (o.repo, o.issue)
            ended[key] = ended.get(key, 0) + 1
        # Per attempt rather than per issue: an issue dispatched, finished,
        # and dispatched again is in flight, even though an outcome for it
        # exists. Keyed by (repo, issue) so the same number on two Targets
        # is two Runs (issue #37).
        self._in_flight_keys = sorted(
            key for key, n in started.items() if n > ended.get(key, 0)
        )
        self.in_flight = sorted({issue for _, issue in self._in_flight_keys})

    def in_flight_on(self, repo: str) -> list[int]:
        """Issues of `repo` that currently hold a Run slot."""
        return sorted(
            issue for r, issue in self._in_flight_keys if r == repo
        )

    def holds(self, issue: int, repo: str) -> bool:
        """Whether `issue` on `repo` currently holds a Run slot.

        A dispatch with no `task_ref` cannot name a Target; those still
        count against every Target, because the Journal cannot say they
        belong to someone else.
        """
        return (repo, issue) in self._in_flight_keys or (
            None, issue
        ) in self._in_flight_keys

    def runs_in_flight(self) -> int:
        """How many Runs currently hold a slot, across every Target.

        One per (repo, issue), which is the lock Eligibility uses. Unique
        issue numbers collapse two Targets sharing a number into one Run
        (issue #37); the widget's count must not.
        """
        return len(self._in_flight_keys)

    def attempts(self, issue: int, since: str | None, repo: str | None = None) -> int:
        """Dispatches of `issue` since it was last labeled.

        `since` is the tracker's ISO-8601 labeling time. Without one (an
        issue whose timeline holds no labeling) every dispatch counts, which
        is the conservative direction. `repo` scopes the count to one Target
        when two Targets share an issue number.
        """
        after = _parse_time(since)
        return sum(
            1
            for d in self._dispatches
            if d.issue == issue
            and (repo is None or not d.repo or d.repo == repo)
            and (after is None or d.at > after)
        )

    def last_attempt_failed(
        self, issue: int, since: str | None, repo: str | None = None
    ) -> bool:
        """Whether the most recent Run of `issue` since it was last labeled
        was a failure.

        The leftover draft of that failure is not in flight (#102): Eligibility
        uses this so a budgeted retry is not skipped as `proposal-open`.
        """
        after = _parse_time(since)
        matching = [
            o
            for o in self._outcomes
            if o.issue == issue
            and (repo is None or not o.repo or o.repo == repo)
            and (after is None or o.at > after)
        ]
        if not matching:
            return False
        last = max(matching, key=lambda o: o.at)
        return is_failure(last.ended_by, last.proposal)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def spend(conn: psycopg.Connection) -> Spend:
    """The Journal's answer to in-flight runs and attempt counts."""
    dispatches = [
        Dispatch(*row)
        for row in conn.execute(
            "SELECT (payload->>'issue')::bigint, at,"
            "       at < now() - make_interval(hours => %s),"
            "       nullif(split_part(payload->>'task_ref', '#', 1), '')"
            "  FROM journal.events WHERE kind = %s",
            (IN_FLIGHT_STALE_HOURS, events.RUN_DISPATCHED),
        ).fetchall()
    ]
    outcomes = [
        Outcome(*row)
        for row in conn.execute(
            "SELECT nullif(split_part(payload->>'task_ref', '#', 1), ''),"
            "       (payload->>'issue')::bigint, at,"
            "       payload->>'ended_by',"
            "       nullif(payload->>'proposal', '')"
            "  FROM journal.events WHERE kind = %s",
            (events.RUN_OUTCOME,),
        ).fetchall()
    ]
    return Spend(dispatches, outcomes)




@dataclass(frozen=True)
class TrackerQueue:
    """One labeled queue, plus whether GitHub has archived the repository.

    `archived` is a repository fact, not an Eligibility clause. An archived
    Target is read-only, so the Cycle must not dispatch, comment, relabel,
    or refresh Proposals against it. The adapter reports the flag; callers
    that only need the issue list keep using `fetch_queue`.
    """

    issues: list[dict]
    archived: bool = False


def fetch_queue(config: Config, label: str | None = None,
                timeout: float | None = None) -> list[dict]:
    """The labeled queue, through the substitutable tracker command.

    `label` defaults to the Handover label the cycle works, and is a parameter
    because the queue board reads the other labels in the lifecycle through
    this same command (#158) - one definition of "fetch a labeled queue",
    rather than a second one on the page that could normalise the tracker's
    answer differently.

    `timeout` is likewise for the board: a cycle waits as long as the tracker
    takes, but a read inside a page request must not.
    """
    return fetch_tracker(config, label=label, timeout=timeout).issues


def fetch_tracker(config: Config, label: str | None = None,
                  timeout: float | None = None) -> TrackerQueue:
    """The labeled queue and the repository's archived flag.

    A payload that omits `archived` is treated as not archived, which is
    what the offline suite's canned queues do. When the flag is true this
    function returns no issues, even if the payload still listed them, so
    every caller of `fetch_queue` - the board included - sees an empty
    queue rather than work GitHub will refuse.
    """
    try:
        completed = subprocess.run(
            [config.tracker_command, config.task_repo, label or config.label],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=targets.overlaid(config.target.environ()),
        )
    except subprocess.TimeoutExpired as exc:
        raise CycleFailed(
            f"tracker command did not answer within {timeout}s"
        ) from exc
    except OSError as exc:
        raise CycleFailed(f"tracker command could not be run: {exc}") from exc
    if completed.returncode != 0:
        raise CycleFailed(
            f"tracker command exited {completed.returncode}: "
            f"{completed.stderr.strip() or 'no output'}"
        )
    try:
        payload = json.loads(completed.stdout)
        archived = bool(payload.get("archived"))
        if archived:
            # The flag is the whole of the answer. Do not sort or return the
            # listed issues: an archived Target is not a queue, and a
            # malformed record in a leaked list must not fail the Cycle
            # instead of halting.
            return TrackerQueue(issues=[], archived=True)
        # The sort belongs inside the guard: a record with no number is a
        # malformed queue like any other, and it must reach the operator as a
        # journaled cycle.failed rather than as a traceback nothing recorded.
        issues = sorted(payload["issues"], key=lambda record: int(record["number"]))
        return TrackerQueue(issues=issues, archived=False)
    except (ValueError, KeyError, TypeError) as exc:
        raise CycleFailed(f"tracker command did not return a queue: {exc}") from exc


@dataclass(frozen=True)
class TrackerReads:
    """The Handover queue, the review queue, and whether the repository is archived.

    One operation's answer. An archived Target is not a queue: the Cycle
    halts before considering issues, refreshing Proposals, or dispatching.
    """

    handover: list[dict]
    review: list[dict]
    archived: bool = False


class Tracker(abc.ABC):
    """The one read a Cycle needs of the tracker."""

    @abc.abstractmethod
    def read(self, config: Config) -> TrackerReads:
        """The Handover queue, the review queue, and whether the repository is archived.

        A tracker that cannot be read raises CycleFailed: that is a failed
        Cycle, not an empty queue.
        """


class ProductionTracker(Tracker):
    """Today's `fetch_tracker` / `fetch_queue`, as one operation."""

    def read(self, config: Config) -> TrackerReads:
        tracked = fetch_tracker(config)
        if tracked.archived:
            return TrackerReads(handover=[], review=[], archived=True)
        return TrackerReads(
            handover=tracked.issues,
            review=fetch_queue(config, config.review_label),
            archived=False,
        )


class CycleFailed(Exception):
    """The cycle could not run. Journaled, printed, and exits non-zero."""


def review_budget(
    config: Config | None,
    *,
    handover: list[dict] | None = None,
    review: list[dict] | None = None,
    error: str | None = None,
    timeout: float | None = None,
    raise_on_error: bool = False,
) -> dict:
    """The target's review capacity, and what is left of it.

    Counts open issues in the review column (`config.review_label`), excluding
    any issues already accounted for in `handover`.
    Returns:
        {
            "count": count,
            "awaiting": count,
            "cap": cap,
            "remaining": remaining,
        }
    """
    if config is None:
        return {"count": None, "awaiting": None, "cap": None, "remaining": None}

    if error is not None:
        if raise_on_error:
            raise CycleFailed(error)
        count = None
    else:
        if review is None:
            try:
                review = fetch_queue(config, config.review_label, timeout=timeout)
            except CycleFailed:
                if raise_on_error:
                    raise
                review = None
        if review is not None:
            if handover is not None:
                placed = {r.get("number") for r in handover}
                review = [r for r in review if r.get("number") not in placed]
            count = len(review)
        else:
            count = None

    cap = config.review_cap
    remaining = None if count is None or cap is None else max(0, cap - count)
    return {
        "count": count,
        "awaiting": count,
        "cap": cap,
        "remaining": remaining,
    }


@dataclass(frozen=True)
class CycleResult:
    """What one Cycle did, as one value.

    Twelve facts are journaled as `cycle.finished`. The other six are not:
    they exist so the printed report and the firing's exit code can name a
    refused hand-back, the last Run, the last Route, and which Proposals
    were updated or reconciled, without changing the Journal's shape.
    """

    cycle: int
    considered: int
    eligible: list[int]
    skipped: dict[str, int]
    picked: int | None
    dispatches: list[int]
    halted: str | None
    in_flight: list[int] | None
    awaiting_review: int
    review_cap: int
    returned: list[int]
    dry_run: bool
    return_failures: int
    outcome: dict | None
    route: str | None
    updated_proposals: list[str]
    reconciled_proposals: list[str]
    reconcile_failures: list[str]


def run_cycle(
    conn: psycopg.Connection,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    *,
    tracker: Tracker,
    doing: Doing,
    dry_run: bool,
    slots: threading.Semaphore | None = None,
) -> CycleResult:
    """One cycle. Returns the summary it journaled, plus what it then did.

    `tracker` is the reads: Handover queue, review queue, archived flag.
    `doing` is the acts: Guardrail, Proposals, hand-back, Dispatch and Route.
    `dry_run` is a payload fact on started/finished, not a branch in this loop.
    `slots` is the instance-wide cap on concurrent Dispatches (issue #37).
    None means this cycle is the only one running, which is K=1.
    """
    cycle_id = journal.append(
        conn,
        *events.cycle_started(
            repo=config.task_repo,
            label=config.label,
            allowlist=list(config.allowlist),
            review_cap=config.review_cap,
            landing=config.target.landing,
            dry_run=dry_run,
        ),
    )
    dispatches: list[int] = []
    outcomes: list[dict] = []
    routes: list[str] = []
    skipped: dict[str, int] = {}
    skipped_numbers: set[int] = set()
    returned: list[int] = []
    return_failures: int = 0
    first_considered: int | None = None
    last_eligible: list[int] = []
    first_pick: dict | None = None
    halted: str | None = None
    last_spend: Spend | None = None
    last_budget: dict | None = None
    last_upkeep: ProposalUpkeep | None = None
    try:
        doing.observe_guardrail(conn, cycle_id, config)

        while True:
            tracked = tracker.read(config)
            if tracked.archived:
                # GitHub makes an archived repository read-only. Do not
                # consider its issues, refresh its Proposals, or dispatch:
                # those are writes, and a write GitHub will refuse is not a
                # Cycle that ran.
                if first_considered is None:
                    first_considered = 0
                halted = "repository-archived"
                break
            queue = tracked.handover
            review = tracked.review
            budget = review_budget(config, handover=queue, review=review, raise_on_error=True)

            last_upkeep = doing.keep_proposals_current(
                conn,
                cycle_id,
                config,
                dispatch_config,
                queue,
                review or [],
            )

            last_budget = budget

            if first_considered is None:
                first_considered = len(queue)

            cycle_spend = spend(conn)
            last_spend = cycle_spend
            eligible: list[dict] = []
            for record in queue:
                number = int(record["number"])
                if number in dispatches:
                    continue
                labeled_at = record.get("labeledAt")
                verdict = eligibility(
                    record, config,
                    cycle_spend.attempts(
                        number, labeled_at, repo=config.task_repo,
                    ),
                    cycle_spend.last_attempt_failed(
                        number, labeled_at, repo=config.task_repo,
                    ),
                )
                if verdict is None:
                    eligible.append(record)
                    continue
                reason, detail = verdict
                if number not in skipped_numbers:
                    skipped_numbers.add(number)
                    skipped[reason] = skipped.get(reason, 0) + 1
                    journal.append(
                        conn,
                        *events.issue_skipped(
                            cycle=cycle_id,
                            number=number,
                            title=record.get("title"),
                            url=record.get("url"),
                            reason=reason,
                            detail=detail,
                        ),
                    )
                    # The loud skip. Independent of the caps and of the pick: returning an
                    # underspecified issue is handing work back to the operator, not
                    # spending a Run, and an issue the Selector will never seed should not
                    # wait for a free budget to be told so.
                    if reason == "missing-section":
                        handed_back = doing.return_to_operator(
                            conn, cycle_id, config, dispatch_config, record, detail
                        )
                        if handed_back is True:
                            returned.append(number)
                        elif handed_back is False:
                            return_failures += 1

            last_eligible = [int(r["number"]) for r in eligible]

            pick = None
            held_slot = False
            if control.is_paused(conn):
                # The timer keeps running while paused. It still reads the queue and
                # journals Eligibility so the page remains an explanation of what
                # would have happened; only the Dispatch is stopped.
                halted = "paused"
                break
            elif not queue:
                halted = "queue-empty"
                break
            elif config.drain_concurrency == 1 and cycle_spend.in_flight:
                halted = "run-in-flight"
                break
            elif config.drain_concurrency > 1 and cycle_spend.in_flight_on(config.task_repo):
                # Per-Target leftover: a Run this Target already holds, including
                # one a previous cycle died before recording. Other Targets' live
                # Dispatches are the slot cap, not a halt.
                halted = "run-in-flight"
                break
            elif budget["remaining"] == 0:
                halted = "review-cap-reached"
                break
            elif not eligible:
                halted = "none-eligible"
                break
            else:
                # Lowest first: deterministic and explainable, and it works a
                # dependency chain bottom-up because the chain was numbered that way.
                # Acquire a slot before journaling the pick so a pause during the
                # wait still stops the pick rather than dispatching after it.
                if slots is not None:
                    slots.acquire()
                    held_slot = True
                    if control.is_paused(conn):
                        halted = "paused"
                        slots.release()
                        held_slot = False
                        break
                picked_record = eligible[0]
                body_sections = sections(picked_record.get("body") or "")
                check = body_sections.get("check", "")
                pick = {
                    "cycle": cycle_id,
                    "number": int(picked_record["number"]),
                    "title": picked_record.get("title"),
                    "url": picked_record.get("url"),
                    "area": _area(picked_record, body_sections),
                    "check": _check_command(check) or None,
                }
                journal.append(conn, *events.cycle_picked(**pick))
                if first_pick is None:
                    first_pick = pick

            try:
                attempt = cycle_spend.attempts(
                    pick["number"], picked_record.get("labeledAt"),
                    repo=config.task_repo,
                ) + 1
                worked = doing.work_pick(
                    conn, cycle_id, config, dispatch_config, pick, attempt,
                )
                if worked.dispatched:
                    outcomes.append(worked.outcome)
                    routes.append(worked.route)
                    dispatches.append(pick["number"])
            finally:
                if held_slot:
                    slots.release()

            # Stop looking when the doing did not Dispatch: a dry run still
            # journals one pick and then ends, with halted unset.
            if not worked.dispatched:
                break

        result = CycleResult(
            cycle=cycle_id,
            considered=first_considered if first_considered is not None else 0,
            eligible=last_eligible,
            skipped=skipped,
            picked=dispatches[0] if dispatches else (first_pick["number"] if first_pick else None),
            dispatches=dispatches,
            halted=halted,
            in_flight=last_spend.in_flight if last_spend else None,
            awaiting_review=(
                last_budget["count"]
                if last_budget and last_budget["count"] is not None
                else 0
            ),
            review_cap=config.review_cap,
            returned=returned,
            dry_run=dry_run,
            return_failures=return_failures,
            outcome=outcomes[-1] if outcomes else None,
            route=routes[-1] if routes else None,
            updated_proposals=(
                sorted(str(p) for p in last_upkeep.updated) if last_upkeep else []
            ),
            reconciled_proposals=(
                sorted(str(p) for p in last_upkeep.reconciled) if last_upkeep else []
            ),
            reconcile_failures=(
                sorted(str(p) for p in last_upkeep.reconcile_failed) if last_upkeep else []
            ),
        )
        # One cycle summary per drain names every dispatch and the reason it stopped.
        # The six extra fields stay on the value; extra kwargs would TypeError.
        journal.append(
            conn,
            *events.cycle_finished(
                cycle=result.cycle,
                considered=result.considered,
                eligible=result.eligible,
                skipped=result.skipped,
                picked=result.picked,
                dispatches=result.dispatches,
                halted=result.halted,
                in_flight=result.in_flight,
                awaiting_review=result.awaiting_review,
                review_cap=result.review_cap,
                returned=result.returned,
                dry_run=result.dry_run,
            ),
        )
        return result
    except CycleFailed as exc:
        journal.append(conn, *events.cycle_failed(cycle=cycle_id, error=str(exc)))
        raise
