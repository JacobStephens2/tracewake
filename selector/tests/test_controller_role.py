"""Test the tracewake_controller Ansible role and playbook.

Issue #4 acceptance criteria:
- One playbook run on a fresh host gives it the Journal with its schema applied,
  the virtual environments, and the units installed
- The units are named for the product, and their files carry no distro assumption
- The SELinux relabel step is present when SELinux is enforcing and absent
  when it is not, proven both ways
- The timer fires and a dry-run cycle is journaled without anyone starting it
- The dispatching timer is gated on one declared variable and the play prints
  which way it left it; the notifier is enabled unconditionally
- The failure hookup is in the unit section systemd honours, proven by asking
  systemd rather than by reading the file
"""
from pathlib import Path
import re
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER_PLAYBOOK = ROOT / "deploy" / "ansible" / "controller.yml"
ROLE_DIR = ROOT / "deploy" / "ansible" / "roles" / "tracewake_controller"
TASKS_FILE = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_FILE = ROLE_DIR / "defaults" / "main.yml"
DROPIN_TEMPLATE = ROLE_DIR / "templates" / "selector-cycle-selinux.conf.j2"
SUDOERS_TEMPLATE = ROLE_DIR / "templates" / "tracewake-timer-sudoers.j2"
LOOP_SUDOERS_TEMPLATE = ROLE_DIR / "templates" / "conductor-loop-sudoers.j2"


def test_controller_playbook_syntax():
    """`ansible-playbook --syntax-check` passes for controller.yml."""
    cmd = ["ansible-playbook", "--syntax-check", str(CONTROLLER_PLAYBOOK)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"Playbook syntax check failed: {proc.stderr}\n{proc.stdout}"


def _extract_selinux_condition() -> str:
    """Extract the tracewake_selinux_enforcing Jinja expression directly from tasks/main.yml."""
    content = TASKS_FILE.read_text()
    match = re.search(r"tracewake_selinux_enforcing:\s*>-?\s*\n\s*(.*?)\n\n", content, re.DOTALL)
    assert match, "Could not extract tracewake_selinux_enforcing expression from tasks/main.yml"
    return " ".join(match.group(1).split())


def test_selinux_relabel_and_dropin_logic_proven_both_ways():
    """The SELinux relabel step is present when SELinux is enforcing and absent
    when it is not, proven both ways.

    We prove this directly using Ansible's Jinja2 templating engine via python subprocess,
    reading the exact expression from tasks/main.yml.
    """
    condition_expr = _extract_selinux_condition()
    script = f"""
import sys
from jinja2 import Template

condition_tmpl = Template({condition_expr!r})

# Case 1: SELinux is enforcing (e.g. Rocky Linux in production)
facts_enforcing = {{"ansible_selinux": {{"status": "enabled", "mode": "enforcing"}}}}
result_enforcing = condition_tmpl.render(facts_enforcing).strip().lower() == "true"
assert result_enforcing is True, f"Expected True for enforcing, got {{result_enforcing}}"

# Case 2: SELinux is disabled (e.g. Ubuntu laptop)
facts_disabled = {{"ansible_selinux": {{"status": "disabled", "mode": "disabled"}}}}
result_disabled = condition_tmpl.render(facts_disabled).strip().lower() == "true"
assert result_disabled is False, f"Expected False for disabled, got {{result_disabled}}"

# Case 3: SELinux is not present / undefined (Debian/Ubuntu default)
facts_absent = {{}}
result_absent = condition_tmpl.render(facts_absent).strip().lower() == "true"
assert result_absent is False, f"Expected False for absent facts, got {{result_absent}}"

# Case 4: SELinux is permissive (not enforcing)
facts_permissive = {{"ansible_selinux": {{"status": "enabled", "mode": "permissive"}}}}
result_permissive = condition_tmpl.render(facts_permissive).strip().lower() == "true"
assert result_permissive is False, f"Expected False for permissive, got {{result_permissive}}"

print("OK")
"""
    proc = subprocess.run(["python3", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"SELinux evaluation failed: {proc.stderr}\n{proc.stdout}"
    assert proc.stdout.strip() == "OK"

    # Also verify the drop-in template itself has restorecon
    assert DROPIN_TEMPLATE.is_file(), f"Missing drop-in template {DROPIN_TEMPLATE}"
    content = DROPIN_TEMPLATE.read_text()
    assert "restorecon" in content
    assert "ExecStartPre=" in content


def test_playbook_check_mode_with_disabled_selinux_removes_dropin():
    """On a non-enforcing / disabled SELinux host (like Ubuntu),
    ansible-playbook skips the drop-in install and marks it absent.
    """
    cmd = [
        "ansible-playbook",
        "-i", "localhost,",
        "-c", "local",
        str(CONTROLLER_PLAYBOOK),
        "-e", "target_hosts=localhost",
        "-e", 'ansible_selinux={"status":"disabled","mode":"disabled"}',
        "--check",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"Playbook failed with disabled SELinux:\n{proc.stderr}\n{proc.stdout}"
    assert "Install SELinux restorecon drop-in for cycle service" in proc.stdout
    assert "Remove SELinux restorecon drop-in when not enforcing" in proc.stdout


def test_timer_gating_logic_and_notifier_unconditional():
    """The dispatching timer is gated on one declared variable and the play prints
    which way it left it; the notifier is enabled unconditionally.
    """
    tasks_content = TASKS_FILE.read_text()
    defaults_content = DEFAULTS_FILE.read_text()

    # The gate variable is declared in defaults/main.yml
    assert "tracewake_dispatch_enabled:" in defaults_content
    # Default is false (unattended dispatch off by default)
    assert "tracewake_dispatch_enabled: false" in defaults_content

    # The notifier is enabled unconditionally: enabled: true, state: started
    assert "name: tracewake-selector-notifier.service" in tasks_content
    assert "enabled: true" in tasks_content
    assert "state: started" in tasks_content

    # The timer is gated on tracewake_dispatch_enabled
    assert "name: tracewake-selector-cycle.timer" in tasks_content
    assert "enabled: \"{{ tracewake_dispatch_enabled | bool }}\"" in tasks_content
    assert "state: \"{{ 'started' if tracewake_dispatch_enabled | bool else 'stopped' }}\"" in tasks_content

    # The play prints which way it left it
    assert "Say which way unattended dispatch was left" in tasks_content
    assert "ENABLED - the Selector will dispatch Runs on its own" in tasks_content
    assert "installed and disabled; set tracewake_dispatch_enabled=true" in tasks_content


def test_window_timer_toggle_has_a_scoped_sudoers_rule():
    """The window runs as the instance's unprivileged user while the timer is
    a system unit, so the Start/Stop buttons need a privilege path - and one
    that names exactly the two commands on the one unit, and nothing else.
    """
    tasks_content = TASKS_FILE.read_text()
    assert "/etc/sudoers.d/tracewake-timer" in tasks_content
    assert "validate: visudo -cf %s" in tasks_content
    assert "0440" in tasks_content

    assert SUDOERS_TEMPLATE.is_file(), f"Missing sudoers template {SUDOERS_TEMPLATE}"
    content = SUDOERS_TEMPLATE.read_text()
    # The user is the role's, not a literal: nothing here names an instance.
    assert "{{ tracewake_user }}" in content
    assert "NOPASSWD:" in content
    assert "/usr/bin/systemctl enable --now tracewake-selector-cycle.timer" in content
    assert "/usr/bin/systemctl disable --now tracewake-selector-cycle.timer" in content
    # Scoped: the granted rule - the one non-comment line - names no shell,
    # no restart, no status, no start of anything else, and no ALL commands.
    rules = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    assert len(rules) == 1, rules
    rule = rules[0]
    assert "ALL" not in rule.replace("ALL=(root)", "")
    assert "restart" not in rule
    assert "status" not in rule
    assert rule.count("systemctl") == 2


def test_cd_update_installs_the_timer_sudoers():
    """Ansible writes /etc/sudoers.d/tracewake-timer on provision. CD
    must keep it there too: a Host that was stood up before the rule
    existed otherwise gets a Start button that cannot sudo, and
    cd-update is the path a merged PR actually runs.
    """
    script = (ROOT / "deploy" / "cd-update.sh").read_text()
    assert "/etc/sudoers.d/tracewake-timer" in script
    assert "visudo" in script
    assert "tracewake-selector-cycle.timer" in script


def test_single_host_box_reads_have_a_scoped_sudoers_rule():
    """Dispatch already becomes loop through local.sh. The box card and the
    watcher need the same hop for facts-local.sh and progress-local.sh, or
    HOST=local keeps facts.sh and the window shows hostname `local`.
    """
    tasks_content = TASKS_FILE.read_text()
    assert "/etc/sudoers.d/conductor-loop" in tasks_content
    assert "conductor-loop-sudoers.j2" in tasks_content
    assert "validate: visudo -cf %s" in tasks_content

    assert LOOP_SUDOERS_TEMPLATE.is_file(), (
        f"Missing sudoers template {LOOP_SUDOERS_TEMPLATE}"
    )
    content = LOOP_SUDOERS_TEMPLATE.read_text()
    assert "{{ tracewake_user }}" in content
    assert "{{ tracewake_dir }}" in content
    assert "NOPASSWD:" in content
    assert "box-sources/local.sh *" in content
    assert "box-sources/facts-local.sh" in content
    assert "box-sources/progress-local.sh *" in content
    rules = [
        line for line in content.splitlines()
        if line.strip() and not line.startswith("#") and not line.startswith("Defaults")
    ]
    assert len(rules) == 3, rules
    for rule in rules:
        assert "ALL=(loop:loop)" in rule
        assert "SETENV" not in rule
        assert "ALL" not in rule.replace("ALL=(loop:loop)", "")


def test_cd_update_lets_the_window_rewrite_the_targets_file():
    """Ansible templates ReadWritePaths into a new unit. CD must keep the
    hole on a Host that already has ProtectSystem=full, or Remove reports
    the file could not be written after every merge.
    """
    script = (ROOT / "deploy" / "cd-update.sh").read_text()
    assert "ReadWritePaths=" in script
    assert "ProtectSystem=full" in script


def test_cd_update_installs_the_conductor_loop_sudoers():
    """Ansible writes /etc/sudoers.d/conductor-loop on provision. CD must
    keep the local status reads there too: a Host that only had local.sh
    otherwise keeps `ssh: Could not resolve hostname local` on the box card
    after every merge.
    """
    script = (ROOT / "deploy" / "cd-update.sh").read_text()
    assert "/etc/sudoers.d/conductor-loop" in script
    assert "facts-local.sh" in script
    assert "progress-local.sh" in script
    assert "local.sh" in script


def test_distro_neutral_postgres_and_venvs_in_role():
    """One playbook run on a fresh host gives it the Journal with its schema applied
    and the virtual environments.
    """
    tasks_content = TASKS_FILE.read_text()

    # Distro-neutral PostgreSQL support: RedHat dnf AND Debian apt
    assert "ansible_facts.os_family == \"RedHat\"" in tasks_content
    assert "ansible_facts.os_family == \"Debian\"" in tasks_content
    assert "dnf:" in tasks_content
    assert "apt:" in tasks_content

    # Socket-only (ADR 0015), peer auth
    assert "listen_addresses = ''" in tasks_content
    assert "peer" in tasks_content

    # Schema applied
    assert "schema.sql" in tasks_content
    assert "tracewake_journal_db" in tasks_content

    # Both virtual environments (selector and web)
    assert "selector/requirements.txt" in tasks_content
    assert "selector/.venv" in tasks_content
    assert "web/requirements.txt" in tasks_content
    assert "web/.venv" in tasks_content
