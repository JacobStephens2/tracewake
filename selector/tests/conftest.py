import sys
from datetime import datetime, timedelta, timezone
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


# --- Canned tracker records -------------------------------------------------
#
# Shared by the cycle suite and the dispatch suite, which drive the same
# `cycle.py` through the same tracker seam and differ only in what they let it
# reach afterwards. One definition of "an eligible issue" so that a change to
# what Eligibility needs breaks both suites rather than one.

BODY = """## Problem

Something is wrong.

## Acceptance criteria

- [ ] It is right

## Owning area

The nightly sync script
"""


def hours_ago_iso(hours):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(number, **over):
    """One tracker record, eligible unless a field is overridden."""
    record = {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.invalid/{number}",
        "state": "OPEN",
        "body": BODY,
        "labeledBy": "JacobStephens2",
        "labeledAt": None,
        "blockedBy": 0,
        "openSubIssues": 0,
        "proposals": [],
    }
    record.update(over)
    return record
