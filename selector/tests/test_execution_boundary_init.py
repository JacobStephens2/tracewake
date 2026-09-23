"""Issue #72: fresh-box `sbx policy init` must not hang without Docker sign-in.

Host setup failed loudly nowhere: with no Docker authentication for the Loop
account, `sbx policy init deny-all` waited indefinitely at the "Choose the
egress profile" task. The fix is an auth pre-check with an actionable failure
plus a documented bound on init itself.

These tests assert the role's shape the same way test_box_role.py does: by
reading the declared tasks and defaults. No cloud, daemon, or `sbx` needed.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLE_DIR = ROOT / "deploy" / "ansible" / "roles" / "loop_execution_boundary"
TASKS_FILE = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_FILE = ROLE_DIR / "defaults" / "main.yml"


def _tasks():
    return TASKS_FILE.read_text()


def _defaults():
    return DEFAULTS_FILE.read_text()


def test_init_timeout_is_documented_and_bounded():
    """Init carries a documented bound so unattended Ansible can never hang."""
    defaults = _defaults()
    match = re.search(
        r"loop_execution_boundary_policy_init_timeout:\s*(\d+)", defaults
    )
    assert match, "defaults must declare loop_execution_boundary_policy_init_timeout"
    assert int(match.group(1)) > 0, "the bound must be a positive number of seconds"
    assert "loop_execution_boundary_policy_init_timeout" in _tasks(), (
        "the init task must consume the documented timeout"
    )


def test_auth_precheck_runs_before_any_policy_mutation():
    """`sbx diagnose` gates the store: reset/init/allow all come after it."""
    tasks = _tasks()
    assert "sbx diagnose" in tasks, "the role must probe sign-in state via sbx diagnose"
    diagnose_at = tasks.index("- name: Check the boundary's sign-in state")
    for marker in (
        "- name: Clear the policy store",
        "- name: Choose the egress profile",
        "- name: Allow exactly the hosts the agent needs",
    ):
        assert marker in tasks, f"expected task {marker!r} to still exist"
        assert diagnose_at < tasks.index(marker), (
            f"auth pre-check must precede {marker!r}, or a missing sign-in "
            "destroys or stalls a store that was fine"
        )


def test_auth_failure_names_the_signin_wizard():
    """The failure tells the operator exactly what to run, with LOOP_HOST."""
    tasks = _tasks()
    assert "loop-sbx-login.sh" in tasks, (
        "the auth failure must name wizards/loop-sbx-login.sh"
    )
    assert "LOOP_HOST" in tasks, "the auth failure must name LOOP_HOST"


def test_init_is_bounded_and_detached_from_stdin():
    """Init itself cannot wait on a terminal: timeout plus stdin from /dev/null."""
    tasks = _tasks()
    init_at = tasks.index("- name: Choose the egress profile")
    init_block = tasks[init_at : init_at + 2000]
    assert "timeout:" in init_block, "init must carry a timeout bound"
    assert "/dev/null" in init_block, (
        "init must read stdin from /dev/null, like the posture read: "
        "ansible's sudo invocation can otherwise hand sbx a terminal that "
        "starts an interactive spinner instead of failing"
    )


def test_auth_precheck_is_read_only():
    """The pre-check never mutates: it is a read, safe under --check."""
    tasks = _tasks()
    diagnose_at = tasks.index("- name: Check the boundary's sign-in state")
    check_block = tasks[diagnose_at : diagnose_at + 1500]
    assert "changed_when: false" in check_block, (
        "the auth probe must report changed_when: false"
    )
    assert "timeout:" in check_block, (
        "the auth probe must also be bounded: an unbounded read ahead of init "
        "would leave the hang the timeout was added to kill"
    )


def test_kvm_group_is_declared_before_loop_joins_it():
    """Issue #107: the stock Ubuntu Docker image has no kvm group. The
    role must create it rather than assuming the cloud image did."""
    tasks = _tasks()
    assert re.search(r"name:\s*kvm", tasks), (
        "loop_execution_boundary must declare the kvm group so a Host "
        "without one still applies (issue #107)."
    )
    group_at = tasks.index("name: kvm")
    join_at = tasks.index("The Loop's account can open /dev/kvm")
    assert group_at < join_at, (
        "the kvm group must exist before loop is added to it"
    )


def test_docker_apt_architecture_follows_the_host():
    """Issue #107: the Docker apt source must match the Host's dpkg
    architecture. Hardcoding amd64 breaks an arm64 local Host."""
    tasks = _tasks()
    assert "arch=amd64" not in tasks, (
        "loop_execution_boundary hardcodes arch=amd64 in the Docker apt "
        "source. Use the Host's architecture so an arm64 machine applies "
        "(issue #107)."
    )
    assert "amd64" in tasks and "arm64" in tasks, (
        "the Docker apt source must name both amd64 and arm64 so the "
        "architecture map is visible in the role (issue #107)."
    )
