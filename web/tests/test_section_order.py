"""A Dashboard Account can reorder the headed sections on `/` (#147).

The default list puts The queue below Targets, the status strip, the Host,
and MicroVMs. The choice is stored on the account, not a cookie, so a
second browser and a live-region swap both come back the way this account
left them, and another account still sees the default.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import journal
from app import app
from conftest import csrf_from
from test_roles import signed_in_admin, signed_in_reader
from test_section_folds import folds_on

client = TestClient(app)

DEFAULT_ORDER = [
    "targets",
    "strip",
    "host",
    "microvms",
    "queue",
    "runs",
    "cycles",
    "events",
]


def fold_keys(body: str) -> list[str]:
    """Headed sections in document order."""
    return list(folds_on(body))


def move(browser: TestClient, key: str, direction: str, body: str | None = None):
    if body is None:
        body = browser.get("/").text
    return browser.post(
        "/loop/sections/move",
        data={
            "csrf_token": csrf_from(body),
            "key": key,
            "direction": direction,
        },
        follow_redirects=False,
    )


def test_the_queue_can_be_moved_to_the_top_of_the_home_page(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    home = client.get("/").text
    assert fold_keys(home) == DEFAULT_ORDER
    assert "Rearrange sections" in home

    moved = home
    for _ in range(DEFAULT_ORDER.index("queue")):
        resp = move(client, "queue", "up", moved)
        assert resp.status_code == 303, resp.text
        moved = client.get("/").text

    assert fold_keys(moved)[0] == "queue"
    assert fold_keys(moved) == [
        "queue",
        "targets",
        "strip",
        "host",
        "microvms",
        "runs",
        "cycles",
        "events",
    ]
    assert fold_keys(client.get("/").text)[0] == "queue"
    assert fold_keys(client.get("/loop/live").text)[0] == "queue"


def test_the_rearrange_control_stays_on_the_shell(db):
    """A Journal swap must not shut the list while someone is moving things."""
    home = client.get("/").text
    live = client.get("/loop/live").text
    assert "Rearrange sections" in home
    assert "Rearrange sections" not in live


@pytest.mark.anonymous
def test_another_dashboard_account_keeps_the_default_order(db, dispatch):
    """The order is on the account, not the instance (#147)."""
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    admin = signed_in_admin(db)
    for _ in range(DEFAULT_ORDER.index("queue")):
        resp = move(admin, "queue", "up")
        assert resp.status_code == 303, resp.text
    assert fold_keys(admin.get("/").text)[0] == "queue"

    reader = signed_in_reader(db)
    assert fold_keys(reader.get("/").text) == DEFAULT_ORDER


@pytest.mark.anonymous
def test_a_reader_can_rearrange_their_own_view(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")

    admin = signed_in_admin(db)
    reader = signed_in_reader(db)
    resp = move(reader, "queue", "up")
    assert resp.status_code == 303, resp.text
    keys = fold_keys(reader.get("/").text)
    assert keys.index("queue") < keys.index("microvms")
    assert fold_keys(admin.get("/").text) == DEFAULT_ORDER


def test_a_partial_saved_order_keeps_the_rest_in_default_sequence(db, dispatch):
    """Unknown keys are dropped; omitted sections append in the markup default."""
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
        account_id = conn.execute(
            "SELECT id FROM web.accounts WHERE email = %s",
            ("operator@example.com",),
        ).fetchone()[0]
    dispatch(db, 646, outcome="complete")

    auth.save_section_order(account_id, ["queue", "unknown", "queue", "strip"])
    assert fold_keys(client.get("/").text) == [
        "queue",
        "strip",
        "targets",
        "host",
        "microvms",
        "runs",
        "cycles",
        "events",
    ]


def test_moving_an_unknown_section_leaves_the_order_alone(db, dispatch):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {"dry_run": False})
    dispatch(db, 646, outcome="complete")
    before = fold_keys(client.get("/").text)
    resp = move(client, "not-a-section", "up")
    assert resp.status_code == 303, resp.text
    assert fold_keys(client.get("/").text) == before


def test_a_move_without_csrf_is_refused(db):
    resp = client.post(
        "/loop/sections/move",
        data={"key": "queue", "direction": "up"},
    )
    assert resp.status_code == 403
