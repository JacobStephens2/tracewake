"""--only on the mutation runner, without a full-table run.

The runner edits files in place. These tests drive it for real: a name that
is not in the table must not touch those files, and a named entry must
restore them. The full table is hours; two notices-suite entries are the
stand-in. With no `--only`, the selected set is `selector-mutations.py
--list` - proven on a two-entry table, not by walking all 223.
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


def listed_names() -> list[str]:
    listed = subprocess.check_output(
        [sys.executable, str(MUTATIONS), "--list"], text=True,
    )
    return [line.split("\t")[0] for line in listed.splitlines() if line]


# Two real notices entries, enough to run the no --only path without the
# 223-entry table. main() is the production one: --list prints name, file,
# suite; applying a name rewrites the file.
_TINY_TABLE = """\
import pathlib
import sys

NOTICES = "notices.py"
NOTICES_SUITE = "tests/test_notices.py"

MUTATIONS = {
    "a-failed-run-is-mailed-as-green": (NOTICES, NOTICES_SUITE,
        "if is_failure(ended_by, proposal):",
        "if False:",
    ),
    "stale-rows-are-mailed": (NOTICES, NOTICES_SUITE,
        "    return at is not None and _hours_between(at, now) > config.max_age_hours",
        "    return False",
    ),
}


def main(argv):
    if len(argv) == 2 and argv[1] == "--list":
        for name, (target, suite, _, _) in MUTATIONS.items():
            print(f"{name}\\t{target}\\t{suite}")
        return 0
    if len(argv) != 3:
        print("usage: selector-mutations.py (--list | <name> <file>)", file=sys.stderr)
        return 2
    name, target = argv[1], pathlib.Path(argv[2])
    _, _, old, new = MUTATIONS[name]
    text = target.read_text()
    if text.count(old) != 1:
        print(
            f"selector-mutations.py: {name} matches {text.count(old)} times in "
            f"{target.name}, expected exactly 1",
            file=sys.stderr,
        )
        return 1
    target.write_text(text.replace(old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
"""


def test_empty_only_uses_the_full_list():
    """No `--only` is the table `--list` prints, not a skip. Unknown names
    are still refused before a file is edited (see the tests above)."""
    script = SCRIPT.read_text()
    assert 'mapfile -t listed < <(python3 "${mutations}" --list)' in script
    assert 'if ((${#only_names[@]} > 0)); then' in script
    assert 'rows=("${listed[@]}")' in script
    assert 'else\n    rows=("${listed[@]}")\nfi' in script


def test_with_no_only_the_selected_set_equals_list():
    original = MUTATIONS.read_bytes()
    try:
        MUTATIONS.write_text(_TINY_TABLE)
        names = listed_names()
        assert names == [CHEAP, CHEAP2]
        done = run_check()
        assert done.returncode == 0, done.stdout + done.stderr
        for name in names:
            assert name in done.stdout
        assert f"All {len(names)} mutations caught." in done.stdout
        assert "highest-picked-first" not in done.stdout
    finally:
        MUTATIONS.write_bytes(original)
    assert listed_names()[0] == "highest-picked-first"


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
