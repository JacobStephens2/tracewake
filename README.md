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
`SPEC.md` is the specification, stating Tracewake's bets side by side in the
same terms as OpenAI's Symphony, `INSTALL.md` is the Single-Host installation
guide, and `docs/adr/` holds the decisions, numbered, with the reasoning that
produced them.

## Configuring an instance

An instance is **an env file plus a targets file**. Nothing in the code names a
company, a host, a person or a repository: a value like that has exactly one
right answer per instance and no right answer in the product, so a missing one
stops the cycle at preflight with the value named rather than being filled in
with somebody else's. `selector/tests/test_configuration.py` enforces that
mechanically against every default in the shipping tree. The product ships no
filled-in Instance: those two files are written on the Host.

`INSTALL.md` shows the Single-Host shape; `selector/README.md` says what each
value does. A second repository worked by the same instance is a second
`[[target]]` stanza - its own labels, allowlist, checkouts, token, guest image,
review cap and landing mode - and not a second controller. The target
repository itself stays unaware that Tracewake exists. A remote Box remains a
substituted `SELECTOR_BOX_COMMAND`, not a second setup.

## Layout

| Path | What lives there |
| --- | --- |
| `SPEC.md` | The specification: Tracewake's architecture, bounds, and guarantees stated in OpenAI Symphony's terms. |
| `INSTALL.md` | The step-by-step Single-Host walkthrough, from nothing to a first dry-run cycle. |
| `CONTEXT.md` | The single domain vocabulary for the project. |
| `loop/` | The Run-side half: the Termination Contract, one Run, Seeding, the Proposal, the agent adapters, and the offline bats suite that drives all of it through scripted fakes. Start at `loop/README.md`. |
| `selector/` | The controller: the Cycle, Eligibility, Dispatch, the Journal, the watcher, the notifier, the board, and its pytest suite. Start at `selector/README.md`. |
| `web/` | The window: a FastAPI app that renders the Journal - the queue board, a Run's history, the ADRs - and its suite. |
| `deploy/systemd/` | The units: the Selector's cycle timer and notifier, the window and its Attended Preview. |
| `deploy/ansible/` | `host.yml` installs the Single-Host Controller, local Box, and HTTPS proxy. |
| `wizards/` | Runnable walkthroughs for the steps only a human can take - the browser logins the box does not have a browser for. `host-up.sh` stands the Host up (droplet, play, `/healthz`, credential inventory) for #57. |
| `docs/adr/` | The decisions, numbered 0001 upward. |
| `notes/` | Evidence: what was run, what it printed, and what that settled. |
| `research/` | The source-cited investigations the notes and the ADRs rest on. |

## What Tracewake refuses to do

The invariants that govern Tracewake are defined by what it guarantees and what it
refuses to do:

- **Proposal-Only Output (refuses autonomous merges)**: A Run's sole external output is
  a draft pull request (ADR 0013). A human operator reviews, tests, and merges every
  change. Nothing merges itself.
- **Subscription Credentials (refuses metered keys)**: Agents authenticate against
  flat-rate subscriptions, such as a 1-year `CLAUDE_CODE_OAUTH_TOKEN` or seat (ADR 0004,
  ADR 0020). Preflight refuses metered API keys (such as `ANTHROPIC_API_KEY`),
  eliminating runaway spend.
- **Secret Scans on Diff (refuses credential leaks)**: `propose.sh` inspects outgoing git
  diffs for known token patterns (such as `sk-ant-oat01-`) and refuses to publish a
  proposal containing them.
- **MicroVM Boundary (refuses unisolated execution)**: Every Iteration executes inside
  an isolated microVM or container with pinned network egress and an ephemeral
  filesystem (ADR 0003, ADR 0011).
- **Out-of-Repo Configuration (refuses repo pollution)**: Target repositories carry zero
  Tracewake configuration files, workflow templates, or hooks. Configuration lives
  entirely on the controller.
- **Termination Contract (refuses unverified done signals)**: A Run terminates only when
  one of its five declared Contract bounds binds (ADR 0007). A model's "Completion
  Promise" is advisory and never ends a Run or claims done on its own.
- **Explicit Instance Config (refuses guessed defaults)**: Missing required instance or
  target configuration aborts the cycle immediately at preflight with the missing key
  named, refusing to fall back to arbitrary placeholder values (ADR 0003).
- **Protected Executed Paths (refuses unreviewed execution)**: The Guardrail verifies
  that all executed paths (scripts, selectors, unit files) are protected by GitHub
  rulesets and clean against reviewed git refs (ADR 0026).
- **Review Capacity Bounds (refuses reviewer flooding)**: Dispatches halt when open
  proposals awaiting review reach the target's `review_cap` (ADR 0022), keeping
  unattended throughput aligned with human review capacity.
- **Bounded Retries (refuses infinite loops)**: Failed runs are retried at most once on
  the same branch before escalating to `ready-for-human`.

## Running the suites

Three suites and two mutation checks, all offline - no model, no network, no
spend. The two Python suites need a Postgres role matching the OS user with
`CREATEDB`; they create and drop a throwaway database per run.

```bash
# The Loop - 289 tests, bats
cd loop && bats tests/

# The Selector - 340 tests, pytest
cd selector && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/

# The window - 220 tests, pytest
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

v0 is being assembled. This repository holds the history, the code, the suites
and - since issue #3 - the configuration that replaced the defaults. What is
left is tracked in this repository's issues, under
[Spec: Tracewake v0](https://github.com/JacobStephens2/tracewake/issues/1).

## Licence

MIT. See `LICENSE`.
