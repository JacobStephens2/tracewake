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
        "events", "iterations_seen", "contract_seen", "run_window",
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


# --- Reading in Runs rather than in rows (#160) -----------------------------
#
# The Run history counts in Runs. A row cap is a Run cap of no fixed size,
# because a Run's rows are interleaved with every cycle summary and skip
# written since - so the page whose purpose is that past Runs stay inspectable
# needs a floor rather than a limit, and needs to know what is below it.


def test_events_read_from_a_floor_ignore_the_limit(db):
    """A floor with a cap under it is the cap winning silently, which is the
    failure the floor exists to end."""
    with journal.connect(db) as conn:
        first = journal.append(conn, "test.fill", {"n": 0})
        for n in range(1, 250):
            journal.append(conn, "test.fill", {"n": n})
        rows = journal.events(conn, since=first)
    assert len(rows) == 250


def test_events_can_be_narrowed_to_the_kinds_a_caller_builds_from(db):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {})
        wanted = journal.append(conn, "run.dispatched", {"issue": 1})
        rows = journal.events(conn, kinds=["run.dispatched"])
    assert [e["id"] for e in rows] == [wanted]


def test_the_run_window_starts_at_its_oldest_run(db):
    """The floor is the oldest of the newest N dispatches, not the newest: a
    Run's other rows are written after its dispatch and so sit above it, and a
    floor one Run too high cuts the oldest card in half."""
    with journal.connect(db) as conn:
        ids = [journal.append(conn, "run.dispatched", {"issue": n})
               for n in range(4)]
        journal.append(conn, "run.outcome", {"issue": 3})
        floor, older = journal.run_window(conn, 2)
    assert floor == ids[2]
    assert older == 2


def test_a_window_wider_than_the_journal_leaves_nothing_behind(db):
    with journal.connect(db) as conn:
        ids = [journal.append(conn, "run.dispatched", {"issue": n})
               for n in range(5)]
        assert journal.run_window(conn, 10) == (ids[0], 0)


def test_a_journal_with_no_run_has_no_window(db):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.started", {})
        assert journal.run_window(conn, 5) == (None, 0)
