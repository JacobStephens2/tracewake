"""The Targets section on `/`: which repositories this instance reads issues from.

The queue board still columns one target (the first stanza) until `/` grows a
switcher. This section is the instance's own list: every `[[target]]` in the
targets file, in file order, because that is what a Cycle reads.
"""
import re

from fastapi.testclient import TestClient

from app import app
import fixtures

client = TestClient(app)


def targets_section(body: str) -> str:
    """The Targets section alone, cut out of the page.

    Sliced rather than searched whole: a cycle card or a raw Journal dump can
    name a repository without this section having rendered at all.
    """
    match = re.search(
        r'<aside class="targets"[^>]*>(.*?)</aside>',
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
