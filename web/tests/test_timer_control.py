"""The timer toggle: start and stop the Selector's timer from the window.

The status strip exists to tell a quiet Selector from a dead one; when the
timer reads `inactive` the page must offer the admin the way back up, not
just the diagnosis. Readers see neither button, and a control that fails
says so in the cell rather than 500ing.
"""
import pytest
from fastapi.testclient import TestClient

from app import PROJECT, TIMER_UNIT, app
from conftest import csrf_from
from test_loop_page import strip
from test_roles import post_control, signed_in_reader

client = TestClient(app)


def _timer_read(monkeypatch, tmp_path, text):
    script = tmp_path / "timer-read.sh"
    script.write_text(f"#!/usr/bin/env bash\ncat <<'EOF'\n{text}\nEOF\n")
    script.chmod(0o755)
    monkeypatch.setenv("SELECTOR_TIMER_COMMAND", str(script))


def _timer_control(monkeypatch, tmp_path, *, exit_code=0, stderr=""):
    """A control command that records its argv and answers as told."""
    record = tmp_path / "control-args"
    errfile = tmp_path / "control-stderr"
    errfile.write_text(stderr)
    script = tmp_path / "timer-control.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" > \"{record}\"\n"
        f"cat \"{errfile}\" >&2\n"
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("SELECTOR_TIMER_CONTROL_COMMAND", str(script))
    return record


def test_an_inactive_timer_offers_a_start(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=inactive\nNextElapseUSecRealtime=n/a",
    )
    cell = strip(client.get("/").text)
    assert "timer not running (inactive)" in cell
    assert "Start timer" in cell
    assert 'hx-post="/loop/timer/start"' in cell


def test_an_active_timer_offers_a_stop(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=active\nNextElapseUSecRealtime=Thu 2026-08-27 14:31:00 UTC",
    )
    cell = strip(client.get("/").text)
    assert "Stop timer" in cell
    assert 'hx-post="/loop/timer/stop"' in cell


@pytest.mark.anonymous
def test_a_reader_sees_neither_timer_button(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=inactive\nNextElapseUSecRealtime=n/a",
    )
    assert "Start timer" not in strip(signed_in_reader(db).get("/").text)


def test_start_enables_the_timer_now(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=inactive\nNextElapseUSecRealtime=n/a",
    )
    record = _timer_control(monkeypatch, tmp_path)
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/timer/start",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert record.read_text().split() == [
        "enable", "--now", TIMER_UNIT,
    ]


def test_stop_disables_the_timer_now(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=active\nNextElapseUSecRealtime=Thu 2026-08-27 14:31:00 UTC",
    )
    record = _timer_control(monkeypatch, tmp_path)
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/timer/stop",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert record.read_text().split() == [
        "disable", "--now", TIMER_UNIT,
    ]


def test_the_timer_toggle_names_the_installed_cycle_timer():
    """Start/Stop must name the unit the controller installs.

    A leftover name from before the units were named for the product is a
    button that either enables a unit that does not exist or a command the
    sudoers rule will not match. The independent source of truth is the
    unit file in deploy/systemd/.
    """
    assert TIMER_UNIT == "tracewake-selector-cycle.timer"
    assert (PROJECT / "deploy" / "systemd" / TIMER_UNIT).is_file()


def test_a_failed_control_says_so_in_the_cell(db, monkeypatch, tmp_path):
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=inactive\nNextElapseUSecRealtime=n/a",
    )
    _timer_control(
        monkeypatch, tmp_path, exit_code=1,
        stderr="sudo: a password is required",
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/timer/start",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert "sudo: a password is required" in strip(resp.text)


def test_a_failed_control_keeps_every_line_of_the_error(db, monkeypatch, tmp_path):
    """sudo's useful sentence is not always the last one.

    With NoNewPrivileges set, sudo prints that the flag is set and then a
    line about containers. Showing only the last line is how Start timer
    reported a container warning and hid the cause.
    """
    _timer_read(
        monkeypatch, tmp_path,
        "ActiveState=inactive\nNextElapseUSecRealtime=n/a",
    )
    _timer_control(
        monkeypatch, tmp_path, exit_code=1,
        stderr=(
            'sudo: The "no new privileges" flag is set, which prevents '
            "sudo from running as root.\n"
            "sudo: If sudo is running in a container, you may need to "
            "adjust the container configuration to disable the flag."
        ),
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/timer/start",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    cell = strip(resp.text)
    assert "no new privileges" in cell
    assert "container" in cell


@pytest.mark.anonymous
def test_a_readers_direct_timer_control_is_refused(db, monkeypatch, tmp_path):
    _timer_control(monkeypatch, tmp_path)
    refused = post_control(signed_in_reader(db), "/loop/timer/start")
    assert refused.status_code == 403
    assert (tmp_path / "control-args").exists() is False
