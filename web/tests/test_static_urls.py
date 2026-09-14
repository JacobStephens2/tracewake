"""Static assets are addressed through a root_path-aware base, not a hardcoded path.

Deliberately not `url_for`: Starlette's renders an absolute URL from the
request's own base, and uvicorn here runs without `--proxy-headers`, so behind
Caddy's TLS that base is `http://` and every stylesheet on an https page would
be blocked as mixed content. `_static_base` keeps the property a hardcoded
path lacks without inventing a scheme.

ADR 0016 rejects serving a preview under a path prefix on the live host, so
nothing today depends on this. It is still a latent bug: four templates
hardcoded `/static/...`, which means the browser asks for `/static/...`
whatever prefix the app is mounted under, and a prefixed deployment would
serve one instance's HTML with another's CSS. Silently, and looking fine.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_no_template_hardcodes_a_static_path():
    offenders = [
        p.name
        for p in TEMPLATES.glob("*.html")
        if re.search(r'(href|src)="/static/', p.read_text())
    ]
    assert offenders == []


def test_every_asset_a_page_asks_for_is_actually_served():
    """The other half: `url_for` with a name the mount does not have renders
    an error, and a test that only banned the literal would pass on a page
    that had stopped loading its CSS."""
    for page in ["/", "/adr", "/history"]:
        body = client.get(page).text
        assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', body)
        assert assets, f"{page} references no static asset at all"
        for asset in assets:
            assert client.get(asset).status_code == 200, f"{page} -> {asset}"


def test_asset_urls_carry_no_scheme_or_host():
    """The trap `url_for` walks into. Starlette's renders an absolute URL from
    the request's own base, and uvicorn here is started without
    `--proxy-headers`, so behind Caddy's TLS that base is `http://` and every
    stylesheet on an https page is blocked as mixed content."""
    for page in ["/", "/adr", "/history"]:
        body = client.get(page).text
        assert "http://testserver/static" not in body, page
        assert re.search(r'(?:href|src)="https?://[^"]*/static/', body) is None, page


def test_a_prefixed_mount_asks_for_its_own_assets():
    """The property a hardcoded `/static/` lacks: under a path prefix the
    browser must ask for `<prefix>/static/...`, not the root's."""
    from fastapi.testclient import TestClient as _TestClient

    prefixed = _TestClient(app, root_path="/loop-staging")
    body = prefixed.get("/").text
    assert '"/loop-staging/static/terminal.css"' in body
