# The Loop

A single-operator Ralph. One Run resolves one task and leaves a branch a human
reviews. Spec: issue #73. This directory is issue #79 - the loop itself and the
Termination Contract that makes walking away from it defensible.

```
run.sh --repo <path> [--task-ref <text>]
```

## Before a Run is left unattended

**No Run is left unattended until the Execution Boundary's egress is narrowed to
what the agent needs.** That is an ordering rather than a rule of thumb, and it
is why #100 landed before #83's Run was allowed to go unwatched: `balanced`, the
posture the box came up on, allows 193 hosts including S3, GCS and
githubusercontent, which is a boundary against a runaway agent and not against a
motivated one. An attended Run may run on any posture, because somebody is
watching it. An unattended one may not.

As of 2026-08-24 `loop.etadventures.com` is on `deny-all` plus a two-host
allowlist, declared in `ansible/roles/loop_execution_boundary/defaults/main.yml`
and reconciled by `ansible-playbook loop.yml`. If a Run fails on a host it
needed, the fix is a line in `loop_execution_boundary_egress_common` saying what
broke without it - not widening the profile. The posture, the probes and what
was deliberately left off are in
`lab/single-user-factory/notes/loop-execution-boundary-evidence.md`.

## What is here

| File | What it is |
| --- | --- |
| `contract.sh` | The Termination Contract. Five bounds, one place. |
| `seed-run.sh` | The setup step. One chosen task in, a Plan and a Progress Log out (#82). |
| `run.sh` | One Run. The entry point, and the only thing that is not a declaration. |
| `agents/claude.sh` | The agent as one substitutable command (ADR 0004). |
| `task-sources/github.sh` | The task source as one substitutable command. One task, by number. |
| `check-inventory.sh` | The first task's grade. Derives its own denominator. |
| `assert-credentials.sh` | What the box holds, and what it may not. Both directions (#81). |
| `tests/loop.bats` | The Loop's offline suite. No model, no network, no spend. |
| `tests/check-inventory.bats` | The check's offline suite, seamed separately (#80). |
| `tests/seed-run.bats` | The seed step's offline suite, seamed separately (#82). |
| `tests/fake-agent.sh` | The scripted agent the suite drives the real Run through. |
| `tests/fake-task-source.sh` | The scripted task source, so the seed step's suite reaches no network. |
| `tests/assert-credentials.bats` | The credential inventory's offline suite, seamed separately (#81). |
| `tests/mutation-check.sh` | Breaks each bound and each guard, confirms the suites notice. |

## The Termination Contract

Five bounds, declared in `contract.sh` before a Run starts and written into the
Progress Log at Run start so that reading a finished Run tells you what it was
bound by. Four of the five exist in no published Ralph source.

| Bound | First value | What it stops |
| --- | --- | --- |
| Iterations per Run | 5 | A stochastic system running forever. |
| Iteration wall clock | 15 min | One hung agent process stalling the Run. |
| Turns per Iteration | 40 | An agent thrashing *inside* an Iteration. |
| Run wall clock | 90 min | A Run where every Iteration runs long. |
| Consecutive No-op Iterations | 2 | An agent stuck re-reading the same task. |

A **No-op Iteration** is one after which the repository head is unchanged. A
**Completion Promise** is recorded in the Progress Log and never ends a Run:
nothing verifies it, and Pocock documents his agent lying with it.

These five numbers have no precedent to lean on. They are first guesses, and
correcting them is the first Run's most valuable output - which is why they live
in one file rather than scattered through `run.sh`.

## How a Run reports itself

Stdout, first lines, machine-readable so that triage is one line rather than a
whole log:

```
LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
```

Exit codes: `0` planned end, `1` preflight failed, `2` run-clock, `3`
consecutive-noops, `4` agent-failed, `5` cap reached with an Iteration killed.
Why `0` exists at all, when every Run ends on a bound, is ADR 0007.

## Seeding a Run

A Run is seeded before it is started, by the operator, from one task he chose:

```
seed-run.sh --repo <path> --task <number> --area <text>
            [--task-repo <owner/name>] [--check <command>] [--reseed]
```

It fetches that one task, writes it into the Plan with its acceptance criteria,
and initializes the Progress Log ready for the first Iteration - so starting a
Run is one command and re-running one is the same command again. The first Run's
seeding, in full:

```
seed-run.sh --repo ~/tourbot --task 648 --area 'dashboards and reports' \
    --check "check-inventory.sh --checkout . --inventory docs/tblEmailMessage-inventory.md \
             --scope 'mtourbot/reports/*'"
```

**This is not issue intake, and the difference is load-bearing** (ADR 0010). It
is a human handing over a task he authored, and that authorship is what makes
ADR 0003's content-trust collapse valid: the Execution Boundary contains an
unsupervised agent, it does not defend against hostile input, and `lessons.md`'s
content-trust floor stays deleted only while nothing the Loop reads was written
by somebody else. A Loop that read issues by search would be feeding text the
operator never saw into the prompt of a process that can commit and open a pull
request, with nobody watching for forty-five minutes. That reopens the subsystem
ADR 0003 closed; it is not a feature on top of this one.

The property is enforced rather than honoured. The box's fine-grained token
holds Contents and Pull requests and **no Issues permission**, so a Run cannot
fetch a task even if something inside it tried. `seed-run.sh` runs as the
operator, with his own GitHub identity, off the box; the Plan reaches the box as
a commit like everything else. Same shape as Proposal-Only Output: a property of
what the credential opens. Do not add Issues to that token.

Unlike the wizard's other claims about the token, this one is **not probed** -
GitHub publishes no endpoint reporting a fine-grained token's permission set, and
its list-issues endpoint is satisfied by Pull requests: read, which the token must
hold. ADR 0010 records that gap rather than papering it with a probe that would
fire on a correctly scoped box.

**`--area` is required.** A Run is a handful of Iterations and Tourbot issue 648
is 303 occurrences, so how much of a task one Run is for is a decision - the
operator's, not an Iteration's. The Plan names the owning area and says the rest
of the task is out of scope for this Run, so an Iteration that wanders into the
rest has left the Plan rather than found more of it.

**`--check` is what an Iteration and the operator both grade against**, and it is
the reason the Plan and the check script never had to know about each other: the
command belongs beside the task, in the Plan, and `run.sh` still knows nothing
about either. A Plan seeded with no check says so in as many words, because a
check that is missing and a check that passed must not read the same.

**Acceptance criteria are required.** A task whose criteria section this step
cannot read is refused, not seeded blind - an unattended Run has nobody to ask
what done means. The criteria are lifted out verbatim and given their own section
so an Iteration can find them; the rest of the task follows, with its headings
demoted one level so it nests instead of reading as the Plan's own sections. A
count written into the task is carried across with a note that the check derives
its own denominator (ADR 0008), because #648's "78 files and 276 occurrences" is
exactly the number an Iteration might otherwise grade itself against.

**Re-running it is reproducible.** The two files are a pure function of the task
as fetched, the owning area and the check command, and neither carries a
timestamp - so seeding twice produces the same two files and commits nothing the
second time (`LOOP_SEED_RESULT=unchanged`). Editing the task upstream does change
them, which is the point: the seed is how the current task gets in, and a Run
seeded from a task that has since moved should say so in a diff. Both are written whole rather than
appended to, which is what stops state accumulating - and is also why a Progress
Log that already records a Run stops the seed with exit 2 and the word `--reseed`
rather than overwriting it. The Run's record is the only account of what an
unattended agent did.

Exit codes: `0` seeded, re-seeded, or unchanged. `1` could not run - bad
arguments, a failed fetch, or a task with no readable acceptance criteria. `2`
refused, because a Run is already recorded in the Progress Log.

## The completeness check

The first task (Tourbot issue 648) is a classification exercise, and a
classification exercise has no test suite to grade it. `check-inventory.sh` is
the grade:

```
check-inventory.sh --checkout <tourbot> --inventory <path/to/inventory.md> \
                   [--scope 'mtourbot/reports/*']
```

It derives every occurrence of `tblEmailMessage` from the checkout, reads the
inventory, and exits `2` naming each occurrence the inventory does not account
for - with the line that produced it, because "six are missing" is not something
an Iteration can act on. `0` when the inventory is complete. `1` when it could
not run, which includes deriving a denominator of zero: a wrong `--symbol` or a
mistyped `--scope` produces a check with nothing to check, and reporting success
for that would be a check that passes hardest when it is most broken.

**It never reads a count from the task.** Issue 648 says "78 files and 276
occurrences"; against `tourbot` master on 2026-08-25 the same search answers 359
across the tracked tree and 303 in application code. A check that trusted the
ticket would pass on an inventory that had missed everything landed since it was
written. ADR 0008 records that decision
and the two things that follow from it - exclusions declared in the script
rather than supplied by the caller, and both exclusions and scope printed with
the count each removed, so the denominator can be audited rather than taken.

Same script, both roles: an Iteration runs it to find out what is left, and the
operator runs it afterwards to decide whether the Run's proposal is acceptable.
Nothing in `run.sh` knows about it, deliberately - the Loop is task-agnostic, and
the command belongs in the Plan beside the task, where `seed-run.sh --check` puts
it.
It reaches no network and writes nothing to the checkout, so it behaves the same
inside the Execution Boundary on `deny-all` egress as it does here. It needs no
host on the allowlist.

Two ways it refuses to grade rather than grading wrongly, both of which cost a
Run nothing and would otherwise be silent: a denominator of zero exits 1, and an
inventory that parses to zero entries says the shape is wrong instead of
reporting that nothing was classified. The second matters because "the work was
not done" and "the check could not read the work" would otherwise produce the
same output.

## The credential inventory

Every isolation argument in spec #73 rests on one sentence about what this box
holds. `assert-credentials.sh` is that sentence, asserted:

```
ssh root@loop.etadventures.com 'su - loop -s /bin/bash -c "bash -s"' \
    < assert-credentials.sh
```

It reports **both** directions, because either alone is a half-truth: the four
credentials the box is allowed to hold, and the four families it may not. `0`
clean, `2` naming every violation, `1` when it could not run.

**It takes two runs.** As `loop` it sees the environment a Run actually gets and
the boundary's session, and cannot look inside `/root`; as root it can look there
and has no `sbx` session. A probe it could not evaluate prints `[partial]`, never
`[clear]` - `[[ -e ]]` is false both for "not there" and for "not allowed to
look", and reporting those the same way is how a check comes to be trusted for
something it never did.

**Four, not three.** The spec says three; the Execution Boundary itself needs a
Docker identity, so there is a fourth (ADR 0009). It is a read-only Docker token
that reads Docker Hub and reaches nothing else, and it is a line of the inventory
rather than an exception to it.

The collision spec #73 story 32 wants made impossible has three doors, and the
script checks all three: `ANTHROPIC_API_KEY` in the Run's environment, the same
name exported from a shell profile or `/etc/environment`, and a metered secret
stored in the Execution Boundary by `sbx secret set` or `sbx secret import` -
which is in no environment and no file at all. `agents/claude.sh` guards the
first door at Run start, per-agent, because that collision is a property of the
agent; this script guards the box.

It never fixes what it finds. What to do about a fleet key that reached this box
is not a decision to take unattended.

## Running the suite

On the Loop's box, where `ansible/roles/loop_shell_suite` installs the harness:

```
bats tests/
```

A hundred and thirty-seven tests, no model and no network. Twenty-three drive
`run.sh` unmodified and assert only what a Run externally produces - exit code,
reported bound, Progress Log contents, git history. Thirty-two drive
`check-inventory.sh` against small fixture checkouts. Forty-one drive
`assert-credentials.sh` against a constructed box - a home directory, a system
root and a scripted fake `sbx`, all three of which a tmpdir can hold.
Forty-one drive `seed-run.sh` against a scripted fake task source, and assert
what the Plan ends up saying, what the Progress Log is left ready for, and what
seeding twice does. None of them names an internal function or depends on the
order of steps.

The check is seamed and tested on its own rather than only through a Run because
it is itself the honest failure signal, and a component that is the failure
signal should not have its correctness established only through another
component (spec issue #73, Seam B).

`tests/mutation-check.sh` breaks one thing at a time - each bound of the
Contract, each guard of the check, each credential family, each guard of the
seed step - and confirms the suite goes red. Fifty-three deliberate breaks,
fifty-three caught. It names
exact lines, so a reorganisation will make a mutation stop applying; it says so
and fails rather than reporting a false pass. `--only check-inventory.sh` runs
one subject's set. The evidence is in
`../notes/loop-termination-contract-evidence.md`,
`../notes/loop-completeness-check-evidence.md`,
`../notes/loop-credentials-evidence.md` and `../notes/loop-seed-evidence.md`.

`shellcheck -x *.sh agents/*.sh task-sources/*.sh tests/*.sh` gates the scripts.

## What this directory does not do

- **Push, or open a pull request.** Proposal-Only Output is #83.
- **Run inside the Execution Boundary.** Wrapping the agent call in `sbx` is a
  change to `agents/claude.sh` and nowhere else - the Loop does not know what a
  boundary is. Also #83.
- **Place the box's credentials.** The signing key is generated on the box by
  `ansible/loop.yml` (role `loop_credentials`) and git is configured to sign with
  it, but registering that key to the operator's GitHub account and minting the
  repository-scoped token are browser steps:
  `../wizards/loop-github-credentials.sh` (#81).
- **Get itself onto the box.** Nothing in `ansible/loop.yml` places this
  directory on `loop.etadventures.com` yet. The first end-to-end Run needs that,
  and it is the first thing #83 will find missing.
