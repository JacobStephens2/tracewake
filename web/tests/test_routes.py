"""Top-level routes for the window: /, /history, /events, /adr.

Parent spec:
The window. / is the board; /history, /adr, /events as today; the pipeline demo
and /demo/run are removed.
"""
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_top_level_routes_render():
    assert client.get("/").status_code == 200
    assert client.get("/history").status_code == 200
    assert client.get("/adr").status_code == 200


def test_loop_is_a_redirect_to_home():
    """Issue #53: the Dashboard's home is `/`; `/loop` is a leftover path."""
    resp = client.get("/loop", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/"


def test_loop_history_alias_still_works():
    assert client.get("/loop/history").status_code == 200


def test_loop_redirect_keeps_a_path_prefix():
    """Same property the asset and fragment tests pin: under a prefix the
    browser must be sent to this instance's home, not the root's."""
    prefixed = TestClient(app, root_path="/loop-staging")
    resp = prefixed.get("/loop", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/loop-staging/"


def test_events_route_is_configured():
    route_paths = {route.path for route in app.routes}
    assert "/events" in route_paths
    assert "/loop/events" in route_paths

