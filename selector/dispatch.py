"""Turning a picked issue into a started Run (issue #154).

The cycle decides *which* issue (cycle.py); this decides nothing at all. It
performs, in order, the five mechanical steps that used to be a seeding
session at a keyboard:

    1. put the work checkout on the Run's branch,
    2. Seed it - the Loop's own seed-run.sh, unchanged,
    3. push the branch, so the Plan reaches the box as a commit,
    4. start the Run on the box with propose-and-notify,
    5. hand back the Run's stdout summary, which today evaporates.

Every outward reach is a substitutable command (the Loop's *_COMMAND
convention, ADR 0004), which is both how a different tracker or a different
box becomes a different script rather than a change here, and how the offline
suite drives the real sequence with no box, no model and no GitHub.

Two of those steps deserve their reasoning written down.

**Seeding still happens here, off the box.** ADR 0010's enforcement is that
the box's token holds no Issues permission, so a Run cannot fetch its own
task; ADR 0014 moved the Handover to the label but did not move Seeding. The
Selector runs as the operator's identity on this VM, reads the issue, writes
the Plan, and commits it - so what the agent will read is in a diff before the
Run starts, exactly as when a human typed the command.

**The branch is prepared from the remote, not from whatever the checkout was
left on.** A dispatch that reused a stale working tree would seed a Plan on
top of somebody else's half-finished work and propose the lot. When the Run's
branch already exists on the remote - which is what a retry finds (#155) - it
is taken from there rather than recreated from the base, so a retry continues
the same branch instead of discarding the first attempt's commits. What that
branch also carries is the first attempt's Progress Log, which Seeding will
not overwrite - so it is moved aside first and kept (`keep_previous_progress`,
#301) rather than either discarded or allowed to stop the retry.

Nothing here writes to the Journal. cycle.py journals what this returns, so
that the Journal's shape is decided in one file rather than two.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import events
import targets

HERE = Path(__file__).resolve().parent
LOOP = HERE.parent / "loop"


class DispatchFailed(Exception):
    """The Run could not be started. Journaled as the outcome and paged.

    Deliberately not raised for a Run that ran and ended badly: a Run ending
    on `agent-failed` is the Termination Contract working, and reporting it
    the same way as an SSH that never connected would make the Selector's own
    health unreadable. The discriminator is whether the box reported a bound
    at all - see `start_run`.
    """


@dataclass(frozen=True)
class DispatchConfig:
    """What a dispatch needs, for one target.

    `work_repo` and `command_env` are the target's; everything else is the
    instance's. `command_env` is the overlay every outward command is run
    with - the target's box checkout, its repository token and its guest
    image (issue #3) - laid over the process environment at the call site
    rather than exported into it, so that two targets worked one after the
    other cannot inherit each other's.
    """

    work_repo: Path
    command_env: dict
    remote: str
    branch_prefix: str
    seed_command: str
    progress_log_path: str
    run_heading: str
    box_command: str
    issue_command: str
    command_timeout_seconds: int
    run_timeout_seconds: int
    checks_timeout_seconds: int
    checks_poll_seconds: int

    @classmethod
    def for_target(cls, target: targets.Target) -> "DispatchConfig":
        env = os.environ.get
        return cls(
            # The checkout Seeding happens in, declared per target: the
            # Selector seeds off the box as the operator (ADR 0010), so it
            # needs its own clone of each repository it works.
            work_repo=target.work_repo,
            command_env=target.environ(),
            remote=env("SELECTOR_WORK_REMOTE", "origin"),
            branch_prefix=env("SELECTOR_BRANCH_PREFIX", "loop/"),
            seed_command=env("SELECTOR_SEED_COMMAND", str(LOOP / "seed-run.sh")),
            # Both read from the Loop's own variables rather than Selector
            # ones, because contract.sh declares them for exactly this reason:
            # "two scripts agreeing on a literal string by both spelling it
            # out is a seam that breaks silently". The Selector is now the
            # third reader of the pair, and a Selector that spelled either out
            # would simply stop finding the Progress Log it is supposed to
            # keep - with no error, and Seeding's refusal on the other side.
            progress_log_path=env("LOOP_PROGRESS_LOG_PATH", "PROGRESS.md"),
            run_heading=env("LOOP_RUN_HEADING", "## Run started"),
            box_command=env(
                "SELECTOR_BOX_COMMAND", str(HERE / "box-sources" / "local.sh")
            ),
            issue_command=env(
                "SELECTOR_ISSUE_COMMAND", str(HERE / "issue-sources" / "github.sh")
            ),
            # Two timeouts, because the two things being waited on are
            # nothing like each other. Everything except the Run is a call
            # that should answer in seconds - a fetch, a push, a comment - and
            # giving those the Run's budget would let one wedged tracker call
            # hold a cycle open for two hours.
            command_timeout_seconds=int(
                env("SELECTOR_COMMAND_TIMEOUT_SECONDS", "300")
            ),
            # The Run's own is longer than the Termination Contract's run
            # clock (90 minutes) by enough to cover the checkout and the
            # proposal. It is a backstop for a box command that wedged, not a
            # second bound on the Run: the Run bounds itself, and a number
            # here that could fire first would be a bound nobody declared in
            # contract.sh.
            run_timeout_seconds=int(
                env("SELECTOR_DISPATCH_TIMEOUT_SECONDS", "7200")
            ),
            # How long a Proposal's checks may stay pending before the
            # Selector stops waiting (#155). CI starts when the Run pushes,
            # so at the moment the box hands back its summary the checks have
            # almost always only just been queued - reading once and calling
            # the answer final would route nearly every green Run to a human.
            # Fifteen minutes is tourbot's suite with room, and what is on the
            # other side of the bound is a comment to the operator rather than
            # a guess, so erring short is safe.
            checks_timeout_seconds=int(
                env("SELECTOR_CHECKS_TIMEOUT_SECONDS", "900")
            ),
            checks_poll_seconds=int(env("SELECTOR_CHECKS_POLL_SECONDS", "30")),
        )


# --- Reading a report -------------------------------------------------------


_REPORT_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def report_fields(text: str) -> dict[str, str]:
    """The KEY=VALUE lines the Loop's scripts report themselves with.

    seed-run.sh, run.sh and propose.sh all lead their stdout with them, which
    is the whole reason the Selector can capture an outcome from a box that
    persists none of it. Prose after the block is ignored rather than parsed.
    """
    fields = {}
    for line in text.splitlines():
        match = _REPORT_LINE.match(line.strip())
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


# The first Iteration's rendered briefing, as run.sh emits it on stdout (#80):
# the lines between these two markers. A block rather than a KEY=VALUE line
# because it is prose, not a field - and `report_fields` already ignores both
# markers, so the two wire formats share the stream without sharing a parser.
BRIEFING_BEGIN = "LOOP_BRIEFING_BEGIN"
BRIEFING_END = "LOOP_BRIEFING_END"


def extract_briefing(text: str) -> str | None:
    """The briefing block run.sh rendered at Run start, or None.

    None is an old box, not a broken one: a Run from before the briefing was
    emitted has no stored fact to journal, and refusing the dispatch for that
    would turn every box that has not been ansible-applied yet into an outage.
    """
    lines = []
    inside = False
    for line in text.splitlines():
        if line.strip() == BRIEFING_BEGIN:
            lines, inside = [], True
            continue
        if line.strip() == BRIEFING_END and inside:
            return "\n".join(lines).strip() or None
        if inside:
            lines.append(line)
    return None


def _run(argv: list[str], *, timeout: int | None = None,
         stdin: str | None = None,
         overlay: dict | None = None) -> subprocess.CompletedProcess:
    """One outward command, bounded, with the target's values overlaid.

    `overlay` is `DispatchConfig.command_env` at every call site that has a
    config; `None` where there is none to have. It is laid over a copy of
    this process's environment rather than replacing it, because the
    commands also need what the launcher put there - the credentials, the
    PATH - and a command run with only the overlay would find neither.
    """
    try:
        return subprocess.run(
            argv, input=stdin, capture_output=True, text=True, timeout=timeout,
            env=targets.overlaid(overlay),
        )
    except OSError as exc:
        raise DispatchFailed(f"could not run {argv[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DispatchFailed(f"{argv[0]} did not finish within {timeout}s") from exc


def _said(completed: subprocess.CompletedProcess) -> str:
    """Whatever a failed command told us, in one place.

    stderr first, stdout second, and a word rather than an empty string last:
    a Run nobody watched has to be diagnosable from the row it left behind,
    and "the box refused: " with nothing after it says less than "no output".
    """
    return completed.stderr.strip() or completed.stdout.strip() or "no output"


def _git(config: DispatchConfig, *args: str) -> str:
    # Timed out like everything else here: a `git fetch` of a repository this
    # size against a network that has gone away would otherwise hold the
    # dispatch open with nothing bounding it.
    completed = _run(["git", "-C", str(config.work_repo), *args],
                     timeout=config.command_timeout_seconds)
    if completed.returncode != 0:
        raise DispatchFailed(f"git {' '.join(args)} failed: {_said(completed)}")
    return completed.stdout.strip()


# --- The branch -------------------------------------------------------------


def branch_name(config: DispatchConfig, number: int, area: str) -> str:
    """`loop/645-the-nightly-sync-script`.

    The number is what makes it unambiguous and the area is what makes it
    readable in a branch list; the area is slugged rather than validated
    because it is free text the operator wrote (seed-run.sh's `--area` is
    matched against nothing, for reasons its comments give). An area that
    slugs to nothing leaves the number, which is still a usable branch.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", area.lower()).strip("-")[:48].strip("-")
    return f"{config.branch_prefix}{number}-{slug}" if slug else \
        f"{config.branch_prefix}{number}"


def remote_has_branch(config: DispatchConfig, branch: str) -> bool:
    """Does the remote already carry this Run's branch?

    Asked in two places and spelled out in neither: it is what makes a
    dispatch a continuation rather than a first attempt, and `prepare_branch`
    and `keep_previous_progress` have to give the same answer or the second
    would append this issue's record to a file the first never continued.
    Reads the remote-tracking ref, so it presumes the fetch `prepare_branch`
    does; nothing else here asks the question before that fetch.
    """
    return _run(
        ["git", "-C", str(config.work_repo), "rev-parse", "--verify",
         "--quiet", f"{config.remote}/{branch}"],
        timeout=config.command_timeout_seconds,
    ).returncode == 0


def prepare_branch(config: DispatchConfig, number: int, area: str) -> str:
    """Put the work checkout on the Run's branch, and say which it is."""
    if not (config.work_repo / ".git").exists():
        raise DispatchFailed(
            f"no work checkout at {config.work_repo} - the Selector seeds off the"
            " box, as the operator, so it needs its own clone of the task"
            " repository (see README.md)"
        )
    branch = branch_name(config, number, area)
    _git(config, "fetch", "--prune", config.remote)

    if remote_has_branch(config, branch):
        # A retry (#155) continues the branch the first attempt left behind
        # rather than resetting it to the base, which would discard whatever
        # that attempt committed and propose an empty diff.
        _git(config, "checkout", "-B", branch, f"{config.remote}/{branch}")
        return branch

    base = _git(config, "symbolic-ref", "--short",
                f"refs/remotes/{config.remote}/HEAD")
    _git(config, "checkout", "-B", branch, base)
    return branch


def kept_log_path(config: DispatchConfig) -> str:
    """`PROGRESS-earlier.md` beside `PROGRESS.md`, whatever the latter is called."""
    live = Path(config.progress_log_path)
    return str(live.with_name(f"{live.stem}-earlier{live.suffix}"))


KEPT_LOG_PREAMBLE = (
    "# Earlier Progress Logs\n\nThe record of the attempts before this one on"
    " this branch, moved aside by the Selector so that re-seeding does not"
    " discard it (#301). The live log is {live}.\n"
)


def keep_previous_progress(config: DispatchConfig, branch: str) -> str | None:
    """Move an earlier attempt's Progress Log aside. Returns where, or None.

    The gap #301 was: `prepare_branch` continues the branch a previous attempt
    left behind, that attempt's Run committed a Progress Log to it, and
    `seed-run.sh` refuses to overwrite one (exit 2). Every second dispatch of
    an issue that actually ran therefore died at Seeding, which with
    MAX_ATTEMPTS = 2 made the retry unreachable and spent the whole budget on
    dispatches that started nothing.

    It is not only a retry, which is why what is looked at is the log in the
    checkout rather than the shape of the dispatch. A Run proposes its
    Progress Log along with its work, so a merged Proposal puts one on the
    BASE branch - and from then on every dispatch cut from the base meets the
    refusal too, first attempt or not. That is the state tourbot#646 was
    actually in: tourbot master carries the log of the Run merged as its #711.
    The checkout is also exactly what seed-run.sh's guard reads, so a guard
    cleared by reading anything else would be cleared by inference.

    Moved rather than discarded, and that is the whole choice. `--reseed`
    would have been one flag, but seed-run.sh's refusal is not squeamishness:
    the Progress Log is the ONLY account of what a Run did, and a retry that
    binned it would leave the reason the first attempt failed unreadable to
    the second attempt's reviewer - and to the second attempt, which reads the
    branch it is continuing. It is committed on the Run's branch, so the
    record travels with the work into the Proposal.

    **Kept per branch, not for ever.** Appending is right within one branch: a
    third attempt has to keep the second WITHOUT discarding the first. Across
    branches it would be a leak - the kept file is proposed and merged like
    any other file, so the next branch cut from the base would inherit an
    unrelated issue's kept log and append to it, and one file would grow for
    the life of the repository. So a branch the remote already has continues
    its own file, and a branch cut from the base starts one. Nothing is lost
    by that: what the base carries was merged, and a merged file's history is
    on the base branch, which is the durable place for it.

    Nothing here decides whether to re-seed: after this the Progress Log is
    simply absent, so Seeding writes a fresh one by its ordinary path and its
    refusal keeps every bit of its force for the case it was written for - a
    Run's record about to be lost.
    """
    live = config.work_repo / config.progress_log_path
    if not live.exists():
        return None
    text = live.read_text()
    # The heading seed-run.sh counts Runs by, so the two agree about what a
    # record is. A Progress Log that only records having been seeded is not a
    # record of anything: it is what Seeding is about to write again, byte for
    # byte, and moving it aside would put an empty log in the kept file and a
    # commit on the branch on every single dispatch.
    if not re.search(f"^{re.escape(config.run_heading)}", text, re.MULTILINE):
        return None

    kept = kept_log_path(config)
    target = config.work_repo / kept
    continuing = remote_has_branch(config, branch) and target.exists()
    earlier = target.read_text() if continuing else \
        KEPT_LOG_PREAMBLE.format(live=config.progress_log_path)
    target.write_text(f"{earlier.rstrip()}\n\n{text.strip()}\n")
    live.unlink()

    _git(config, "add", "--", kept, config.progress_log_path)
    if _run(["git", "-C", str(config.work_repo), "diff", "--cached", "--quiet"],
            timeout=config.command_timeout_seconds).returncode != 0:
        _git(config, "commit", "--quiet", "--message",
             f"Loop: Keep the previous attempt's Progress Log in {kept}")
    return kept


# --- Seeding ----------------------------------------------------------------


def seed(config: DispatchConfig, task_repo: str, number: int, area: str,
         check: str | None) -> dict[str, str]:
    """The Loop's own seed step, unchanged and unwrapped.

    A refusal here is a dispatch that must not continue: seed-run.sh exits 1
    for a task whose acceptance criteria it cannot read, which is the same
    judgement Eligibility already made and is worth honouring twice - it is
    the component that would have to guess what "done" means.
    """
    argv = [
        config.seed_command,
        "--repo", str(config.work_repo),
        "--task", str(number),
        "--task-repo", task_repo,
        "--area", area,
    ]
    if check:
        argv += ["--check", check]
    completed = _run(argv, timeout=config.command_timeout_seconds,
                     overlay=config.command_env)
    if completed.returncode != 0:
        raise DispatchFailed(f"Seeding refused #{number}: {_said(completed)}")
    return report_fields(completed.stdout)


def push(config: DispatchConfig, branch: str) -> None:
    """The Plan reaches the box as a commit, like everything else.

    No force, for propose.sh's reason: a push that would have to overwrite the
    remote has found a conflict, and resolving one unattended is not something
    this gets to do.
    """
    _git(config, "push", "--set-upstream", config.remote, branch)


# --- Starting the Run -------------------------------------------------------


def start_run(config: DispatchConfig, branch: str, task_ref: str) -> dict:
    """Start the Run on the box and hand back what it reported.

    Blocking on purpose. The Run's summary is the thing the box does not
    persist - `run.sh` prints it and exits - so the only moment it can be
    captured is while the dispatch is still holding the process. That is also
    why the in-flight lock is written before this is called: this call owns
    the next ninety minutes.

    A Run that ends on a bound exits non-zero, and that is the Contract
    working rather than a dispatch failure. The two are told apart by whether
    the box reported a bound at all: a summary with LOOP_RUN_ENDED_BY in it
    came from a Run, and anything else came from a box that never got as far
    as starting one.
    """
    completed = _run(
        [config.box_command, branch, task_ref],
        timeout=config.run_timeout_seconds,
        overlay=config.command_env,
    )
    fields = report_fields(completed.stdout)
    if "LOOP_RUN_ENDED_BY" not in fields:
        raise DispatchFailed(
            f"the box started no Run (exit {completed.returncode}): "
            f"{_said(completed)}"
        )
    return {
        "ended_by": fields["LOOP_RUN_ENDED_BY"],
        "exit": int(fields.get("LOOP_RUN_EXIT") or completed.returncode),
        "iterations": int(fields.get("LOOP_RUN_ITERATIONS") or 0),
        "faults": fields.get("LOOP_RUN_FAULTS") or "none",
        "proposal": fields.get("LOOP_PROPOSE_URL") or None,
        "proposed": fields.get("LOOP_RUN_PROPOSAL") or None,
        "notified": fields.get("LOOP_RUN_NOTIFIED") or None,
        # The stored fact of what the agent read (#80): rendered on the box
        # at Run start, journaled by the cycle below. None from a box that
        # predates the emission, which journals nothing rather than failing.
        "briefing": extract_briefing(completed.stdout),
    }


# --- Issue bookkeeping ------------------------------------------------------


def comment(config: DispatchConfig, task_repo: str, number: int, body: str) -> None:
    """One comment on the issue, body on stdin.

    On stdin rather than as an argument: the body is markdown with newlines
    and backticks in it, and an argument would put the whole of it in this
    VM's process listing.
    """
    completed = _run(
        [config.issue_command, task_repo, "comment", str(number)],
        timeout=config.command_timeout_seconds,
        stdin=body,
        overlay=config.command_env,
    )
    if completed.returncode != 0:
        raise DispatchFailed(
            f"could not comment on #{number}: {_said(completed)}"
        )


def relabel(config: DispatchConfig, task_repo: str, number: int, *,
            add: str, remove: str) -> None:
    completed = _run(
        [config.issue_command, task_repo, "relabel", str(number), add, remove],
        timeout=config.command_timeout_seconds,
        overlay=config.command_env,
    )
    if completed.returncode != 0:
        raise DispatchFailed(f"could not relabel #{number}: {_said(completed)}")


def checks(config: DispatchConfig, task_repo: str, proposal: str) -> dict:
    """What CI makes of one Proposal: `{"state", "failing"}`.

    `state` is `green`, `red`, `pending` or `none`, and `failing` names the
    checks that are red. `none` is its own answer rather than a flavour of
    green: "every check passed" and "no check ran" are opposite facts about
    how much a Proposal has been verified, and a reader that collapsed them
    could not tell them apart afterwards.

    The translation from whatever the tracker actually reports lives in the
    substitutable command, not here - which is what lets a different tracker
    be a different script (ADR 0004), and what lets the offline suite answer
    with a file.
    """
    completed = _run(
        [config.issue_command, task_repo, "checks", proposal],
        timeout=config.command_timeout_seconds,
        overlay=config.command_env,
    )
    if completed.returncode != 0:
        raise DispatchFailed(
            f"could not read the checks on {proposal}: {_said(completed)}"
        )
    try:
        payload = json.loads(completed.stdout)
        state = payload["state"]
        failing = list(payload.get("failing") or [])
    except (ValueError, KeyError, TypeError) as exc:
        raise DispatchFailed(
            f"the checks on {proposal} did not parse: {exc}"
        ) from exc
    if state not in events.CHECK_STATES:
        raise DispatchFailed(f"unknown check state {state!r} on {proposal}")
    return {"state": state, "failing": failing}


def settled_checks(config: DispatchConfig, task_repo: str, proposal: str) -> dict:
    """The same, waited on until CI has decided or the wait is spent.

    Polled rather than watched because the seam is a command that answers and
    exits; a command that blocked until CI finished would be a second timeout
    to reason about and would hide the waiting from the Journal.

    A wait of zero reads exactly once, which is both what the offline suite
    wants and the honest reading of "do not wait": the answer is whatever CI
    says right now.
    """
    deadline = time.monotonic() + config.checks_timeout_seconds
    while True:
        answer = checks(config, task_repo, proposal)
        if answer["state"] != "pending":
            return answer
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return answer
        time.sleep(min(config.checks_poll_seconds, remaining))


def update_branch(config: DispatchConfig, task_repo: str, proposal: str | int) -> None:
    """Bring a proposal's branch up to date with the latest changes from its base."""
    completed = _run(
        [config.issue_command, task_repo, "update-branch", str(proposal)],
        timeout=config.command_timeout_seconds,
        overlay=config.command_env,
    )
    if completed.returncode != 0:
        raise DispatchFailed(
            f"could not update branch for proposal {proposal}: {_said(completed)}"
        )

