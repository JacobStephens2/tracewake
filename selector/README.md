# The Selector

The Selector (issue #151's spec; vocabulary in `../CONTEXT.md`) drains
tourbot's `ready-for-agent` queue unattended. This directory is its home.

What exists so far is **a cycle that picks and dispatches** - the reasoning
(issue #153) and the dispatch that acts on it (issue #154) - on top of the
**Selector Journal** (ADR 0015, issue #152). What is not built yet is the
outcome bookkeeping that moves an issue's label once a Run has ended (#155),
the timer that runs cycles unattended (#156) and the Iteration watcher (#157).

## The cycle

```bash
cd lab/single-user-factory/selector
.venv/bin/python cycle.py --dry-run    # reason, journal, change nothing
.venv/bin/python cycle.py              # and act on it
```

One cycle reads the labeled queue through the tracker command, applies
Eligibility to every issue in it, orders what survives lowest-first, applies
the caps, and appends the whole of that reasoning to the Journal. Then, unless
`--dry-run`, it acts: the pick is dispatched, and every issue skipped for a
missing section is handed back to the operator.

The two modes share every line of the deciding, so a dry-run is the cycle that
would have happened rather than a separate approximation of one.

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

The order is not arbitrary: `missing-section` is the loud skip - see below -
so the cheap, quiet reasons are tested first. An issue that is blocked anyway
is not shouted at for a gap.

**Caps.** One Run in flight, `SELECTOR_DAILY_CAP` (4) dispatches per rolling
24 hours. Rolling rather than calendar: "four a day" is a spend bound, and a
calendar boundary would let eight Runs happen inside three hours across
midnight. A cap that halts a cycle still lets it reason and journal first, so
the Journal answers "what would it have picked?" as well as "what did it?".

**Configuration**, all environment, all with working defaults:

| variable | default | what it is |
| --- | --- | --- |
| `SELECTOR_TRACKER_COMMAND` | `tracker-sources/github.sh` | the labeled queue, read |
| `SELECTOR_TASK_REPO` | `Educational-Travel-Adventures/tourbot` | |
| `SELECTOR_LABEL` | `ready-for-agent` | the Handover |
| `SELECTOR_NEEDS_INFO_LABEL` | `needs-info` | where a loud skip sends an issue |
| `SELECTOR_LABELER_ALLOWLIST` | `JacobStephens2` | comma-separated |
| `SELECTOR_DAILY_CAP` | `4` | dispatches per rolling 24h |
| `SELECTOR_JOURNAL_DSN` | `dbname=selector` | |
| `SELECTOR_WORK_REPO` | `/var/lib/conductor/selector-work/tourbot` | the checkout Seeding happens in |
| `SELECTOR_WORK_REMOTE` | `origin` | |
| `SELECTOR_BRANCH_PREFIX` | `loop/` | |
| `SELECTOR_SEED_COMMAND` | `../loop/seed-run.sh` | the Loop's own seed step |
| `SELECTOR_BOX_COMMAND` | `box-sources/ssh.sh` | the box, and the Run on it |
| `SELECTOR_ISSUE_COMMAND` | `issue-sources/github.sh` | comments and label swaps |
| `SELECTOR_DISPATCH_TIMEOUT_SECONDS` | `7200` | backstop for a wedged SSH |

`box-sources/ssh.sh` reads four more: `SELECTOR_BOX_HOST`
(`root@loop.etadventures.com`), `SELECTOR_BOX_USER` (`loop`),
`SELECTOR_BOX_REPO` (`/home/loop/tourbot`) and `SELECTOR_BOX_LOOP`
(`/home/loop/loop`).

Widening the allowlist is one entry here plus a note in ADR 0014, which is
what story 35 asks for.

## Dispatch

A cycle that picked something and was not asked for a dry-run dispatches it.
Five steps, in this order, all of them through substitutable commands:

1. **The branch.** `git fetch`, then `loop/<number>-<owning-area>` from the
   base branch - or from the branch itself when the remote already has one,
   so a retry continues what the first attempt committed instead of resetting
   it away.
2. **Seeding.** The Loop's own `seed-run.sh`, unchanged, with `--area` and
   `--check` taken from the issue's sections. It still runs HERE rather than
   on the box: ADR 0010's enforcement is that the box's token holds no Issues
   permission, and ADR 0014 moved the Handover to the label without moving
   Seeding. Its refusal - a task whose acceptance criteria it cannot read -
   ends the dispatch rather than being worked around.
3. **The push.** The Plan reaches the box as a commit, like everything else.
4. **The Run.** `box-sources/ssh.sh` puts the box's checkout on the branch and
   runs `run.sh --repo ... --propose --notify`. It blocks for the length of
   the Run, which is the point: the box persists no record of one, so the only
   moment the summary exists anywhere is while something is holding the
   process.
5. **The outcome.** The `LOOP_RUN_*` block is parsed and journaled - the bound
   that ended the Run, its exit code, its Iterations, its faults and its
   Proposal URL.

`run.dispatched` is journaled **before** step 4 and `run.outcome` after it.
That gap is the in-flight lock every later cycle reads, so the ordering is the
concurrency control rather than bookkeeping: a dispatch journaled only on
success would leave the ninety minutes a Run takes unguarded.

A Run that ended on a bound is an outcome, not a failure - the Termination
Contract working is not the Selector failing, and a cycle exits 0 for it. A
box that started no Run is a failure, and the two are told apart by whether
the box reported `LOOP_RUN_ENDED_BY` at all.

**A dropped connection reads as a dispatch that failed.** The summary is
printed when the Run ends, so an SSH session that died at minute forty
produces no `LOOP_RUN_ENDED_BY` and is journaled `dispatch-failed` even though
the Run may have finished on the box and opened its Proposal. Nothing here can
tell the two apart from this side; what closes the gap is the watcher (#157),
which is reading the box's Progress Log while the Run happens.

**The work checkout is not created for you.** Seeding commits the Plan, so the
checkout needs an identity to commit as - the operator's, because that is
whose Handover this is. On this VM `conductor`'s global git config already
carries it, signing key included, so the clone is the whole of the setup:

```bash
git clone https://github.com/Educational-Travel-Adventures/tourbot \
    /var/lib/conductor/selector-work/tourbot
```

It is deliberately **not** `/srv/orchestration/tourbot`. That checkout is
shared with the operators who work in it from their own code-server, and a
dispatch that switched its branch out from under one of them would be the
Selector reaching into somebody else's working tree.

## The loud skip

An issue that passes every other Eligibility clause and is missing
`Acceptance criteria` or `Owning area` is not quietly passed over: the
Selector comments on it naming the gap and the way back, swaps
`ready-for-agent` for `needs-info`, and journals `issue.returned`. The comment
goes first - a swap that landed with no comment would take the issue out of
the queue with nothing on it saying why.

It happens for every such issue in the queue and independently of the caps:
handing work back is not spending a Run, and an issue the Selector will never
seed should not wait for a free budget to be told so. A hand-back GitHub
refused is journaled as `issue.return-failed` and exits the cycle non-zero, so
the timer's `OnFailure` pages (story 31).

Dispatch against the real box - the SSH hop's two-shell quoting, the whole
chain end to end, and the one acceptance criterion that needs the operator -
is written up in `../notes/selector-dispatch-evidence.md`.

The first live dry-run is written up in
`../notes/selector-first-dry-run-evidence.md`, including what it found: no
tourbot issue carried the `Owning area` section the label started promising on
2026-08-26, so on that day every otherwise-ready issue in the queue was one
the loud skip above would hand straight back.

## Files

- `cycle.py` - the cycle above: Eligibility, ordering, caps, and the
  journaling of every decision. Deterministic code, never an agent (ADR 0014).
- `dispatch.py` - the five mechanical steps between a pick and a started Run.
  It decides nothing and journals nothing: `cycle.py` journals what it
  returns, so the Journal's shape is settled in one file rather than two.
- `tracker-sources/github.sh` - the default `SELECTOR_TRACKER_COMMAND`: one
  `gh api graphql` call normalized to a flat record per issue (labeler,
  native blocker count, open sub-issues, open Proposals). Substitutable, and
  the seam the offline suite drives.
- `issue-sources/github.sh` - the default `SELECTOR_ISSUE_COMMAND`: one
  comment, or one label swap, as the operator. This is the half of the work
  the box deliberately cannot do - its token holds no Issues permission at
  all - so every write to the tracker happens here.
- `box-sources/ssh.sh` - the default `SELECTOR_BOX_COMMAND`: one SSH hop that
  puts the box's checkout on the Run's branch and runs `run.sh --propose
  --notify`, printing what the Run reported. It holds no credential of its
  own and starts nothing else.
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
- `tests/` - three suites at three boundaries. `test_journal.py` is the
  Journal against the real engine: NOTIFY asserted by a live listener,
  mutation blocked by the guard triggers, ordering. `test_cycle.py` runs the
  real `cycle.py --dry-run` against canned queue states, reading only the
  commands it issued and the rows it wrote - plus a tripwire PATH (`gh`,
  `git`, `ssh`, `seed-run.sh` shimmed to log and fail) that makes "a dry run
  touches nothing but the Journal" a checked property of every scenario.
  `test_dispatch.py` runs the same real `cycle.py` with the dispatch commands
  scripted and **git real**, against a bare repository in a tmpdir: the
  branch, the push and the retry case are asserted against what actually
  ended up on a remote. Its fake box also reads the Journal while it is
  "running", which is how the in-flight lock being held for the length of a
  Run is checked rather than assumed.

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

It breaks one guard at a time - the allowlist, each Eligibility clause, each
cap, the fence-aware section reader, the tracker's failure path, and now each
step of dispatch and each half of the loud skip - and every one must turn its
suite red. A guard whose removal leaves it green is a guard nothing verifies.
Same contract as the Loop's `loop/tests/mutation-check.sh`; adding a guard
means adding its mutation to `tests/selector-mutations.py`, where each entry
names the file it breaks and the suite that has to notice.

## Inspecting the live Journal

```bash
psql -d selector -c "SELECT id, at, kind, payload FROM journal.events ORDER BY id DESC LIMIT 20;"
```

Appending by hand is legitimate (the schema NOTIFYs either way); updating or
deleting raises `journal.events is append-only` unless you deliberately drop
the guard triggers first.
