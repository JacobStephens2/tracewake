"""Throwaway test databases on the local Postgres instance.

Shared by the Selector suite and the lab webapp (dashboard) suite: each test
gets its own freshly created database with schema.sql applied, and the
database is dropped afterwards, so tests exercise the real engine - triggers,
NOTIFY, peer auth over the unix socket - without ever touching the live
`selector` database. Prior art: the ETA Factory's disposable-database
Postgres suites.

Requires the connecting OS user to have a same-named Postgres role with
CREATEDB (the `conductor` role has it; see selector/README.md).

A run that is killed outright never reaches the `finally:` that drops, so the
harness also keeps the books on itself: `leaked()` names the databases this
process created and failed to drop, which both suites print at the end of a
run, and `sweep()` clears the residue of runs that are no longer around to be
asked. See #178, where 23 of them accumulated unnoticed because the only way
to learn the count was to go and look.
"""
from __future__ import annotations

import argparse
import contextlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")

# Every throwaway database is named from this, and the sweep matches on it.
# Nothing outside it is ever a candidate for dropping: the live `selector`
# Journal and #175's `selector_staging` both sit next to these on the same
# instance and share the first word of the name.
PREFIX = "selector_test_"

# The databases this process created and has not yet seen dropped. A set of
# names rather than a count of rows in pg_database, because two suites can run
# at once - and an operator can start one while another is mid-flight - so a
# count reports somebody else's live database as this run's leak. That is not
# hypothetical: the tally recorded on #178 read 25 when the leak was 23.
_created: set[str] = set()

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


def new_name() -> str:
    """A fresh throwaway database name."""
    return f"{PREFIX}{secrets.token_hex(4)}"


@contextlib.contextmanager
def throwaway_db():
    """Yield the DSN of a fresh schema-loaded database; drop it on exit."""
    name = new_name()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    _created.add(name)
    dsn = f"dbname={name}"
    # Held open for as long as the database exists, and not only to apply the
    # schema with. It is what makes a live throwaway database distinguishable
    # from an abandoned one: `sweep()` spares anything with a backend on it,
    # and without this connection there is routinely nothing attached between
    # one statement and the next - the test's own connections come and go, and
    # a test whose subject connects from a subprocess may have none at all for
    # most of its life. A concurrent run's database would then look exactly
    # like residue, and the sweep would take it out from under the run.
    keeper = psycopg.connect(dsn, autocommit=True)
    try:
        keeper.execute(SCHEMA.read_text())
        yield dsn
    finally:
        keeper.close()
        _drop(name)
        # Only on the way out of a drop that returned. A drop that raised
        # still raises, and leaves the name on the books for the summary to
        # report, which is the case worth hearing about.
        _created.discard(name)


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


# --- What this run leaked ---------------------------------------------------


def leaked() -> list[str]:
    """The databases this process created and did not manage to drop."""
    return sorted(_created)


def leak_report() -> str | None:
    """A summary line for the end of a test run, or None when nothing leaked.

    Reporting, not failing. A leaked database is real and worth saying out
    loud, but turning an otherwise green suite red at teardown is the habit
    #178 set out to break - it trains people to re-run rather than read. The
    drop raising is what fails a run; this is what makes the residue findable
    afterwards.
    """
    names = leaked()
    if not names:
        return None
    return (
        f"{len(names)} throwaway database(s) left behind by this run: "
        + ", ".join(names)
        + f"\nClear them with: {Path(__file__).name} --sweep"
    )


def pytest_terminal_summary(terminalreporter):
    """Say which throwaway databases this run failed to take away again.

    A pytest hook, living with the harness rather than in either suite's
    conftest, which import it by name. Both suites create databases through
    this module, so both need the report, and two copies of it would be two
    definitions of what a leak is - the same argument `append_run` is shared
    under. #178: the residue used to be discoverable only by an operator
    running a count against pg_database, which is to say by accident - the 23
    that prompted this were found while scoping #175.
    """
    report = leak_report()
    if report:
        terminalreporter.write_sep("=", "throwaway databases left behind", red=True)
        terminalreporter.write_line(report)


@dataclass
class SweepResult:
    """What a sweep did, and what it deliberately did not do."""

    dropped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(self.dropped)} dropped"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} in use, left alone: " + ", ".join(self.skipped))
        return "; ".join(parts)


def sweep(dry_run: bool = False) -> SweepResult:
    """Drop every throwaway database no session is attached to.

    For the residue of runs that are gone - killed with SIGKILL, or the box
    rebooted - which no `finally:` can reach and no in-process bookkeeping
    remembers. It cannot tell those apart from a concurrent suite's live
    database, so it reads the one difference there is: a backend on it. A
    database with a session attached is somebody's, and is skipped.

    The drop is deliberately without FORCE. FORCE is right in `_drop`, where
    the only backends that can be on the database are the test's own; here it
    would terminate a colleague's running suite. Without it, one that gains a
    session between the listing and the drop raises ObjectInUse, and is
    recorded as skipped rather than taken.
    """
    result = SweepResult()
    # `_` is a single-character wildcard in LIKE, and the prefix has two of
    # them; unescaped, the pattern would also match a database somebody named
    # `selectorXtestY...`.
    pattern = PREFIX.replace("_", r"\_") + "%"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        candidates = admin.execute(
            "SELECT d.datname FROM pg_database d"
            " WHERE d.datname LIKE %s AND NOT EXISTS ("
            "   SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)"
            " ORDER BY d.datname",
            (pattern,),
        ).fetchall()
        in_use = admin.execute(
            "SELECT DISTINCT datname FROM pg_stat_activity WHERE datname LIKE %s",
            (pattern,),
        ).fetchall()
        result.skipped = sorted(row[0] for row in in_use)
        for (name,) in candidates:
            if dry_run:
                result.dropped.append(name)
                continue
            try:
                admin.execute(f'DROP DATABASE "{name}"')
            except psycopg.errors.ObjectInUse:
                result.skipped.append(name)
            else:
                result.dropped.append(name)
                _created.discard(name)
    result.skipped = sorted(set(result.skipped))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sweep",
        action="store_true",
        help=f"drop every {PREFIX}* database no session is attached to",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --sweep, report without dropping"
    )
    args = parser.parse_args()
    if not args.sweep:
        parser.error("nothing to do; --sweep is the only action")
    outcome = sweep(dry_run=args.dry_run)
    print(("would drop: " if args.dry_run else "") + outcome.summary())
    for dropped in outcome.dropped:
        print(f"  {dropped}")
