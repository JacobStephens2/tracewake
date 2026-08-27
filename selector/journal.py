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


def iterations_seen(conn: psycopg.Connection, issue: int,
                    attempt: int | None) -> set[int]:
    """Which of a Run's Iterations are already journaled.

    The watcher (#157) re-reads a cumulative Progress Log every minute, so
    what it has already recorded has to come from somewhere that outlives the
    process - a second watcher for the same Run, after a cycle was restarted,
    must not append the same Iteration twice. Scoped by attempt because a
    retry is its own Run with its own Iteration 1.
    """
    rows = conn.execute(
        "SELECT DISTINCT (payload->>'iteration')::int FROM journal.events"
        " WHERE kind = 'run.iteration'"
        "   AND payload->>'issue' = %s"
        "   AND payload->>'attempt' IS NOT DISTINCT FROM %s"
        "   AND payload->>'iteration' ~ '^[0-9]+$'",
        (str(issue), None if attempt is None else str(attempt)),
    ).fetchall()
    return {row[0] for row in rows}


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


# --- Liveness ---------------------------------------------------------------
#
# The read half of the NOTIFY the schema's trigger already fires. It lives
# here rather than in the dashboard because it is Journal SQL - the channel
# name, and the re-read of the row a notification only names by id - and the
# dashboard is the Journal's window, not a second definition of it (ADR 0015).
# Nothing in the Selector calls it: v1 pushes to a page and triggers no work.

# How long a listener waits before saying something anyway. A stream silent
# for minutes is indistinguishable from a dead one, and an idle connection
# through a proxy is eventually reaped.
KEEPALIVE_SECONDS = 20.0


async def listen(after: int | None = None, to: str | None = None,
                 keepalive: float = KEEPALIVE_SECONDS):
    """Yield `(what, payload)` as rows land: the Journal, pushed.

    `what` is one of:

    - `ready`   - the LISTEN is established; payload names the newest row id
                  (None on an empty Journal). Emitted before anything else so
                  a caller knows from when it is covered. Without it a client
                  that acted the moment it had a connection could write a row
                  before the LISTEN existed and then wait forever to hear
                  about it.
    - `event`   - one row: `{"id": ..., "kind": ...}`.
    - `silence` - `keepalive` seconds passed with nothing to say.

    `after` replays the rows written since that id before going live, which is
    what makes a dropped connection recoverable: those rows fired their NOTIFY
    into a socket nobody was holding, and after the last row of a Run there is
    no later row to bring the reader up to date.

    Async because its caller is a web request that must not hold a thread for
    the hours a page stays open; the Selector's own paths stay synchronous and
    do not call this.
    """
    conn = await psycopg.AsyncConnection.connect(to or dsn(), autocommit=True)
    try:
        await conn.execute(f"LISTEN {CHANNEL}")
        cursor = await conn.execute("SELECT max(id) FROM journal.events")
        newest = (await cursor.fetchone())[0]
        yield "ready", {"newest": newest}
        # Everything up to `newest` is either replayed below or already known
        # to the caller, so a notification naming any of it is a duplicate -
        # and there can be one, because psycopg keeps a backlog and a row
        # inserted during the query above is in it.
        sent = newest or 0
        if after is not None:
            for row in await _since(conn, after):
                sent = max(sent, row["id"])
                yield "event", row
        while True:
            fired = False
            # `stop_after=1` rather than draining the generator, because the
            # rows are read below on this same connection and `notifies()`
            # holds its lock for as long as it is being iterated: a query
            # issued from inside the loop deadlocks, silently, forever. One
            # notification is enough anyway - what is read is "every row since
            # the last one sent", so a burst of ten arrives in one read and
            # the nine remaining notifications find nothing left to fetch.
            async for _note in conn.notifies(timeout=keepalive, stop_after=1):
                fired = True
            if not fired:
                yield "silence", None
                continue
            # Re-read rather than trust the notification: NOTIFY carries only
            # the id (its payload is capped at 8000 bytes), and the kind is
            # what a reader decides with.
            for row in await _since(conn, sent):
                sent = max(sent, row["id"])
                yield "event", row
    finally:
        await conn.close()


async def _since(conn, after: int, limit: int = 500) -> list[dict]:
    """Rows newer than `after`, oldest first - the order they happened in."""
    cursor = await conn.execute(
        "SELECT id, kind FROM journal.events WHERE id > %s"
        " ORDER BY id LIMIT %s",
        (after, limit),
    )
    return [{"id": r[0], "kind": r[1]} for r in await cursor.fetchall()]
