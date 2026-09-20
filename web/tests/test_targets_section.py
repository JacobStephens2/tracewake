"""The Targets section on `/`: which repositories this instance reads issues from.

The queue board columns every target, each card naming its repository. This
section is the instance's own list: every `[[target]]` in the targets file, in
file order, because that is what a Cycle reads. An admin may enroll or
unenroll from the window; a reader may not.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import csrf_from
from test_roles import post_control, signed_in_reader
import fixtures
import targets

client = TestClient(app)


def targets_section(body: str) -> str:
    """The Targets section alone, cut out of the page.

    Sliced rather than searched whole: a cycle card or a raw Journal dump can
    name a repository without this section having rendered at all. Nested
    <details> (the extra add fields) are walked rather than matched by the
    first closing tag, which would cut the form in half.
    """
    start = re.search(r'<details class="fold targets"[^>]*>', body)
    assert start, "no targets section on the page"
    i = start.end()
    depth = 1
    while depth:
        next_open = body.find("<details", i)
        next_close = body.find("</details>", i)
        assert next_close != -1, "unclosed targets section"
        if next_open != -1 and next_open < next_close:
            depth += 1
            i = next_open + len("<details")
        else:
            depth -= 1
            if depth == 0:
                return body[start.end():next_close]
            i = next_close + len("</details>")
    raise AssertionError("unclosed targets section")


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


ADD_FIELDS = {
    "repo": "acme/gamma",
    "work_repo": "/nonexistent/work/gamma",
    "box_repo": "/nonexistent/box/gamma",
    "token_file": "/nonexistent/token/gamma",
    "guest_template": "gamma-guest:1",
    "labeler_allowlist": "an-operator",
}


def test_an_admin_is_offered_an_add_form(db):
    section = targets_section(client.get("/").text)
    assert 'hx-post="/loop/targets/add"' in section
    assert ">Add Target</button>" in section
    for name in ADD_FIELDS:
        assert f'name="{name}"' in section


def test_the_add_form_arrives_filled_from_the_last_target(
    db, monkeypatch, tmp_path
):
    """A second Target is a repository name, not six fields to retype.

    guest_template and the allowlist are instance facts; the path
    fields show the last Target's layout with `name` standing in for
    the short name the operator is about to type.
    """
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {
                "repo": "acme/alpha",
                "work_repo": "/nonexistent/work/alpha",
                "box_repo": "/nonexistent/box/alpha",
                "token_file": "/nonexistent/token/alpha.token",
                "guest_template": "shared-guest:1",
                "labeler_allowlist": ["an-operator"],
            },
        ),
    )
    section = targets_section(client.get("/").text)
    assert 'value="shared-guest:1"' in section
    assert 'value="an-operator"' in section
    assert 'placeholder="/nonexistent/work/name"' in section
    assert 'placeholder="/nonexistent/box/name"' in section
    assert 'placeholder="/nonexistent/token/name.token"' in section


def test_adding_with_only_the_repository_fills_the_rest_from_the_last_target(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {
                "repo": "acme/alpha",
                "work_repo": "/nonexistent/work/alpha",
                "box_repo": "/nonexistent/box/alpha",
                "token_file": "/nonexistent/token/alpha.token",
                "guest_template": "shared-guest:1",
            },
        ),
    )
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/targets/add",
        data={"csrf_token": token, "repo": "acme/gamma"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") == "/"
    section = targets_section(client.get("/").text)
    assert "acme/alpha" in section
    assert "acme/gamma" in section
    loaded = targets.load()
    gamma = next(t for t in loaded if t.repo == "acme/gamma")
    assert str(gamma.work_repo) == "/nonexistent/work/gamma"
    assert gamma.box_repo == "/nonexistent/box/gamma"
    assert gamma.token_file == "/nonexistent/token/gamma.token"
    assert gamma.guest_template == "shared-guest:1"
    assert gamma.labeler_allowlist == ("an-operator",)


@pytest.mark.anonymous
def test_a_reader_is_not_offered_an_add_form(db):
    section = targets_section(signed_in_reader(db).get("/").text)
    assert "Add Target" not in section
    assert "/loop/targets/add" not in section


def test_adding_a_target_from_the_window_enrolls_it(
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
        "/loop/targets/add",
        data={"csrf_token": token, **ADD_FIELDS},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") == "/"
    section = targets_section(client.get("/").text)
    assert "acme/alpha" in section
    assert "acme/gamma" in section


def test_adding_the_first_target_from_an_unconfigured_instance(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        str(tmp_path / "missing.toml"),
    )
    section = targets_section(client.get("/").text)
    assert 'hx-post="/loop/targets/add"' in section
    token = csrf_from(client.get("/").text)
    resp = client.post(
        "/loop/targets/add",
        data={"csrf_token": token, **ADD_FIELDS},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") == "/"
    section = targets_section(client.get("/").text)
    assert "acme/gamma" in section
    assert "could not be read" not in section


def test_a_failed_add_opens_the_targets_section(
    db, monkeypatch, tmp_path
):
    """The error is the reason the section is on screen.

    A first visit keeps Targets shut so the queue is the page. A
    refusal has to be readable without opening the fold by hand, so
    the fragment that carries the error arrives open.
    """
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    token = csrf_from(client.get("/").text)
    incomplete = dict(ADD_FIELDS, token_file="")
    resp = client.post(
        "/loop/targets/add",
        data={"csrf_token": token, **incomplete},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert re.search(
        r'<details class="fold targets"[^>]*\sopen[\s>]',
        resp.text,
    )


def test_adding_a_stanza_short_of_a_required_value_keeps_the_list(
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
    incomplete = dict(ADD_FIELDS, token_file="")
    resp = client.post(
        "/loop/targets/add",
        data={"csrf_token": token, **incomplete},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("hx-redirect") is None
    section = targets_section(resp.text)
    assert "<code>acme/alpha</code>" in section
    assert "<code>acme/gamma</code>" not in section
    assert "token_file" in section
    assert 'value="acme/gamma"' in section


@pytest.mark.anonymous
def test_a_readers_direct_add_is_refused(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/alpha"},
        ),
    )
    reader = signed_in_reader(db)
    refused = post_control(reader, "/loop/targets/add")
    assert refused.status_code == 403
    assert "acme/alpha" in targets_section(reader.get("/").text)
    assert "acme/gamma" not in targets_section(reader.get("/").text)
