"""The Single-Host play's public dry-run output, on a disposable Ubuntu host.

Set TRACEWAKE_ANSIBLE_TEST_CONTAINER to a prepared container (see
deploy/ansible/tests/README.md). No cloud, DNS, or agent credentials are used.
"""
import os
from pathlib import Path
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
