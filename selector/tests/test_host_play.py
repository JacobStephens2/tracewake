"""The Single-Host play's public dry-run output, on a disposable Ubuntu host.

Set TRACEWAKE_ANSIBLE_TEST_CONTAINER to a prepared container (see
deploy/ansible/tests/README.md). No cloud, DNS, or agent credentials are used.
"""
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
PLAY = ROOT / "deploy/ansible/host.yml"


def test_host_play_syntax():
    result = subprocess.run(
        ["ansible-playbook", "--syntax-check", str(PLAY)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("agent", ["claude", "grok"])
def test_host_check_mode_installs_dashboard_and_selected_agent(agent, tmp_path):
    container = os.environ.get("TRACEWAKE_ANSIBLE_TEST_CONTAINER")
    if not container:
        pytest.skip("set TRACEWAKE_ANSIBLE_TEST_CONTAINER for the Ubuntu check-mode test")
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(f"""
tracewake:
  hosts:
    localhost:
      ansible_connection: local
      {"loop_agent_name: grok" if agent == "grok" else "# Use the product agent default."}
      tracewake_hostname: dashboard.example.test
      tracewake_repository: https://example.test/operator/product.git
      tracewake_revision: main
      loop_commit_author_name: Test Operator
      loop_commit_author_email: operator@example.test
      loop_signing_key_comment: test-host
      loop_target_repository: operator/target
      loop_scripts_workspace: /home/loop/workspace
""")
    subprocess.run(["sudo", "docker", "cp", str(inventory),
                    f"{container}:/tmp/inventory.yml"], check=True)
    result = subprocess.run(
        ["sudo", "docker", "exec", container,
         "ansible-playbook",
         "-i", "/tmp/inventory.yml", str(PLAY), "--check", "--diff"],
        capture_output=True, text=True, timeout=240,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "failed=0" in output
    assert "dashboard.example.test {" in output
    assert "reverse_proxy 127.0.0.1:8100" in output
    assert "flush_interval -1" in output
    assert "header_up X-Forwarded-Proto {scheme}" in output
    assert "uvicorn app:app --host 127.0.0.1 --port 8100" in output
    assert f"agent: {agent}" in output
    if agent == "grok":
        assert "x.ai:443" in output
    untouched = subprocess.run(
        ["sudo", "docker", "exec", container, "bash", "-c",
         "! getent passwd loop && ! getent passwd conductor "
         "&& test ! -e /etc/caddy/Caddyfile && test ! -e /srv/tracewake"],
        capture_output=True, text=True,
    )
    assert untouched.returncode == 0, "check mode modified the empty Host"


def test_caddyfile_can_serve_http_without_acme():
    """Issue #107: a local Host has no public DNS name, so Caddy must be
    able to listen on :80 without issuing a certificate."""
    template = ROOT / "deploy/ansible/roles/tracewake_proxy/templates/Caddyfile.j2"
    text = template.read_text()
    assert "tracewake_tls" in text, (
        "Caddyfile.j2 must branch on tracewake_tls so a local Host can "
        "serve HTTP (issue #107)."
    )
    assert "auto_https off" in text
    assert ":80 {" in text


def test_host_play_installs_the_proxy_before_the_execution_boundary():
    """Issue #107: Caddy is what makes the window reachable from outside
    the Host. If the Execution Boundary role fails on a missing sbx login,
    the Dashboard must already answer."""
    text = PLAY.read_text()
    proxy_at = text.index("tracewake_proxy")
    boundary_at = text.index("loop_execution_boundary")
    assert proxy_at < boundary_at, (
        "tracewake_proxy must run before loop_execution_boundary so a Host "
        "answers on the Dashboard before sbx sign-in (issue #107)."
    )


def test_host_play_can_leave_a_bind_mounted_checkout_alone():
    """Issue #107: a local Host bind-mounts the working tree at
    /srv/tracewake. The play must be able to skip the git clone so an
    apply does not reset the operator's branch."""
    tasks = (ROOT / "deploy/ansible/roles/tracewake_host/tasks/main.yml").read_text()
    assert "tracewake_manage_checkout" in tasks, (
        "tracewake_host must honour tracewake_manage_checkout so a "
        "bind-mounted checkout is not overwritten (issue #107)."
    )


def test_web_unit_can_reload_on_a_bind_mounted_tree():
    """Issue #107: iterating on the window is a file save, not a re-apply.
    The unit must be able to pass --reload when inventory asks."""
    unit = (ROOT / "deploy/systemd/tracewake-web.service").read_text()
    assert "tracewake_web_reload" in unit, (
        "tracewake-web.service must honour tracewake_web_reload so a local "
        "Host restarts the window when the bind-mounted tree changes "
        "(issue #107)."
    )


def test_web_unit_shortens_stop_timeout_when_reloading():
    """Issue #114: a --reload window must not sit for systemd's default
    stop timeout when a live /loop/events stream holds the worker."""
    unit = (ROOT / "deploy/systemd/tracewake-web.service").read_text()
    assert "TimeoutStopSec" in unit, (
        "tracewake-web.service must set TimeoutStopSec when reload is on "
        "(issue #114)."
    )
    reload_blocks = re.findall(
        r"{%[-\s]*if tracewake_web_reload[^%]*%}(.*?){%[-\s]*endif[-\s]*%}",
        unit,
        re.S,
    )
    assert any("TimeoutStopSec" in block for block in reload_blocks), (
        "TimeoutStopSec must be gated on tracewake_web_reload so production "
        "keeps systemd's default (issue #114)."
    )
    match = re.search(r"TimeoutStopSec\s*=\s*(\d+)", unit)
    assert match, "TimeoutStopSec must set a numeric seconds value"
    seconds = int(match.group(1))
    assert seconds <= 15, (
        f"TimeoutStopSec={seconds} is not short; a live SSE stream would "
        "hold the worker for systemd's default 90s (issue #114)."
    )


def _local_inventory(tmp_path, extra=""):
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(f"""
tracewake:
  hosts:
    localhost:
      ansible_connection: local
      tracewake_hostname: dashboard.example.test
      tracewake_repository: https://example.test/operator/product.git
      tracewake_revision: main
      loop_commit_author_name: Test Operator
      loop_commit_author_email: operator@example.test
      loop_signing_key_comment: test-host
      loop_target_repository: operator/target
      loop_scripts_workspace: /home/loop/workspace
      {extra}
""")
    return inventory


def test_host_check_mode_http_dashboard_when_tls_off(tmp_path):
    container = os.environ.get("TRACEWAKE_ANSIBLE_TEST_CONTAINER")
    if not container:
        pytest.skip("set TRACEWAKE_ANSIBLE_TEST_CONTAINER for the Ubuntu check-mode test")
    inventory = _local_inventory(tmp_path, extra="tracewake_tls: false")
    subprocess.run(["sudo", "docker", "cp", str(inventory),
                    f"{container}:/tmp/inventory-http.yml"], check=True)
    result = subprocess.run(
        ["sudo", "docker", "exec", container,
         "ansible-playbook",
         "-i", "/tmp/inventory-http.yml", str(PLAY), "--check", "--diff"],
        capture_output=True, text=True, timeout=240,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "failed=0" in output
    assert "auto_https off" in output
    assert ":80 {" in output
    assert "reverse_proxy 127.0.0.1:8100" in output
    assert "dashboard.example.test {" not in output
