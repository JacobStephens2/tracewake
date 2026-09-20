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

A cycle has two modes and one body of reasoning. `--dry-run` reaches the
tracker command and the owner-wide search, both read-only, and writes exactly
one thing, the Journal. Without it the same reasoning is followed by acting on
it (issue #154): the pick is dispatched - branch, Seeding, push, the Run
started on the box - and an issue skipped for a missing section is returned to
the operator with a comment and a `needs-info` swap rather than quietly passed
over. The unenrolled-Target warning (issue #39) is a Journal row in both
modes; mail is the notifier's, and only for a live newly appearing gap.

The two modes share every line of the deciding, so a dry-run is the cycle that
would have happened and not a separate approximation of one.

Everything the cycle reaches is a substitutable command (the Loop's *_COMMAND
convention, ADR 0004), which is also the seam the offline suite drives:
tests script canned queue states through SELECTOR_TRACKER_COMMAND and read the
Journal back.

Usage:

    cycle.py [--dry-run] [--target owner/name]

What it works comes from configuration and never from a default (issue #3).
The **instance** is environment - the tracker command, the box, the Journal,
the Seeding command, the issue command; the **targets** are stanzas in
`targets.toml`, one per repository, each carrying its own labels, labeler
allowlist, checkouts, repository token, guest image, review cap and landing
mode. See README.md. INSTALL.md shows the Single-Host shape of both files.

Without `--target` every declared target is worked, each with its own
`cycle.started`/`cycle.finished` pair: a second repository is a second stanza,
not a second controller. With `SELECTOR_DRAIN_CONCURRENCY` greater than 1,
Dispatches on different Targets overlap up to that cap; within a Target they
stay serial. A required value that is absent stops everything at preflight,
naming the value, before the tracker is read.

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
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control  # noqa: E402
import dispatch  # noqa: E402
import events  # noqa: E402
import journal  # noqa: E402
import targets  # noqa: E402
import watcher  # noqa: E402
from events import (  # noqa: E402
    MAX_ATTEMPTS,
    NO_PROPOSAL,
    RUN_FAILURE_BOUNDS,
    is_failure,
    outcome_name,
)

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

# Every comment the Selector posts ends with this. One copy, because four
# comments that each carried their own would drift, and the line is a claim
# about what wrote the comment - the thing a reader is entitled to see said
# the same way every time.
SIGNATURE = (
    "\n\n*Posted by the Selector. Deterministic code, not an agent - no model"
    " wrote this and none read the issue.*"
)


# How long a dispatch with no outcome still counts as a Run in flight. The
# Termination Contract's run clock is 6 hours (loop/contract.sh,
# LOOP_RUN_TIMEOUT_SECONDS), so nothing legitimate is still running after
# this; what is, is a cycle that died between dispatching and recording the
# outcome. Without the bound that one death wedges every later cycle at
# `run-in-flight` forever, which is the failure the rolling cap window exists
# to prevent and this lock needs just as much.
IN_FLIGHT_STALE_HOURS = 8

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

# How many Dispatches one Cycle may hold at once (issue #37). Serial is the
# established behavior, so the default is 1; zero or negative is not a drain
# and is refused at preflight. Review Cap, not this number, remains the
# throughput bound (ADR 0021 as amended).
DRAIN_CONCURRENCY_VAR = "SELECTOR_DRAIN_CONCURRENCY"
DEFAULT_DRAIN_CONCURRENCY = 1

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


@dataclass(frozen=True)
class Config:
    """One cycle's configuration: the target it works, and the instance it
    works it from.

    The two halves are deliberately one object. Everything below `target` is
    the instance - the same for every repository this controller works - and
    the target carries what differs: its labels, who may hand work over on
    it, its checkouts, its token, its guest image, its review cap and what a
    finished Run does with its work. A second target is a second `Config`
    around the same instance values, which is what makes it a stanza rather
    than a second controller (issue #3).

    The target's own fields are read through properties rather than copied,
    so there is one spelling of "the review label" and a Config cannot be
    built that disagrees with the stanza it came from.
    """

    target: targets.Target
    tracker_command: str
    box_facts_command: str
    box_facts_timeout_seconds: int
    guardrail_command: str
    guardrail_timeout_seconds: int
    board_timeout_seconds: int
    guardrail_trees: tuple[targets.GuardrailTree, ...]
    drain_concurrency: int

    @property
    def task_repo(self) -> str:
        return self.target.repo

    @property
    def label(self) -> str:
        return self.target.labels.ready

    @property
    def needs_info_label(self) -> str:
        return self.target.labels.needs_info

    @property
    def review_label(self) -> str:
        return self.target.labels.review

    @property
    def human_label(self) -> str:
        return self.target.labels.human

    @property
    def allowlist(self) -> tuple[str, ...]:
        return self.target.labeler_allowlist

    @property
    def review_cap(self) -> int:
        return self.target.review_cap

    @classmethod
    def for_target(cls, target: targets.Target) -> "Config":
        env = os.environ.get
        return cls(
            target=target,
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
            guardrail_command=env(
                "SELECTOR_GUARDRAIL_COMMAND",
                str(HERE / "guardrail-sources" / "protection.sh"),
            ),
            # One `gh api` call and one walk of the deployed tree, on a cycle
            # that has work to do: the same reasoning as the box read above.
            guardrail_timeout_seconds=int(
                env("SELECTOR_GUARDRAIL_TIMEOUT_SECONDS", "30")
            ),
            # Shorter still, and for a sharper version of the same reason: the
            # queue board's tracker reads happen inside a page request. Three
            # of them against a live GitHub queue took about three seconds when
            # this was measured, so ten seconds is a tracker that is broken
            # rather than slow - and a column saying so beats a page that
            # hangs.
            board_timeout_seconds=int(
                env("SELECTOR_BOARD_TIMEOUT_SECONDS", "10")
            ),
            guardrail_trees=targets.load_guardrail_trees(),
            drain_concurrency=_drain_concurrency(),
        )


    @classmethod
    def load(cls, repo: str | None = None) -> tuple["Config", ...]:
        """Every target this cycle is to work, configured.

        The preflight (issue #3): it raises `targets.NotConfigured` naming the
        missing value, and it is called before the tracker command is run, so
        a half-configured instance stops without having read or written
        anything.

        Both halves, and the instance first. An instance value that is absent
        is not caught by the script that reads it until the cycle has already
        read the queue, seeded a branch and pushed it - `observe_box` treats a
        box it cannot read as a status failure and carries on, by design - so
        checking it here is what makes "before any tracker read" true of the
        instance and not only of the targets.
        """
        targets.Instance.from_env()
        _drain_concurrency()
        return tuple(
            cls.for_target(target)
            for target in targets.select(targets.load(), repo)
        )


def _drain_concurrency() -> int:
    """How many Dispatches this Cycle may hold at once.

    Unset defaults to 1, which is today's serial drain. Zero or negative is
    a configuration error: a cap of nothing is a paused instance, and pausing
    has its own control. Named at preflight like every other instance value.
    """
    raw = os.environ.get(DRAIN_CONCURRENCY_VAR, str(DEFAULT_DRAIN_CONCURRENCY))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise targets.NotConfigured(
            f"{DRAIN_CONCURRENCY_VAR} is {raw!r}, which is not a positive"
            " whole number. It names how many Dispatches a Cycle may hold"
            " at once."
        ) from None
    if value < 1:
        raise targets.NotConfigured(
            f"{DRAIN_CONCURRENCY_VAR} is {value}, and a concurrency of less"
            f" than 1 is not a drain: {DRAIN_CONCURRENCY_VAR} names how many"
            " Dispatches a Cycle may hold at once."
        )
    return value


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


class Spend:
    """What the Selector has already spent, read back from the Journal."""

    def __init__(
        self,
        dispatches: list[Dispatch],
        outcomes: list[tuple[str | None, int]],
    ):
        self._dispatches = dispatches
        started: dict[tuple[str | None, int], int] = {}
        for d in dispatches:
            if d.stale:
                continue
            key = (d.repo, d.issue)
            started[key] = started.get(key, 0) + 1
        ended: dict[tuple[str | None, int], int] = {}
        for repo, issue in outcomes:
            key = (repo, issue)
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
        (row[0], row[1])
        for row in conn.execute(
            "SELECT nullif(split_part(payload->>'task_ref', '#', 1), ''),"
            "       (payload->>'issue')::bigint FROM journal.events"
            " WHERE kind = %s",
            (events.RUN_OUTCOME,),
        ).fetchall()
    ]
    return Spend(dispatches, outcomes)


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
        # is journaled as a dispatch. The cycle still fails loudly.
        journal.append(conn, *events.cycle_failed(cycle=cycle_id, error=str(exc)))
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
        # The sort belongs inside the guard: a record with no number is a
        # malformed queue like any other, and it must reach the operator as a
        # journaled cycle.failed rather than as a traceback nothing recorded.
        return sorted(payload["issues"], key=lambda record: int(record["number"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise CycleFailed(f"tracker command did not return a queue: {exc}") from exc


def fetch_owner_search(owner: str, label: str) -> list[dict]:
    """Handover-labeled issues across an owner's repositories.

    One owner-wide search per Cycle (issue #39). Substitutable like the
    per-target tracker command, and deliberately *not* overlaid with a
    target's environment: a per-target token must not be the credential that
    lists other repositories.
    """
    command = os.environ.get(
        "SELECTOR_SEARCH_COMMAND",
        str(HERE / "search-sources" / "github.sh"),
    )
    try:
        completed = subprocess.run(
            [command, owner, label],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise CycleFailed(f"search command could not be run: {exc}") from exc
    if completed.returncode:
        raise CycleFailed(
            f"search command exited {completed.returncode}: "
            f"{completed.stderr.strip() or 'no output'}"
        )
    try:
        payload = json.loads(completed.stdout)
        return list(payload["issues"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CycleFailed(
            f"search command did not return a labeled-issue list: {exc}"
        ) from exc


def _handover_label(declared: tuple[targets.Target, ...]) -> str:
    """The label the owner-wide search looks for.

    Unenrolled repositories have no stanza, so they cannot declare a rename.
    When every declared Target shares one ready label, that is the Handover
    this instance uses; otherwise the product vocabulary.
    """
    labels = {target.labels.ready for target in declared}
    if len(labels) == 1:
        return labels.pop()
    return targets.DEFAULT_LABELS["ready"]


def last_live_unenrolled_repos(conn: psycopg.Connection) -> set[str]:
    """Repos in the most recent non-dry-run unenrolled warning.

    Deduplication is keyed off the Journal (issue #39): a standing gap
    journals again with `new=[]`, and a dry-run must not eat the live
    notification.
    """
    row = conn.execute(
        "SELECT payload FROM journal.events"
        " WHERE kind = %s"
        "   AND COALESCE(payload->>'dry_run', 'false') NOT IN ('true', 'True')"
        " ORDER BY id DESC LIMIT 1",
        (events.TARGET_UNENROLLED,),
    ).fetchone()
    if not row:
        return set()
    payload = row[0] or {}
    repos = payload.get("repos") or []
    return {
        str(entry["repo"])
        for entry in repos
        if isinstance(entry, dict) and entry.get("repo")
    }


def group_unenrolled(issues: list[dict], declared: set[str]) -> list[dict]:
    """The gap: labeled issues whose repository is not a declared Target."""
    grouped: dict[str, list[dict]] = {}
    for record in issues:
        repo = record.get("repo")
        if not repo or repo in declared:
            continue
        grouped.setdefault(repo, []).append(
            {
                "number": record.get("number"),
                "title": record.get("title"),
                "url": record.get("url"),
            }
        )
    gap = []
    for repo in sorted(grouped):
        found = grouped[repo]
        found.sort(
            key=lambda item: int(item["number"])
            if str(item.get("number") or "").isdigit()
            else 0
        )
        gap.append({"repo": repo, "issues": found})
    return gap


def observe_unenrolled(
    conn: psycopg.Connection,
    instance: targets.Instance,
    declared: tuple[targets.Target, ...],
    *,
    dry_run: bool,
) -> None:
    """Journal the unenrolled-Target gap, or the empty set when one closed.

    Warn-only: this function reads the tracker and writes the Journal. It
    does not comment, relabel, or edit any Target configuration.
    """
    label = _handover_label(declared)
    found = fetch_owner_search(instance.search_owner, label)
    gap = group_unenrolled(found, {target.repo for target in declared})
    seen = last_live_unenrolled_repos(conn)
    if not gap:
        # Record the empty set so a later reappearance is new, rather than
        # matching the last non-empty row and staying silent. A gap that
        # was never open is not journaled.
        if not seen:
            return
        journal.append(
            conn,
            *events.target_unenrolled(
                owner=instance.search_owner,
                label=label,
                repos=[],
                new=[],
                dry_run=dry_run,
            ),
        )
        return
    new = [entry["repo"] for entry in gap if entry["repo"] not in seen]
    journal.append(
        conn,
        *events.target_unenrolled(
            owner=instance.search_owner,
            label=label,
            repos=gap,
            new=new,
            dry_run=dry_run,
        ),
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


def run_cycle(
    conn: psycopg.Connection,
    config: Config,
    dispatch_config: dispatch.DispatchConfig,
    *,
    dry_run: bool,
    slots: threading.Semaphore | None = None,
) -> dict:
    """One cycle. Returns the summary it journaled, plus what it then did.

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
    updated_proposals: set = set()
    failed_proposals: set = set()
    if not dry_run:
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

    while True:
        try:
            queue = fetch_queue(config)
            review = fetch_queue(config, config.review_label)
            budget = review_budget(config, handover=queue, review=review, raise_on_error=True)
        except CycleFailed as exc:
            journal.append(conn, *events.cycle_failed(cycle=cycle_id, error=str(exc)))
            raise

        if not dry_run:
            update_proposals_freshness(
                conn,
                cycle_id,
                config,
                dispatch_config,
                queue + (review or []),
                updated=updated_proposals,
                failed=failed_proposals,
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
            verdict = eligibility(
                record, config,
                cycle_spend.attempts(
                    number, record.get("labeledAt"), repo=config.task_repo,
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
                if reason == "missing-section" and not dry_run:
                    if _return_to_operator(
                        conn, cycle_id, config, dispatch_config, record, detail
                    ):
                        returned.append(number)
                    else:
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

        # Before each dispatch: read box facts.
        # Not in a dry run: a dry run reaches the tracker and nothing else, and
        # that property is worth more than a status card on a cycle that changed
        # nothing.
        try:
            if not dry_run:
                facts, box_error = observe_box(config)
                journal.append(
                    conn,
                    *(
                        events.box_observed(cycle=cycle_id, **facts)
                        if facts
                        else events.box_unreachable(cycle=cycle_id, error=box_error)
                    ),
                )

            if pick and not dry_run:
                attempt = cycle_spend.attempts(
                    pick["number"], picked_record.get("labeledAt"),
                    repo=config.task_repo,
                ) + 1
                outcome = _dispatch_pick(
                    conn, cycle_id, config, dispatch_config, pick, attempt,
                )
                outcomes.append(outcome)
                # Routing is separate from dispatching, and after it, because the two
                # answer different questions: `_dispatch_pick` records what the Run
                # did, and this decides what that means for the issue. Keeping the
                # outcome row unconditional is what stops a label swap GitHub refused
                # from erasing the Journal's record that a Run ever ran.
                route = _route(
                    conn, cycle_id, config, dispatch_config, pick,
                    outcome, attempt,
                )
                routes.append(route)
                dispatches.append(pick["number"])
        finally:
            if held_slot:
                slots.release()

        if dry_run or not pick:
            break

    summary = {
        "cycle": cycle_id,
        "considered": first_considered if first_considered is not None else 0,
        "eligible": last_eligible,
        "skipped": skipped,
        "picked": dispatches[0] if dispatches else (first_pick["number"] if first_pick else None),
        "dispatches": dispatches,
        "halted": halted,
        "in_flight": last_spend.in_flight if last_spend else None,
        "awaiting_review": (
            last_budget["count"]
            if last_budget and last_budget["count"] is not None
            else 0
        ),
        "review_cap": config.review_cap,
        "returned": returned,
        "dry_run": dry_run,
    }
    # One cycle summary per drain names every dispatch and the reason it stopped.
    journal.append(conn, *events.cycle_finished(**summary))

    summary["return_failures"] = return_failures
    summary["outcome"] = outcomes[-1] if outcomes else None
    summary["route"] = routes[-1] if routes else None
    summary["updated_proposals"] = sorted(str(p) for p in updated_proposals)
    return summary


def _run_targets(
    conn: psycopg.Connection,
    configs: tuple[Config, ...],
    *,
    dry_run: bool,
) -> tuple[list[dict | None], CycleFailed | None]:
    """Work every target. Sequential when K is 1; concurrent otherwise.

    One Cycle per target, each with its own `cycle.started` / `cycle.finished`
    pair. Parallelism is K concurrent Dispatches across those Cycles, still
    serial within a Target (issue #37). A target that fails does not cancel
    in-flight Runs on the others; the first failure is returned after they
    finish so the caller can page.
    """
    if not configs:
        return [], None
    concurrency = configs[0].drain_concurrency
    parallel = (not dry_run) and concurrency > 1 and len(configs) > 1
    if not parallel:
        # One cycle per target, each with its own `cycle.started` and
        # `cycle.finished` pair. A second target is a second stanza and a
        # second pass here - not a second controller, a second timer or a
        # second Journal (issue #3).
        #
        # A target that fails ends the whole invocation, and the targets
        # after it are not worked. Deliberately: a cycle that failed exits
        # non-zero and the unit's OnFailure pages, and carrying on to the
        # next target would turn one page into a cycle that reports both a
        # failure and a success. The timer fires again in thirty minutes, so
        # what a failed first target costs the second is one cycle, not its
        # queue.
        return [
            run_cycle(
                conn,
                config,
                dispatch.DispatchConfig.for_target(config.target),
                dry_run=dry_run,
            )
            for config in configs
        ], None

    slots = threading.BoundedSemaphore(concurrency)
    summaries: list[dict | None] = [None] * len(configs)
    errors: list[CycleFailed] = []

    def run_one(index: int, config: Config) -> None:
        try:
            with journal.connect() as target_conn:
                summaries[index] = run_cycle(
                    target_conn,
                    config,
                    dispatch.DispatchConfig.for_target(config.target),
                    dry_run=dry_run,
                    slots=slots,
                )
        except CycleFailed as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(configs)
    ) as pool:
        futs = [
            pool.submit(run_one, i, config)
            for i, config in enumerate(configs)
        ]
        for fut in concurrent.futures.as_completed(futs):
            fut.result()
    return summaries, (errors[0] if errors else None)


def _report(summary: dict) -> None:
    print(f"considered   {summary['considered']}")
    print(f"eligible     {summary['eligible'] or 'none'}")
    for reason, count in sorted(summary["skipped"].items()):
        print(f"  skipped    {count} x {reason}")
    if summary["returned"]:
        print(f"returned     {summary['returned']} (commented, swapped to needs-info)")
    if summary.get("return_failures"):
        print(f"  FAILED     {summary['return_failures']} issue(s) could not be returned")
    if summary.get("updated_proposals"):
        print(f"proposals    {', '.join(str(p) for p in summary['updated_proposals'])} updated")
    if summary.get("dispatches"):
        print(f"dispatches   {', '.join(f'#{n}' for n in summary['dispatches'])}")
    elif summary["picked"]:
        suffix = " (dry run - not dispatched)" if summary["dry_run"] else ""
        print(f"pick         #{summary['picked']}{suffix}")
    else:
        print(f"pick         none ({summary['halted']})")
    outcome = summary.get("outcome")
    if outcome:
        print(f"branch       {outcome['branch']}")
        print(
            f"run          {outcome['ended_by']}"
            f" (exit {outcome.get('exit', '?')},"
            f" {outcome.get('iterations', '?')} iteration(s),"
            f" faults {outcome.get('faults', '?')})"
        )
        print(f"proposal     {outcome.get('proposal') or 'none'}")
    if summary.get("route"):
        print(f"routed       {summary['route']}")
    print(
        f"budget       {summary['awaiting_review']}/{summary['review_cap']}"
        f" awaiting review"
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
    parser.add_argument(
        "--target",
        metavar="OWNER/NAME",
        help="work only the target declared with this repo. Without it every"
        " target in the targets file is worked, in the order it declares"
        " them.",
    )
    args = parser.parse_args(argv)

    try:
        with journal.connect() as conn:
            if not conn.execute(
                "SELECT pg_try_advisory_lock(%s)", (CYCLE_LOCK_KEY,)
            ).fetchone()[0]:
                # Not a failure, and deliberately exit 0: under a
                # thirty-minute timer and a ninety-minute Run this happens two
                # or three times per Run, and a timer whose OnFailure paged
                # for it would page for the Selector working.
                journal.append(
                    conn, *events.cycle_skipped(reason="cycle-in-progress")
                )
                print("cycle.py: another cycle is running; this one stood down")
                return 0

            # Preflight (issue #3). Before the tracker is read and before
            # anything is dispatched: an instance missing a required value
            # stops here, with the value named, rather than part-way through
            # a cycle that has already commented on somebody's issue. It is
            # journaled as well as printed, because a cycle that refused to
            # start is exactly the silence story 31 asks to be paged for.
            try:
                configs = Config.load(args.target)
            except targets.NotConfigured as exc:
                journal.append(
                    conn, *events.cycle_failed(cycle=None, error=str(exc))
                )
                raise CycleFailed(str(exc)) from exc

            # One owner-wide search per Cycle, before any per-target drain,
            # so a Handover on an unenrolled repository cannot vanish
            # silently while the declared Targets are worked (issue #39).
            # `--target` narrows the drain, not the enrollment set.
            try:
                observe_unenrolled(
                    conn,
                    targets.Instance.from_env(),
                    targets.load(),
                    dry_run=args.dry_run,
                )
            except CycleFailed as exc:
                journal.append(
                    conn, *events.cycle_failed(cycle=None, error=str(exc))
                )
                raise

            failures = 0
            summaries, target_error = _run_targets(
                conn, configs, dry_run=args.dry_run
            )
            for config, summary in zip(configs, summaries):
                if summary is None:
                    continue
                if len(configs) > 1:
                    print(f"target       {config.task_repo}")
                _report(summary)
                failures += summary.get("return_failures") or 0
            if target_error is not None:
                raise target_error
    except CycleFailed as exc:
        print(f"cycle.py: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"cycle.py: journal unavailable: {exc}", file=sys.stderr)
        return 1
    # A Run that ended on a bound is not a Selector failure; an issue the
    # Selector could not hand back is. Story 31 asks for the Selector's own
    # failures to page, and a comment or a label swap GitHub refused means the
    # issue is still sitting in the queue with nothing on it saying why.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
