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

import control  # noqa: E402
import dispatch  # noqa: E402
import journal  # noqa: E402
import watcher  # noqa: E402

HERE = Path(__file__).resolve().parent

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

# One automatic retry, then the give-up swap (#155). Two dispatches for an
# issue is the budget spent - counted since the issue was last labeled, so
# re-applying the label after a give-up is a fresh Handover with a fresh
# budget, which is how an operator says "try that again".
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

# Every comment the Selector posts ends with this. One copy, because four
# comments that each carried their own would drift, and the line is a claim
# about what wrote the comment - the thing a reader is entitled to see said
# the same way every time.
SIGNATURE = (
    "\n\n*Posted by the Selector. Deterministic code, not an agent - no model"
    " wrote this and none read the issue.*"
)

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

# One cycle at a time, whoever started it. A Postgres advisory lock on the
# Journal connection rather than a lock file, for two reasons: the Journal is
# already the one thing every cycle holds open, and a lock the DATABASE owns
# is released by the connection dying - a lock file left behind by a killed
# cycle would wedge the timer until somebody noticed and deleted it.
#
# It matters because overlap is the normal case here, not the exceptional one:
# the timer fires every thirty minutes and a dispatch holds the process for as
# long as the Run lasts, which the Termination Contract bounds at ninety
# minutes. Two cycles reasoning at once would read the same spend, and could
# pick and dispatch the same issue twice.
#
# The in-flight lock in `Spend` does NOT cover this. That one is journaled and
# is about Runs; this one is about processes, and is what stops a second cycle
# before it has read anything at all.
CYCLE_LOCK_KEY = 0x5E1EC7

# What the box card on /loop is built from (#156): three facts read off the
# box, over the box surface, once per cycle. Keys are the box's own
# `LOOP_BOX_*` names, mapped here to the Journal's.
BOX_FACT_KEYS = {
    "LOOP_BOX_SCRIPTS_HASH": "scripts_hash",
    "LOOP_BOX_GUEST_TEMPLATE": "guest_template",
    "LOOP_BOX_AGENT": "agent",
    "LOOP_BOX_AGENT_VERSION": "agent_version",
}


@dataclass(frozen=True)
class Config:
    task_repo: str
    label: str
    needs_info_label: str
    review_label: str
    human_label: str
    allowlist: tuple[str, ...]
    daily_cap: int
    tracker_command: str
    box_facts_command: str
    box_facts_timeout_seconds: int
    board_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            task_repo=env(
                "SELECTOR_TASK_REPO", "Educational-Travel-Adventures/tourbot"
            ),
            label=env("SELECTOR_LABEL", "ready-for-agent"),
            needs_info_label=env("SELECTOR_NEEDS_INFO_LABEL", "needs-info"),
            review_label=env("SELECTOR_REVIEW_LABEL", "awaiting-review"),
            human_label=env("SELECTOR_HUMAN_LABEL", "ready-for-human"),
            allowlist=tuple(
                name.strip()
                for name in env("SELECTOR_LABELER_ALLOWLIST", "JacobStephens2").split(",")
                if name.strip()
            ),
            daily_cap=int(env("SELECTOR_DAILY_CAP", "4")),
            tracker_command=env(
                "SELECTOR_TRACKER_COMMAND", str(HERE / "tracker-sources" / "github.sh")
            ),
            box_facts_command=env(
                "SELECTOR_BOX_FACTS_COMMAND", str(HERE / "box-sources" / "facts.sh")
            ),
            # Short on purpose. This is a status read, and a status read that
            # can hold a cycle open is worse than one that goes missing: the
            # dispatch behind it is what the cycle is for.
            box_facts_timeout_seconds=int(
                env("SELECTOR_BOX_FACTS_TIMEOUT_SECONDS", "60")
            ),
            # Shorter still, and for a sharper version of the same reason: the
            # queue board's tracker reads happen inside a page request. Three
            # of them against the live tourbot queue took about three seconds
            # on 2026-08-27, so ten seconds is a tracker that is broken rather
            # than slow - and a column saying so beats a page that hangs.
            board_timeout_seconds=int(
                env("SELECTOR_BOARD_TIMEOUT_SECONDS", "10")
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


def spend(conn: psycopg.Connection) -> Spend:
    """The Journal's answer to "what has been spent". Public because the page
    shows the same budget the cycle enforces (#156): two readings of "Runs
    today" that could disagree would be a status strip that reassures about a
    cap it is not the one reading."""
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


# --- The box, as the page shows it -----------------------------------------


def observe_box(config: Config) -> tuple[dict | None, str | None]:
    """Read the box's own facts through the box surface: the hash of the Loop
    scripts it is holding, the guest template an Iteration is built from, and
    the agent version installed on it.

    Returns `(facts, None)` or `(None, error)`. It never raises, and a failure
    never ends the cycle: an unreachable box is not the Selector failing, and
    the dispatch behind this read fails on its own and pages on its own. One
    outage should page once.

    Read once per cycle rather than at request time on /loop, for the reason
    the Journal exists (ADR 0015): the page is a window, and a window that
    opened an SSH session per view would make an unreachable box look like a
    broken dashboard.
    """
    try:
        done = subprocess.run(
            [config.box_facts_command],
            capture_output=True,
            text=True,
            timeout=config.box_facts_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"the box did not answer within {config.box_facts_timeout_seconds}s"
        )
    except OSError as exc:
        return None, f"{config.box_facts_command}: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return None, (detail[-1] if detail else f"exit {done.returncode}")
    # Every fact is optional. The box's copy of the Loop is updated by an
    # ansible apply rather than by a merge, so it can be older than this
    # repository - a key it does not report is the ordinary case, and the card
    # says so rather than filling it in from the controller's copy, which
    # would be the page asserting something it did not observe.
    facts = {name: None for name in BOX_FACT_KEYS.values()}
    for line in done.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in BOX_FACT_KEYS:
            facts[BOX_FACT_KEYS[key.strip()]] = value.strip() or None
    return facts, None


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
            watcher.WatchConfig.from_env(),
        ):
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


def _failure_comment(config: Config, outcome: str, attempt: int,
                     proposal: str | None) -> str:
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
    return f"""The Selector has now dispatched this issue {attempt} times. The last Run {ran}.

The retry budget is {MAX_ATTEMPTS} attempts per Handover, and it is now spent, so the label has been swapped to `{config.human_label}`. No further Run will be started for this issue: a third dispatch is refused whatever the label says.{where}

If the failure was transient, or you have changed something that fixes it, re-apply `{config.label}`: that is a fresh Handover with a fresh retry budget."""


def _red_checks_comment(config: Config, proposal: str,
                        failing: list[str]) -> str:
    """The failing check names, and why no repair Run is coming.

    Deliberately not a repair loop (spec #151, out of scope): CI output
    reaches the operator and the fix is a human's. Saying so on the issue is
    what stops the silence being read as "the Selector will handle it".
    """
    named = "\n".join(f"- `{name}`" for name in failing)
    return f"""The Run ended cleanly and left a Proposal, but its checks are red.

{proposal}

Failing:

{named}

No repair Run will be started - committing CI output back into the branch for a fix-up Run is deliberately out of the Selector's scope. The label has been swapped to `{config.human_label}`."""


def _unsettled_checks_comment(config: Config, proposal: str,
                              waited: int) -> str:
    """CI had not decided by the time the Selector stopped waiting.

    The surprising half of this path, so it says the wait out loud: pending is
    not green, and a Proposal whose checks are merely slow is handed over
    rather than held. It also says the checks may have finished since, because
    they usually will have - the operator is being asked to look, not told the
    work is broken.
    """
    return f"""The Run ended cleanly and left a Proposal, but its checks did not finish within the {waited}s the Selector waits.

{proposal}

Unverified work is not put in the review queue, so the label has been swapped to `{config.human_label}` rather than `{config.review_label}`. The checks may well have gone green since; read them on the Proposal."""


def _no_checks_comment(config: Config, proposal: str) -> str:
    """A Proposal that no check ran against at all.

    Deliberately not green. "Every check passed" and "no check ran" are
    opposite facts about how far a Proposal has been verified, and on a
    repository that does have CI - which the task repository does - this
    answer usually means something went wrong upstream: a workflow file that
    will not parse, Actions disabled, a run that never triggered. Sending that
    to review as though it had passed would be the same false pass as a
    permission error read as an all-clear, reached by a different road.
    """
    return f"""The Run ended cleanly and left a Proposal, but no check ran against it at all.

{proposal}

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

    @property
    def kind(self) -> str:
        return f"issue.{self.name}"


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
    payload: dict,
    route: Route,
) -> str:
    """Comment (when there is something to say), swap the label, journal it.

    The comment goes first, for the same reason it does in the loud skip: a
    swap that landed with no comment takes the issue out of the agent queue
    with nothing on it saying why. The green route passes no body - the
    Proposal is the artifact and a comment restating that it exists is noise
    on an issue the operator is about to open anyway.
    """
    payload = {**payload, "label": route.label}
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
        journal.append(conn, "issue.route-failed", {**payload, "error": str(exc)})
        raise CycleFailed(str(exc)) from exc
    journal.append(conn, route.kind, payload)
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
    ended_by = outcome["outcome"]
    failure = ended_by in RUN_FAILURE_BOUNDS or not proposal
    # What the Journal records this attempt as, which is not always the bound
    # that ended it: a Run that reached its cap and proposed nothing gets its
    # own name, because "iteration-cap with a Proposal" and "iteration-cap
    # with nothing to show" are opposite results and a Journal that called
    # them the same thing could not be read back.
    journaled_as = ended_by if ended_by in RUN_FAILURE_BOUNDS else (
        ended_by if proposal else NO_PROPOSAL
    )

    payload = {
        "cycle": cycle_id,
        "issue": number,
        "title": pick.get("title"),
        "url": pick.get("url"),
        "attempt": attempt,
        "outcome": journaled_as,
        "proposal": proposal,
    }

    if failure and attempt < MAX_ATTEMPTS:
        # No label swap and no comment. The issue keeps `ready-for-agent`, so
        # the next cycle picks it up again by the ordinary route - the retry
        # is the queue working rather than a second dispatch path - and
        # prepare_branch continues the branch this attempt left behind.
        journal.append(conn, "issue.retrying", {**payload, "of": MAX_ATTEMPTS})
        return "retrying"

    if failure:
        return _hand_over(
            conn, config, dispatch_config, number,
            _failure_comment(config, journaled_as, attempt, proposal),
            payload, GIVEN_UP(config),
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
            conn, "issue.route-failed",
            {**payload, "label": None, "error": str(exc)},
        )
        raise CycleFailed(str(exc)) from exc

    state = answer["state"]
    payload = {**payload, "checks": state}

    if state == "green":
        # The only route into the review queue, and the only one that posts no
        # comment. Everything else lands on the operator.
        return _hand_over(
            conn, config, dispatch_config, number, None,
            payload, AWAITING_REVIEW(config),
        )

    # Everything below hands the issue to the operator with the same label,
    # and differs only in what the comment says - because "CI failed", "CI
    # never answered" and "no CI ran" call for three different next actions
    # from the person reading the issue.
    if state == "pending":
        body = _unsettled_checks_comment(
            config, proposal, dispatch_config.checks_timeout_seconds
        )
        failing = []
    elif state == "none":
        body = _no_checks_comment(config, proposal)
        failing = []
    else:
        # Red, and anything a future check state adds: not green is not
        # reviewable, and an unrecognised state must never reach the review
        # queue as though it had passed.
        body = _red_checks_comment(config, proposal, answer["failing"])
        failing = answer["failing"]

    return _hand_over(
        conn, config, dispatch_config, number, body,
        {**payload, "failing": failing}, HANDED_TO_HUMAN(config),
    )


# --- The cycle --------------------------------------------------------------


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
    try:
        completed = subprocess.run(
            [config.tracker_command, config.task_repo, label or config.label],
            capture_output=True,
            text=True,
            timeout=timeout,
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
    # Before the queue, and long before the dispatch that holds the process
    # for the length of a Run. A box card read after the Run would go stale
    # for exactly the ninety minutes the page is most worth looking at.
    #
    # Not in a dry run: a dry run reaches the tracker and nothing else, and
    # that property is worth more than a status card on a cycle that changed
    # nothing.
    if not dry_run:
        facts, box_error = observe_box(config)
        journal.append(
            conn,
            "box.observed" if facts else "box.unreachable",
            {"cycle": cycle_id, **(facts or {"error": box_error})},
        )

    try:
        queue = fetch_queue(config)
    except CycleFailed as exc:
        journal.append(conn, "cycle.failed", {"cycle": cycle_id, "error": str(exc)})
        raise

    cycle_spend = spend(conn)
    eligible, skipped = [], {}
    returned, return_failures = [], 0
    for record in queue:
        number = int(record["number"])
        verdict = eligibility(
            record, config, cycle_spend.attempts(number, record.get("labeledAt"))
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
    if control.is_paused(conn):
        # The timer keeps running while paused. It still reads the queue and
        # journals Eligibility so the page remains an explanation of what
        # would have happened; only the Dispatch is stopped.
        halted = "paused"
    elif not queue:
        halted = "queue-empty"
    elif cycle_spend.in_flight:
        halted = "run-in-flight"
    elif cycle_spend.recent_dispatches >= config.daily_cap:
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
            "area": _area(picked_record, body_sections),
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
        "in_flight": cycle_spend.in_flight,
        "dispatched_in_window": cycle_spend.recent_dispatches,
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
    summary["route"] = None
    if pick and not dry_run:
        attempt = cycle_spend.attempts(
            pick["number"], picked_record.get("labeledAt")
        ) + 1
        summary["outcome"] = _dispatch_pick(
            conn, cycle_id, config, dispatch_config, pick, attempt,
        )
        # Routing is separate from dispatching, and after it, because the two
        # answer different questions: `_dispatch_pick` records what the Run
        # did, and this decides what that means for the issue. Keeping the
        # outcome row unconditional is what stops a label swap GitHub refused
        # from erasing the Journal's record that a Run ever ran.
        summary["route"] = _route(
            conn, cycle_id, config, dispatch_config, pick,
            summary["outcome"], attempt,
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
    if summary.get("route"):
        print(f"routed       {summary['route']}")
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
            if not conn.execute(
                "SELECT pg_try_advisory_lock(%s)", (CYCLE_LOCK_KEY,)
            ).fetchone()[0]:
                # Not a failure, and deliberately exit 0: under a
                # thirty-minute timer and a ninety-minute Run this happens two
                # or three times per Run, and a timer whose OnFailure paged
                # for it would page for the Selector working.
                journal.append(conn, "cycle.skipped", {"reason": "cycle-in-progress"})
                print("cycle.py: another cycle is running; this one stood down")
                return 0
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
