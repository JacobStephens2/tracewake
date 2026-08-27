"""The Journal at its seam: real local Postgres, throwaway database per test.

Nothing here reads Selector internals - only the journal module's public
surface and the schema's observable behavior (issue #152's acceptance
criteria)."""
import psycopg
import pytest

import journal


def test_appended_event_fires_notify(db):
    """An append reaches a LISTENing connection as a NOTIFY carrying the id."""
    with journal.connect(db) as listener:
        listener.execute(f"LISTEN {journal.CHANNEL}")
        with journal.connect(db) as writer:
            event_id = journal.append(writer, "test.ping", {"n": 1})
        got = list(listener.notifies(timeout=5, stop_after=1))
    assert len(got) == 1
    assert got[0].channel == journal.CHANNEL
    assert got[0].payload == str(event_id)


def test_hand_appended_insert_also_notifies(db):
    """NOTIFY lives in the schema, so a bare SQL INSERT (psql by hand) fires
    it too - not only journal.append."""
    with journal.connect(db) as listener:
        listener.execute(f"LISTEN {journal.CHANNEL}")
        with journal.connect(db) as other:
            other.execute(
                "INSERT INTO journal.events (kind, payload)"
                " VALUES ('test.hand', '{}'::jsonb)"
            )
        got = list(listener.notifies(timeout=5, stop_after=1))
    assert len(got) == 1
    assert got[0].channel == journal.CHANNEL


def test_writer_surface_is_append_only():
    """The module's own callables are exactly connect, append, and read paths -
    any new mutator (update, delete, purge...) fails this allowlist."""
    own = {
        name
        for name, obj in vars(journal).items()
        if callable(obj) and getattr(obj, "__module__", None) == "journal"
    }
    assert own == {
        "dsn", "connect", "append",
        # Reads, all of them. `listen` and `_since` are the push side (#159):
        # they hold a LISTEN and re-read the rows a notification names, and
        # they write nothing - a Journal that could be changed by something
        # watching it would not be a Journal.
        "events", "iterations_seen",
        "keepalive_seconds", "listen", "_drain", "_since",
    }


def test_schema_blocks_update_delete_truncate(db):
    """Append-only is enforced in the schema, not just by writer discipline."""
    with journal.connect(db) as conn:
        event_id = journal.append(conn, "test.guard", {})
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute(
                "UPDATE journal.events SET kind = 'x' WHERE id = %s", (event_id,)
            )
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM journal.events WHERE id = %s", (event_id,))
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("TRUNCATE journal.events")
        rows = journal.events(conn)
        assert [e["id"] for e in rows] == [event_id]
        assert rows[0]["kind"] == "test.guard"


def test_events_read_newest_first(db):
    with journal.connect(db) as conn:
        first = journal.append(conn, "test.one", {"seq": 1})
        second = journal.append(conn, "test.two", {"seq": 2})
        rows = journal.events(conn)
    assert [e["id"] for e in rows] == [second, first]
    assert rows[0]["kind"] == "test.two"
    assert rows[0]["payload"] == {"seq": 2}
    assert rows[0]["at"] is not None


def test_events_respects_limit(db):
    with journal.connect(db) as conn:
        for n in range(5):
            journal.append(conn, "test.fill", {"n": n})
        rows = journal.events(conn, limit=2)
    assert len(rows) == 2
    assert rows[0]["payload"] == {"n": 4}
