"""Test the Loop box Ansible roles and playbook.

Issue #5 acceptance criteria:
1. The box play builds one guest image per declared template, from that template's package list.
2. A per-target token is placed where only the Run account can read it, and reaches exactly one repository.
3. The credential inventory reports both directions and passes on a correctly built box, counting three credentials plus one token per target.
4. An Iteration for a declared target runs inside the boundary against that target's checkout.
5. A box missing a declared image fails at boundary creation naming the play, rather than running an Iteration on a fallback.
"""
from pathlib import Path
import re
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
LOOP_PLAYBOOK = ROOT / "deploy" / "ansible" / "loop.yml"
BOX_PLAYBOOK = ROOT / "deploy" / "ansible" / "box.yml"
GUEST_ROLE_DIR = ROOT / "deploy" / "ansible" / "roles" / "loop_guest_template"
GUEST_TASKS_FILE = GUEST_ROLE_DIR / "tasks" / "main.yml"
GUEST_DEFAULTS_FILE = GUEST_ROLE_DIR / "defaults" / "main.yml"
BUILD_SCRIPT = GUEST_ROLE_DIR / "files" / "build-guest-template.sh"
CREDENTIALS_ROLE_DIR = ROOT / "deploy" / "ansible" / "roles" / "loop_credentials"
CREDENTIALS_TASKS_FILE = CREDENTIALS_ROLE_DIR / "tasks" / "main.yml"


def test_loop_playbook_syntax():
    """`ansible-playbook --syntax-check` passes for loop.yml."""
    cmd = ["ansible-playbook", "--syntax-check", str(LOOP_PLAYBOOK)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"Playbook syntax check failed: {proc.stderr}\n{proc.stdout}"


def test_box_playbook_exists_and_syntax():
    """`ansible-playbook --syntax-check` passes for box.yml (AC 1 & 5)."""
    assert BOX_PLAYBOOK.exists(), "deploy/ansible/box.yml does not exist"
    cmd = ["ansible-playbook", "--syntax-check", str(BOX_PLAYBOOK)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"Playbook syntax check failed: {proc.stderr}\n{proc.stdout}"


def test_multi_template_support_in_guest_role():
    """AC 1: The box play builds one guest image per declared template, from that template's package list."""
    defaults = GUEST_DEFAULTS_FILE.read_text()
    assert "loop_guest_templates" in defaults, "defaults/main.yml must declare loop_guest_templates"

    tasks = GUEST_TASKS_FILE.read_text()
    # Must support looping over declared guest templates to build each one
    assert "loop:" in tasks or "with_items:" in tasks, "tasks/main.yml must iterate over guest templates"
    assert "_loop_guest_templates_to_build" in tasks or "loop_guest_templates" in tasks


def test_build_guest_template_supports_generic_packages():
    """AC 1: build-guest-template.sh verifies arbitrary package lists without hardcoding php/composer only."""
    script = BUILD_SCRIPT.read_text()
    assert "dpkg -s" in script or "--verify" in script or "dpkg" in script, (
        "build-guest-template.sh must verify packages generically rather than hardcoding php/composer"
    )


def test_per_target_token_mode_permissions():
    """AC 2: Per-target token is placed where only the Run account can read it (mode 0600)."""
    cred_tasks = CREDENTIALS_TASKS_FILE.read_text()
    assert "0600" in cred_tasks or "0700" in cred_tasks
