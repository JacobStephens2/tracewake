"""The throwaway-database harness's own two guarantees (#178).

Every other suite here trusts `throwaway_db()` to take its database away
again. That trust was misplaced once already: 23 `selector_test_*` databases
were found on the box in 2026-08, left by a drop that lost a race, and the
count was discoverable only by an operator going and looking. The drop itself
is fixed. What is tested here is the part that makes the next one loud - the
harness knows what it created and did not manage to drop - and the sweep for
the residue a killed run leaves behind, which no `finally:` can ever cover.

Note that this suite really does sweep the box: the sweep's contract is every
orphan on the instance, so the test of it is the real thing rather than a
scoped imitation, and running this file clears whatever residue was lying
around. That is safe by construction - the prefix and the no-session rule are
themselves asserted below - and it is how the original 23 went.
"""
import contextlib
import subprocess
import sys
import textwrap

import psycopg
import pytest

import testdb


@pytest.fixture(autouse=True)
def _postgres_or_skip():
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")


@pytest.fixture
def orphan():
    """A throwaway-shaped database nothing is tracking, as a killed run leaves.

    Created straight through the admin connection rather than through
    `throwaway_db()`, because the whole point of the ones it stands in for is
    that no live process has them on its books.
    """
    name = testdb.new_name()
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    yield name
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _exists(name: str) -> bool:
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        row = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
    return row is not None


# --- What this process leaked ----------------------------------------------


def test_a_database_that_was_dropped_is_not_reported_as_leaked():
    with testdb.throwaway_db() as dsn:
        assert dsn.startswith(f"dbname={testdb.PREFIX}")
    assert testdb.leaked() == []
    assert testdb.leak_report() is None


def test_a_database_whose_drop_failed_is_reported_by_name(monkeypatch):
    """The drop still raises - a red teardown is the loudest signal there is -
    but the name survives the raise, so the summary can say which one."""
    monkeypatch.setattr(testdb, "_drop", _refuse_to_drop)
    with pytest.raises(RuntimeError):
        with testdb.throwaway_db() as dsn:
            name = dsn.removeprefix("dbname=")

    assert testdb.leaked() == [name]
    report = testdb.leak_report()
    assert name in report
    assert "--sweep" in report, "the report should say how to clear it"

    testdb._created.discard(name)
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def _refuse_to_drop(name, attempts=20):
    raise RuntimeError("drop refused")


@contextlib.contextmanager
def _a_concurrent_run():
    """A second process inside `throwaway_db()`, as a colleague's suite is.

    It holds the database the way a real run does and no other way - no
    connection of its own open across the wait - because that is the state
    that made the first version of the sweep wrong: between one statement and
    the next, a live throwaway database had nothing attached to it and was
    indistinguishable from residue.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_A_DATABASE_OPEN],
        cwd=str(testdb.SCHEMA.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        name = child.stdout.readline().strip()
        assert name.startswith(testdb.PREFIX), f"child said {name!r}"
        assert _exists(name)
        yield name
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    assert not _exists(name), "the child dropped its own database on the way out"


def test_a_database_another_process_is_using_is_not_reported_as_leaked():
    """The trap this check exists to avoid.

    Counting `selector_test_%` rows in `pg_database` at session end reports a
    concurrent suite's in-flight database as this run's leak. It is not a
    hypothetical: the count recorded on #178 read 25 when the leak was 23,
    because two were live at the time. So the assertion is both halves - the
    bare count sees the child's database, and `leaked()` does not.
    """
    with _a_concurrent_run() as name:
        assert name in _bare_count_names(), "the child's database is live"
        assert testdb.leaked() == []


def test_sweep_spares_a_database_a_concurrent_run_is_using():
    """The sweep is destructive and runs against the whole instance, so the
    one thing it must never do is take a database out from under a suite that
    is still using it.

    This is a regression test with a scar. The first version of the sweep
    asked `pg_stat_activity` whether anything was attached, and a live
    throwaway database answered "nothing" - the harness closed the connection
    it applied the schema with before handing the DSN over. The sweep would
    have dropped a colleague's database mid-run. The harness now holds one
    connection open for the database's lifetime, which is what makes the
    question the sweep asks a question worth asking.
    """
    with _a_concurrent_run() as name:
        assert name not in testdb.sweep(dry_run=True).dropped, "a dry run would take it"
        result = testdb.sweep()
        assert name in result.skipped
        assert name not in result.dropped
        assert _exists(name), "the sweep dropped a live run's database"


_HOLD_A_DATABASE_OPEN = textwrap.dedent(
    """
    import sys
    import testdb

    with testdb.throwaway_db() as dsn:
        print(dsn.removeprefix("dbname="), flush=True)
        sys.stdin.read()
    """
)


def _bare_count_names():
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        return [
            r[0]
            for r in admin.execute(
                r"SELECT datname FROM pg_database WHERE datname LIKE 'selector\_test\_%'"
            ).fetchall()
        ]


# --- The sweep --------------------------------------------------------------


def test_sweep_drops_an_orphan_no_session_is_using(orphan):
    assert _exists(orphan)
    result = testdb.sweep()
    assert orphan in result.dropped
    assert not _exists(orphan)


def test_a_dry_sweep_reports_without_dropping(orphan):
    result = testdb.sweep(dry_run=True)
    assert orphan in result.dropped
    assert _exists(orphan), "a dry run must leave the database alone"


def test_sweep_leaves_a_database_with_a_session_attached_alone(orphan):
    """A concurrent suite's database looks exactly like an orphan. The
    difference is the backend on it, so that is what the sweep reads - and it
    drops without FORCE, so one that attaches mid-sweep is skipped rather than
    terminated."""
    with psycopg.connect(f"dbname={orphan}", autocommit=True):
        result = testdb.sweep()
        assert orphan in result.skipped
        assert orphan not in result.dropped
        assert _exists(orphan)


def test_sweep_never_touches_a_database_outside_the_throwaway_prefix():
    """`selector` is the live Journal and `selector_staging` is #175's preview.
    Neither is a throwaway, and a sweep that took one would be the worst
    outcome this issue could produce."""
    before = _all_database_names()
    testdb.sweep()
    after = _all_database_names()
    assert [n for n in before - after if not n.startswith("selector_test_")] == []
    assert "selector" in after


def _all_database_names():
    with psycopg.connect(testdb.ADMIN_DSN, autocommit=True) as admin:
        return {r[0] for r in admin.execute("SELECT datname FROM pg_database").fetchall()}


# --- The report reaching a run's output -------------------------------------


def test_a_leak_is_named_in_the_output_of_the_run_that_leaked_it(tmp_path):
    """End to end through a real pytest run, because `leak_report()` returning
    a string and an operator seeing it are two different claims, and it is the
    second one #178 asks for. The child imports this suite's own
    `pytest_terminal_summary` rather than a copy of it, so the hook under test
    is the hook that ships.
    """
    here = testdb.SCHEMA.parent
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent(
            f"""
            import importlib.util
            import sys

            sys.path.insert(0, {str(here)!r})
            spec = importlib.util.spec_from_file_location(
                "selector_conftest", {str(here / "tests" / "conftest.py")!r}
            )
            real = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(real)
            pytest_terminal_summary = real.pytest_terminal_summary
            """
        )
    )
    (tmp_path / "test_leak.py").write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(here)!r})
            import testdb

            def test_that_leaks():
                testdb._created.add("selector_test_deadbeef")
            """
        )
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert "throwaway databases left behind" in run.stdout
    assert "selector_test_deadbeef" in run.stdout
    assert "--sweep" in run.stdout
    assert run.returncode == 0, "a leak is reported, not turned into a failure"
