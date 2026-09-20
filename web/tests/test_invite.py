"""An admin invites a Dashboard Account (issue #41).

HTTP-level, against a throwaway database, with the mail command captured:
an admin's invite mails a single-use link, the invitee sets a password once,
and a reader cannot reach the account-management surface.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

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

INVITEE_EMAIL = "reader@example.com"
INVITEE_PASSWORD = "invitee-sets-this-once"
ROLE_ACTION = re.compile(
    r'action="(/accounts/(\d+)/role)"',
    re.IGNORECASE,
)
DEACTIVATE_ACTION = re.compile(
    r'action="(/accounts/(\d+)/deactivate)"',
    re.IGNORECASE,
)


@pytest.fixture
def mailbox(tmp_path, monkeypatch):
    """The dashboard's mail surface, scripted: one log of argv plus stdin."""
    delivered = tmp_path / "delivered.log"
    command = tmp_path / "mail.sh"
    command.write_text(
        "#!/usr/bin/env bash\n"
        "{\n"
        '  printf -- "--- to: %s\\n" "$1"\n'
        '  printf -- "--- subject: %s\\n" "$2"\n'
        '  printf -- "--- link: %s\\n" "${3:-}"\n'
        "  cat\n"
        '  printf -- "\\n--- end ---\\n"\n'
        f'}} >> "{delivered}"\n'
    )
    command.chmod(0o755)
    monkeypatch.setenv("WINDOW_MAIL_COMMAND", str(command))

    class Box:
        def text(self) -> str:
            return delivered.read_text() if delivered.exists() else ""

        def invocations(self) -> int:
            return self.text().count("--- end ---")

        def to(self) -> str:
            for line in self.text().splitlines():
                if line.startswith("--- to: "):
                    return line[len("--- to: "):]
            return ""

        def link(self) -> str:
            for line in self.text().splitlines():
                if line.startswith("--- link: "):
                    return line[len("--- link: "):]
            return ""

    return Box()


def admin_browser(db) -> TestClient:
    seed_admin(db)
    client = browser()
    posted = sign_in(client)
    assert posted.status_code == 303, posted.text
    return client


def invite(client: TestClient, *, email=INVITEE_EMAIL, role="reader"):
    page = client.get("/accounts")
    assert page.status_code == 200, page.text
    return client.post("/accounts", data={
        "email": email,
        "role": role,
        "csrf_token": csrf_from(page.text),
    })


def redeem(client: TestClient, link: str, password: str = INVITEE_PASSWORD):
    path = urlparse(link).path
    page = client.get(path)
    assert page.status_code == 200, page.text
    token = csrf_from(page.text) if "csrf_token" in page.text else ""
    return client.post(path, data={
        "password": password,
        "csrf_token": token,
    })


def account_id_for(page_text: str, email: str) -> str:
    """The account-management row for `email`, identified by its role form."""
    for match in ROLE_ACTION.finditer(page_text):
        # The email sits on the same row as the action; take the enclosing
        # element rather than guessing at table markup.
        start = max(0, match.start() - 400)
        chunk = page_text[start:match.end() + 200]
        if email in chunk:
            return match.group(2)
    raise AssertionError(f"no role form for {email} in {page_text}")


# --- Invite and mail -------------------------------------------------------


def test_the_accounts_page_says_dashboard_account_not_window_account(db):
    admin = admin_browser(db)
    page = admin.get("/accounts")
    assert page.status_code == 200, page.text
    assert "Invite a Dashboard Account to this control panel by email" in page.text
    assert "Dashboard accounts" in page.text
    assert "Window Account" not in page.text
    assert "Window accounts" not in page.text


def test_an_admin_invite_mails_exactly_one_usable_link(db, mailbox):
    admin = admin_browser(db)
    posted = invite(admin)
    assert posted.status_code == 303, posted.text
    assert mailbox.invocations() == 1
    assert mailbox.to() == INVITEE_EMAIL
    delivered = mailbox.text()
    assert "--- subject: You're invited to this Tracewake dashboard" in delivered
    assert "You have been invited to this Tracewake dashboard as reader." in delivered
    assert "window" not in delivered.lower()
    link = mailbox.link()
    assert "/invite/" in link

    invitee = browser()
    path = urlparse(link).path
    landing = invitee.get(path)
    assert landing.status_code == 200, landing.text
    assert "password" in landing.text.lower()


def test_redeeming_the_link_sets_a_password_once(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    link = mailbox.link()

    invitee = browser()
    first = redeem(invitee, link)
    assert first.status_code == 303, first.text
    landed = invitee.get(first.headers["location"])
    assert landed.status_code == 200

    second = redeem(browser(), link, password="a-different-password")
    assert second.status_code != 303
    assert "not valid" in second.text.lower()
    # The first password is the one that works; the second write did not.
    again = sign_in(browser(), email=INVITEE_EMAIL, password=INVITEE_PASSWORD)
    assert again.status_code == 303
    stolen = sign_in(browser(), email=INVITEE_EMAIL, password="a-different-password")
    assert stolen.status_code == 200
    assert "email or password" in stolen.text.lower()


def test_an_expired_invite_is_refused(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    link = mailbox.link()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE web.account_tokens"
            " SET expires_at = now() - interval '1 hour'"
        )
    refused = redeem(browser(), link)
    assert refused.status_code != 303
    signed = sign_in(browser(), email=INVITEE_EMAIL, password=INVITEE_PASSWORD)
    assert signed.status_code == 200
    assert "email or password" in signed.text.lower()


def test_an_invited_never_activated_account_cannot_sign_in(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    posted = sign_in(browser(), email=INVITEE_EMAIL, password=INVITEE_PASSWORD)
    assert posted.status_code == 200
    assert "email or password" in posted.text.lower()


def test_invite_tokens_are_never_stored_recoverably(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    token = urlparse(mailbox.link()).path.rsplit("/", 1)[-1]
    assert token
    with psycopg.connect(db) as conn:
        stored = [row[0] for row in conn.execute(
            "SELECT token_hash FROM web.account_tokens"
        )]
    assert stored
    assert token not in stored
    assert all(len(h) == 64 and re.fullmatch(r"[0-9a-f]+", h) for h in stored)


# --- Role and deactivation -------------------------------------------------


def test_the_invited_role_is_the_sign_in_role_and_an_admin_can_change_it(
    db, mailbox,
):
    admin = admin_browser(db)
    invite(admin, role="reader")
    redeemed = redeem(browser(), mailbox.link())
    assert redeemed.status_code == 303

    reader = browser()
    signed = sign_in(
        reader, email=INVITEE_EMAIL, password=INVITEE_PASSWORD,
    )
    assert signed.status_code == 303
    denied = reader.get("/accounts")
    assert denied.status_code == 403

    page = admin.get("/accounts")
    account_id = account_id_for(page.text, INVITEE_EMAIL)
    changed = admin.post(f"/accounts/{account_id}/role", data={
        "role": "admin",
        "csrf_token": csrf_from(page.text),
    })
    assert changed.status_code == 303, changed.text
    allowed = reader.get("/accounts")
    assert allowed.status_code == 200
    assert "invite" in allowed.text.lower()


def test_deactivating_an_account_ends_its_sessions_and_blocks_sign_in(
    db, mailbox,
):
    admin = admin_browser(db)
    invite(admin)
    redeemed = redeem(browser(), mailbox.link())
    assert redeemed.status_code == 303
    header = cookie_headers(redeemed)
    token = re.search(r"(?:__Host-)?session=([^;]+)", header).group(1)
    name = header.split("=", 1)[0].strip()

    live = browser()
    live.cookies.set(name, token)
    assert live.get("/").status_code == 200

    page = admin.get("/accounts")
    account_id = account_id_for(page.text, INVITEE_EMAIL)
    ended = admin.post(f"/accounts/{account_id}/deactivate", data={
        "csrf_token": csrf_from(page.text),
    })
    assert ended.status_code == 303, ended.text
    assert live.get("/").status_code == 303
    blocked = sign_in(
        browser(), email=INVITEE_EMAIL, password=INVITEE_PASSWORD,
    )
    assert blocked.status_code == 200
    assert "email or password" in blocked.text.lower()


def test_a_reader_cannot_reach_the_account_management_surface(db, mailbox):
    admin = admin_browser(db)
    invite(admin, role="reader")
    assert redeem(browser(), mailbox.link()).status_code == 303

    reader = browser()
    assert sign_in(
        reader, email=INVITEE_EMAIL, password=INVITEE_PASSWORD,
    ).status_code == 303
    assert reader.get("/accounts").status_code == 403
    posted = reader.post("/accounts", data={
        "email": "someone-else@example.com",
        "role": "reader",
        "csrf_token": "not-checked-if-forbidden",
    })
    assert posted.status_code == 403
    assert mailbox.invocations() == 1
    listing = admin.get("/accounts")
    assert listing.status_code == 200
    assert "someone-else@example.com" not in listing.text
    board = reader.get("/")
    assert board.status_code == 200
    assert 'href="/accounts"' not in board.text


def test_an_unset_mail_command_is_refused_by_name(db):
    admin = admin_browser(db)
    page = admin.get("/accounts")
    posted = admin.post("/accounts", data={
        "email": INVITEE_EMAIL,
        "role": "reader",
        "csrf_token": csrf_from(page.text),
    })
    assert posted.status_code == 200, posted.text
    assert "WINDOW_MAIL_COMMAND" in posted.text
    shown = unescape(posted.text)
    assert "the dashboard's mail surface" in shown
    assert "the window's mail surface" not in shown
    listing = admin.get("/accounts")
    assert INVITEE_EMAIL not in listing.text


def test_deactivating_an_unredeemed_invite_refuses_the_link(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    page = admin.get("/accounts")
    account_id = account_id_for(page.text, INVITEE_EMAIL)
    assert admin.post(f"/accounts/{account_id}/deactivate", data={
        "csrf_token": csrf_from(page.text),
    }).status_code == 303
    refused = redeem(browser(), mailbox.link())
    assert refused.status_code != 303
    assert "not valid" in refused.text.lower()
    signed = sign_in(browser(), email=INVITEE_EMAIL, password=INVITEE_PASSWORD)
    assert signed.status_code == 200
    assert "email or password" in signed.text.lower()
