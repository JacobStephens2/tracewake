"""Roles gate the controls (issue #40).

A reader reaches every page and the live stream; pause and resume are
absent from the board and refused if posted directly. An unauthenticated
request to a control is refused, not redirected into a success. An admin's
pause and resume keep working.
"""
from __future__ import annotations

import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import auth
import control
import journal
from app import app
from conftest import _test_password_hash, csrf_from
from test_sign_in import (
    ADMIN_PASSWORD,
    browser,
    seed_admin,
    sign_in,
)

READER_EMAIL = "reader@example.com"
READER_PASSWORD = ADMIN_PASSWORD

PAGES = [
    "/",
    "/history",
    "/adr",
    "/loop/live",
    "/loop/history",
    "/loop/history/live",
]

pytestmark = pytest.mark.anonymous


def seed_reader(db, email=READER_EMAIL):
    with journal.connect() as conn:
        conn.execute(
            "INSERT INTO web.accounts (email, password_hash, role)"
            " VALUES (%s, %s, 'reader')",
            (email.strip().lower(), _test_password_hash()),
        )


def signed_in_reader(db) -> TestClient:
    seed_reader(db)
    client = browser()
    posted = sign_in(client, email=READER_EMAIL, password=READER_PASSWORD)
    assert posted.status_code == 303, posted.text
    return client


def signed_in_admin(db) -> TestClient:
    seed_admin(db)
    client = browser()
    posted = sign_in(client)
    assert posted.status_code == 303, posted.text
    return client


def post_control(client: TestClient, path: str):
    page = client.get("/")
    assert page.status_code == 200, page.text
    return client.post(
        path,
        data={"csrf_token": csrf_from(page.text)},
        headers={"HX-Request": "true"},
    )


@pytest.mark.parametrize("path", PAGES)
def test_a_reader_reaches_every_page(db, path):
    client = signed_in_reader(db)
    response = client.get(path)
    assert response.status_code == 200, response.text


def test_a_reader_reaches_the_live_event_stream(db, monkeypatch):
    """The TestClient buffers a stream until it ends, so this is a real
    uvicorn - the same reason test_liveness.py cannot use the client."""
    seed_reader(db)
    with journal.connect() as conn:
        account_id = conn.execute(
            "SELECT id FROM web.accounts WHERE email = %s",
            (READER_EMAIL,),
        ).fetchone()[0]
    raw = auth.create_session(account_id)
    monkeypatch.setattr(auth, "session_token_from_request", lambda req: raw)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not running.started:
        assert time.monotonic() < deadline, "the test server did not start"
        assert thread.is_alive(), "the test server died while starting"
        time.sleep(0.05)
    port = running.servers[0].sockets[0].getsockname()[1]
    try:
        with httpx.Client(timeout=5) as visitor:
            with visitor.stream("GET", f"http://127.0.0.1:{port}/loop/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith(
                    "text/event-stream"
                )
                line = next(response.iter_lines())
                assert line.startswith("retry:") or line.startswith(":") or line.startswith("event:")
    finally:
        running.should_exit = True
        thread.join(timeout=10)


def test_a_readers_direct_pause_is_refused_and_does_not_change_the_flag(db):
    client = signed_in_reader(db)
    assert 'data-selector-pause="paused"' not in client.get("/").text
    refused = post_control(client, "/loop/pause")
    assert refused.status_code == 403
    assert 'data-selector-pause="paused"' not in client.get("/").text


def test_a_readers_direct_resume_is_refused_and_does_not_change_the_flag(db):
    with journal.connect() as conn:
        control.set_paused(conn, True)
    client = signed_in_reader(db)
    assert 'data-selector-pause="paused"' in client.get("/").text
    refused = post_control(client, "/loop/resume")
    assert refused.status_code == 403
    assert 'data-selector-pause="paused"' in client.get("/").text


def test_the_readers_board_carries_no_control_affordances(db):
    client = signed_in_reader(db)
    body = client.get("/").text
    assert "Pause dispatch" not in body
    assert "Resume dispatch" not in body
    assert "/loop/pause" not in body
    assert "/loop/resume" not in body
    with journal.connect() as conn:
        control.set_paused(conn, True)
    paused = client.get("/").text
    assert 'data-selector-pause="paused"' in paused
    assert "Resume dispatch" not in paused
    assert "/loop/resume" not in paused


def test_an_unauthenticated_control_request_is_refused_not_redirected_into_a_success(db):
    seed_admin(db)
    client = browser()
    refused = client.post("/loop/pause", data={"csrf_token": "not-a-token"})
    assert refused.status_code == 403
    assert "sign-in" not in refused.headers.get("location", "")
    resumed = client.post("/loop/resume", data={"csrf_token": "not-a-token"})
    assert resumed.status_code == 403
    posted = sign_in(client)
    assert posted.status_code == 303
    assert 'data-selector-pause="paused"' not in client.get("/").text


def test_an_admin_can_pause_and_resume(db):
    client = signed_in_admin(db)
    paused = post_control(client, "/loop/pause")
    assert paused.status_code == 200, paused.text
    assert 'data-selector-pause="paused"' in paused.text
    assert 'data-selector-pause="paused"' in client.get("/").text
    resumed = post_control(client, "/loop/resume")
    assert resumed.status_code == 200, resumed.text
    assert 'data-selector-pause="paused"' not in resumed.text
    assert 'data-selector-pause="paused"' not in client.get("/").text
