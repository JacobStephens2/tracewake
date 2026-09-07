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


def test_loop_aliases_still_work():
    assert client.get("/loop").status_code == 200
    assert client.get("/loop/history").status_code == 200


def test_events_route_is_configured():
    route_paths = {route.path for route in app.routes}
    assert "/events" in route_paths
    assert "/loop/events" in route_paths

