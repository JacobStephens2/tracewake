# Tracewake

Tracewake turns a labeled issue into a reviewable pull request, unattended.

An operator labels an issue `ready-for-agent`. On its next Cycle the Selector
reads the tracker, decides whether that issue is Eligible, seeds a Run from it,
and dispatches the Run to a box, where a coding agent works inside a microVM
under a Termination Contract with five declared bounds. The Run's only output
is a Proposal - a draft pull request - which the operator reviews. Nothing
merges itself.

It is the same problem OpenAI's Symphony poses, with the opposite bets: the
label as the point of Handover rather than a workflow state, one Run at a time
rather than ten, a fresh microVM per Iteration rather than a persistent
workspace, a Termination Contract rather than a stall timer, Proposal-Only
Output rather than a landing state, and a Journal that records why every
decision went the way it did.

`CONTEXT.md` is the vocabulary - every capitalised term above is defined there -
and `docs/adr/` holds the decisions, numbered, with the reasoning that produced
them.

## Layout

| Path | What lives there |
| --- | --- |
| `loop/` | The Run-side half: the Termination Contract, one Run, Seeding, the Proposal, the agent adapters, and the offline bats suite that drives all of it through scripted fakes. Start at `loop/README.md`. |
| `selector/` | The controller: the Cycle, Eligibility, Dispatch, the Journal, the watcher, the notifier, the board, and its pytest suite. Start at `selector/README.md`. |
| `web/` | The window: a FastAPI app that renders the Journal - the queue board, a Run's history, the ADRs - and its suite. |
| `deploy/systemd/` | The units: the Selector's cycle timer and notifier, the window and its Attended Preview. |
| `deploy/ansible/` | The roles that build a box and a controller. |
| `wizards/` | Runnable walkthroughs for the steps only a human can take - the browser logins the box does not have a browser for. |
| `docs/adr/` | The decisions, numbered 0001 upward. |
| `notes/` | Evidence: what was run, what it printed, and what that settled. |
| `research/` | The source-cited investigations the notes and the ADRs rest on. |

## Running the suites

Three suites and two mutation checks, all offline - no model, no network, no
spend. The two Python suites need a Postgres role matching the OS user with
`CREATEDB`; they create and drop a throwaway database per run.

```bash
# The Loop - 321 tests, bats
cd loop && bats tests/

# The Selector - 279 tests, pytest
cd selector && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/

# The window - 139 tests, pytest
cd web && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

The mutation checks break one bound or one guard at a time and confirm the
suites go red. Anything whose removal leaves a suite green is something that
suite does not actually verify, which on a project with one reviewer is the
highest-value verification available. Each takes minutes: one suite run per
mutation.

```bash
loop/tests/mutation-check.sh
selector/tests/mutation-check.sh
```

## Status

v0 is being assembled. This repository holds the history, the code and the
suites; the work of lifting ETA's own values out of the code and into
configuration is tracked in this repository's issues, under
[Spec: Tracewake v0](https://github.com/JacobStephens2/tracewake/issues/1).

## Licence

MIT. See `LICENSE`.
