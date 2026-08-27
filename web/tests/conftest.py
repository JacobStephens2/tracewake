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
def dispatch(db):
    """Runs in the Journal, as the Selector would have written them.

    Backdating needs an explicit `at`, which an INSERT may set and no UPDATE
    ever could - journal.events is append-only, so a test that wants history
    writes history rather than editing it.
    """
    import psycopg
    from psycopg.types.json import Jsonb

    def append(dsn, number, *, outcome=None, hours_ago=0):
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO journal.events (at, kind, payload)"
                " VALUES (now() - make_interval(hours => %s), %s, %s)",
                (hours_ago, "run.dispatched", Jsonb({"issue": number})),
            )
            if outcome is not None:
                conn.execute(
                    "INSERT INTO journal.events (at, kind, payload)"
                    " VALUES (now() - make_interval(hours => %s), %s, %s)",
                    (hours_ago, "run.outcome",
                     Jsonb({"issue": number, "outcome": outcome})),
                )
    return append
