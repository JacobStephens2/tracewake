"""The Iteration watcher: what a Run is doing, while it is doing it (#157).

A Run persists nothing. `run.sh` prints its summary when it ends and exits, so
until the dispatch returns - ninety minutes, at the Termination Contract's run
clock - the only record of what is happening is the Progress Log in the box's
own checkout, which no one on this side of the SSH hop can see.

This reads it. While `dispatch.start_run` is blocking, a thread asks the box
for its Progress Log about once a minute, parses the Iteration records
`run.sh` appends to it, and journals each one it has not journaled before. The
page then shows Iterations landing during a Run rather than five of them
arriving at once when it ends (spec #151, story 26).

Three properties are worth stating, because each is a way this could go wrong
unattended:

**It journals what is NEW, not what it read.** The log is a cumulative file
and every poll re-reads the whole of it, so a watcher that journaled its
reading would leave one row per poll per Iteration. What is already journaled
is read back out of the Journal at watch start and remembered in the process
after that, so a re-dispatch cannot duplicate it either.

**It reads THIS Run's records.** A retry works the branch the first attempt
left behind, so the log it reads opens with that attempt's Run block and its
Iterations. Blocks are told apart by when the Run started: anything older than
the moment this watch began - minus a tolerance for two clocks that are only
approximately equal - belongs to a Run that is over.

**It cannot fail a dispatch.** A watcher is a window. A box that will not hand
over its log is journaled once, and the Run it is watching is untouched - the
dispatch fails or succeeds on its own, and paging twice for one outage is how
an alert stops being read.

Everything it reaches is one substitutable command (ADR 0004):
`SELECTOR_BOX_PROGRESS_COMMAND`, handed the Run's branch, printing the log on
stdout. The offline suite drives that seam with a file that grows.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

import journal

HERE = Path(__file__).resolve().parent

# The two headings `run.sh` writes, from loop/contract.sh's LOOP_RUN_HEADING
# and LOOP_ITERATION_HEADING. Copied rather than sourced: the Loop is a
# different program on a different host, reached over SSH, and this side reads
# what that side's shipped defaults produce. A box that overrode them would
# report nothing here rather than something wrong, which is the safer half of
# the trade.
_RUN_HEADING = re.compile(r"^## Run started (?P<stamp>\S+)\s*$")
_ITERATION_HEADING = re.compile(
    r"^### Iteration (?P<number>\d+) - (?P<stamp>\S+)\s*$"
)

# The line `run.sh` prints above the Contract summary, from contract.sh's
# `loop_contract_summary`. Copied for the same reason the headings above are.
_CONTRACT_HEADING = re.compile(r"^Termination Contract:\s*$")

_AGENT_EXIT = re.compile(r"^- Agent exit: (?P<code>-?\d+)(?: \((?P<note>.*)\))?\s*$")
_TURN_BOUND = re.compile(r"^- Turn bound: (?P<turns>\d+)\s*$")
_HEAD = re.compile(r"^- Head: (?P<before>\S+) -> (?P<after>\S+)\s*$")
_NOOP = re.compile(r"^- No-op Iteration: head unchanged at (?P<head>\S+)\s*$")
_PROMISE = re.compile(r"^- Completion Promise: (?P<state>.*?)\s*$")
_DIRTY = "- Uncommitted changes left in the working tree"

_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_stamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --- Reading the log --------------------------------------------------------


def iteration_records(text: str) -> list[dict]:
    """Every complete Iteration record in the log's LAST Run block.

    The last block, because the log is the branch's and a branch can carry
    more than one Run - a retry's, and the attempt before it. Which of those
    is the one being watched is not this function's question; `of_this_run`
    answers that from when the block started.

    Complete, because the log is read WHILE it is being appended to. A record
    is only taken once the line after its own has arrived - `run.sh` closes
    every record with a blank line - so a block caught mid-write is left for
    the next poll. The Journal is append-only: a row put there saying half of
    an Iteration can never be corrected, only contradicted by a second row.

    The Loop's record is not the only thing in this file. The agent writes its
    own narrative into it between records, headings and bullet lists included
    (that is what the log is FOR), so a record is exactly the run of `- ` lines
    under a `### Iteration N - <stamp>` heading and ends at the first line that
    is not one. A parser that ran a record to the next heading instead would
    swallow the agent's prose into it AND would not report Iteration 1 until
    Iteration 2 had finished, which is the one thing this watcher is for.
    """
    run_started = None
    records: list[dict] = []
    current: dict | None = None
    in_fields = False

    def close(record: dict | None) -> None:
        # A record is a record once the Loop's own first line is under its
        # heading. Everything else on it is optional: a No-op has no `Head:`
        # line, and a clean Iteration has no fault output.
        if record is not None and record.get("agent_exit") is not None:
            records.append(record)

    for line in text.splitlines():
        run_heading = _RUN_HEADING.match(line)
        # A block boundary needs a stamp that parses. The agent writes its own
        # headings into this file, so the same caution the Iteration record
        # gets is owed here: a line the agent wrote that happens to read
        # `## Run started ...` would otherwise discard every record already
        # found AND leave the block undatable, which `of_this_run` then drops
        # entirely - the page silently showing no Iterations for the rest of
        # the Run, with nothing saying why. That is the exact failure this
        # watcher exists to prevent, so the boundary is only taken from a line
        # that looks like the one `run.sh` writes.
        if run_heading and _parse_stamp(run_heading.group("stamp")):
            close(current)
            current = None
            in_fields = False
            # A new Run block: everything read so far belonged to a Run that
            # has ended, and this side is only ever watching one.
            records = []
            run_started = run_heading.group("stamp")
            continue
        heading = _ITERATION_HEADING.match(line)
        if heading:
            close(current)
            in_fields = False
            current = {
                "iteration": int(heading.group("number")),
                "started": heading.group("stamp"),
                "run_started": run_started,
                "agent_exit": None,
                "exit_note": None,
                # One of the Termination Contract's five bounds, and the box
                # writes it on every record. Carried because the Run's panel
                # is supposed to show the bounds it is running under (spec
                # #151, story 25) and this is the only place they reach this
                # side while a Run is in flight.
                "turn_bound": None,
                "noop": False,
                "head_before": None,
                "head_after": None,
                "promise": False,
                "dirty": False,
            }
            continue
        if current is None:
            continue
        if not line.startswith("- "):
            # The record ends at the first line that is not one of its own.
            # Only once it has started, because `run.sh` puts a blank line
            # between the heading and the first field.
            if in_fields:
                close(current)
                current = None
                in_fields = False
            continue
        in_fields = True
        exit_line = _AGENT_EXIT.match(line)
        if exit_line:
            current["agent_exit"] = int(exit_line.group("code"))
            current["exit_note"] = exit_line.group("note")
            continue
        turn_bound = _TURN_BOUND.match(line)
        if turn_bound:
            current["turn_bound"] = int(turn_bound.group("turns"))
            continue
        head = _HEAD.match(line)
        if head:
            current["head_before"] = head.group("before")
            current["head_after"] = head.group("after")
            continue
        noop = _NOOP.match(line)
        if noop:
            current["noop"] = True
            current["head_before"] = noop.group("head")
            current["head_after"] = noop.group("head")
            continue
        promise = _PROMISE.match(line)
        if promise:
            current["promise"] = promise.group("state").startswith("recorded")
            continue
        if line.strip() == _DIRTY:
            current["dirty"] = True
    # Deliberately no `close(current)` here. A record still open at the end of
    # the text is one whose terminating line has not been written yet, which
    # is the half-written case above: it is complete on the next poll, and a
    # poll costs a minute where a wrong row costs forever.
    return records


def contract_record(text: str) -> dict | None:
    """The Termination Contract of the log's LAST Run block, or None.

    The bounds a Run executes under are the box's, not this side's: they are
    the environment `run.sh` was started with, and the only place they cross
    the SSH hop is the summary it writes into the Progress Log at Run start.
    Reading them here is what lets the current-Run panel show the Contract the
    Run is actually under (spec #151, story 25) rather than repeating this
    side's own configuration back at the operator, and it carries the
    discipline skills with the bounds because they are one summary (#162).

    The summary is written once per Run, before the first Iteration, so it
    reaches the page within a poll of the dispatch - which covers most of a
    Run's first quarter of an hour, when there is otherwise nothing to show.

    Complete or nothing, like an Iteration record: the summary is taken only
    once the line that ends it has arrived. The log is read while it is being
    appended to and the Journal is append-only, so half a Contract journaled
    here is a wrong row that can only ever be contradicted.

    It is also not the only run of `- ` lines in the block - every Iteration
    record is one too - so it ends at the first line that is not one of its
    own. A reader that ran to the end of the block instead would put Iteration
    1's agent exit on the panel as one of the Run's bounds.
    """
    run_started = None
    lines: list[str] | None = None
    complete = False
    for line in text.splitlines():
        heading = _RUN_HEADING.match(line)
        # The same guard the records get: only a heading carrying a stamp that
        # parses is a block boundary, because the agent writes headings here.
        if heading and _parse_stamp(heading.group("stamp")):
            run_started = heading.group("stamp")
            lines, complete = None, False
            continue
        if run_started is None or complete:
            continue
        if lines is None:
            if _CONTRACT_HEADING.match(line):
                lines = []
            continue
        if line.startswith("- "):
            lines.append(line[2:].strip())
        elif lines:
            complete = True
        elif line.strip():
            # Bullets that do not FOLLOW the heading are not the summary. The
            # blank line `run.sh` writes between the two is allowed through;
            # anything else means this heading is the agent quoting the words
            # rather than the Loop writing its terms, and the next run of
            # bullets in the file is Iteration 1's own record.
            lines = None
    if not (run_started and lines and complete):
        return None
    return {"run_started": run_started, "contract": lines}


def of_this_run(records: list[dict], watch_started: datetime,
                skew: float) -> list[dict]:
    """The records that belong to the Run being watched, by when it started.

    A retry's watcher starts while the box is still fetching and checking out,
    so for the first seconds the newest Run block in the log is the PREVIOUS
    attempt's - identical in every other respect to the one about to be
    written. The discriminator is time: a Run that started before this watch
    did is over.

    `skew` is how far the box's clock may sit behind this one before that
    reasoning breaks. Both are NTP-synced, so the tolerance is generous on
    purpose: a guard that is too tight loses a live Run's Iterations
    altogether, where one that is too loose can only mistake an attempt that
    ended within the last few minutes.
    """
    floor = watch_started - timedelta(seconds=skew)
    kept = []
    for record in records:
        started = _parse_stamp(record.get("run_started") or "")
        if started is None or started < floor:
            continue
        kept.append(record)
    return kept


# --- The watch --------------------------------------------------------------


@dataclass(frozen=True)
class WatchConfig:
    progress_command: str
    interval_seconds: float
    timeout_seconds: float
    clock_skew_seconds: float

    @classmethod
    def from_env(cls) -> "WatchConfig":
        env = os.environ.get
        return cls(
            progress_command=env(
                "SELECTOR_BOX_PROGRESS_COMMAND",
                str(HERE / "box-sources" / "progress.sh"),
            ),
            # A minute. The acceptance criterion is that an Iteration record
            # appears within about two minutes of the box writing it, and an
            # Iteration takes minutes - so a faster poll would be a second SSH
            # session per minute buying nothing, and a slower one would make
            # the page lag the box by more than the criterion allows.
            interval_seconds=float(env("SELECTOR_WATCH_INTERVAL_SECONDS", "60")),
            # Shorter than the interval, so a wedged read cannot queue up
            # behind the next one. It is a read of one file over a connection
            # that is already open for the Run.
            timeout_seconds=float(env("SELECTOR_WATCH_TIMEOUT_SECONDS", "30")),
            clock_skew_seconds=float(
                env("SELECTOR_WATCH_CLOCK_SKEW_SECONDS", "300")
            ),
        )


class Watcher:
    """One Run, watched. Started before the box is reached and stopped after
    it answers; the stop does one last read, because an Iteration written in
    the final seconds of a Run has no next poll coming and would otherwise be
    missing from the page forever."""

    def __init__(self, run: dict, config: WatchConfig):
        self.run = run
        self.config = config
        self.started = _now()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name="iteration-watcher", daemon=True
        )
        self._reported_failure = False
        # Set from the Journal when the watch starts (`_watch`), so a second
        # watcher for one Run cannot append a second Contract row.
        self._contract_journaled = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Bounded: a watcher that will not come back must not hold a cycle
        # open past the Run it was watching. It is a daemon thread, so a join
        # that times out costs a missing final read and nothing else - and a
        # final read that lands slowly can append its last `run.iteration`
        # after `run.outcome`. Harmless by construction: the page keys a card
        # by issue and attempt and orders Iterations by their own number, so
        # the Journal's own ordering carries no meaning here.
        self._thread.join(timeout=self.config.timeout_seconds + 5)

    # -- inside the thread --------------------------------------------------

    def _watch(self) -> None:
        try:
            conn = journal.connect()
        except psycopg.Error:
            # Nothing can be journaled, including the failure to journal. The
            # cycle's own connection is what pages if the Journal is gone.
            return
        try:
            seen = journal.iterations_seen(
                conn, self.run["issue"], self.run.get("attempt")
            )
            self._contract_journaled = journal.contract_seen(
                conn, self.run["issue"], self.run.get("attempt")
            )
            while True:
                self._poll(conn, seen)
                if self._stop.wait(self.config.interval_seconds):
                    break
            # The final read: the Run has ended, so whatever it wrote after
            # the last poll is complete and is still only on the box.
            self._poll(conn, seen)
        finally:
            conn.close()

    def _poll(self, conn: psycopg.Connection, seen: set[int]) -> None:
        text = self._read(conn)
        if text is None:
            return
        self._journal_contract(conn, text)
        for record in of_this_run(
            iteration_records(text), self.started, self.config.clock_skew_seconds
        ):
            if record["iteration"] in seen:
                continue
            seen.add(record["iteration"])
            journal.append(conn, "run.iteration", {**self.run, **record})

    def _journal_contract(self, conn: psycopg.Connection, text: str) -> None:
        """The Run's terms, once.

        The log is cumulative and every poll re-reads the whole of it, so the
        flag is what keeps ninety polls from leaving ninety identical rows -
        and it is seeded from the Journal rather than from False, so a watch
        that started over mid-Run does not add a second one either. The clock
        guard is the same one the records get: the block a retry's watcher
        reads for its first seconds is the previous attempt's, and that attempt
        ran under the Contract of its own dispatch.
        """
        if self._contract_journaled:
            return
        record = contract_record(text)
        if record is None:
            return
        if not of_this_run([record], self.started, self.config.clock_skew_seconds):
            return
        self._contract_journaled = True
        journal.append(conn, "run.contract", {**self.run, **record})

    def _read(self, conn: psycopg.Connection) -> str | None:
        try:
            done = subprocess.run(
                [self.config.progress_command, self.run["branch"]],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._failed(conn, str(exc))
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()
            return self._failed(
                conn, detail[-1] if detail else f"exit {done.returncode}"
            )
        return done.stdout

    def _failed(self, conn: psycopg.Connection, error: str) -> None:
        """Said once per Run, not once per poll.

        A box that cannot be read at minute one cannot be read at minute
        ninety either, and ninety identical rows would bury the Journal the
        page is rendered from under an outage it already reported.
        """
        if self._reported_failure:
            return None
        self._reported_failure = True
        try:
            journal.append(conn, "run.watch-failed", {**self.run, "error": error})
        except psycopg.Error:
            pass
        return None


@contextmanager
def watching(run: dict, config: WatchConfig):
    """Watch `run` for the length of the block.

    The Run itself is what the block does; this only reads. An exception in
    there stops the watch on the way out like anything else, and never becomes
    the exception the caller sees.
    """
    watcher = Watcher(run, config)
    watcher.start()
    try:
        yield watcher
    finally:
        watcher.stop()
