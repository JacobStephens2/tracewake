"""Password change for signed-in accounts.

HTTP-level, against a throwaway database:
- An unauthenticated request 303-redirects to sign-in with next=/password.
- A signed-in user sees the change password form.
- An incorrect current password or empty new password refuses the change.
- A valid change updates the account's password, drops other sessions,
  and keeps the current session active.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import psycopg
import pytest

from conftest import csrf_from
from test_sign_in import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    browser,
    cookie_headers,
    seed_admin,
    sign_in,
)

pytestmark = pytest.mark.anonymous

NEW_PASSWORD = "updated-battery-staple"


def authed_client(db):
    seed_admin(db)
    client = browser()
    posted = sign_in(client)
    assert posted.status_code == 303, posted.text
    return client


def test_unauthenticated_request_to_password_redirects_to_sign_in(db):
    client = browser()
    resp = client.get("/password")
    assert resp.status_code == 303
    assert "/sign-in" in resp.headers["location"]
    assert "next=%2Fpassword" in resp.headers["location"] or "next=/password" in resp.headers["location"]


def test_authenticated_user_can_view_password_page(db):
    client = authed_client(db)
    resp = client.get("/password")
    assert resp.status_code == 200
    assert "Change password" in resp.text
    assert ADMIN_EMAIL in resp.text
    assert "current_password" in resp.text
    assert "new_password" in resp.text


def test_change_password_with_wrong_current_password_fails(db):
    client = authed_client(db)
    page = client.get("/password")
    token = csrf_from(page.text)

    posted = client.post("/password", data={
        "current_password": "wrong-password",
        "new_password": NEW_PASSWORD,
        "csrf_token": token,
    })
    assert posted.status_code == 200
    assert "Current password is incorrect" in posted.text

    # Verify old password still works and new password does not
    another = browser()
    failed = sign_in(another, password=NEW_PASSWORD)
    assert failed.status_code == 200
    success = sign_in(another, password=ADMIN_PASSWORD)
    assert success.status_code == 303


def test_change_password_with_empty_new_password_fails(db):
    client = authed_client(db)
    page = client.get("/password")
    token = csrf_from(page.text)

    posted = client.post("/password", data={
        "current_password": ADMIN_PASSWORD,
        "new_password": "",
        "csrf_token": token,
    })
    assert posted.status_code == 200
    assert "new password is required" in posted.text.lower()


def test_change_password_succeeds_and_allows_login_with_new_password(db):
    client = authed_client(db)
    page = client.get("/password")
    token = csrf_from(page.text)

    posted = client.post("/password", data={
        "current_password": ADMIN_PASSWORD,
        "new_password": NEW_PASSWORD,
        "csrf_token": token,
    })
    assert posted.status_code == 303
    assert "/password?saved=1" in posted.headers["location"]

    # Verify message on redirect
    view = client.get(posted.headers["location"])
    assert view.status_code == 200
    assert "Password changed." in view.text

    # Verify old password no longer works
    another = browser()
    old_failed = sign_in(another, password=ADMIN_PASSWORD)
    assert old_failed.status_code == 200

    # Verify new password works
    new_success = sign_in(another, password=NEW_PASSWORD)
    assert new_success.status_code == 303


def test_change_password_invalidates_other_sessions(db):
    seed_admin(db)
    client1 = browser()
    sign_in(client1)
    assert client1.get("/").status_code == 200

    client2 = browser()
    sign_in(client2)
    assert client2.get("/").status_code == 200

    # client1 changes password
    page = client1.get("/password")
    token = csrf_from(page.text)
    posted = client1.post("/password", data={
        "current_password": ADMIN_PASSWORD,
        "new_password": NEW_PASSWORD,
        "csrf_token": token,
    })
    assert posted.status_code == 303

    # client1 remains signed in
    assert client1.get("/").status_code == 200

    # client2 session was invalidated and gets redirected to sign-in
    resp2 = client2.get("/")
    assert resp2.status_code == 303
    assert "/sign-in" in resp2.headers["location"]
