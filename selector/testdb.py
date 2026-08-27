"""Throwaway test databases on the local Postgres instance.

Shared by the Selector suite and the lab webapp (dashboard) suite: each test
gets its own freshly created database with schema.sql applied, and the
database is dropped afterwards, so tests exercise the real engine - triggers,
NOTIFY, peer auth over the unix socket - without ever touching the live
`selector` database. Prior art: the ETA Factory's disposable-database
Postgres suites.

Requires the connecting OS user to have a same-named Postgres role with
CREATEDB (the `conductor` role has it; see selector/README.md).
"""
import contextlib
import secrets
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")

# The maintenance database every role may connect to; used only to create and
# drop the throwaway ones.
ADMIN_DSN = "dbname=postgres"


def available() -> bool:
    """True when the local Postgres accepts a peer-auth socket connection."""
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


def append_run(dsn: str, issue: int, *, outcome=None, hours_ago: int = 0) -> None:
    """Write a dispatch (and optionally its outcome) into a test Journal.

    Here rather than in either suite's conftest because both need it and the
    shape is the Journal's: the Selector suite seeds spend to drive the caps,
    and the dashboard suite seeds the same rows to drive the budget cell. Two
    copies would be two definitions of what a Run looks like in the Journal,
    and the page and the cap are supposed to be reading the same thing.

    Backdating needs an explicit `at`, which an INSERT may set and no UPDATE
    ever could - journal.events is append-only, so a test that wants history
    writes history rather than editing it.
    """
    rows = [("run.dispatched", {"issue": issue})]
    if outcome is not None:
        rows.append(("run.outcome", {"issue": issue, "outcome": outcome}))
    with psycopg.connect(dsn, autocommit=True) as conn:
        for kind, payload in rows:
            conn.execute(
                "INSERT INTO journal.events (at, kind, payload)"
                " VALUES (now() - make_interval(hours => %s), %s, %s)",
                (hours_ago, kind, Jsonb(payload)),
            )


@contextlib.contextmanager
def throwaway_db():
    """Yield the DSN of a fresh schema-loaded database; drop it on exit."""
    name = f"selector_test_{secrets.token_hex(4)}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        dsn = f"dbname={name}"
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(SCHEMA.read_text())
        yield dsn
    finally:
        _drop(name)


def _drop(name: str, attempts: int = 20) -> None:
    """Drop the throwaway database, waiting out a backend that has not quite
    exited.

    WITH (FORCE) terminates other sessions, but a connection that is already
    on its way out can still lose the race and leave the drop reporting
    "database is being accessed by other users". Rare, and reachable from any
    test whose subject connects from a subprocess (the Selector's cycle tests
    do), so it is retried here rather than left as an occasional red teardown
    that means nothing.

    The second retried error is subtler. FORCE terminates every backend on the
    database, and an autovacuum worker is a backend - one owned by a role this
    connection is not, so terminating it is refused outright ("permission
    denied to terminate process"). Nothing is wrong when that happens: the
    worker finishes on its own within a moment and the next attempt has
    nothing left to signal. It shows up on the tests that write enough rows in
    one statement to interest autovacuum, which is why it reads as a flake in
    a different file each time it appears.
    """
    transient = (psycopg.errors.ObjectInUse, psycopg.errors.InsufficientPrivilege)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for attempt in range(attempts):
            try:
                admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
                return
            except transient:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.1)
