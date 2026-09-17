"""The box surface, read locally: status reads for Single-Host instances.

`box-sources/facts-local.sh` and `box-sources/progress-local.sh` are the
Single-Host siblings of `facts.sh` and `progress.sh` (ADR 0019): the same
contract - the same `LOOP_BOX_*` lines, the same Progress Log semantics -
with no SSH hop. An instance that sets `SELECTOR_BOX_HOST=local` and keeps
the ssh-based reads gets `ssh: Could not resolve hostname local` on the box
card; these are what that combination is missing.

Both read as the Run account, the same transition `box-as-loop.sh` makes for
a dispatch: the credential-expiry files live under that account's home, and a
read taken as the wrong account would silently report `not reported` for the
one fact the card headlines.
"""
from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

import pytest

BOX_SOURCES = Path(__file__).resolve().parents[1] / "box-sources"
FACTS_LOCAL = BOX_SOURCES / "facts-local.sh"
PROGRESS_LOCAL = BOX_SOURCES / "progress-local.sh"

ADAPTER = """#!/usr/bin/env bash
if [[ "${1:-}" == "--guest-template" ]]; then
    printf 'loop-test-guest:1\\n'
elif [[ "${1:-}" == "--credential-expiry" ]]; then
    printf '2030-01-02T03:04:05Z\\n'
fi
"""


@pytest.fixture
def box_home(tmp_path: Path) -> Path:
    """A Run account's home: a loop tree, an adapter, expiry files."""
    home = tmp_path / "loop-home"
    loop = home / "loop"
    agents = loop / "agents"
    agents.mkdir(parents=True)
    (loop / "run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (loop / "contract.sh").write_text("# bounds\n")
    adapter = agents / "testagent.sh"
    adapter.write_text(ADAPTER)
    adapter.chmod(0o755)
    expiry_dir = home / ".config" / "loop"
    expiry_dir.mkdir(parents=True)
    # Later than the adapter's answer: the card carries the nearest.
    (expiry_dir / "other.expiry").write_text("2031-05-06T07:08:09Z\n")
    return home


DATE_SHIM = """#!/usr/bin/env bash
# GNU `date -u -d` for machines without it: the two call shapes the local
# reads use, answered with the same strings. Production runs on Linux; this
# keeps the suite portable without touching the scripts.
expr=""
fmt="+%s"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d) expr="$2"; shift 2;;
        -u) shift;;
        *) fmt="$1"; shift;;
    esac
done
python3 - "${expr}" "${fmt}" <<'EOF'
import sys
import datetime
expr, fmt = sys.argv[1], sys.argv[2]
if expr.startswith("@"):
    moment = datetime.datetime.fromtimestamp(
        int(expr[1:]), datetime.timezone.utc)
else:
    text = expr[:-1] + "+00:00" if expr.endswith("Z") else expr
    moment = datetime.datetime.fromisoformat(text)
if fmt == "+%s":
    print(int(moment.timestamp()))
else:
    print(moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
EOF
"""


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """Shims for the GNU-isms the reads rely on, ahead of the real PATH."""
    directory = tmp_path / "bin"
    directory.mkdir()
    date = directory / "date"
    date.write_text(DATE_SHIM)
    date.chmod(0o755)
    return directory


def run(
    script: Path, *argv: str, bin_dir: Path | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    environ = dict(os.environ)
    if bin_dir is not None:
        environ["PATH"] = f"{bin_dir}{os.pathsep}{environ['PATH']}"
    environ.pop("SELECTOR_BOX_HOST", None)
    environ["SELECTOR_BOX_USER"] = getpass.getuser()
    environ.update(env)
    return subprocess.run(
        [str(script), *argv], capture_output=True, text=True,
        env=environ, timeout=30,
    )


def facts(box_home: Path) -> dict[str, str]:
    return {
        "SELECTOR_BOX_LOOP": str(box_home / "loop"),
        "SELECTOR_BOX_AGENT": "testagent",
        "HOME": str(box_home),
    }


def test_local_facts_answer_without_any_box_host(tmp_path: Path, box_home: Path, bin_dir: Path) -> None:
    """No SELECTOR_BOX_HOST, no ssh: the read is local or it is nothing."""
    result = run(FACTS_LOCAL, bin_dir=bin_dir, **facts(box_home))
    assert result.returncode == 0, result.stderr
    lines = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert lines["LOOP_BOX_AGENT"] == "testagent"
    assert lines["LOOP_BOX_GUEST_TEMPLATE"] == "loop-test-guest:1"
    assert lines["LOOP_BOX_CREDENTIAL_EXPIRES_AT"] == "2030-01-02T03:04:05Z"
    assert len(lines["LOOP_BOX_SCRIPTS_HASH"]) == 12


def test_local_facts_hash_moves_with_the_tree(tmp_path: Path, box_home: Path, bin_dir: Path) -> None:
    before = run(FACTS_LOCAL, bin_dir=bin_dir, **facts(box_home)).stdout
    (box_home / "loop" / "run.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    after = run(FACTS_LOCAL, bin_dir=bin_dir, **facts(box_home)).stdout
    assert before != after


def test_local_facts_omit_what_the_box_cannot_answer(tmp_path: Path) -> None:
    """A fact the box cannot answer is not printed, never guessed at."""
    result = run(
        FACTS_LOCAL,
        SELECTOR_BOX_LOOP=str(tmp_path / "no-such-loop"),
        SELECTOR_BOX_AGENT="no-such-agent",
        HOME=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert "LOOP_BOX_SCRIPTS_HASH" not in result.stdout
    assert "LOOP_BOX_GUEST_TEMPLATE" not in result.stdout


def test_local_facts_become_the_run_account(tmp_path: Path, box_home: Path) -> None:
    """Read as someone else, the script reaches them through sudo - the same
    transition a dispatch makes - rather than reading a stranger's files."""
    calls = tmp_path / "sudo-calls"
    sudo = tmp_path / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> \"{calls}\"\n"
        "exit 0\n"
    )
    sudo.chmod(0o755)
    environ = dict(os.environ)
    environ["PATH"] = f"{tmp_path}{os.pathsep}{environ['PATH']}"
    environ.pop("SELECTOR_BOX_HOST", None)
    environ.update({
        "SELECTOR_BOX_USER": "loop",
        "SELECTOR_BOX_LOOP": str(box_home / "loop"),
        "HOME": str(box_home),
    })
    result = subprocess.run(
        [str(FACTS_LOCAL)], capture_output=True, text=True,
        env=environ, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert getpass.getuser() != "loop", "this test needs a non-loop invoker"
    recorded = calls.read_text().split()
    assert recorded[:3] == ["-u", "loop", "-g"] or recorded[:2] == ["-u", "loop"]
    assert recorded[-2:] == [str(FACTS_LOCAL)] or recorded[-1] == str(FACTS_LOCAL)


def test_local_facts_refuse_an_account_name_that_is_not_one(
    tmp_path: Path, box_home: Path
) -> None:
    result = run(FACTS_LOCAL, SELECTOR_BOX_USER="not a user", **facts(box_home))
    assert result.returncode != 0


def test_local_progress_cats_the_run_checkout(
    tmp_path: Path, box_home: Path
) -> None:
    """The watcher's read, with no hop: the log in the target's checkout."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "PROGRESS.md").write_text("## Run started\n")
    result = run(
        PROGRESS_LOCAL, "loop/1-a-thing",
        SELECTOR_BOX_REPO=str(repo),
        SELECTOR_BOX_USER=getpass.getuser(),
        HOME=str(box_home),
    )
    assert result.returncode == 0, result.stderr
    assert "## Run started" in result.stdout


def test_local_progress_fails_loudly_without_a_log(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    result = run(
        PROGRESS_LOCAL, "loop/1-a-thing",
        SELECTOR_BOX_REPO=str(repo),
        SELECTOR_BOX_USER=getpass.getuser(),
        HOME=str(tmp_path),
    )
    assert result.returncode != 0


def test_local_progress_refuses_without_a_checkout() -> None:
    result = run(
        PROGRESS_LOCAL, "loop/1-a-thing",
        SELECTOR_BOX_REPO="",
        SELECTOR_BOX_USER=getpass.getuser(),
    )
    assert result.returncode != 0
    assert "SELECTOR_BOX_REPO" in result.stderr
