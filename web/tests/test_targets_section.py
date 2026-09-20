"""The Targets section on `/`: which repositories this instance reads issues from.

The queue board still columns one target (the first stanza) until `/` grows a
switcher. This section is the instance's own list: every `[[target]]` in the
targets file, in file order, because that is what a Cycle reads. An admin
may unenroll one from the window; a reader may not.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import csrf_from
from test_roles import post_control, signed_in_reader
import fixtures

client = TestClient(app)


def targets_section(body: str) -> str:
    """The Targets section alone, cut out of the page.

    Sliced rather than searched whole: a cycle card or a raw Journal dump can
    name a repository without this section having rendered at all.
    """
    match = re.search(
        r'<details class="fold targets"[^>]*>(.*?)</details>',
        body,
        re.DOTALL,
    )
    assert match, "no targets section on the page"
    return match.group(1)


def test_the_home_page_names_the_configured_target(db):
    section = targets_section(client.get("/").text)
    assert "acme/widgets" in section


def test_every_declared_target_is_listed_in_file_order(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
            {"repo": "acme/beta"},
        ),
    )
    section = targets_section(client.get("/").text)
    assert section.index("acme/alpha") < section.index("acme/beta")
    assert "acme/widgets" not in section


def test_an_unconfigured_instance_does_not_invent_a_target(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        str(tmp_path / "missing.toml"),
    )
    resp = client.get("/")
    assert resp.status_code == 200
    section = targets_section(resp.text)
    assert "acme/widgets" not in section
    assert "could not be read" in section


def test_an_admin_is_offered_a_remove_for_each_target(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
            {"repo": "acme/beta"},
        ),
    )
    section = targets_section(client.get("/").text)
    assert section.count(">Remove</button>") == 2
    assert 'hx-post="/loop/targets/remove"' in section
    assert "acme/alpha" in section
    assert "acme/beta" in section


@pytest.mark.anonymous
def test_a_reader_is_not_offered_a_remove(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    section = targets_section(signed_in_reader(db).get("/").text)
    assert "Remove" not in section
    assert "/loop/targets/remove" not in section
    assert "acme/alpha" in section


def test_removing_a_target_from_the_window_unenrolls_it(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
            {"repo": "acme/beta"},
        ),
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/targets/remove",
        data={"csrf_token": token, "repo": "acme/alpha"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") == "/"
    section = targets_section(client.get("/").text)
    assert "acme/alpha" not in section
    assert "acme/beta" in section


def test_removing_a_repository_that_is_not_declared_keeps_the_list(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/targets/remove",
        data={"csrf_token": token, "repo": "acme/nothing"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") is None
    section = targets_section(resp.text)
    assert "acme/alpha" in section
    assert "no target declares" in section
    assert section.count(">Remove</button>") == 1


def test_removing_the_last_target_shows_the_instance_as_unconfigured(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/targets/remove",
        data={"csrf_token": token, "repo": "acme/alpha"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") == "/"
    section = targets_section(client.get("/").text)
    assert "acme/alpha" not in section
    assert "[[target]]" in section


@pytest.mark.anonymous
def test_a_readers_direct_remove_is_refused(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    reader = signed_in_reader(db)
    refused = post_control(reader, "/loop/targets/remove")
    assert refused.status_code == 403
    assert "acme/alpha" in targets_section(reader.get("/").text)
