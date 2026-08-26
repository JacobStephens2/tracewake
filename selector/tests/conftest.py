import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import pytest
from psycopg.types.json import Jsonb

import testdb


@pytest.fixture
def db():
    """DSN of a throwaway schema-loaded database on the local Postgres."""
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")
    with testdb.throwaway_db() as dsn:
        yield dsn


@pytest.fixture
def dispatch():
    """Append a dispatch (and optionally its outcome) to a test Journal.

    Backdating needs an explicit `at`, which an INSERT may set and no UPDATE
    ever could - the table is append-only, so a test that wants history writes
    history rather than editing it.
    """
    def append(dsn, number, *, outcome=None, hours_ago=0):
        with psycopg.connect(dsn, autocommit=True) as conn:
            for kind, payload in (
                ("run.dispatched", {"issue": number}),
                ("run.outcome", {"issue": number, "outcome": outcome}),
            ):
                if kind == "run.outcome" and outcome is None:
                    continue
                conn.execute(
                    "INSERT INTO journal.events (at, kind, payload)"
                    " VALUES (now() - make_interval(hours => %s), %s, %s)",
                    (hours_ago, kind, Jsonb(payload)),
                )
    return append
