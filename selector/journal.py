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
from typing import AsyncIterator

import psycopg
from psycopg.types.json import Jsonb

# The constants alone: this module's own reader is named `events`, so the
# vocabulary module cannot come in under its usual name.
from events import RUN_CONTRACT, RUN_DISPATCHED, RUN_ITERATION

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
        " WHERE kind = %s"
        "   AND payload->>'issue' = %s"
        "   AND payload->>'attempt' IS NOT DISTINCT FROM %s"
        "   AND payload->>'iteration' ~ '^[0-9]+$'",
        (RUN_ITERATION, str(issue),
         None if attempt is None else str(attempt)),
    ).fetchall()
    return {row[0] for row in rows}


def contract_seen(conn: psycopg.Connection, issue: int,
                  attempt: int | None) -> bool:
    """Whether this Run's Termination Contract is already journaled.

    The question `iterations_seen` answers, for the row there is exactly one
    of per Run: the watcher re-reads a cumulative log every minute, and a watch
    that started over - a restarted cycle, a retry on the same branch - must
    not append a second Contract for one Run. Scoped by attempt because a retry
    is its own Run and can be dispatched under different terms.

    Unbounded in time, exactly like `iterations_seen`, and it inherits that
    query's residual: `cycle.attempts` counts dispatches since the issue was
    last labeled, so a fresh Handover of an issue worked before is attempt 1
    again. Its rows then land on the earlier attempt's card - the page keys a
    card by issue and attempt - and this query finds that card's Contract and
    writes none. The card is already the wrong shape in that case; a Contract
    scoped differently from the Iterations beside it would make it wrong in a
    second, less visible way instead.
    """
    row = conn.execute(
        "SELECT 1 FROM journal.events"
        " WHERE kind = %s"
        "   AND payload->>'issue' = %s"
        "   AND payload->>'attempt' IS NOT DISTINCT FROM %s"
        " LIMIT 1",
        (RUN_CONTRACT, str(issue),
         None if attempt is None else str(attempt)),
    ).fetchone()
    return row is not None


def events(conn: psycopg.Connection, limit: int = 200,
           since: int | None = None,
           kinds: list[str] | None = None) -> list[dict]:
    """The most recent events, newest first.

    `since` reads every row from that id onward instead of taking the newest
    `limit`, and `kinds` narrows to the event kinds the caller builds from.
    Both exist for the Run history (#160), which counts in Runs rather than in
    rows: a Run's rows are interleaved with every cycle summary and skip
    written since, so a row cap is a Run cap of no fixed size - and the page
    whose purpose is that past Runs stay inspectable is the wrong one to
    silently forget them from. `run_window` picks the id; this reads from it.
    """
    where, params = [], []
    if since is not None:
        where.append("id >= %s")
        params.append(since)
    if kinds is not None:
        where.append("kind = ANY(%s)")
        params.append(list(kinds))
    sql = "SELECT id, at, kind, payload FROM journal.events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if since is None:
        # A floor and a cap together would be the cap winning, silently, once
        # the window held more rows than it - which is the failure `since`
        # exists to end.
        sql += " LIMIT %s"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [
        {"id": r[0], "at": r[1], "kind": r[2], "payload": r[3]} for r in rows
    ]


def event(conn: psycopg.Connection, event_id: int) -> dict | None:
    """One row by id, or None if there is no such row.

    The notifier's read (#280): a NOTIFY names a row by id only - its payload
    is capped at 8000 bytes - and what the notifier decides with is the whole
    payload. `events(since=...)` would answer with every row from there
    onward, which is a different question.
    """
    row = conn.execute(
        "SELECT id, at, kind, payload FROM journal.events WHERE id = %s",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "at": row[1], "kind": row[2], "payload": row[3]}


def run_window(conn: psycopg.Connection, runs: int) -> tuple[int | None, int]:
    """Where the most recent `runs` Runs start, and how many are older.

    Returns the id of the oldest of those Runs' `run.dispatched` rows - the
    floor to read `events` from - and the count of dispatches below it, so a
    page can own up to the Runs it is not showing rather than end at a silent
    edge. `(None, 0)` when the Journal holds no dispatch at all.

    Counted in dispatches because a dispatch is what a Run card is built
    around: every other row of a Run is written after it and therefore above
    it, so reading from this floor cannot cut a card in half.
    """
    rows = conn.execute(
        "SELECT id FROM journal.events WHERE kind = %s"
        " ORDER BY id DESC LIMIT %s",
        (RUN_DISPATCHED, runs),
    ).fetchall()
    if not rows:
        return None, 0
    floor = rows[-1][0]
    older = conn.execute(
        "SELECT count(*) FROM journal.events"
        " WHERE kind = %s AND id < %s",
        (RUN_DISPATCHED, floor),
    ).fetchone()[0]
    return floor, older


# --- Liveness ---------------------------------------------------------------
#
# The read half of the NOTIFY the schema's trigger already fires. It lives
# here rather than in the dashboard because it is Journal SQL - the channel
# name, and the re-read of the row a notification only names by id - and the
# dashboard is the Journal's window, not a second definition of it (ADR 0015).
# Nothing in the Selector calls it: v1 pushes to a page and triggers no work.

# How long a listener waits before saying something anyway. A stream silent
# for minutes is indistinguishable from a dead one, and an idle connection
# through a proxy is eventually reaped. Overridable in the same shape as
# `dsn()` - default and env read in one place, so there is one answer to
# "where does this come from".
_DEFAULT_KEEPALIVE_SECONDS = 20.0

# How many rows one read fetches. A cap rather than a limit on what is
# delivered: `_drain` pages until a short batch says it is done, because a
# replay that stopped at the cap would leave a reconnecting page stale with
# nothing left to wake it - the exact failure the replay exists to prevent.
_BATCH = 500


def keepalive_seconds() -> float:
    return float(
        os.environ.get("SELECTOR_SSE_KEEPALIVE_SECONDS")
        or _DEFAULT_KEEPALIVE_SECONDS
    )


async def listen(after: int | None = None, to: str | None = None,
                 keepalive: float | None = None) -> AsyncIterator[tuple]:
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
    if keepalive is None:
        keepalive = keepalive_seconds()
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
            async for row in _drain(conn, after):
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
            async for row in _drain(conn, sent):
                sent = max(sent, row["id"])
                yield "event", row
    finally:
        await conn.close()


async def _drain(conn: psycopg.AsyncConnection,
                 after: int) -> AsyncIterator[dict]:
    """Every row newer than `after`, in batches, oldest first.

    Paged rather than one capped read, because the caller has no way to ask
    for the rest: a replay truncated at the cap leaves a reconnected page
    holding a stale id with no later NOTIFY coming to correct it.
    """
    while True:
        rows = await _since(conn, after, limit=_BATCH)
        for row in rows:
            after = row["id"]
            yield row
        if len(rows) < _BATCH:
            return


async def _since(conn: psycopg.AsyncConnection, after: int,
                 limit: int = _BATCH) -> list[dict]:
    """Rows newer than `after`, oldest first - the order they happened in."""
    cursor = await conn.execute(
        "SELECT id, kind FROM journal.events WHERE id > %s"
        " ORDER BY id LIMIT %s",
        (after, limit),
    )
    return [{"id": r[0], "kind": r[1]} for r in await cursor.fetchall()]
