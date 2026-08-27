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


@pytest.fixture
def dispatch():
    """Runs in the Journal, as the Selector would have written them.

    `testdb.append_run`, the same helper the Selector's own suite seeds spend
    with. The page's budget cell and the Selector's cap read the same rows, so
    a second definition of what those rows look like would be a way for the
    two to drift apart in the one place they must not.
    """
    return testdb.append_run
