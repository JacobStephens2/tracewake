#!/usr/bin/env python3
"""The Selector's cycle: read the tracker, apply Eligibility, journal the lot.

One cycle is: fetch the labeled queue through the tracker command, decide for
every issue in it whether it is Eligible and - when it is not - exactly why,
order what survives lowest-first, apply the caps, and append the whole of that
reasoning to the Selector Journal. The pick is the cycle's answer; the Journal
is its argument for the answer, which is what makes "why not #646 yesterday?"
a question with a recorded reply (spec #151, story 22).

Deterministic code, never an agent: no model output executes here, which is
what lets the Selector live on the VM that holds production credentials (ADR
0014).

A cycle has two modes and one body of reasoning. `--dry-run` reaches exactly
one thing outside itself - the tracker command, read-only - and writes exactly
one thing, the Journal. Without it the same reasoning is followed by acting on
it (issue #154): the pick is dispatched - branch, Seeding, push, the Run
started on the box - and an issue skipped for a missing section is returned to
the operator with a comment and a `needs-info` swap rather than quietly passed
over.

The two modes share every line of the deciding, so a dry-run is the cycle that
would have happened and not a separate approximation of one.

Everything the cycle reaches is a substitutable command (the Loop's *_COMMAND
convention, ADR 0004), which is also the seam the offline suite drives:
tests script canned queue states through SELECTOR_TRACKER_COMMAND and read the
Journal back.

Usage:

    cycle.py [--dry-run]

Everything it reads is environment (see README.md): the tracker command, the
repository, the label, the allowlist, the cap, and - for dispatch - the work
checkout, the Seeding command, the box command and the issue command.

Exit codes:

    0  the cycle ran and journaled its reasoning (with or without a pick), and
       anything it acted on succeeded. A Run that ended on a bound is a
       success here: which bound ended it is the Run's business, and the
       Termination Contract working is not the Selector failing.
    1  the cycle could not run, or something it tried to do failed - a tracker
       that did not answer, a Seeding refusal, a box that started no Run, a
       label swap GitHub rejected. Non-zero on purpose: the timer's OnFailure
       pages the operator, because a dead Selector must never look like a
       quiet queue (story 31).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import journal  # noqa: E402

HERE = Path(__file__).resolve().parent

# The sections `ready-for-agent` now promises (ADR 0014). Acceptance criteria
# is what seed-run.sh already refuses without; Owning area is what scopes the
# Run. Check is optional and supplies the Run's backpressure.
REQUIRED_SECTIONS = ("Acceptance criteria", "Owning area")

# One automatic retry, then the give-up swap (#155). Two dispatches for an
# issue is the budget spent - counted since the issue was last labeled, so
# re-applying the label after a give-up is a fresh Handover with a fresh
# budget, which is how an operator says "try that again".
MAX_ATTEMPTS = 2

# Rolling rather than calendar. "4 Runs a day" is a spend bound, and a
# calendar boundary lets eight Runs happen inside three hours across midnight
# while every one of them is within its day.
CAP_WINDOW_HOURS = 24

# How long a dispatch with no outcome still counts as a Run in flight. The
# Termination Contract's run clock is 90 minutes (loop/contract.sh,
# LOOP_RUN_TIMEOUT_SECONDS), so nothing legitimate is still running after
# this; what is, is a cycle that died between dispatching and recording the
# outcome. Without the bound that one death wedges every later cycle at
# `run-in-flight` forever, which is the failure the rolling cap window exists
# to prevent and this lock needs just as much.
IN_FLIGHT_STALE_HOURS = 4


@dataclass(frozen=True)
class Config:
    task_repo: str
    label: str
    needs_info_label: str
    allowlist: tuple[str, ...]
    daily_cap: int
    tracker_command: str

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            task_repo=env(
                "SELECTOR_TASK_REPO", "Educational-Travel-Adventures/tourbot"
            ),
            label=env("SELECTOR_LABEL", "ready-for-agent"),
            needs_info_label=env("SELECTOR_NEEDS_INFO_LABEL", "needs-info"),
            allowlist=tuple(
                name.strip()
                for name in env("SELECTOR_LABELER_ALLOWLIST", "JacobStephens2").split(",")
                if name.strip()
            ),
            daily_cap=int(env("SELECTOR_DAILY_CAP", "4")),
            tracker_command=env(
                "SELECTOR_TRACKER_COMMAND", str(HERE / "tracker-sources" / "github.sh")
            ),
        )


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


def _check_command(text: str) -> str:
    """The `Check` section's command: a fenced block if it has one, else the
    first line. Both spellings appear in the tracker."""
    fenced = re.search(r"^ {0,3}(?:`{3,}|~{3,}).*?\n(.*?)^ {0,3}(?:`{3,}|~{3,})",
                       text + "\n", re.DOTALL | re.MULTILINE)
    if fenced:
        return fenced.group(1).strip()
    return _first_line(text)


# --- Eligibility ------------------------------------------------------------


def eligibility(record: dict, config: Config, attempts: int) -> tuple[str, str] | None:
    """None when the issue is Eligible, else (reason, detail).

    The order is deliberate: the cheap, quiet reasons are tested before
    `missing-section`, which is the loud one - it comments on the issue and
    swaps its label (#154). An issue that is blocked anyway should not be
    shouted at for a gap the operator will fill when it is its turn.
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
    open_proposals = [
        p for p in record.get("proposals") or [] if p.get("state", "OPEN") == "OPEN"
    ]
    if open_proposals:
        urls = ", ".join(str(p.get("url") or p.get("number")) for p in open_proposals)
        return ("proposal-open", f"in flight: {urls}")
    if attempts >= MAX_ATTEMPTS:
        return (
            "attempts-exhausted",
            f"{attempts} dispatches already; the retry budget is {MAX_ATTEMPTS}",
        )
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
            + "; this issue has neither the section nor anything under it",
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
    """One `run.dispatched` row, with the two age questions already answered
    by the database that knows what "now" is."""

    issue: int
    at: datetime
    within_cap_window: bool
    stale: bool


class Spend:
    """What the Selector has already spent, read back from the Journal."""

    def __init__(self, dispatches: list[Dispatch], outcomes: list[int]):
        self._dispatches = dispatches
        self.recent_dispatches = sum(1 for d in dispatches if d.within_cap_window)
        started: dict[int, int] = {}
        for d in dispatches:
            if d.stale:
                continue
            started[d.issue] = started.get(d.issue, 0) + 1
        ended: dict[int, int] = {}
        for issue in outcomes:
            ended[issue] = ended.get(issue, 0) + 1
        # Per attempt rather than per issue: an issue dispatched, finished,
        # and dispatched again is in flight, even though an outcome for it
        # exists.
        self.in_flight = sorted(
            issue for issue, n in started.items() if n > ended.get(issue, 0)
        )

    def attempts(self, issue: int, since: str | None) -> int:
        """Dispatches of `issue` since it was last labeled.

        `since` is the tracker's ISO-8601 labeling time. Without one (an
        issue whose timeline holds no labeling) every dispatch counts, which
        is the conservative direction.
        """
        after = _parse_time(since)
        return sum(
            1
            for d in self._dispatches
            if d.issue == issue and (after is None or d.at > after)
        )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _spend(conn: psycopg.Connection) -> Spend:
    dispatches = [
        Dispatch(*row)
        for row in conn.execute(
            "SELECT (payload->>'issue')::bigint, at,"
            "       at > now() - make_interval(hours => %s),"
            "       at < now() - make_interval(hours => %s)"
            "  FROM journal.events WHERE kind = 'run.dispatched'",
            (CAP_WINDOW_HOURS, IN_FLIGHT_STALE_HOURS),
        ).fetchall()
    ]
    outcomes = [
        row[0]
        for row in conn.execute(
            "SELECT (payload->>'issue')::bigint FROM journal.events"
            " WHERE kind = 'run.outcome'"
        ).fetchall()
    ]
    return Spend(dispatches, outcomes)


# --- Returning an underspecified issue to the operator ----------------------


def _missing_section_comment(config: Config, detail: str) -> str:
    """What the operator reads on the issue when the Selector will not guess.

    Named gap first, then the whole contract, then what to do about it. It
    says how to put the issue back in the queue because the swap to
    `needs-info` takes it out of one, and a return with no way back is a
    queue that only ever drains into a siding.
    """
    return f"""The Selector picked this up from `{config.label}` and stopped before seeding a Run: {detail}.

`{config.label}` promises two sections a Run is built from (ADR 0014):

- `## Acceptance criteria` - one list item per criterion. They are carried into the Run's Plan verbatim and are its only definition of done.
- `## Owning area` - one phrase naming the part of this issue a single Run is scoped to. A Run is a handful of Iterations; deciding how much of an issue one is for is the operator's call, not an Iteration's.

Optionally `## Check` - a command that grades the work, run by the Run as it goes and by you afterwards.

The label has been swapped to `{config.needs_info_label}`. Add what is missing and re-apply `{config.label}`: that is a fresh Handover, with a fresh retry budget, and the next cycle picks it up.

*Posted by the Selector. Deterministic code, not an agent - no model wrote this and none read the issue.*"""


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
    payload = {
        "cycle": cycle_id,
        "number": number,
        "title": record.get("title"),
        "url": record.get("url"),
        "reason": "missing-section",
        "detail": detail,
        "added_label": config.needs_info_label,
        "removed_label": config.label,
    }
    try:
        dispatch.comment(
            dispatch_config,
            config.task_repo,
            number,
            _missing_section_comment(config, detail),
        )
        dispatch.relabel(
            dispatch_config,
            config.task_repo,
            number,
            add=config.needs_info_label,
            remove=config.label,
        )
    except dispatch.DispatchFailed as exc:
        journal.append(conn, "issue.return-failed", {**payload, "error": str(exc)})
        return False
    journal.append(conn, "issue.returned", payload)
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
    outcome = {
        "cycle": cycle_id,
        "issue": number,
        "title": pick.get("title"),
        "url": pick.get("url"),
        "task_ref": task_ref,
        "attempt": attempt,
    }
    try:
        branch = dispatch.prepare_branch(dispatch_config, number, pick["area"])
    except dispatch.DispatchFailed as exc:
        # Nothing has been dispatched, so nothing holds the lock and nothing
        # is journaled as a dispatch. The cycle still fails loudly.
        journal.append(
            conn, "cycle.failed", {"cycle": cycle_id, "error": str(exc)}
        )
        raise CycleFailed(str(exc)) from exc

    outcome["branch"] = branch
    journal.append(
        conn,
        "run.dispatched",
        {**outcome, "area": pick.get("area"), "check": pick.get("check")},
    )
    try:
        seeded = dispatch.seed(
            dispatch_config, config.task_repo, number, pick["area"], pick.get("check")
        )
        dispatch.push(dispatch_config, branch)
        summary = dispatch.start_run(dispatch_config, branch, task_ref)
    except dispatch.DispatchFailed as exc:
        journal.append(
            conn,
            "run.outcome",
            {**outcome, "outcome": "dispatch-failed", "error": str(exc)},
        )
        raise CycleFailed(str(exc)) from exc

    outcome.update(summary)
    # `outcome` beside `ended_by` rather than instead of it: a dispatch that
    # never started a Run has no bound to name, so the page and any later
    # reader need one key that is filled in for every row of this kind.
    outcome["outcome"] = summary["ended_by"]
    outcome["seed"] = seeded.get("LOOP_SEED_RESULT")
    outcome["criteria"] = seeded.get("LOOP_SEED_CRITERIA")
    journal.append(conn, "run.outcome", outcome)
    return outcome


# --- The cycle --------------------------------------------------------------


def fetch_queue(config: Config) -> list[dict]:
    """The labeled queue, through the substitutable tracker command."""
    try:
        completed = subprocess.run(
            [config.tracker_command, config.task_repo, config.label],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise CycleFailed(f"tracker command could not be run: {exc}") from exc
    if completed.returncode != 0:
        raise CycleFailed(
            f"tracker command exited {completed.returncode}: "
            f"{completed.stderr.strip() or 'no output'}"
        )
    try:
        payload = json.loads(completed.stdout)
        # The sort belongs inside the guard: a record with no number is a
        # malformed queue like any other, and it must reach the operator as a
        # journaled cycle.failed rather than as a traceback nothing recorded.
        return sorted(payload["issues"], key=lambda record: int(record["number"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise CycleFailed(f"tracker command did not return a queue: {exc}") from exc


class CycleFailed(Exception):
    """The cycle could not run. Journaled, printed, and exits non-zero."""


def run_cycle(
    conn: psycopg.Connection,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    *,
    dry_run: bool,
) -> dict:
    """One cycle. Returns the summary it journaled, plus what it then did."""
    cycle_id = journal.append(
        conn,
        "cycle.started",
        {
            "repo": config.task_repo,
            "label": config.label,
            "allowlist": list(config.allowlist),
            "daily_cap": config.daily_cap,
            "dry_run": dry_run,
        },
    )
    try:
        queue = fetch_queue(config)
    except CycleFailed as exc:
        journal.append(conn, "cycle.failed", {"cycle": cycle_id, "error": str(exc)})
        raise

    spend = _spend(conn)
    eligible, skipped = [], {}
    returned, return_failures = [], 0
    for record in queue:
        number = int(record["number"])
        verdict = eligibility(
            record, config, spend.attempts(number, record.get("labeledAt"))
        )
        if verdict is None:
            eligible.append(record)
            continue
        reason, detail = verdict
        skipped[reason] = skipped.get(reason, 0) + 1
        journal.append(
            conn,
            "issue.skipped",
            {
                "cycle": cycle_id,
                "number": number,
                "title": record.get("title"),
                "url": record.get("url"),
                "reason": reason,
                "detail": detail,
            },
        )
        # The loud skip. Independent of the caps and of the pick: returning an
        # underspecified issue is handing work back to the operator, not
        # spending a Run, and an issue the Selector will never seed should not
        # wait for a free budget to be told so.
        if reason == "missing-section" and not dry_run:
            if _return_to_operator(
                conn, cycle_id, config, dispatch_config, record, detail
            ):
                returned.append(number)
            else:
                return_failures += 1

    halted, pick, picked_record = None, None, None
    if not queue:
        halted = "queue-empty"
    elif spend.in_flight:
        halted = "run-in-flight"
    elif spend.recent_dispatches >= config.daily_cap:
        halted = "daily-cap-reached"
    elif not eligible:
        halted = "none-eligible"
    else:
        # Lowest first: deterministic and explainable, and it works a
        # dependency chain bottom-up because the chain was numbered that way.
        picked_record = eligible[0]
        body_sections = sections(picked_record.get("body") or "")
        check = body_sections.get("check", "")
        pick = {
            "cycle": cycle_id,
            "number": int(picked_record["number"]),
            "title": picked_record.get("title"),
            "url": picked_record.get("url"),
            "area": _first_line(body_sections.get("owning area", "")),
            "check": _check_command(check) or None,
        }
        journal.append(conn, "cycle.picked", pick)

    summary = {
        "cycle": cycle_id,
        "considered": len(queue),
        "eligible": [int(r["number"]) for r in eligible],
        "skipped": skipped,
        "picked": pick["number"] if pick else None,
        "halted": halted,
        "in_flight": spend.in_flight,
        "dispatched_in_window": spend.recent_dispatches,
        "daily_cap": config.daily_cap,
        "returned": returned,
        "dry_run": dry_run,
    }
    # Journaled before the dispatch rather than after it. A dispatch holds the
    # process for as long as the Run lasts, and a cycle card that only
    # appeared once the Run had finished would leave the page with no record
    # of the decision that started it for ninety minutes.
    journal.append(conn, "cycle.finished", summary)

    summary["return_failures"] = return_failures
    summary["outcome"] = None
    if pick and not dry_run:
        summary["outcome"] = _dispatch_pick(
            conn,
            cycle_id,
            config,
            dispatch_config,
            pick,
            spend.attempts(pick["number"], picked_record.get("labeledAt")) + 1,
        )
    return summary


def _report(summary: dict) -> None:
    print(f"considered   {summary['considered']}")
    print(f"eligible     {summary['eligible'] or 'none'}")
    for reason, count in sorted(summary["skipped"].items()):
        print(f"  skipped    {count} x {reason}")
    if summary["returned"]:
        print(f"returned     {summary['returned']} (commented, swapped to needs-info)")
    if summary.get("return_failures"):
        print(f"  FAILED     {summary['return_failures']} issue(s) could not be returned")
    if summary["picked"]:
        suffix = " (dry run - not dispatched)" if summary["dry_run"] else ""
        print(f"pick         #{summary['picked']}{suffix}")
    else:
        print(f"pick         none ({summary['halted']})")
    outcome = summary.get("outcome")
    if outcome:
        print(f"branch       {outcome['branch']}")
        print(
            f"run          {outcome['outcome']}"
            f" (exit {outcome.get('exit', '?')},"
            f" {outcome.get('iterations', '?')} iteration(s),"
            f" faults {outcome.get('faults', '?')})"
        )
        print(f"proposal     {outcome.get('proposal') or 'none'}")
    print(
        f"budget       {summary['dispatched_in_window']}/{summary['daily_cap']}"
        f" dispatches in the last {CAP_WINDOW_HOURS}h"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One Selector cycle: pick the lowest eligible labeled issue,"
        " and unless --dry-run, dispatch it."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reason and journal; dispatch nothing and write nothing to the"
        " tracker. The cycle that would have happened.",
    )
    args = parser.parse_args(argv)

    config = Config.from_env()
    dispatch_config = dispatch.DispatchConfig.from_env()
    try:
        with journal.connect() as conn:
            summary = run_cycle(conn, config, dispatch_config, dry_run=args.dry_run)
    except CycleFailed as exc:
        print(f"cycle.py: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"cycle.py: journal unavailable: {exc}", file=sys.stderr)
        return 1
    _report(summary)
    # A Run that ended on a bound is not a Selector failure; an issue the
    # Selector could not hand back is. Story 31 asks for the Selector's own
    # failures to page, and a comment or a label swap GitHub refused means the
    # issue is still sitting in the queue with nothing on it saying why.
    return 1 if summary.get("return_failures") else 0


if __name__ == "__main__":
    sys.exit(main())
