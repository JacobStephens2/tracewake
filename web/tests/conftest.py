import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]  # lab/webapp
sys.path.insert(0, str(BASE))
# The shared throwaway-test-database harness lives with the Journal it tests.
sys.path.insert(0, str(BASE.parent / "single-user-factory" / "selector"))

import pytest

import testdb


@pytest.fixture
def db(monkeypatch):
    """A throwaway Journal database, wired into the app via the DSN env var."""
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")
    with testdb.throwaway_db() as dsn:
        monkeypatch.setenv("SELECTOR_JOURNAL_DSN", dsn)
        yield dsn
