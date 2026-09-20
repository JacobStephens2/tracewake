"""Forgot password (issue #43).

HTTP-level, against a throwaway database, with the mail command captured:
an activated account's request mails a single-use hour-long link; a
never-activated account's request sends nothing; the form's visible
response is the same either way.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import psycopg
import pytest

from conftest import csrf_from
from test_invite import (
    INVITEE_EMAIL,
    admin_browser,
    invite,
    mailbox,
)
from test_sign_in import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    browser,
    cookie_headers,
    seed_admin,
    sign_in,
)

pytestmark = pytest.mark.anonymous

NEW_PASSWORD = "new-horse-battery"
UNKNOWN_EMAIL = "nobody@example.com"


def request_reset(client, email):
    page = client.get("/forgot")
    assert page.status_code == 200, page.text
    return client.post("/forgot", data={
        "email": email,
        "csrf_token": csrf_from(page.text),
    })


def redeem_reset(client, link, password=NEW_PASSWORD):
    path = urlparse(link).path
    page = client.get(path)
    assert page.status_code == 200, page.text
    token = csrf_from(page.text) if "csrf_token" in page.text else ""
    return client.post(path, data={
        "password": password,
        "csrf_token": token,
    })


def last_link(mailbox) -> str:
    links = [
        line[len("--- link: "):]
        for line in mailbox.text().splitlines()
        if line.startswith("--- link: ")
    ]
    assert links, mailbox.text()
    return links[-1]


def last_to(mailbox) -> str:
    recipients = [
        line[len("--- to: "):]
        for line in mailbox.text().splitlines()
        if line.startswith("--- to: ")
    ]
    assert recipients, mailbox.text()
    return recipients[-1]


# --- Request and mail ------------------------------------------------------


def test_an_activated_account_reset_mails_exactly_one_usable_link(db, mailbox):
    seed_admin(db)
    posted = request_reset(browser(), ADMIN_EMAIL)
    assert posted.status_code == 200, posted.text
    assert mailbox.invocations() == 1
    assert last_to(mailbox) == ADMIN_EMAIL
    delivered = mailbox.text()
    assert "--- subject: Reset your Tracewake dashboard password" in delivered
    assert "A password reset was requested for this Tracewake dashboard." in delivered
    assert "window" not in delivered.lower()
    link = last_link(mailbox)
    assert "/reset/" in link

    visitor = browser()
    landing = visitor.get(urlparse(link).path)
    assert landing.status_code == 200, landing.text
    assert "password" in landing.text.lower()


def test_redeeming_the_link_sets_a_password_once(db, mailbox):
    seed_admin(db)
    request_reset(browser(), ADMIN_EMAIL)
    link = last_link(mailbox)

    first = redeem_reset(browser(), link)
    assert first.status_code == 303, first.text
    landed = browser()
    signed = sign_in(landed, password=NEW_PASSWORD)
    assert signed.status_code == 303
    assert landed.get("/").status_code == 200

    second = redeem_reset(browser(), link, password="a-different-password")
    assert second.status_code != 303
    assert "not valid" in second.text.lower()
    stolen = sign_in(browser(), password="a-different-password")
    assert stolen.status_code == 200
    assert "email or password" in stolen.text.lower()
    old = sign_in(browser(), password=ADMIN_PASSWORD)
    assert old.status_code == 200
    assert "email or password" in old.text.lower()


def test_an_expired_reset_is_refused(db, mailbox):
    seed_admin(db)
    request_reset(browser(), ADMIN_EMAIL)
    link = last_link(mailbox)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE web.account_tokens"
            " SET expires_at = now() - interval '1 hour'"
            " WHERE purpose = 'reset'"
        )
    refused = redeem_reset(browser(), link)
    assert refused.status_code != 303
    signed = sign_in(browser(), password=NEW_PASSWORD)
    assert signed.status_code == 200
    assert "email or password" in signed.text.lower()
    still = sign_in(browser(), password=ADMIN_PASSWORD)
    assert still.status_code == 303


def test_a_never_activated_account_request_sends_nothing(db, mailbox):
    admin = admin_browser(db)
    invite(admin)
    assert mailbox.invocations() == 1
    posted = request_reset(browser(), INVITEE_EMAIL)
    assert posted.status_code == 200, posted.text
    assert mailbox.invocations() == 1


def test_the_form_response_is_identical_for_known_unknown_and_never_activated(
    db, mailbox,
):
    admin = admin_browser(db)
    invite(admin)
    visitor = browser()
    known = request_reset(visitor, ADMIN_EMAIL)
    unknown = request_reset(visitor, UNKNOWN_EMAIL)
    never = request_reset(visitor, INVITEE_EMAIL)
    assert known.status_code == 200
    assert unknown.status_code == 200
    assert never.status_code == 200
    assert known.text == unknown.text == never.text
    body = known.text.lower()
    assert "unknown" not in body
    assert "no such" not in body
    assert "not found" not in body
    assert ADMIN_EMAIL not in known.text
    assert UNKNOWN_EMAIL not in known.text
    assert INVITEE_EMAIL not in known.text


def test_a_completed_reset_ends_the_accounts_other_sessions(db, mailbox):
    seed_admin(db)
    live = browser()
    posted = sign_in(live)
    assert posted.status_code == 303
    header = cookie_headers(posted)
    token = re.search(r"(?:__Host-)?session=([^;]+)", header).group(1)
    name = header.split("=", 1)[0].strip()
    assert live.get("/").status_code == 200

    request_reset(browser(), ADMIN_EMAIL)
    redeemed = redeem_reset(browser(), last_link(mailbox))
    assert redeemed.status_code == 303, redeemed.text
    assert live.get("/").status_code == 303
    leftover = browser()
    leftover.cookies.set(name, token)
    assert leftover.get("/").status_code == 303


def test_reset_tokens_are_never_stored_recoverably(db, mailbox):
    seed_admin(db)
    request_reset(browser(), ADMIN_EMAIL)
    token = urlparse(last_link(mailbox)).path.rsplit("/", 1)[-1]
    assert token
    with psycopg.connect(db) as conn:
        stored = [row[0] for row in conn.execute(
            "SELECT token_hash FROM web.account_tokens WHERE purpose = 'reset'"
        )]
    assert stored
    assert token not in stored
    assert all(len(h) == 64 and re.fullmatch(r"[0-9a-f]+", h) for h in stored)
