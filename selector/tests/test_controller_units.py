"""Test that Tracewake's systemd units are named for the product and distro-neutral.

Issue #4 acceptance criteria:
- The units are named for the product, and their files carry no distro assumption
- The failure hookup is in the unit section systemd honours, proven by asking
  systemd rather than by reading the file
"""
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"

EXPECTED_UNITS = {
    "tracewake-selector-cycle.service",
    "tracewake-selector-cycle.timer",
    "tracewake-selector-notifier.service",
    "tracewake-web.service",
    "tracewake-web-staging.service",
}


def test_unit_files_are_named_for_the_product():
    """Units must be named for the product (tracewake-*)."""
    assert SYSTEMD_DIR.is_dir(), f"Missing systemd directory at {SYSTEMD_DIR}"
    unit_names = {p.name for p in SYSTEMD_DIR.iterdir() if p.suffix in (".service", ".timer")}
    assert EXPECTED_UNITS.issubset(unit_names), f"Missing units: {EXPECTED_UNITS - unit_names}"
    for name in unit_names:
        assert name.startswith("tracewake-"), f"Unit {name} is not named for the product"


def test_unit_files_carry_no_distro_assumption():
    """Unit files must carry no distro assumption (e.g. SELinux restorecon).

    SELinux relabeling is added via a drop-in by tracewake_controller when
    SELinux is enforcing, never hardcoded in the product's unit files.
    """
    for unit_path in SYSTEMD_DIR.iterdir():
        if unit_path.suffix not in (".service", ".timer"):
            continue
        content = unit_path.read_text()
        assert "restorecon" not in content, (
            f"{unit_path.name} contains 'restorecon'. SELinux handling belongs"
            f" in a drop-in added only when SELinux is enforcing, not in distro-neutral units."
        )


def test_unit_files_carry_no_instance_specific_paths():
    """Unit files must not hardcode an instance's paths or credentials."""
    banned_paths = [
        "/srv/orchestration",
        "/etc/orchestration",
        "/srv/lab-webapp-staging",
        "/var/lib/conductor",
    ]
    for unit_path in SYSTEMD_DIR.iterdir():
        if unit_path.suffix not in (".service", ".timer"):
            continue
        content = unit_path.read_text()
        for banned in banned_paths:
            assert banned not in content, (
                f"{unit_path.name} contains instance-specific path {banned!r}. "
                f"Use templated variables or instance environment files instead."
            )


def _assert_directive_in_section(directive_pattern: re.Pattern, expected_section: str = "Unit") -> None:
    """Assert that matching directives in all service units appear in the expected section."""
    section_re = re.compile(r"^\s*\[([A-Za-z0-9_]+)\]\s*$", re.M)
    for unit_path in SYSTEMD_DIR.iterdir():
        if unit_path.suffix != ".service":
            continue
        content = unit_path.read_text()
        current_section = None
        for line in content.splitlines():
            line_s = line.strip()
            sec_match = section_re.match(line_s)
            if sec_match:
                current_section = sec_match.group(1)
            elif directive_pattern.match(line_s):
                assert current_section == expected_section, (
                    f"{unit_path.name}: {line_s} is in [{current_section}], "
                    f"must be in [{expected_section}] where systemd honours it."
                )


def test_failure_hookup_is_in_unit_section():
    """OnFailure= must be in [Unit], NOT [Service].

    systemd silently ignores OnFailure= in [Service], leading to silent alert failures.
    """
    _assert_directive_in_section(re.compile(r"^\s*OnFailure\s*="))


def test_start_limit_is_in_unit_section():
    """StartLimitIntervalSec= and StartLimitBurst= must be in [Unit], NOT [Service]."""
    _assert_directive_in_section(re.compile(r"^\s*StartLimit(?:IntervalSec|Burst)\s*="))


def _systemd_is_available() -> bool:
    import shutil
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True)
        return proc.returncode in (0, 1)  # 0=running, 1=degraded
    except OSError:
        return False


def _render_unit_template(content: str, context: dict) -> str:
    import json
    cmd = [
        "/usr/bin/python3",
        "-c",
        "import sys, json, jinja2; "
        "data = json.loads(sys.argv[1]); "
        "tmpl = sys.stdin.read(); "
        "sys.stdout.write(jinja2.Template(tmpl).render(**data))",
        json.dumps(context),
    ]
    proc = subprocess.run(cmd, input=content, text=True, capture_output=True, check=True)
    return proc.stdout


DEFAULT_TEST_CONTEXT = {
    "tracewake_dir": "/srv/tracewake",
    "tracewake_user": "conductor",
    "tracewake_group": "conductor",
    "tracewake_user_home": "/var/lib/conductor",
    "tracewake_env_file": "/etc/tracewake/tracewake.env",
    "tracewake_extra_env_files": [],
    "tracewake_cycle_wrapper": "",
    "tracewake_cycle_args": "",
    "tracewake_on_failure": "notify-unit-failure@%n.service",
    "tracewake_web_port": 8100,
    "tracewake_staging_user": "labstage",
    "tracewake_staging_group": "labstage",
    "tracewake_staging_dir": "/srv/tracewake-staging",
    "tracewake_staging_journal_db": "selector_staging",
}


@contextmanager
def _transient_systemd_unit(unit_name: str, content: str):
    """Install a transient unit into /run/systemd/system/, reload daemon, and remove on exit."""
    run_unit_path = Path("/run/systemd/system") / unit_name
    p = subprocess.Popen(["sudo", "tee", str(run_unit_path)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    p.communicate(content.encode())
    assert p.returncode == 0
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    try:
        yield unit_name
    finally:
        subprocess.run(["sudo", "rm", "-f", str(run_unit_path)], capture_output=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True)


def test_failure_hookup_honoured_by_systemd_directly():
    """Issue #4 acceptance criterion:
    The failure hookup is in the unit section systemd honours, proven by
    asking systemd rather than by reading the file.

    We prove:
    1. Systemd returns the OnFailure unit name when OnFailure is in [Unit].
    2. Systemd returns empty string when OnFailure is in [Service] (ignored).
    3. Our shipped units (tracewake-selector-cycle.service, tracewake-selector-notifier.service)
       register their failure hookup with systemd when loaded.
    """
    if not _systemd_is_available():
        pytest.skip("systemd is not running on this host")

    units_to_test = [
        "tracewake-selector-cycle.service",
        "tracewake-selector-notifier.service",
    ]

    for unit_name in units_to_test:
        unit_tmpl = (SYSTEMD_DIR / unit_name).read_text()
        rendered = _render_unit_template(unit_tmpl, DEFAULT_TEST_CONTEXT)
        test_unit_name = f"test-{unit_name}"
        with _transient_systemd_unit(test_unit_name, rendered):
            val = subprocess.check_output(
                ["systemctl", "show", test_unit_name, "-p", "OnFailure", "--value"],
                text=True,
            ).strip()

            assert val == f"notify-unit-failure@{test_unit_name}.service", (
                f"systemd returned {val!r}, expected failure handler registered"
            )

    # Negative proof: placing OnFailure in [Service] results in systemd ignoring it (empty value)
    bad_unit = """[Unit]
Description=Test Bad Unit Placement
[Service]
Type=oneshot
ExecStart=/bin/true
OnFailure=notify-unit-failure@%n.service
"""
    with _transient_systemd_unit("test-bad-onfailure.service", bad_unit):
        bad_val = subprocess.check_output(
            ["systemctl", "show", "test-bad-onfailure.service", "-p", "OnFailure", "--value"],
            text=True,
        ).strip()
        assert bad_val == "", f"Expected systemd to ignore OnFailure in [Service], got {bad_val!r}"


def test_window_unit_allows_the_timer_sudo():
    """The Start/Stop buttons run `sudo -n systemctl`. systemd's
    NoNewPrivileges flag makes sudo refuse before sudoers is consulted,
    with a message about containers. The window unit must not set it.

    Staging keeps the flag: that process is not the live toggle.
    """
    content = (SYSTEMD_DIR / "tracewake-web.service").read_text()
    assert "NoNewPrivileges=true" not in content, (
        "tracewake-web.service must not set NoNewPrivileges=true; "
        "the timer toggle cannot sudo through that flag"
    )


def test_cycle_unit_has_infinite_start_timeout():
    """Acceptance criterion 7 for issue #8:
    The unit no longer bounds a drain by its start timeout, while the
    per-dispatch backstop still bounds a Run.
    """
    unit_path = SYSTEMD_DIR / "tracewake-selector-cycle.service"
    content = unit_path.read_text()
    assert re.search(r"^\s*TimeoutStartSec\s*=\s*infinity\s*$", content, re.M), (
        "tracewake-selector-cycle.service must set TimeoutStartSec=infinity"
    )
