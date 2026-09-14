"""The Attended Preview banner (ADR 0016).

An Attended Preview runs unreviewed code, and it is permissible only while
somebody is looking at it. The banner is the half of that which tells you
*what* you are looking at; `RuntimeMaxSec` on the unit is the half that stops
a preview outliving the attention. So these tests care about two things: that
a preview always says it is one, and that the live app never does.
"""
import json

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

PAGES = ["/", "/adr", "/history"]


def _lease(tmp_path, **over):
    payload = {
        "branch": "feat/queue-board",
        "sha": "1c904956e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5",
        "started_at": "2026-08-26T21:00:00+00:00",
        "started_by": "jstephens",
    }
    payload.update(over)
    path = tmp_path / "lease.json"
    path.write_text(json.dumps(payload))
    return path


def test_the_live_app_shows_no_banner(monkeypatch):
    """The default. A banner on the live app would train the operator to read
    past it, which is the one thing it cannot afford."""
    monkeypatch.delenv("LAB_PREVIEW_LEASE", raising=False)
    for page in PAGES:
        assert "attended preview" not in client.get(page).text.lower(), page


def test_a_preview_names_its_branch_and_sha_on_every_page(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_PREVIEW_LEASE", str(_lease(tmp_path)))
    for page in PAGES:
        body = client.get(page).text
        assert "attended preview" in body.lower(), page
        assert "feat/queue-board" in body, page
        assert "1c90495" in body, page
        assert "jstephens" in body, page


def test_the_sha_is_shown_short_not_whole(monkeypatch, tmp_path):
    """Forty hex characters in a banner is noise; seven is a thing you can
    compare against `git log` at a glance."""
    monkeypatch.setenv("LAB_PREVIEW_LEASE", str(_lease(tmp_path)))
    body = client.get("/").text
    assert "1c904956e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5" not in body


def test_the_banner_says_how_long_the_preview_has_been_up(monkeypatch, tmp_path):
    """Uptime is the attendedness signal: a preview that has been up for three
    hours is one nobody is watching."""
    from datetime import datetime, timedelta, timezone

    started = datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)
    monkeypatch.setenv(
        "LAB_PREVIEW_LEASE", str(_lease(tmp_path, started_at=started.isoformat()))
    )
    body = client.get("/").text
    assert "2h05m" in body


def test_a_preview_with_no_lease_file_still_declares_itself(monkeypatch, tmp_path):
    """The env var is what makes this instance a preview; the lease only says
    which branch. Losing the lease must not turn the banner off, or the one
    page that cannot afford to look live starts looking live."""
    monkeypatch.setenv("LAB_PREVIEW_LEASE", str(tmp_path / "absent.json"))
    body = client.get("/").text
    assert "attended preview" in body.lower()
    assert "lease is unreadable" in body.lower()


def test_an_unreadable_lease_does_not_break_the_page(monkeypatch, tmp_path):
    bad = tmp_path / "lease.json"
    bad.write_text("{not json")
    monkeypatch.setenv("LAB_PREVIEW_LEASE", str(bad))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "lease is unreadable" in resp.text.lower()


def test_the_banner_quotes_the_bound_that_is_configured(monkeypatch, tmp_path):
    """Four hours lives in RuntimeMaxSec on the unit. A banner that hardcoded
    its own copy would keep saying "4 hours" the day the unit said six."""
    monkeypatch.setenv("LAB_PREVIEW_LEASE", str(_lease(tmp_path)))
    monkeypatch.setenv("LAB_PREVIEW_MAX_AGE_SECONDS", str(6 * 60 * 60))
    body = client.get("/").text
    assert "after 6 hours" in body
    assert "after 4 hours" not in body
