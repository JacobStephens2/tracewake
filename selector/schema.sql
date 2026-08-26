-- The Selector Journal (ADR 0015): one append-only events table in the local
-- Postgres, peer auth over the unix socket. Idempotent: safe to re-apply to
-- the live `selector` database and applied fresh to every throwaway test
-- database by testdb.py.

CREATE SCHEMA IF NOT EXISTS journal;

CREATE TABLE IF NOT EXISTS journal.events (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    kind    text        NOT NULL,
    payload jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- NOTIFY on insert lives in the schema, not the writer, so every append path
-- (journal.py, a hand psql INSERT) reaches the dashboard the same way. The
-- payload is the event id: 8000-byte NOTIFY limit never binds, listeners
-- re-read the row.
CREATE OR REPLACE FUNCTION journal.notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('journal_events', NEW.id::text);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_notify ON journal.events;
CREATE TRIGGER events_notify
    AFTER INSERT ON journal.events
    FOR EACH ROW EXECUTE FUNCTION journal.notify_event();

-- Append-only is discipline in the writer and a hard stop here: no role,
-- including the table owner, can UPDATE, DELETE, or TRUNCATE without first
-- deliberately dropping these triggers.
CREATE OR REPLACE FUNCTION journal.forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'journal.events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_append_only ON journal.events;
CREATE TRIGGER events_append_only
    BEFORE UPDATE OR DELETE ON journal.events
    FOR EACH ROW EXECUTE FUNCTION journal.forbid_mutation();

DROP TRIGGER IF EXISTS events_no_truncate ON journal.events;
CREATE TRIGGER events_no_truncate
    BEFORE TRUNCATE ON journal.events
    FOR EACH STATEMENT EXECUTE FUNCTION journal.forbid_mutation();
