"""The Selector Journal: append-only writer and request-time reader.

ADR 0015: the Journal is a table in the local Postgres (`selector` database),
reached by peer auth over the unix socket - zero credentials anywhere. Every
insert fires NOTIFY on CHANNEL via a schema trigger, so a write can reach the
dashboard as a push; in v1 NOTIFY drives SSE only, and nothing that decides
work reads this module - the tracker remains the only work source.

This is the whole write surface: append. There is no update and no delete
here, and schema.sql's guard triggers hold the same line inside the database.
"""
from __future__ import annotations

import os

import psycopg
from psycopg.types.json import Jsonb

CHANNEL = "journal_events"

# One convention, worth stating because it looks like an inconsistency: an
# event ABOUT AN ISSUE (`issue.skipped`, `issue.returned`) carries `number`,
# and an event about a RUN (`run.dispatched`, `run.outcome`) carries `issue` -
# the run is the subject there and the issue is which one it is for. A reader
# querying for one and finding rows of the other kind empty has met this and
# not a bug.

_DEFAULT_DSN = "dbname=selector"


def dsn() -> str:
    """The live Journal's DSN; SELECTOR_JOURNAL_DSN overrides (tests point it
    at a throwaway database)."""
    return os.environ.get("SELECTOR_JOURNAL_DSN", _DEFAULT_DSN)


def connect(to: str | None = None) -> psycopg.Connection:
    """Autocommit so appends NOTIFY at statement end and LISTEN needs no
    explicit commit."""
    return psycopg.connect(to or dsn(), autocommit=True)


def append(conn: psycopg.Connection, kind: str, payload: dict | None = None) -> int:
    """Append one event, returning its id. The insert itself fires NOTIFY."""
    row = conn.execute(
        "INSERT INTO journal.events (kind, payload) VALUES (%s, %s) RETURNING id",
        (kind, Jsonb(payload or {})),
    ).fetchone()
    return row[0]


def events(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    """The most recent events, newest first."""
    rows = conn.execute(
        "SELECT id, at, kind, payload FROM journal.events"
        " ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        {"id": r[0], "at": r[1], "kind": r[2], "payload": r[3]} for r in rows
    ]
