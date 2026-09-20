"""--only on the mutation runner, without a full-table run.

The runner edits files in place. These tests drive it for real: a name that
is not in the table must not touch those files, and a named entry must
restore them. The full table is hours; two notices-suite entries are the
stand-in.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

SELECTOR = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve().parent / "mutation-check.sh"
MUTATIONS = Path(__file__).resolve().parent / "selector-mutations.py"
PYTHON = SELECTOR / ".venv" / "bin" / "python"

# Notices is tens of tests and sub-second; cycle.py's suite is 1-2 minutes.
CHEAP = "a-failed-run-is-mailed-as-green"
CHEAP2 = "stale-rows-are-mailed"
CHEAP_TARGET = "notices.py"


def _path() -> str:
    path = os.environ.get("PATH", "")
    extra = Path("/opt/homebrew/opt/util-linux/bin")
    if extra.is_dir():
        return f"{extra}{os.pathsep}{path}"
    return path


def _python() -> str:
    return str(PYTHON) if PYTHON.is_file() else sys.executable


def run_check(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args, _python()],
        capture_output=True,
        text=True,
        cwd=str(SELECTOR),
        env={**os.environ, "PATH": _path()},
        timeout=timeout,
    )


def fingerprints() -> dict[Path, tuple[int, bytes]]:
    listed = subprocess.check_output(
        [sys.executable, str(MUTATIONS), "--list"], text=True,
    )
    files = sorted({
        SELECTOR / line.split("\t")[1]
        for line in listed.splitlines() if line
    })
    return {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files}


def test_unknown_name_is_refused_and_touches_nothing():
    before = fingerprints()
    done = run_check("--only", "not-a-real-entry", timeout=15)
    assert done.returncode != 0
    assert "not-a-real-entry" in done.stderr
    assert fingerprints() == before


def test_two_unknown_names_are_both_named_and_nothing_is_mutated():
    before = fingerprints()
    done = run_check(
        "--only", "not-a-real-entry", "--only", "also-fake", timeout=15,
    )
    assert done.returncode != 0
    assert "not-a-real-entry" in done.stderr
    assert "also-fake" in done.stderr
    assert fingerprints() == before


def test_unknown_name_beside_a_real_one_still_touches_nothing():
    before = fingerprints()
    done = run_check("--only", CHEAP, "--only", "not-a-real-entry", timeout=15)
    assert done.returncode != 0
    assert "not-a-real-entry" in done.stderr
    assert fingerprints() == before
    assert CHEAP not in done.stdout


def test_named_entry_runs_its_suite_and_restores_the_file():
    target = SELECTOR / CHEAP_TARGET
    before = target.read_bytes()
    done = run_check("--only", CHEAP)
    assert done.returncode == 0, done.stdout + done.stderr
    assert CHEAP in done.stdout
    assert "caught" in done.stdout
    assert "All 1 mutations caught." in done.stdout
    assert "highest-picked-first" not in done.stdout
    assert target.read_bytes() == before


def test_two_names_run_two_and_not_the_rest():
    done = run_check("--only", CHEAP, "--only", CHEAP2)
    assert done.returncode == 0, done.stdout + done.stderr
    assert CHEAP in done.stdout
    assert CHEAP2 in done.stdout
    assert "All 2 mutations caught." in done.stdout
    assert "REACHED" not in done.stderr
    assert "highest-picked-first" not in done.stdout
    assert "leak-not-tracked" not in done.stdout


def test_a_second_run_is_refused_while_the_lock_is_held():
    # The runner's flock(1) is flock(2) on this script file. Holding the same
    # lock in-process is the overlapping run, without a child that can outlive
    # the test and block the next one.
    with SCRIPT.open("r") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        done = run_check("--only", CHEAP, timeout=15)
    assert done.returncode != 0
    assert "another run is already mutating" in done.stderr
    assert "caught" not in done.stdout
