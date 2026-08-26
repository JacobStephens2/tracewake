# The Selector

The Selector (issue #151's spec; vocabulary in `../CONTEXT.md`) drains
tourbot's `ready-for-agent` queue unattended. This directory is its home.

What exists so far is **the cycle in dry-run** (issue #153) on top of the
**Selector Journal** (ADR 0015, issue #152). A cycle reasons all the way to a
pick and journals the reasoning; it does not yet dispatch - Seeding, the Run
branch, the SSH start and the issue bookkeeping are issue #154.

## The cycle

```bash
cd lab/single-user-factory/selector
.venv/bin/python cycle.py --dry-run
```

One cycle reads the labeled queue through the tracker command, applies
Eligibility to every issue in it, orders what survives lowest-first, applies
the caps, and appends the whole of that reasoning to the Journal. `--dry-run`
is currently required: a cycle asked to dispatch refuses rather than picking
and silently stopping.

**Eligibility** (ADR 0014). An issue is skipped with the first of these that
holds, and the reason is the string journaled with it:

| reason | what it means |
| --- | --- |
| `labeler-not-allowlisted` | the most recent `ready-for-agent` labeling on the timeline was not by an allowlisted operator - the Handover is the label, so the labeler is who is trusted |
| `blocked-by-open-dependency` | native tracker edges report open blockers. Prose "Blocked by" text is deliberately not read |
| `has-open-sub-issues` | a parent spec is not a unit of work |
| `proposal-open` | an open pull request closes it: the issue is in flight |
| `attempts-exhausted` | already dispatched `MAX_ATTEMPTS` times (one automatic retry) |
| `missing-section` | the label promises `Acceptance criteria` and `Owning area` sections and one is absent or empty |

The order is not arbitrary: `missing-section` is the loud skip - #154 comments
on the issue and swaps it to `needs-info` - so the cheap, quiet reasons are
tested first. An issue that is blocked anyway is not shouted at for a gap.

**Caps.** One Run in flight, `SELECTOR_DAILY_CAP` (4) dispatches per rolling
24 hours. Rolling rather than calendar: "four a day" is a spend bound, and a
calendar boundary would let eight Runs happen inside three hours across
midnight. A cap that halts a cycle still lets it reason and journal first, so
the Journal answers "what would it have picked?" as well as "what did it?".

**Configuration**, all environment, all with working defaults:

| variable | default |
| --- | --- |
| `SELECTOR_TRACKER_COMMAND` | `tracker-sources/github.sh` |
| `SELECTOR_TASK_REPO` | `Educational-Travel-Adventures/tourbot` |
| `SELECTOR_LABEL` | `ready-for-agent` |
| `SELECTOR_LABELER_ALLOWLIST` | `JacobStephens2` (comma-separated) |
| `SELECTOR_DAILY_CAP` | `4` |
| `SELECTOR_JOURNAL_DSN` | `dbname=selector` |

Widening the allowlist is one entry here plus a note in ADR 0014, which is
what story 35 asks for.

The first live dry-run is written up in
`../notes/selector-first-dry-run-evidence.md`, including what it found: no
tourbot issue yet carries the `Owning area` section the label started
promising on 2026-08-26, so the queue has operator work in it before #154 can
dispatch anything.

## Files

- `cycle.py` - the cycle above: Eligibility, ordering, caps, and the
  journaling of every decision. Deterministic code, never an agent (ADR 0014).
- `tracker-sources/github.sh` - the default `SELECTOR_TRACKER_COMMAND`: one
  `gh api graphql` call normalized to a flat record per issue (labeler,
  native blocker count, open sub-issues, open Proposals). Substitutable, and
  the seam the offline suite drives.
- `schema.sql` - one append-only `journal.events` table in the local
  Postgres. NOTIFY on insert and the append-only guard both live in schema
  triggers, so every append path behaves the same, including a hand `psql`
  INSERT. Idempotent; re-apply freely.
- `journal.py` - the whole write surface (`append`) plus the reader
  (`events`, newest first). No update, no delete. `SELECTOR_JOURNAL_DSN`
  overrides the DSN (tests do); default is `dbname=selector`, peer auth over
  the unix socket, zero credentials.
- `testdb.py` - the shared throwaway-test-database harness: create a random
  database, apply `schema.sql`, drop it afterwards. Used by this suite and by
  `lab/webapp/tests/` (the dashboard side). Needs a Postgres role matching
  the OS user with CREATEDB.
- `tests/` - the two seams. `test_journal.py` is the Journal against the real
  engine: NOTIFY asserted by a live listener, mutation blocked by the guard
  triggers, ordering. `test_cycle.py` runs the real `cycle.py` as a
  subprocess against canned queue states, reading only the commands it issued
  and the rows it wrote - plus a tripwire PATH (`gh`, `git`, `ssh`,
  `seed-run.sh` shimmed to log and fail) that makes "dry-run touches nothing
  but the Journal" a checked property of every scenario.

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

Then the mutation check, which is the suite's own grade:

```bash
tests/mutation-check.sh          # ~6 minutes: one suite run per mutation
```

It breaks one guard in `cycle.py` at a time - the allowlist, each Eligibility
clause, each cap, the fence-aware section reader, the tracker's failure path  - 
and every one must turn the suite red. A guard whose removal leaves it green
is a guard nothing verifies. Same contract as the Loop's
`loop/tests/mutation-check.sh`; adding a guard means adding its mutation to
`tests/cycle-mutations.py`.

## Inspecting the live Journal

```bash
psql -d selector -c "SELECT id, at, kind, payload FROM journal.events ORDER BY id DESC LIMIT 20;"
```

Appending by hand is legitimate (the schema NOTIFYs either way); updating or
deleting raises `journal.events is append-only` unless you deliberately drop
the guard triggers first.
