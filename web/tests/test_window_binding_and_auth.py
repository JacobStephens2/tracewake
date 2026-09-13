"""The window binds to loopback and requires sign-in (issue #38, ADR 0027).

The binding half of the old criterion remains: both systemd units listen on
127.0.0.1. The auth half is reversed: unauthenticated requests 303 to sign-in.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app

pytestmark = pytest.mark.anonymous

client = TestClient(app, follow_redirects=False)

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"
ADR = ROOT / "docs" / "adr" / "0027-the-window-requires-sign-in.md"


def test_systemd_units_bind_to_loopback():
    """Both tracewake-web.service and tracewake-web-staging.service must bind to 127.0.0.1."""
    units = ["tracewake-web.service", "tracewake-web-staging.service"]
    for unit_name in units:
        unit_file = SYSTEMD_DIR / unit_name
        assert unit_file.is_file(), f"Missing unit file {unit_file}"
        content = unit_file.read_text()
        exec_start_lines = [
            line.strip() for line in content.splitlines() if line.strip().startswith("ExecStart=")
        ]
        assert exec_start_lines, f"No ExecStart found in {unit_name}"
        for line in exec_start_lines:
            assert "--host 127.0.0.1" in line, (
                f"{unit_name} ExecStart must bind explicitly to 127.0.0.1: {line}"
            )
            assert "0.0.0.0" not in line, (
                f"{unit_name} ExecStart must not bind to 0.0.0.0"
            )


def test_an_unauthenticated_request_is_sent_to_sign_in():
    """The reversed criterion: pages 303 to sign-in; they do not 401-challenge."""
    for path in ["/", "/adr"]:
        resp = client.get(path)
        assert resp.status_code == 303, f"{path} returned status {resp.status_code}"
        assert "/login" in resp.headers["location"]
        assert "WWW-Authenticate" not in resp.headers


def test_the_adr_records_the_reversal():
    assert ADR.is_file(), f"missing {ADR.name}"
    text = ADR.read_text()
    assert "ships no authentication of its own" in text
    assert "loopback" in text.lower()
