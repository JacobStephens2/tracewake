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
    """
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for attempt in range(attempts):
            try:
                admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
                return
            except psycopg.errors.ObjectInUse:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.1)
