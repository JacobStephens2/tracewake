"""The demonstration route and its fragment are gone, leaving no unreachable template (AC 2).

Issue #13:
The pipeline demonstration that was the front page belongs to a factory this
product deliberately does not build, and it goes; what remains is the board, the
history, the decisions and the stream.
"""
from pathlib import Path
import re

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)

WEB_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = WEB_DIR / "templates"
APP_FILE = WEB_DIR / "app.py"


def test_demonstration_route_is_gone():
    resp = client.post("/demo/run")
    assert resp.status_code == 404


def test_demonstration_fragment_is_gone():
    assert not (TEMPLATES_DIR / "_run.html").exists(), (
        "_run.html must be removed"
    )


def test_old_demo_front_page_is_gone():
    assert not (TEMPLATES_DIR / "index.html").exists(), (
        "index.html demonstration template must be removed"
    )


def test_no_unreachable_templates():
    """Every template in web/templates must be reachable from app routes or other reachable templates."""
    all_templates = {p.name for p in TEMPLATES_DIR.glob("*.html")}
    assert all_templates, "No templates found in web/templates"

    app_source = APP_FILE.read_text()
    # Templates directly rendered in app.py
    rendered_in_app = set(re.findall(r'["\']([a-zA-Z0-9_\-]+\.html)["\']', app_source)) & all_templates

    assert rendered_in_app, "No templates directly rendered in app.py"

    # Find reachability closure via {% include "..." %} and {% extends "..." %}
    include_re = re.compile(r'{%[-+]?\s*(?:include|extends)\s*["\']([a-zA-Z0-9_\-]+\.html)["\']')

    reachable = set(rendered_in_app)
    queue = list(rendered_in_app)
    while queue:
        current = queue.pop(0)
        content = (TEMPLATES_DIR / current).read_text()
        for target in include_re.findall(content):
            if target in all_templates and target not in reachable:
                reachable.add(target)
                queue.append(target)

    unreachable = all_templates - reachable
    assert not unreachable, f"Found unreachable templates: {unreachable}"
