"""The box surface: what crosses the seam to start a Run.

`box-sources/ssh.sh` (across an SSH hop) and `box-sources/local.sh` (executed
directly on the controller) are siblings under ADR 0004 and ADR 0019: they share
the exact same contract, so neither can drift from what dispatch.py expects.

Both take `<branch> <task-ref>`, require `SELECTOR_BOX_REPO`, fetch origin and
check out the run branch, carry the target's repository token and guest template
without emitting empty exports, invoke `run.sh --repo <repo> --task-ref <task-ref>
--propose --notify`, and propagate the Run's stdout report and exit code.

Local dispatch adds the credential inventory gate: `loop/assert-credentials.sh`
is run as preflight, refusing dispatch and naming every violation if any
forbidden credentials (or missing required credentials) are found on the box.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dispatch  # noqa: E402
import targets  # noqa: E402

SSH_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "ssh.sh"
LOCAL_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "local.sh"
FACTS_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "facts.sh"
PROGRESS_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "progress.sh"
PROGRESS_LOCAL_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "progress-local.sh"
FACTS_LOCAL_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "facts-local.sh"

BOX_SOURCES = [SSH_SOURCE, LOCAL_SOURCE]

TARGET_ENV = {
    "SELECTOR_BOX_HOST": "root@box.invalid",
    "SELECTOR_BOX_REPO": "/home/loop/gadgets",
    "LOOP_GITHUB_TOKEN_FILE": "/home/loop/.config/loop/gadgets-token",
    "LOOP_GUEST_TEMPLATE": "gadgets-python:1",
}


@dataclass
class DispatchedRun:
    repo: str | None
    branch: str | None
    task_ref: str | None
    token_file: str | None
    guest_template: str | None
    run_args: list[str]
    git_calls: list[str]
    assert_called: bool
    ssh_called: bool
    assert_args: list[str] = field(default_factory=list)


class Runner:
    def __init__(
        self,
        bin_dir: Path,
        loop_dir: Path,
        home_dir: Path,
        ssh_args_file: Path,
        git_calls_file: Path,
        assert_calls_file: Path,
        run_calls_file: Path,
        run_env_file: Path,
        assert_shim: Path,
    ) -> None:
        self.bin_dir = bin_dir
        self.loop_dir = loop_dir
        self.home_dir = home_dir
        self.ssh_args_file = ssh_args_file
        self.git_calls_file = git_calls_file
        self.assert_calls_file = assert_calls_file
        self.run_calls_file = run_calls_file
        self.run_env_file = run_env_file
        self.assert_shim = assert_shim

    def run(self, script: Path, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
        environ = dict(os.environ)
        environ["PATH"] = f"{self.bin_dir}:{environ['PATH']}"
        environ.setdefault("SELECTOR_BOX_LOOP", str(self.loop_dir))
        environ.setdefault("SELECTOR_ASSERT_CREDENTIALS_COMMAND", str(self.assert_shim))
        environ.setdefault("SELECTOR_BOX_HOME", str(self.home_dir))
        for name in TARGET_ENV:
            environ.pop(name, None)
        environ.update({k: str(v) for k, v in env.items()})
        return subprocess.run(
            [str(script), *argv], capture_output=True, text=True,
            env=environ, timeout=30,
        )

    def sent(self) -> str:
        return self.ssh_args_file.read_text() if self.ssh_args_file.exists() else ""

    def run_started(self, source: Path) -> bool:
        if source == SSH_SOURCE:
            return bool(self.sent())
        return self.run_calls_file.exists()

    def dispatched(self, source: Path) -> DispatchedRun:
        if source == SSH_SOURCE:
            sent = self.sent().replace("\\n", "\n")
            ssh_called = bool(sent)
            token_match = re.search(r"export LOOP_GITHUB_TOKEN_FILE=([^\s\n]+)", sent)
            guest_match = re.search(r"export LOOP_GUEST_TEMPLATE=([^\s\n]+)", sent)
            repo_match = re.search(r"git -C ([^\s]+) fetch", sent)
            checkout_match = re.search(r"checkout -B ([^\s]+)", sent)
            task_match = re.search(r"--task-ref ([^\s]+)", sent)
            exec_match = re.search(r"exec ([^\'\n]+)", sent)

            token_file = token_match.group(1).strip("'\"") if token_match else None
            guest_template = guest_match.group(1).strip("'\"") if guest_match else None
            repo = repo_match.group(1).strip("'\"") if repo_match else None
            branch = checkout_match.group(1).strip("'\"") if checkout_match else None
            task_ref = task_match.group(1).strip("'\"") if task_match else None
            run_args = shlex.split(exec_match.group(1).strip())[1:] if exec_match else []
            git_calls = [line.strip().strip("'\"") for line in sent.splitlines() if "git -C " in line]
            return DispatchedRun(
                repo=repo, branch=branch, task_ref=task_ref,
                token_file=token_file, guest_template=guest_template,
                run_args=run_args, git_calls=git_calls,
                assert_called=False, ssh_called=ssh_called,
            )
        else:
            ssh_called = self.ssh_args_file.exists() and bool(self.ssh_args_file.read_text())
            assert_called = self.assert_calls_file.exists()
            git_calls = self.git_calls_file.read_text().splitlines() if self.git_calls_file.exists() else []
            run_lines = self.run_calls_file.read_text().splitlines() if self.run_calls_file.exists() else []
            run_args = shlex.split(run_lines[0]) if run_lines else []
            env_lines = self.run_env_file.read_text().splitlines() if self.run_env_file.exists() else []
            env_map = dict(line.split("=", 1) for line in env_lines if "=" in line)
            token_file = env_map.get("token") if env_map.get("token") != "<unset>" else None
            guest_template = env_map.get("guest") if env_map.get("guest") != "<unset>" else None

            repo = None
            if "--repo" in run_args:
                repo = run_args[run_args.index("--repo") + 1]
            task_ref = None
            if "--task-ref" in run_args:
                task_ref = run_args[run_args.index("--task-ref") + 1]
            branch = None
            for call in git_calls:
                if "checkout -B " in call:
                    parts = shlex.split(call)
                    if "-B" in parts:
                        branch = parts[parts.index("-B") + 1]

            assert_lines = self.assert_calls_file.read_text().splitlines() if self.assert_calls_file.exists() else []
            assert_args = shlex.split(assert_lines[0]) if assert_lines else []

            return DispatchedRun(
                repo=repo, branch=branch, task_ref=task_ref,
                token_file=token_file, guest_template=guest_template,
                run_args=run_args, git_calls=git_calls,
                assert_called=assert_called, ssh_called=ssh_called,
                assert_args=assert_args,
            )


@pytest.fixture
def box_runner(tmp_path: Path) -> Runner:
    """A runner providing shims for ssh, git, assert-credentials.sh, and run.sh."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    ssh_args_file = tmp_path / "ssh.args"
    git_calls_file = tmp_path / "git.calls"
    assert_calls_file = loop_dir / "assert-credentials.calls"
    run_calls_file = loop_dir / "run.calls"
    run_env_file = loop_dir / "run.env"

    ssh_shim = bin_dir / "ssh"
    ssh_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" > "{ssh_args_file}"\n'
        'if [[ -n "${RUN_STDOUT:-}" ]]; then\n'
        '    printf "%b" "${RUN_STDOUT}"\n'
        'fi\n'
        'exit "${RUN_EXIT_CODE:-0}"\n'
    )
    ssh_shim.chmod(0o755)

    git_shim = bin_dir / "git"
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{git_calls_file}"\n'
        "exit 0\n"
    )
    git_shim.chmod(0o755)

    assert_shim = loop_dir / "assert-credentials.sh"
    assert_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{assert_calls_file}"\n'
        'if [[ -n "${ASSERT_CREDENTIALS_FAIL:-}" ]]; then\n'
        '    printf "Violations:\\n  - database-credential: %s\\n" "${ASSERT_CREDENTIALS_FAIL}" >&2\n'
        '    exit 2\n'
        'fi\n'
        'if [[ -n "${ASSERT_CREDENTIALS_ERROR:-}" ]]; then\n'
        '    printf "Error: %s\\n" "${ASSERT_CREDENTIALS_ERROR}" >&2\n'
        '    exit "${ASSERT_CREDENTIALS_EXIT_CODE:-1}"\n'
        'fi\n'
        'exit 0\n'
    )
    assert_shim.chmod(0o755)

    run_shim = loop_dir / "run.sh"
    run_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{run_calls_file}"\n'
        'printf "token=%s\\nguest=%s\\n" "${LOOP_GITHUB_TOKEN_FILE-<unset>}" "${LOOP_GUEST_TEMPLATE-<unset>}" > "'
        f'{run_env_file}"\n'
        'if [[ -n "${RUN_STDOUT:-}" ]]; then\n'
        '    printf "%b" "${RUN_STDOUT}"\n'
        'else\n'
        '    printf "LOOP_RUN_ENDED_BY=iteration-cap\\nLOOP_RUN_EXIT=0\\n"\n'
        'fi\n'
        'exit "${RUN_EXIT_CODE:-0}"\n'
    )
    run_shim.chmod(0o755)

    return Runner(
        bin_dir=bin_dir,
        loop_dir=loop_dir,
        home_dir=home_dir,
        ssh_args_file=ssh_args_file,
        git_calls_file=git_calls_file,
        assert_calls_file=assert_calls_file,
        run_calls_file=run_calls_file,
        run_env_file=run_env_file,
        assert_shim=assert_shim,
    )


# --- Shared contract: both sources drive through the same interface ---------


def test_the_ssh_box_command_remains_in_the_tree_and_tested() -> None:
    """A remote Box is a substituted command, not a deleted one (issue #54)."""
    assert SSH_SOURCE.is_file()
    assert SSH_SOURCE in BOX_SOURCES


def test_the_product_default_box_command_is_local(monkeypatch) -> None:
    """Single-Host is the product default. ssh.sh is the substitute an
    instance opts into, not the fallback an unset instance gets."""
    monkeypatch.delenv("SELECTOR_BOX_COMMAND", raising=False)
    target = targets.Target(
        repo="acme/widgets",
        labels=targets.Labels(
            ready="ready-for-agent",
            needs_info="needs-info",
            review="awaiting-review",
            human="ready-for-human",
        ),
        labeler_allowlist=("an-operator",),
        work_repo=Path("/nonexistent/work"),
        box_repo="/box/widgets",
        token_file="/tokens/widgets",
        guest_template="widgets:1",
        landing="propose",
        review_cap=20,
    )
    config = dispatch.DispatchConfig.for_target(target)
    assert Path(config.box_command) == LOCAL_SOURCE


@pytest.mark.parametrize("source", BOX_SOURCES)
def test_the_targets_checkout_token_and_image_reach_the_run(box_runner: Runner, source: Path) -> None:
    """The contract: a target's checkout, token and guest image reach the Run
    whether dispatched across an SSH hop or directly on the controller."""
    result = box_runner.run(source, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    dispatched = box_runner.dispatched(source)
    assert dispatched.repo == "/home/loop/gadgets"
    assert dispatched.branch == "loop/645-a-thing"
    assert dispatched.task_ref == "acme/gadgets#645"
    assert dispatched.token_file == "/home/loop/.config/loop/gadgets-token"
    assert dispatched.guest_template == "gadgets-python:1"
    assert "--propose" in dispatched.run_args
    assert "--notify" in dispatched.run_args
    if source == LOCAL_SOURCE:
        assert "--token-file" in dispatched.assert_args
        assert "/home/loop/.config/loop/gadgets-token" in dispatched.assert_args


@pytest.mark.parametrize("source", BOX_SOURCES)
def test_a_target_without_a_token_or_image_sends_no_empty_values(box_runner: Runner, source: Path) -> None:
    """An empty value is worse than none: it would put an empty
    `LOOP_GITHUB_TOKEN_FILE` in the Run's environment and override whatever
    the box's own credential helper was configured with."""
    result = box_runner.run(
        source, "loop/645-a-thing", "acme/gadgets#645",
        SELECTOR_BOX_HOST="root@box.invalid",
        SELECTOR_BOX_REPO="/home/loop/gadgets",
        LOOP_GITHUB_TOKEN_FILE="",
        LOOP_GUEST_TEMPLATE="",
    )

    assert result.returncode == 0, result.stderr
    dispatched = box_runner.dispatched(source)
    assert dispatched.token_file is None
    assert dispatched.guest_template is None


@pytest.mark.parametrize("source", BOX_SOURCES)
def test_an_unset_repo_refuses_by_name(box_runner: Runner, source: Path) -> None:
    """SELECTOR_BOX_REPO has no default; missing it must fail naming the variable."""
    env = dict(TARGET_ENV)
    env["SELECTOR_BOX_REPO"] = ""
    result = box_runner.run(source, "loop/645-a-thing", "acme/gadgets#645", **env)

    assert result.returncode != 0
    assert "SELECTOR_BOX_REPO" in result.stderr
    assert not box_runner.run_started(source), "it reached the box anyway"


@pytest.mark.parametrize("source", BOX_SOURCES)
def test_the_branch_is_fetched_and_checked_out_before_run(box_runner: Runner, source: Path) -> None:
    """Both sources must fetch origin and checkout the run branch into the target repo."""
    result = box_runner.run(source, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    dispatched = box_runner.dispatched(source)
    assert any("fetch --prune" in call for call in dispatched.git_calls)
    assert any("checkout -B loop/645-a-thing" in call for call in dispatched.git_calls)


@pytest.mark.parametrize("source", BOX_SOURCES)
def test_the_run_exit_code_and_output_propagate(box_runner: Runner, source: Path) -> None:
    """The box source blocks for the duration of the Run and yields its stdout and exit code."""
    result = box_runner.run(
        source, "loop/645-a-thing", "acme/gadgets#645",
        RUN_EXIT_CODE="4",
        RUN_STDOUT="LOOP_RUN_ENDED_BY=agent-failed\nLOOP_RUN_EXIT=4\n",
        **TARGET_ENV,
    )

    assert result.returncode == 4
    assert "LOOP_RUN_ENDED_BY=agent-failed" in result.stdout
    assert "LOOP_RUN_EXIT=4" in result.stdout


# --- SSH-specific surface ---------------------------------------------------


def test_the_targets_checkout_token_and_image_cross_the_hop(box_runner: Runner) -> None:
    result = box_runner.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
                            **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    sent = box_runner.sent()
    assert "root@box.invalid" in sent
    assert "/home/loop/gadgets" in sent
    assert "export LOOP_GITHUB_TOKEN_FILE=/home/loop/.config/loop/gadgets-token" in sent
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in sent


def test_a_target_without_a_token_or_image_sends_no_empty_exports(box_runner: Runner) -> None:
    result = box_runner.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
                            SELECTOR_BOX_HOST="root@box.invalid",
                            SELECTOR_BOX_REPO="/home/loop/gadgets")

    assert result.returncode == 0, result.stderr
    assert "LOOP_GITHUB_TOKEN_FILE" not in box_runner.sent()
    assert "LOOP_GUEST_TEMPLATE" not in box_runner.sent()


@pytest.mark.parametrize(
    "missing", ["SELECTOR_BOX_HOST", "SELECTOR_BOX_REPO"])
def test_an_unset_instance_value_refuses_by_name(box_runner: Runner, missing: str) -> None:
    """No default, because a default here would be one company's box wired
    into everybody's copy - and a dispatch that reached a machine nobody
    configured is worse than one that did not run."""
    env = dict(TARGET_ENV)
    env[missing] = ""
    result = box_runner.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **env)

    assert result.returncode != 0
    assert missing in result.stderr
    assert box_runner.sent() == "", "it reached the box anyway"


def test_the_status_read_reports_the_targets_image(box_runner: Runner) -> None:
    """The box card says which boundary a Run would be built inside, and with
    more than one target that is a per-target answer."""
    result = box_runner.run(FACTS_SOURCE, **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in box_runner.sent()


def test_the_status_read_refuses_without_a_box(box_runner: Runner) -> None:
    result = box_runner.run(FACTS_SOURCE, SELECTOR_BOX_HOST="")

    assert result.returncode != 0
    assert "SELECTOR_BOX_HOST" in result.stderr


def test_each_export_is_its_own_line(box_runner: Runner) -> None:
    box_runner.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    sent = box_runner.sent().replace("\\n", "\n")
    lines = [line.strip() for line in sent.split("\n")]
    assert "export LOOP_GITHUB_TOKEN_FILE=/home/loop/.config/loop/gadgets-token" in lines
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in lines
    assert any(line.startswith("exec ") for line in lines), sent


# --- Local-specific surface (Issue #6, ADR 0019) ----------------------------


def test_local_box_has_no_ssh_hop(box_runner: Runner) -> None:
    """Local dispatch executes directly on the controller without SSH."""
    result = box_runner.run(LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    dispatched = box_runner.dispatched(LOCAL_SOURCE)
    assert not dispatched.ssh_called
    assert dispatched.assert_called


def test_local_dispatch_refuses_naming_violations_when_credential_inventory_fails(box_runner: Runner) -> None:
    """Local dispatch is refused, naming every violation, when the credential inventory reports one."""
    result = box_runner.run(
        LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
        ASSERT_CREDENTIALS_FAIL="MYSQL_PWD is set in the environment",
        **TARGET_ENV,
    )

    assert result.returncode == 2
    assert "credential inventory reported violations:" in result.stderr
    assert "MYSQL_PWD is set in the environment" in result.stderr
    assert not box_runner.run_started(LOCAL_SOURCE)


def test_local_dispatch_exits_one_when_credential_inventory_fails_to_run(box_runner: Runner) -> None:
    """When assert-credentials.sh fails to run (exit 1), dispatch exits 1, not 2 (preserving the distinction from credential violations)."""
    result = box_runner.run(
        LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
        ASSERT_CREDENTIALS_ERROR="unrecognized argument --foo",
        ASSERT_CREDENTIALS_EXIT_CODE="1",
        **TARGET_ENV,
    )

    assert result.returncode == 1
    assert "assert-credentials.sh failed to run (exit 1):" in result.stderr
    assert "unrecognized argument --foo" in result.stderr
    assert "credential inventory reported violations:" not in result.stderr
    assert not box_runner.run_started(LOCAL_SOURCE)


def test_local_dispatch_fails_when_credential_check_cannot_run(box_runner: Runner) -> None:
    """If assert-credentials.sh is missing or unrunnable, dispatch must fail rather than proceed un-gated."""
    result = box_runner.run(
        LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
        SELECTOR_ASSERT_CREDENTIALS_COMMAND="/nonexistent/assert-credentials.sh",
        **TARGET_ENV,
    )

    assert result.returncode == 1
    assert "assert-credentials.sh not executable or not found" in result.stderr
    assert not box_runner.run_started(LOCAL_SOURCE)


def test_local_dispatch_fails_when_credential_script_is_not_executable(tmp_path: Path, box_runner: Runner) -> None:
    """A regular non-executable file at assert_script must fail preflight rather than throwing permission denied."""
    non_exec = tmp_path / "not-executable-assert.sh"
    non_exec.write_text("#!/usr/bin/env bash\nexit 0\n")
    non_exec.chmod(0o644)

    result = box_runner.run(
        LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
        SELECTOR_ASSERT_CREDENTIALS_COMMAND=str(non_exec),
        **TARGET_ENV,
    )

    assert result.returncode == 1
    assert "assert-credentials.sh not executable or not found" in result.stderr
    assert not box_runner.run_started(LOCAL_SOURCE)


def test_local_box_does_not_skip_credential_inventory(box_runner: Runner) -> None:
    """Local dispatch must not skip the credential inventory check (assert-credentials.sh)."""
    result = box_runner.run(LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    dispatched = box_runner.dispatched(LOCAL_SOURCE)
    assert dispatched.assert_called, "credential inventory check was skipped"


def test_local_box_tells_the_gate_which_agent_runs(box_runner: Runner) -> None:
    """The gate's model row follows the instance's agent, not its default."""
    result = box_runner.run(
        LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
        **{**TARGET_ENV, "SELECTOR_BOX_AGENT": "grok"},
    )

    assert result.returncode == 0, result.stderr
    args = box_runner.dispatched(LOCAL_SOURCE).assert_args
    assert "--agent" in args
    assert args[args.index("--agent") + 1] == "grok"


def test_local_box_gate_defaults_to_claude(box_runner: Runner) -> None:
    """An instance naming no agent keeps the gate's historical default."""
    result = box_runner.run(LOCAL_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    args = box_runner.dispatched(LOCAL_SOURCE).assert_args
    assert "--agent" in args
    assert args[args.index("--agent") + 1] == "claude"


def test_production_credentials_cause_local_dispatch_refusal(tmp_path):
    """An operator machine holding production credentials must fail refusal.
    Driven against the real loop/assert-credentials.sh script with a production env variable.
    """
    real_assert = Path(__file__).resolve().parents[2] / "loop" / "assert-credentials.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_shim = bin_dir / "git"
    git_shim.write_text("#!/usr/bin/env bash\nexit 0\n")
    git_shim.chmod(0o755)

    home_dir = tmp_path / "home"
    home_dir.mkdir()

    environ = dict(os.environ)
    environ["PATH"] = f"{bin_dir}:{environ['PATH']}"
    environ["SELECTOR_BOX_REPO"] = "/tmp"
    environ["SELECTOR_BOX_HOME"] = str(home_dir)
    environ["SELECTOR_BOX_SBX"] = "false"
    environ["SELECTOR_ASSERT_CREDENTIALS_COMMAND"] = str(real_assert)
    environ["MYSQL_PWD"] = "super-secret-prod-password"

    proc = subprocess.run(
        [str(LOCAL_SOURCE), "loop/645-test", "acme/gadgets#645"],
        capture_output=True, text=True, env=environ, timeout=30,
    )

    assert proc.returncode == 2
    assert "database-credential" in proc.stderr
    assert "MYSQL_PWD is set in the environment" in proc.stderr


def test_local_box_drives_a_real_run_against_tmpdir(tmp_path):
    """A Run dispatches, works and proposes with no SSH hop when the box is local.
    Driven against a real git repository in tmpdir with a scripted sbx / loop scripts.
    """
    bare_repo = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

    work_repo = tmp_path / "work"
    subprocess.run(["git", "clone", str(bare_repo), str(work_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "config", "user.email", "test@example.com"], check=True)

    (work_repo / "README.md").write_text("initial\n")
    subprocess.run(["git", "-C", str(work_repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "commit", "-m", "Initial commit"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "push", "origin", "HEAD:main"], check=True)

    subprocess.run(["git", "-C", str(work_repo), "checkout", "-b", "loop/645-a-thing"], check=True)
    (work_repo / "PLAN.md").write_text("A plan\n")
    subprocess.run(["git", "-C", str(work_repo), "add", "PLAN.md"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "commit", "-m", "Loop: Seed the Run"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "push", "origin", "loop/645-a-thing"], check=True)
    subprocess.run(["git", "-C", str(work_repo), "checkout", "main"], check=True)

    box_repo = tmp_path / "box_repo"
    subprocess.run(["git", "clone", str(bare_repo), str(box_repo)], check=True, capture_output=True)

    fake_loop = tmp_path / "fake_loop"
    fake_loop.mkdir()
    fake_assert = fake_loop / "assert-credentials.sh"
    fake_assert.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_assert.chmod(0o755)

    fake_run = fake_loop / "run.sh"
    fake_run.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'repo=""\n'
        'while (($# > 0)); do\n'
        '  case "$1" in\n'
        '    --repo) repo="$2"; shift 2 ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        'printf "work done\\n" >> "${repo}/WORK.txt"\n'
        'git -C "${repo}" add WORK.txt\n'
        'git -C "${repo}" -c user.name="Loop" -c user.email="loop@example.com" commit -m "Loop: Iteration 1"\n'
        'git -C "${repo}" push origin HEAD\n'
        'printf "LOOP_RUN_ENDED_BY=iteration-cap\\nLOOP_RUN_EXIT=0\\nLOOP_RUN_ITERATIONS=1\\nLOOP_RUN_FAULTS=none\\nLOOP_RUN_PROPOSAL=https://github.com/example/pull/1\\n"\n'
        'exit 0\n'
    )
    fake_run.chmod(0o755)

    environ = dict(os.environ)
    environ["SELECTOR_BOX_REPO"] = str(box_repo)
    environ["SELECTOR_BOX_LOOP"] = str(fake_loop)
    environ["LOOP_GITHUB_TOKEN_FILE"] = "/tmp/fake-token"
    environ["LOOP_GUEST_TEMPLATE"] = "test:1"

    proc = subprocess.run(
        [str(LOCAL_SOURCE), "loop/645-a-thing", "acme/gadgets#645"],
        capture_output=True, text=True, env=environ, timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert "LOOP_RUN_ENDED_BY=iteration-cap" in proc.stdout
    assert "LOOP_RUN_EXIT=0" in proc.stdout

    pushed_tree = subprocess.run(
        ["git", "--git-dir", str(bare_repo), "ls-tree", "-r", "--name-only", "loop/645-a-thing"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "WORK.txt" in pushed_tree


# --- Local read surface: the Progress Log and the box facts without SSH -----
#
# Single-Host Mode dispatches through local.sh (no SSH hop), so the watcher
# and the status card need read commands that do not SSH either. progress.sh
# and facts.sh both open an SSH session to SELECTOR_BOX_HOST, which a
# Single-Host instance sets to the unresolvable name `local` (INSTALL.md) -
# every poll then journals `run.watch-failed` with `Could not resolve
# hostname local` while the Run beside it works perfectly.


def test_local_progress_read_prints_the_log_without_ssh(
    tmp_path: Path, box_runner: Runner
) -> None:
    """The watcher reads the box checkout's Progress Log with no SSH hop."""
    box_repo = tmp_path / "box_repo"
    box_repo.mkdir()
    (box_repo / "PROGRESS.md").write_text("## Run started 2026-09-17T04:00:00Z\n")

    result = box_runner.run(
        PROGRESS_LOCAL_SOURCE, "loop/645-a-thing",
        SELECTOR_BOX_REPO=str(box_repo),
    )

    assert result.returncode == 0, result.stderr
    assert "## Run started 2026-09-17T04:00:00Z" in result.stdout
    assert not box_runner.sent(), "it opened an SSH session anyway"


def test_local_progress_read_fails_naming_the_missing_log(
    tmp_path: Path, box_runner: Runner
) -> None:
    """A missing log is a failure naming the path, not empty output: before
    the first Iteration the file exists, so its absence means the box is not
    where this thinks it is."""
    box_repo = tmp_path / "box_repo"
    box_repo.mkdir()

    result = box_runner.run(
        PROGRESS_LOCAL_SOURCE, "loop/645-a-thing",
        SELECTOR_BOX_REPO=str(box_repo),
    )

    assert result.returncode != 0
    assert "PROGRESS.md" in result.stderr
    assert not box_runner.sent(), "it opened an SSH session anyway"


def test_local_progress_read_refuses_without_a_repo(box_runner: Runner) -> None:
    result = box_runner.run(
        PROGRESS_LOCAL_SOURCE, "loop/645-a-thing", SELECTOR_BOX_REPO=""
    )

    assert result.returncode != 0
    assert "SELECTOR_BOX_REPO" in result.stderr
    assert not box_runner.sent(), "it opened an SSH session anyway"


def test_local_progress_read_honours_a_custom_log_path(
    tmp_path: Path, box_runner: Runner
) -> None:
    box_repo = tmp_path / "box_repo"
    box_repo.mkdir()
    (box_repo / "JOURNAL.md").write_text("## Run started 2026-09-17T04:00:00Z\n")

    result = box_runner.run(
        PROGRESS_LOCAL_SOURCE, "loop/645-a-thing",
        SELECTOR_BOX_REPO=str(box_repo),
        SELECTOR_BOX_PROGRESS_PATH="JOURNAL.md",
    )

    assert result.returncode == 0, result.stderr
    assert "## Run started 2026-09-17T04:00:00Z" in result.stdout


def _write_local_adapter(loop_dir: Path, agent: str = "claude") -> None:
    """A fake agent adapter answering --guest-template and --credential-expiry."""
    agents_dir = loop_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    adapter = agents_dir / f"{agent}.sh"
    adapter.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "--guest-template" ]]; then\n'
        '    printf "%s\\n" "${LOOP_GUEST_TEMPLATE:-stock-guest:1}"\n'
        "elif [[ \"${1:-}\" == \"--credential-expiry\" ]]; then\n"
        '    printf "2030-01-01T00:00:00Z\\n"\n'
        "fi\n"
        "exit 0\n"
    )
    adapter.chmod(0o755)


def _write_local_agent(bin_dir: Path, agent: str = "claude") -> None:
    """A fake agent binary answering --version."""
    binary = bin_dir / agent
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s 9.9.9 (Test)\\n" "${0##*/}"\n'
    )
    binary.chmod(0o755)


def _write_local_sha256sum(bin_dir: Path) -> None:
    """A fake `sha256sum` (absent on macOS) printing a fixed digest, so the
    scripts-hash pipeline runs identically everywhere."""
    shim = bin_dir / "sha256sum"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if (($# == 0)); then\n'
        "  while IFS= read -r _; do :; done\n"
        "fi\n"
        'printf "abc123def456  fixture\\n"\n'
    )
    shim.chmod(0o755)


def _write_local_date(bin_dir: Path) -> None:
    """A fake GNU `date` answering the two shapes facts-local.sh uses, so the
    expiry assertions hold on machines without `date -d` (macOS)."""
    shim = bin_dir / "date"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'spec=""; fmt=""\n'
        'while (($# > 0)); do\n'
        '  case "$1" in\n'
        '    -d) spec="$2"; shift 2;;\n'
        '    +*) fmt="$1"; shift;;\n'
        '    *) shift;;\n'
        "  esac\n"
        "done\n"
        'if [[ "${spec}" == "2030-01-01T00:00:00Z" && "${fmt}" == "+%s" ]]; then\n'
        '  printf "1893456000\\n"\n'
        'elif [[ "${spec}" == "@1893456000" ]]; then\n'
        '  printf "2030-01-01T00:00:00Z\\n"\n'
        "else\n"
        "  exit 1\n"
        "fi\n"
    )
    shim.chmod(0o755)


def test_local_facts_read_reports_without_ssh(box_runner: Runner) -> None:
    """The status card reads what the box is holding with no SSH hop."""
    _write_local_adapter(box_runner.loop_dir)
    _write_local_agent(box_runner.bin_dir)
    _write_local_sha256sum(box_runner.bin_dir)
    _write_local_date(box_runner.bin_dir)

    result = box_runner.run(
        FACTS_LOCAL_SOURCE,
        SELECTOR_BOX_LOOP=str(box_runner.loop_dir),
        HOME=str(box_runner.home_dir),
    )

    assert result.returncode == 0, result.stderr
    assert "LOOP_BOX_SCRIPTS_HASH=" in result.stdout
    assert "LOOP_BOX_AGENT=claude" in result.stdout
    assert "LOOP_BOX_GUEST_TEMPLATE=stock-guest:1" in result.stdout
    assert "LOOP_BOX_AGENT_VERSION=claude 9.9.9 (Test)" in result.stdout
    assert "LOOP_BOX_CREDENTIAL_EXPIRES_AT=2030-01-01T00:00:00Z" in result.stdout
    assert not box_runner.sent(), "it opened an SSH session anyway"


def test_local_facts_read_carries_the_targets_image(box_runner: Runner) -> None:
    """The card says which boundary THIS target's Iterations would be built
    from, not the box's default."""
    _write_local_adapter(box_runner.loop_dir)
    _write_local_agent(box_runner.bin_dir)
    _write_local_sha256sum(box_runner.bin_dir)
    _write_local_date(box_runner.bin_dir)

    result = box_runner.run(
        FACTS_LOCAL_SOURCE,
        SELECTOR_BOX_LOOP=str(box_runner.loop_dir),
        LOOP_GUEST_TEMPLATE="gadgets-python:1",
        HOME=str(box_runner.home_dir),
    )

    assert result.returncode == 0, result.stderr
    assert "LOOP_BOX_GUEST_TEMPLATE=gadgets-python:1" in result.stdout


def test_local_facts_read_omits_what_it_cannot_answer(box_runner: Runner) -> None:
    """A fact the box cannot answer is omitted, not guessed: the loop copy
    under test has no adapter, and the named agent is on no PATH."""
    _write_local_sha256sum(box_runner.bin_dir)
    result = box_runner.run(
        FACTS_LOCAL_SOURCE,
        SELECTOR_BOX_LOOP=str(box_runner.loop_dir),
        SELECTOR_BOX_AGENT="no-such-agent",
        HOME=str(box_runner.home_dir),
    )

    assert result.returncode == 0, result.stderr
    assert "LOOP_BOX_SCRIPTS_HASH=" in result.stdout
    assert "LOOP_BOX_GUEST_TEMPLATE=" not in result.stdout
    assert "LOOP_BOX_AGENT_VERSION=" not in result.stdout
