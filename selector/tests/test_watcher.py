"""The Iteration watcher at two boundaries (issue #157).

The first half is the parser, driven with Progress Log snapshots exactly as
`run.sh` writes them: what an Iteration record is, which Run's records count,
and which half-written record does not count yet.

The second half is the real `cycle.py` dispatching a real Run against a
scripted box that WRITES A PROGRESS LOG WHILE IT RUNS, with the watch interval
collapsed from a minute to a fraction of a second. Nothing here reads Selector
internals: what is asserted is the rows that ended up in the Journal, which is
the same thing `/loop` reads.

The offline acceptance criterion is that exactly the new Iteration records are
journaled and never a duplicate, so the fake box below deliberately holds each
snapshot still for several polls: a watcher that journaled what it read rather
than what is new would leave five rows where there is one Iteration.
"""
import pathlib
import textwrap

import pytest

import watcher
from conftest import _script, events, issue

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


# --- Progress Log snapshots -------------------------------------------------
#
# Written by hand rather than captured, and kept verbatim: they are the shape
# `run.sh` appends (loop/run.sh, the Iteration record block), and the point of
# the parser is that it reads THAT rather than something this suite invented.

SEEDED = """# Progress Log

Task: acme/widgets#645 - Widen the sync window
Owning area: The nightly sync script

Seeded by seed-run.sh. No Iteration has run yet.
"""

RUN_HEADER = """
## Run started {stamp}

Task: acme/widgets#645

Termination Contract:

- Iterations per Run: 5
- Iteration wall clock: 900s
- Turns per Iteration: 100
- Run wall clock: 5400s
- Consecutive No-op Iterations that abort: 2
- Completion Promise: recorded, never terminal
- Agent command: /home/loop/loop/agents/claude.sh
- Discipline skills: /tdd for code work, /diagnosing-bugs for something \
broken or slow, /code-review before every commit

"""

ITERATION_ONE = """
### Iteration 1 - 2026-08-27T12:00:05Z

- Agent exit: 0
- Turn bound: 100
- Head: 111111111111 -> 222222222222
- Completion Promise: not recorded

"""

ITERATION_TWO = """
### Iteration 2 - 2026-08-27T12:10:05Z

- Agent exit: 124 (killed at its 900s wall clock)
- Turn bound: 100
- No-op Iteration: head unchanged at 222222222222
- Completion Promise: recorded (advisory - the Run continues)
- Uncommitted changes left in the working tree

Agent output, last 40 lines:

    something the agent said

"""


def now_stamp():
    """The box's own stamp format, for the tests that need a Run block the
    clock guard will accept."""
    return watcher._now().strftime("%Y-%m-%dT%H:%M:%SZ")


def log(stamp="2026-08-27T12:00:00Z", *iterations):
    return SEEDED + RUN_HEADER.format(stamp=stamp) + "".join(iterations)


# --- The parser -------------------------------------------------------------


def test_a_seeded_log_holds_no_iterations():
    assert watcher.iteration_records(SEEDED) == []


def test_an_iteration_record_is_read_whole():
    (record,) = watcher.iteration_records(log("2026-08-27T12:00:00Z", ITERATION_ONE))
    assert record["iteration"] == 1
    assert record["started"] == "2026-08-27T12:00:05Z"
    assert record["agent_exit"] == 0
    assert record["turn_bound"] == 100
    assert record["noop"] is False
    assert record["head_before"] == "111111111111"
    assert record["head_after"] == "222222222222"
    assert record["promise"] is False
    assert record["dirty"] is False
    assert record["run_started"] == "2026-08-27T12:00:00Z"


def test_a_cut_off_iteration_carries_what_cut_it_off():
    records = watcher.iteration_records(
        log("2026-08-27T12:00:00Z", ITERATION_ONE, ITERATION_TWO)
    )
    assert [r["iteration"] for r in records] == [1, 2]
    second = records[1]
    assert second["agent_exit"] == 124
    assert second["exit_note"] == "killed at its 900s wall clock"
    assert second["noop"] is True
    assert second["head_before"] == "222222222222"
    assert second["promise"] is True
    assert second["dirty"] is True


def test_only_the_current_runs_iterations_are_read():
    """A retry reads a log the first attempt already wrote into. Its records
    are that Run's, not this one's, and journaling them under this attempt
    would put another Run's Iterations on this card."""
    two_runs = (
        log("2026-08-27T12:00:00Z", ITERATION_ONE, ITERATION_TWO)
        + RUN_HEADER.format(stamp="2026-08-27T14:00:00Z")
        + ITERATION_ONE.replace("Iteration 1 - 2026-08-27T12:00:05Z",
                                "Iteration 1 - 2026-08-27T14:00:05Z")
    )
    records = watcher.iteration_records(two_runs)
    assert [r["started"] for r in records] == ["2026-08-27T14:00:05Z"]
    assert records[0]["run_started"] == "2026-08-27T14:00:00Z"


def test_a_heading_with_no_record_under_it_is_not_a_record():
    """The other half of "complete". A heading whose fields never arrived is
    closed when the NEXT heading starts, and without the completeness check it
    would be journaled as an Iteration with no agent exit, no head and no
    promise - a permanent row saying nothing, in a Journal that can only ever
    be added to."""
    orphaned = "\n### Iteration 1 - 2026-08-27T12:00:05Z\n\nthe agent wrote here instead\n"
    records = watcher.iteration_records(
        log("2026-08-27T12:00:00Z", orphaned, ITERATION_TWO)
    )
    assert [r["iteration"] for r in records] == [2]


def test_a_half_written_record_is_not_a_record_yet():
    """The log is read while it is being appended to. A heading with no
    `Agent exit` under it is a write in progress, and journaling it would put
    a row on the page that says nothing and can never be corrected - the
    Journal is append-only."""
    partial = log("2026-08-27T12:00:00Z", ITERATION_ONE) + \
        "\n### Iteration 2 - 2026-08-27T12:10:05Z\n"
    assert [r["iteration"] for r in watcher.iteration_records(partial)] == [1]


def test_a_run_heading_the_agent_wrote_is_not_a_block_boundary():
    """The agent writes its own headings into this file, and one that read
    `## Run started ...` without a stamp would otherwise discard every record
    already found and leave the block undatable - which `of_this_run` drops
    entirely, so the page shows no Iterations for the rest of the Run and
    nothing says why."""
    quoted = (
        log("2026-08-27T12:00:00Z", ITERATION_ONE)
        + "\n## Run started when I say so\n\nsomething the agent wrote\n"
        + ITERATION_TWO
    )
    records = watcher.iteration_records(quoted)
    assert [r["iteration"] for r in records] == [1, 2]
    assert {r["run_started"] for r in records} == {"2026-08-27T12:00:00Z"}


def test_a_run_older_than_the_watch_is_not_this_run(monkeypatch):
    """The clock guard. A retry's watcher starts while the box is still
    checking out, so the newest Run block in the log it reads is the previous
    attempt's - told apart by when it started, because the two are otherwise
    identical."""
    started = watcher._parse_stamp("2026-08-27T14:00:00Z")
    old = watcher.iteration_records(log("2026-08-27T12:00:00Z", ITERATION_ONE))
    assert watcher.of_this_run(old, started, skew=300) == []
    fresh = watcher.iteration_records(log("2026-08-27T14:00:30Z", ITERATION_ONE))
    assert len(watcher.of_this_run(fresh, started, skew=300)) == 1


def test_a_box_clock_a_little_behind_is_still_this_run():
    """Both clocks are NTP-synced, but "a little behind" must not read as
    "another Run": the cost of the guard being tight is a live Run whose
    Iterations never appear."""
    started = watcher._parse_stamp("2026-08-27T14:00:00Z")
    just_behind = watcher.iteration_records(
        log("2026-08-27T13:58:00Z", ITERATION_ONE)
    )
    assert len(watcher.of_this_run(just_behind, started, skew=300)) == 1


def test_the_agents_own_narrative_is_not_read_as_a_record():
    """A real Progress Log from the box (tests/fixtures/), trimmed. The log is
    the Run's memory and the AGENT writes most of it - its own `## Iteration
    2` headings, its own bullet lists - so a parser that took a record to run
    until the next heading would swallow the agent's prose into it and would
    not report Iteration 1 until Iteration 2 had finished, which is the one
    thing this watcher exists to do."""
    text = (FIXTURES / "box-progress-run-648.md").read_text()
    records = watcher.iteration_records(text)
    assert [r["iteration"] for r in records] == [1]
    (first,) = records
    assert first["agent_exit"] == 0
    assert first["head_before"] == "f0f4d749e966"
    assert first["head_after"] == "06d7a82a8309"
    assert first["promise"] is False
    assert first["run_started"] == "2026-08-25T16:32:48Z"
    # The agent's own bullets sit between this record and the next heading,
    # and none of them is on it.
    assert first["dirty"] is False


# --- The Termination Contract the Run is under (#162) -----------------------


def test_the_contract_is_read_from_the_run_block():
    """The bounds a Run is executing under exist only on the box, and the Run
    writes them into its log once at Run start. Reading them here is what lets
    the current-Run panel show the Contract rather than repeating whatever this
    side's own configuration happens to say (spec #151, story 25)."""
    record = watcher.contract_record(log("2026-08-27T12:00:00Z", ITERATION_ONE))
    assert record["run_started"] == "2026-08-27T12:00:00Z"
    assert "Iterations per Run: 5" in record["contract"]
    # The discipline the Iterations were told to work in, carried with the
    # bounds because it is part of the same summary (#162).
    (skills,) = [
        line for line in record["contract"]
        if line.startswith("Discipline skills:")
    ]
    assert "/tdd" in skills
    assert "/diagnosing-bugs" in skills
    assert "/code-review" in skills


def test_the_contract_ends_where_the_summary_ends():
    """The bullets under the Contract are not the only bullets in the file.
    A reader that ran to the end of the block would swallow Iteration 1's own
    fields into the Contract and put them on the panel as bounds."""
    record = watcher.contract_record(log("2026-08-27T12:00:00Z", ITERATION_ONE))
    assert not any("Agent exit" in line for line in record["contract"])


def test_a_half_written_contract_is_not_a_contract_yet():
    """Same rule as an Iteration record: the log is read while it is being
    appended to, and a Contract journaled mid-write can never be corrected."""
    partial = SEEDED + RUN_HEADER.format(stamp="2026-08-27T12:00:00Z").rstrip("\n")
    assert watcher.contract_record(partial) is None


def test_a_log_with_no_contract_block_yields_nothing():
    """A box that wrote no summary - an older `run.sh`, or the trimmed log this
    suite keeps as a fixture - leaves the panel with no Contract card rather
    than with an empty one."""
    assert watcher.contract_record(SEEDED) is None
    assert watcher.contract_record(
        (FIXTURES / "box-progress-run-648.md").read_text()
    ) is None


def test_the_contract_of_an_earlier_attempt_is_not_this_runs():
    """Told apart by when the Run started, exactly as its Iterations are: a
    retry's watcher reads the previous attempt's block while the box is still
    checking out, and that attempt ran under whatever the Contract said then."""
    started = watcher._parse_stamp("2026-08-27T14:00:00Z")
    old = watcher.contract_record(log("2026-08-27T12:00:00Z", ITERATION_ONE))
    assert watcher.of_this_run([old], started, skew=300) == []


# --- The watcher against a live dispatch ------------------------------------


@pytest.fixture
def watched_box(box, tmp_path):
    """The dispatch fixture's box, rewritten to write a Progress Log while the
    Run is in flight - one snapshot appended at a time, each held still long
    enough for several polls to read it."""
    progress = box.progress_file

    def snapshots(*chunks, hold="0.35", delay=None):
        """Each chunk is appended to the log, then held still for `hold`
        seconds - long enough for several polls at the interval these tests
        run at, which is what makes "never a duplicate" an assertion rather
        than a coincidence of timing."""
        lines = []
        if delay:
            # Nothing on the box until after the watch's first read, which is
            # how a test can say "only the final read could have seen this".
            lines.append(f"sleep {delay}")
        for index, chunk in enumerate(chunks):
            piece = tmp_path / f"chunk-{index}.txt"
            piece.write_text(chunk)
            lines.append(f'cat "{piece}" >> "{progress}"')
            lines.append(f"sleep {hold}")
        _script(tmp_path / "box.sh", textwrap.dedent(f"""
            printf 'box %s\\n' "$*" >> "{tmp_path}/commands.log"
        """) + "\n".join(lines) + f'\ncat "{tmp_path}/run-summary.txt"\n')

    progress.write_text("")
    box.snapshots = snapshots
    return box


def _run_with_a_progress_log(box, dsn, *chunks):
    box.snapshots(*chunks)
    return box.run(
        dsn,
        [issue(645)],
        SELECTOR_WATCH_INTERVAL_SECONDS="0.05",
        SELECTOR_WATCH_TIMEOUT_SECONDS="10",
    )


def test_iterations_are_journaled_while_the_run_is_in_flight(db, watched_box):
    """The offline acceptance criterion: exactly the new Iteration records,
    never a duplicate, from a log that grew three times under a watcher that
    read it far more often than that."""
    now = now_stamp()
    result = _run_with_a_progress_log(
        watched_box, db,
        SEEDED + RUN_HEADER.format(stamp=now),
        ITERATION_ONE,
        ITERATION_TWO,
    )
    assert result.returncode == 0, result.stderr
    rows = events(db, "run.iteration")
    assert [r["payload"]["iteration"] for r in rows] == [1, 2]
    first = rows[0]["payload"]
    assert first["issue"] == 645
    assert first["attempt"] == 1
    assert first["branch"] == "loop/645-the-nightly-sync-script"
    assert first["agent_exit"] == 0
    assert rows[1]["payload"]["exit_note"] == "killed at its 900s wall clock"


def test_the_last_iteration_lands_even_though_the_run_ended(db, watched_box):
    """The final read. An Iteration written in the last seconds of a Run would
    otherwise be missing from the page forever, because the watcher's next
    poll never comes."""
    now = now_stamp()
    result = _run_with_a_progress_log(
        watched_box, db,
        SEEDED + RUN_HEADER.format(stamp=now) + ITERATION_ONE + ITERATION_TWO,
    )
    assert result.returncode == 0, result.stderr
    assert [r["payload"]["iteration"] for r in events(db, "run.iteration")] == [1, 2]


def test_only_the_final_read_can_catch_a_run_s_last_iteration(db, watched_box):
    """The final read, isolated. With the interval longer than the Run, the
    first read happens before the box has written anything and no second one
    is ever due - so an Iteration that appears here at all appeared because
    the watch does one last read on its way out. Without it, the last
    Iteration of every Run would be missing from the page for good."""
    watched_box.snapshots(
        log(now_stamp(), ITERATION_ONE, ITERATION_TWO), hold="0", delay="1"
    )
    result = watched_box.run(
        db, [issue(645)],
        SELECTOR_WATCH_INTERVAL_SECONDS="30",
        SELECTOR_WATCH_TIMEOUT_SECONDS="10",
    )
    assert result.returncode == 0, result.stderr
    assert [r["payload"]["iteration"] for r in events(db, "run.iteration")] == [1, 2]


def test_a_progress_log_that_cannot_be_read_does_not_fail_the_run(db, box, tmp_path):
    """A watcher is a window, not a step of the dispatch. A box that will not
    hand over its log says so once and the Run is unaffected."""
    result = box.run(
        db, [issue(645)],
        SELECTOR_BOX_PROGRESS_COMMAND=str(
            _script(tmp_path / "no-progress.sh",
                    'printf "no such file\\n" >&2\nexit 1\n')
        ),
        SELECTOR_WATCH_INTERVAL_SECONDS="0.05",
        SELECTOR_WATCH_TIMEOUT_SECONDS="10",
    )
    assert result.returncode == 0, result.stderr
    assert events(db, "run.iteration") == []
    failures = events(db, "run.watch-failed")
    assert len(failures) == 1, "said once, not once per poll"
    assert "no such file" in failures[0]["payload"]["error"]


def test_the_watcher_reads_the_box_through_one_substitutable_command(db, watched_box):
    """ADR 0004, and the seam this suite drives: the log is read by a command
    that is handed the Run's branch, so a box reached another way is another
    script and no change here."""
    now = now_stamp()
    _run_with_a_progress_log(
        watched_box, db, SEEDED + RUN_HEADER.format(stamp=now) + ITERATION_ONE
    )
    assert "progress loop/645-the-nightly-sync-script" in watched_box.commands()


def test_a_previous_attempts_iterations_are_not_journaled_as_this_ones(
    db, watched_box
):
    """The clock guard end to end: the log the box hands over holds a Run that
    ended hours ago and nothing else, which is what a retry's watcher reads
    while the box is still checking the branch out."""
    result = _run_with_a_progress_log(
        watched_box, db, log("2026-08-27T02:00:00Z", ITERATION_ONE, ITERATION_TWO)
    )
    assert result.returncode == 0, result.stderr
    assert events(db, "run.iteration") == []


def test_the_contract_is_journaled_once_for_the_run(db, watched_box):
    """The Contract reaches the Journal as its own row, written the first poll
    that can read the whole of it - so the panel can show the terms of a Run
    before its first Iteration has ended, which is most of a Run's first
    quarter-hour. Once, from a log that was re-read many times over."""
    now = now_stamp()
    result = _run_with_a_progress_log(
        watched_box, db,
        SEEDED + RUN_HEADER.format(stamp=now),
        ITERATION_ONE,
    )
    assert result.returncode == 0, result.stderr
    rows = events(db, "run.contract")
    assert len(rows) == 1, "said once, not once per poll"
    payload = rows[0]["payload"]
    assert payload["issue"] == 645
    assert payload["attempt"] == 1
    assert any("/code-review" in line for line in payload["contract"])


def test_an_earlier_attempts_contract_is_not_journaled_as_this_ones(
    db, watched_box
):
    """The clock guard, end to end: the only Run block on the box is one that
    ended hours ago, and its terms are not this Run's."""
    result = _run_with_a_progress_log(
        watched_box, db, log("2026-08-27T02:00:00Z", ITERATION_ONE)
    )
    assert result.returncode == 0, result.stderr
    assert events(db, "run.contract") == []


def test_the_watcher_reads_the_targets_own_checkout(db, watched_box):
    """The Progress Log the watcher reads is in the target's checkout on the
    box, and `SELECTOR_BOX_REPO` lives only in that target's stanza. A watch
    built from the bare environment would run the real `progress.sh` with
    nothing telling it which checkout to read: every poll of every real Run
    journaling `run.watch-failed`, beside a dispatch that worked perfectly.
    Found by review, 2026-09-04.
    """
    _run_with_a_progress_log(
        watched_box, db, (FIXTURES / "box-progress-run-648.md").read_text())

    assert "progress-env box_repo=/nonexistent/box" in watched_box.commands()
