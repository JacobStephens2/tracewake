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
| `run.sh` | One Run. The entry point, and the only thing that is not a declaration. |
| `agents/claude.sh` | The agent as one substitutable command (ADR 0004). |
| `tests/loop.bats` | The offline suite. No model, no network, no spend. |
| `tests/fake-agent.sh` | The scripted agent the suite drives the real Run through. |
| `tests/mutation-check.sh` | Breaks each bound and confirms the suite notices. |

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

## Running the suite

On the Loop's box, where `ansible/roles/loop_shell_suite` installs the harness:

```
bats tests/
```

Twenty-three tests, under twenty seconds, no model and no network. Every one drives
`run.sh` unmodified and asserts only what a Run externally produces - exit code,
reported bound, Progress Log contents, git history. None names an internal
function or depends on the order of steps inside the loop.

`tests/mutation-check.sh` breaks each bound in turn and confirms the suite goes
red - ten deliberate breaks, ten caught. It names exact lines of `run.sh`, so a
reorganisation of the Loop will make it stop applying; it says so and fails
rather than reporting a false pass. The evidence is in
`../notes/loop-termination-contract-evidence.md`.

`shellcheck -x run.sh contract.sh agents/*.sh tests/*.sh` gates the scripts.

## What this directory does not do

- **Seed the Plan.** `run.sh` refuses to start without one, because putting the
  task into the repository is a setup step (#82) and keeping it one is what makes
  ADR 0003's content-trust reasoning valid.
- **Push, or open a pull request.** Proposal-Only Output is #83.
- **Run inside the Execution Boundary.** Wrapping the agent call in `sbx` is a
  change to `agents/claude.sh` and nowhere else - the Loop does not know what a
  boundary is. Also #83.
- **Get itself onto the box.** Nothing in `ansible/loop.yml` places this
  directory on `loop.etadventures.com` yet. The first end-to-end Run needs that,
  and it is the first thing #83 will find missing.
