"""MicroVMs on the dashboard (#97), at HTTP level.

The sampler is the substitutable seam: tests replace it and assert the page
shows the microVMs it returned. The box-surface command behind the default
sampler is a different seam, exercised below by pointing
SELECTOR_BOX_MICROVMS_COMMAND at a script.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import column
from microvms import Sample, SamplerError, MicroVM

client = TestClient(app)


def microvms_widget(body: str) -> str:
    """The MicroVMs fold, cut out of the page."""
    match = re.search(
        r'<details class="fold microvms"[^>]*>(.*?)</details>', body, re.DOTALL
    )
    assert match, "no MicroVMs widget on the page"
    return match.group(1)


def _sample(*vms: MicroVM) -> Sample:
    return Sample(vms=vms)


def test_the_home_page_shows_when_no_microvm_is_running(db, monkeypatch):
    monkeypatch.setattr("microvms.sample", lambda: _sample())

    widget = microvms_widget(client.get("/").text)

    assert "none running" in widget
    assert "loop-4242" not in widget


def test_the_queue_board_shows_the_boxs_microvms(db, monkeypatch):
    monkeypatch.setattr(
        "microvms.sample",
        lambda: _sample(
            MicroVM(
                name="loop-4242-1700000000",
                agent="claude",
                status="running",
                workspace="/home/loop/work/gadgets",
            )
        ),
    )

    widget = microvms_widget(client.get("/").text)

    assert "loop-4242-1700000000" in widget
    assert "claude" in widget
    assert "running" in widget
    assert "/home/loop/work/gadgets" in widget
    assert "none running" not in widget


def test_the_figures_ride_the_live_region(db, monkeypatch):
    """The same HTML the SSE swap fetches, so a reload is not required."""
    monkeypatch.setattr(
        "microvms.sample",
        lambda: _sample(
            MicroVM(
                name="loop-7-1",
                agent="grok",
                status="running",
                workspace="/home/loop/work/tracewake",
            )
        ),
    )
    fragment = client.get("/loop/live").text
    assert fragment.lstrip().startswith('<div id="live-region"')
    widget = microvms_widget(fragment)
    assert "loop-7-1" in widget
    assert "grok" in widget


def test_a_sampler_failure_degrades_the_widget_not_the_board(
    db, tracker, monkeypatch
):
    tracker.queue("ready-for-agent", [tracker.issue(101)])

    def boom():
        raise SamplerError("sbx is not on PATH")

    monkeypatch.setattr("microvms.sample", boom)

    resp = client.get("/")
    assert resp.status_code == 200
    assert 'data-microvms="degraded"' in resp.text
    widget = microvms_widget(resp.text)
    assert "sbx is not on PATH" in widget
    assert "#101" in column(resp.text, "eligible")


def test_two_microvms_are_both_shown(db, monkeypatch):
    monkeypatch.setattr(
        "microvms.sample",
        lambda: _sample(
            MicroVM(
                name="loop-1-1",
                agent="claude",
                status="running",
                workspace="/a",
            ),
            MicroVM(
                name="loop-2-2",
                agent="codex",
                status="stopped",
                workspace="/b",
            ),
        ),
    )
    widget = microvms_widget(client.get("/").text)
    assert "loop-1-1" in widget
    assert "loop-2-2" in widget
    assert "codex" in widget
    assert "stopped" in widget


# Bound at import time so the autouse idle stub (conftest) does not hide
# the real sampler from these tests.
from microvms import sample as read_microvms  # noqa: E402


def _command(tmp_path: Path, stdout: str, *, exit_code: int = 0) -> Path:
    payload = tmp_path / "microvms.json"
    payload.write_text(stdout)
    script = tmp_path / "microvms-command.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"cat {payload}\n"
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return script


def test_the_sampler_lists_microvms_from_the_box_command(tmp_path, monkeypatch):
    payload = json.dumps({
        "sandboxes": [
            {
                "name": "loop-4242-1700000000",
                "agent": "claude",
                "status": "running",
                "workspace": "/home/loop/work/gadgets",
            }
        ]
    })
    monkeypatch.setenv(
        "SELECTOR_BOX_MICROVMS_COMMAND", str(_command(tmp_path, payload))
    )

    found = read_microvms()

    assert found.vms == (
        MicroVM(
            name="loop-4242-1700000000",
            agent="claude",
            status="running",
            workspace="/home/loop/work/gadgets",
        ),
    )


def test_an_empty_list_is_none_running(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SELECTOR_BOX_MICROVMS_COMMAND",
        str(_command(tmp_path, '{"sandboxes":[]}')),
    )
    assert read_microvms().vms == ()


def test_a_failed_command_is_a_sampler_failure(tmp_path, monkeypatch):
    script = tmp_path / "microvms-command.sh"
    script.write_text(
        "#!/usr/bin/env bash\nprintf 'sbx is not on PATH\\n' >&2\nexit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("SELECTOR_BOX_MICROVMS_COMMAND", str(script))
    with pytest.raises(SamplerError, match="sbx is not on PATH"):
        read_microvms()


def test_unparseable_output_is_a_sampler_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SELECTOR_BOX_MICROVMS_COMMAND",
        str(_command(tmp_path, "No sandboxes found.")),
    )
    with pytest.raises(SamplerError, match="not JSON"):
        read_microvms()


def test_the_preview_fixture_is_a_shape_the_sampler_reads(monkeypatch):
    """An Attended Preview lists a fixture, not the live box (ADR 0016).
    The fixture has to be a shape today's sampler reads, or the widget
    degrades in every preview."""
    preview = (
        Path(__file__).resolve().parents[2]
        / "selector" / "preview-sources" / "microvms.sh"
    )
    monkeypatch.setenv("SELECTOR_BOX_MICROVMS_COMMAND", str(preview))
    found = read_microvms()
    assert found.vms == (
        MicroVM(
            name="preview-fixture",
            agent="claude",
            status="running",
            workspace="/preview/workspace",
        ),
    )
