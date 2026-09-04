"""The box surface: what actually crosses the SSH hop.

`box-sources/ssh.sh` is the one place a target's box checkout, repository
token and guest image become something the Run can read, and it is the place
they are easiest to lose: `ssh` forwards no environment, so a per-target value
set on the controller is simply absent on the box unless this script carries
it. The failure that produces is silent and expensive - every target's Run
using whatever token the box was last configured with.

Driven with `ssh` shimmed on PATH, so the assertions are about the command
that would have been sent rather than about a machine.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SSH_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "ssh.sh"
FACTS_SOURCE = Path(__file__).resolve().parents[1] / "box-sources" / "facts.sh"

TARGET_ENV = {
    "SELECTOR_BOX_HOST": "root@box.invalid",
    "SELECTOR_BOX_REPO": "/home/loop/gadgets",
    "LOOP_GITHUB_TOKEN_FILE": "/home/loop/.config/loop/gadgets-token",
    "LOOP_GUEST_TEMPLATE": "gadgets-python:1",
}


@pytest.fixture
def ssh(tmp_path):
    """A shimmed `ssh` that records its argv and exits 0."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    recorded = tmp_path / "ssh.args"
    shim = bin_dir / "ssh"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" > "{recorded}"\n'
    )
    shim.chmod(0o755)

    class Runner:
        def run(self, script, *argv, **env):
            environ = dict(os.environ)
            environ["PATH"] = f"{bin_dir}:{environ['PATH']}"
            for name in TARGET_ENV:
                environ.pop(name, None)
            environ.update({k: str(v) for k, v in env.items()})
            return subprocess.run(
                [str(script), *argv], capture_output=True, text=True,
                env=environ, timeout=30,
            )

        def sent(self):
            return recorded.read_text() if recorded.exists() else ""

    return Runner()


def test_the_targets_checkout_token_and_image_cross_the_hop(ssh):
    result = ssh.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
                     **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    sent = ssh.sent()
    assert "root@box.invalid" in sent
    assert "/home/loop/gadgets" in sent
    assert "export LOOP_GITHUB_TOKEN_FILE=/home/loop/.config/loop/gadgets-token" in sent
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in sent


def test_a_target_without_a_token_or_image_sends_no_empty_exports(ssh):
    """An empty export is worse than none: it would put an empty
    `LOOP_GITHUB_TOKEN_FILE` in the Run's environment and override whatever
    the box's own credential helper was configured with."""
    result = ssh.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645",
                     SELECTOR_BOX_HOST="root@box.invalid",
                     SELECTOR_BOX_REPO="/home/loop/gadgets")

    assert result.returncode == 0, result.stderr
    assert "LOOP_GITHUB_TOKEN_FILE" not in ssh.sent()
    assert "LOOP_GUEST_TEMPLATE" not in ssh.sent()


@pytest.mark.parametrize(
    "missing", ["SELECTOR_BOX_HOST", "SELECTOR_BOX_REPO"])
def test_an_unset_instance_value_refuses_by_name(ssh, missing):
    """No default, because a default here would be one company's box wired
    into everybody's copy - and a dispatch that reached a machine nobody
    configured is worse than one that did not run."""
    env = dict(TARGET_ENV)
    env[missing] = ""
    result = ssh.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **env)

    assert result.returncode != 0
    assert missing in result.stderr
    assert ssh.sent() == "", "it reached the box anyway"


def test_the_status_read_reports_the_targets_image(ssh):
    """The box card says which boundary a Run would be built inside, and with
    more than one target that is a per-target answer."""
    result = ssh.run(FACTS_SOURCE, **TARGET_ENV)

    assert result.returncode == 0, result.stderr
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in ssh.sent()


def test_the_status_read_refuses_without_a_box(ssh):
    result = ssh.run(FACTS_SOURCE, SELECTOR_BOX_HOST="")

    assert result.returncode != 0
    assert "SELECTOR_BOX_HOST" in result.stderr


def test_each_export_is_its_own_line(ssh):
    """Not merely present - present as a command.

    Command substitution strips trailing newlines, so an earlier spelling of
    this assembled the exports with `$(printf 'export ...=%q\\n')` arguments
    and produced

        export LOOP_GITHUB_TOKEN_FILE=/home/loop/tokexec /home/loop/loop/run.sh

    on one line: a dispatch that starts nothing, on every Run that has a
    token. A substring assertion passed straight through it, which is why
    this one is about the line breaks. Found by review, 2026-09-04.
    """
    ssh.run(SSH_SOURCE, "loop/645-a-thing", "acme/gadgets#645", **TARGET_ENV)

    # The remote command reaches `ssh` %q-quoted for `su -c`, so a newline
    # arrives as the two characters `\\` and `n`.
    sent = ssh.sent().replace("\\n", "\n")
    lines = [line.strip() for line in sent.split("\n")]
    assert "export LOOP_GITHUB_TOKEN_FILE=/home/loop/.config/loop/gadgets-token" in lines
    assert "export LOOP_GUEST_TEMPLATE=gadgets-python:1" in lines
    assert any(line.startswith("exec ") for line in lines), sent
