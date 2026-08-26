# The Selector — Journal first

The Selector (issue #151's spec; vocabulary in `../CONTEXT.md`) will drain
tourbot's `ready-for-agent` queue unattended. This directory is its home.
What exists so far is the **Selector Journal** (ADR 0015) and its shared test
plumbing — issue #152:

- `schema.sql` — one append-only `journal.events` table in the local
  Postgres. NOTIFY on insert and the append-only guard both live in schema
  triggers, so every append path behaves the same, including a hand `psql`
  INSERT. Idempotent; re-apply freely.
- `journal.py` — the whole write surface (`append`) plus the reader
  (`events`, newest first). No update, no delete. `SELECTOR_JOURNAL_DSN`
  overrides the DSN (tests do); default is `dbname=selector`, peer auth over
  the unix socket, zero credentials.
- `testdb.py` — the shared throwaway-test-database harness: create a random
  database, apply `schema.sql`, drop it afterwards. Used by this suite and by
  `lab/webapp/tests/` (the dashboard side). Needs a Postgres role matching
  the OS user with CREATEDB.
- `tests/` — the Journal at its seam against the real engine: NOTIFY asserted
  by a live listener, mutation blocked by the guard triggers, ordering.

The window is `lab/webapp`'s `/loop` page, which imports `journal.py` from
here and renders at request time (SSE via LISTEN/NOTIFY is issue #159).

## Host setup

`ansible/roles/selector_journal` (in `site.yml`) owns it: PostgreSQL 16,
socket-only (`listen_addresses = ''`), a peer-auth `conductor` role with
CREATEDB, the `selector` database, schema applied. A rebuilt VM gets all of
it back from the playbook.

## Running the tests

```bash
cd lab/single-user-factory/selector
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

Tests skip (not fail) when the local Postgres is unreachable.

## Inspecting the live Journal

```bash
psql -d selector -c "SELECT id, at, kind, payload FROM journal.events ORDER BY id DESC LIMIT 20;"
```

Appending by hand is legitimate (the schema NOTIFYs either way); updating or
deleting raises `journal.events is append-only` unless you deliberately drop
the guard triggers first.
