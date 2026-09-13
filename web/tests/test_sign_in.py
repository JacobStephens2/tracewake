"""The window requires sign-in (issue #38).

HTTP-level, against a throwaway database: a visitor is redirected to sign-in,
a valid email and password returns them to where they were headed, and
sign-out ends the session server-side. The health endpoint stays open.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import app

WEB = Path(__file__).resolve().parents[1]
SEED_ADMIN = WEB / "seed-admin.py"
ADMIN_EMAIL = "operator@example.com"
ADMIN_PASSWORD = "correct-horse-battery"
CSRF_FIELD = re.compile(
    r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"',
    re.IGNORECASE,
)
NEXT_FIELD = re.compile(
    r'<input[^>]*name="next"[^>]*value="([^"]*)"',
    re.IGNORECASE,
)

pytestmark = pytest.mark.anonymous


def browser(**kwargs) -> TestClient:
    """A client that does not follow redirects, over https so Secure cookies stick."""
    return TestClient(
        app,
        base_url="https://testserver",
        follow_redirects=False,
        **kwargs,
    )


def csrf_token(body: str) -> str:
    match = CSRF_FIELD.search(body)
    assert match, "sign-in form has no csrf_token field"
    return match.group(1)


def seed_operator(dsn: str, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    env = os.environ.copy()
    env["SELECTOR_JOURNAL_DSN"] = dsn
    env["WINDOW_ADMIN_PASSWORD"] = password
    result = subprocess.run(
        [sys.executable, str(SEED_ADMIN), email],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result


def sign_in(client: TestClient, *, email=ADMIN_EMAIL, password=ADMIN_PASSWORD,
            next_path: str | None = None):
    login_path = "/login"
    if next_path is not None:
        login_path = f"/login?next={next_path}"
    page = client.get(login_path)
    assert page.status_code == 200, page.text
    next_match = NEXT_FIELD.search(page.text)
    data = {
        "email": email,
        "password": password,
        "csrf_token": csrf_token(page.text),
        "next": next_path if next_path is not None else (
            next_match.group(1) if next_match else "/"
        ),
    }
    return client.post("/login", data=data)


def cookie_headers(response) -> str:
    return ", ".join(response.headers.get_list("set-cookie"))


def session_cookie_name(header: str) -> str:
    return header.split("=", 1)[0].strip()


# --- Unauthenticated visitors ----------------------------------------------


@pytest.mark.parametrize("path", ["/", "/loop", "/history", "/adr", "/loop/live"])
def test_an_unauthenticated_page_redirects_to_sign_in(path):
    response = browser().get(path)
    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.path.endswith("/login")
    assert parse_qs(parsed.query).get("next") == [path]


def test_the_health_endpoint_answers_without_a_session():
    response = browser().get("/healthz")
    assert response.status_code == 200
    assert response.text.strip() == "ok"


def test_sign_in_returns_the_visitor_to_the_page_they_asked_for(db):
    seed_operator(db)
    client = browser()
    denied = client.get("/history")
    assert denied.status_code == 303
    login = client.get(denied.headers["location"])
    next_match = NEXT_FIELD.search(login.text)
    posted = client.post("/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "csrf_token": csrf_token(login.text),
        "next": next_match.group(1) if next_match else "/",
    })
    assert posted.status_code == 303
    assert posted.headers["location"] == "/history"
    landed = client.get(posted.headers["location"])
    assert landed.status_code == 200
    assert "Run history" in landed.text or "history" in landed.text.lower()


def test_a_foreign_next_is_not_followed(db):
    seed_operator(db)
    client = browser()
    posted = sign_in(client, next_path="https://evil.example/steal")
    assert posted.status_code == 303
    assert posted.headers["location"] == "/"
    assert "evil" not in posted.headers["location"]


# --- Credentials -----------------------------------------------------------


def test_wrong_password_is_refused_without_saying_which_half(db):
    seed_operator(db)
    client = browser()
    posted = sign_in(client, password="not-the-password")
    assert posted.status_code == 200
    body = posted.text.lower()
    assert "email or password" in body
    assert "unknown" not in body
    assert "no such" not in body
    assert "not found" not in body


def test_unknown_email_uses_the_same_words_as_a_wrong_password(db):
    seed_operator(db)
    wrong_password = sign_in(browser(), password="not-the-password")
    unknown_email = sign_in(browser(), email="nobody@example.com")
    assert wrong_password.status_code == 200
    assert unknown_email.status_code == 200

    def message(body: str) -> str:
        match = re.search(r"email or password[^.]*", body, re.I)
        assert match, body
        return match.group(0).lower()

    assert message(wrong_password.text) == message(unknown_email.text)


# --- Sign-out and rotation -------------------------------------------------


def test_sign_out_ends_the_session_and_the_old_cookie_is_worthless(db):
    seed_operator(db)
    client = browser()
    posted = sign_in(client)
    assert posted.status_code == 303
    assert client.get("/").status_code == 200
    header = cookie_headers(posted)
    name = session_cookie_name(header)
    issued = re.search(r"(?:__Host-)?session=([^;]+)", header).group(1)

    page = client.get("/")
    ended = client.post("/logout", data={"csrf_token": csrf_token(page.text)})
    assert ended.status_code == 303
    assert "/login" in ended.headers["location"]
    assert client.get("/").status_code == 303

    stolen = browser()
    stolen.cookies.set(name, issued)
    assert stolen.get("/").status_code == 303


def test_every_login_issues_a_fresh_session_token(db):
    seed_operator(db)
    client = browser()
    first = sign_in(client)
    header_a = cookie_headers(first)
    token_a = re.search(r"(?:__Host-)?session=([^;]+)", header_a).group(1)
    name = session_cookie_name(header_a)
    # Already signed in: the second login is a POST against the live session,
    # which is the privilege-change the token must rotate on.
    page = client.get("/")
    second = client.post("/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "csrf_token": csrf_token(page.text),
    })
    token_b = re.search(
        r"(?:__Host-)?session=([^;]+)", cookie_headers(second)
    ).group(1)
    assert token_a != token_b
    leftover = browser()
    leftover.cookies.set(name, token_a)
    assert leftover.get("/").status_code == 303


def test_session_tokens_and_password_hashes_are_not_stored_recoverably(db):
    seed_operator(db)
    client = browser()
    posted = sign_in(client)
    header = cookie_headers(posted)
    token = re.search(r"(?:__Host-)?session=([^;]+)", header).group(1)
    with psycopg.connect(db) as conn:
        hashes = [row[0] for row in conn.execute(
            "SELECT password_hash FROM web.accounts"
        )]
        assert hashes
        for hashed in hashes:
            assert hashed.startswith("$argon2")
            assert ADMIN_PASSWORD not in hashed
        stored = [row[0] for row in conn.execute(
            "SELECT token_hash FROM web.sessions"
        )]
        assert stored
        assert token not in stored
        assert all(len(h) == 64 and re.fullmatch(r"[0-9a-f]+", h) for h in stored)


# --- Expiry ----------------------------------------------------------------


def test_an_idle_session_is_refused(db):
    seed_operator(db)
    client = browser()
    assert sign_in(client).status_code == 303
    assert client.get("/").status_code == 200
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE web.sessions"
            " SET last_seen_at = now() - interval '31 minutes'"
        )
    assert client.get("/").status_code == 303


def test_an_absolutely_expired_session_is_refused(db):
    seed_operator(db)
    client = browser()
    assert sign_in(client).status_code == 303
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE web.sessions"
            " SET created_at = now() - interval '13 hours',"
            "     last_seen_at = now()"
        )
    assert client.get("/").status_code == 303


# --- Cookie flags ----------------------------------------------------------


def test_the_session_cookie_carries_the_researched_flags(db):
    seed_operator(db)
    posted = sign_in(browser())
    header = cookie_headers(posted).lower()
    assert "__host-session=" in header
    assert "secure" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    assert "domain=" not in header


def test_the_preview_toggle_relaxes_only_secure(db, monkeypatch):
    monkeypatch.setenv("WINDOW_COOKIE_SECURE", "0")
    seed_operator(db)
    posted = sign_in(browser())
    header = cookie_headers(posted)
    lowered = header.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "secure" not in lowered
    assert "__host-" not in lowered


# --- CSRF ------------------------------------------------------------------


def test_a_state_changing_post_without_its_csrf_token_is_refused(db):
    seed_operator(db)
    client = browser()
    assert sign_in(client).status_code == 303
    refused = client.post("/logout")
    assert refused.status_code == 403
    # And the session is still live: a CSRF miss is not a sign-out.
    assert client.get("/").status_code == 200


def test_sign_in_without_its_csrf_token_is_refused(db):
    seed_operator(db)
    client = browser()
    client.get("/login")
    refused = client.post("/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
    })
    assert refused.status_code == 403
    assert client.get("/").status_code == 303


# --- Seeding ---------------------------------------------------------------


def test_the_seeding_command_creates_the_first_admin(db):
    seed_operator(db)
    with psycopg.connect(db) as conn:
        row = conn.execute(
            "SELECT email, role, password_hash FROM web.accounts"
        ).fetchone()
    assert row[0] == ADMIN_EMAIL
    assert row[1] == "admin"
    assert row[2].startswith("$argon2")


def test_seeding_an_existing_account_fails_loudly(db):
    seed_operator(db)
    env = os.environ.copy()
    env["SELECTOR_JOURNAL_DSN"] = db
    env["WINDOW_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    again = subprocess.run(
        [sys.executable, str(SEED_ADMIN), ADMIN_EMAIL],
        env=env,
        capture_output=True,
        text=True,
    )
    assert again.returncode != 0
    assert again.stderr.strip()
    assert "already" in again.stderr.lower()
    with psycopg.connect(db) as conn:
        count = conn.execute("SELECT count(*) FROM web.accounts").fetchone()[0]
    assert count == 1
