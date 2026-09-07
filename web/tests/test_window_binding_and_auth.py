"""The window binds to the loopback interface and ships no authentication of its own (AC 3).

Issue #13:
The window binds to the loopback interface and ships no authentication of its own.
"""
from pathlib import Path
import re

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_DIR = ROOT / "deploy" / "systemd"


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


def test_window_ships_no_authentication_of_its_own():
    """Unauthenticated requests succeed without 401 or 403 challenges."""
    for path in ["/", "/adr"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned status {resp.status_code}"
        assert "WWW-Authenticate" not in resp.headers

    # No security schemes or authentication dependencies configured on app
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant:
            security_reqs = getattr(dependant, "security_requirements", [])
            assert not security_reqs, f"Route {route.path} has unexpected security requirements: {security_reqs}"
