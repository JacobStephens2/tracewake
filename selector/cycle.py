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

DRY-RUN IS THE ONLY MODE THIS FILE IMPLEMENTS (issue #153). A dry-run cycle
reaches exactly one thing outside itself - the tracker command, read-only -
and writes exactly one thing - the Journal. Seeding, the Run branch, the SSH
dispatch and the issue bookkeeping are issue #154, and a cycle asked to
dispatch today refuses rather than silently doing nothing.

Everything the cycle reaches is a substitutable command (the Loop's *_COMMAND
convention, ADR 0004), which is also the seam the offline suite drives:
tests script canned queue states through SELECTOR_TRACKER_COMMAND and read the
Journal back.

Usage:

    cycle.py --dry-run

Everything it reads is environment (see README.md): the tracker command, the
repository, the label, the allowlist, the cap.

Exit codes:

    0  the cycle ran and journaled its reasoning (with or without a pick).
    1  the cycle could not run - bad arguments, or the tracker did not answer.
       Non-zero on purpose: the timer's OnFailure pages the operator, because
       a dead Selector must never look like a quiet queue (story 31).
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


def run_cycle(conn: psycopg.Connection, config: Config) -> dict:
    """One dry-run cycle. Returns the summary it journaled."""
    cycle_id = journal.append(
        conn,
        "cycle.started",
        {
            "repo": config.task_repo,
            "label": config.label,
            "allowlist": list(config.allowlist),
            "daily_cap": config.daily_cap,
            "dry_run": True,
        },
    )
    try:
        queue = fetch_queue(config)
    except CycleFailed as exc:
        journal.append(conn, "cycle.failed", {"cycle": cycle_id, "error": str(exc)})
        raise

    spend = _spend(conn)
    eligible, skipped = [], {}
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

    halted, pick = None, None
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
        record = eligible[0]
        body_sections = sections(record.get("body") or "")
        check = body_sections.get("check", "")
        pick = {
            "cycle": cycle_id,
            "number": int(record["number"]),
            "title": record.get("title"),
            "url": record.get("url"),
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
        "dry_run": True,
    }
    journal.append(conn, "cycle.finished", summary)
    return summary


def _report(summary: dict) -> None:
    print(f"considered   {summary['considered']}")
    print(f"eligible     {summary['eligible'] or 'none'}")
    for reason, count in sorted(summary["skipped"].items()):
        print(f"  skipped    {count} x {reason}")
    if summary["picked"]:
        print(f"pick         #{summary['picked']} (dry run - not dispatched)")
    else:
        print(f"pick         none ({summary['halted']})")
    print(
        f"budget       {summary['dispatched_in_window']}/{summary['daily_cap']}"
        f" dispatches in the last {CAP_WINDOW_HOURS}h"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One Selector cycle: pick the lowest eligible labeled issue."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reason and journal, dispatch nothing. Currently required.",
    )
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "cycle.py: pass --dry-run. Dispatch - Seeding, the Run branch, the\n"
            "SSH start and the issue bookkeeping - is issue #154 and is not\n"
            "built yet; a cycle that silently picked and stopped would look\n"
            "like a working Selector.",
            file=sys.stderr,
        )
        return 1

    config = Config.from_env()
    try:
        with journal.connect() as conn:
            summary = run_cycle(conn, config)
    except CycleFailed as exc:
        print(f"cycle.py: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"cycle.py: journal unavailable: {exc}", file=sys.stderr)
        return 1
    _report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
