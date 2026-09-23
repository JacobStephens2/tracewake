"""Every content tree the window mounts must exist, or the app does not boot.

Merging #69 deleted site/ but left it in the mount loop in app.py, so a
fresh checkout crashed at import and the public origin answered 502.
Importing app above is itself the boot assertion; this pins the pattern,
so a future tree removal has to update the mounts rather than only delete
the directory.
"""
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app import app


def test_every_mounted_content_tree_exists():
    mounts = [
        route for route in app.routes
        if isinstance(getattr(route, "app", None), StaticFiles)
    ]
    assert mounts, "the window mounts no static content trees"
    for route in mounts:
        directory = Path(route.app.directory)
        assert directory.is_dir(), (
            f"mount {route.path} points at {directory}, which does not exist"
        )
