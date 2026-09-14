# Single-host mode is gated by the credential inventory

ADR 0029 makes Single-Host the product default. This ADR remains the gate on
local dispatch: the credential inventory, not the SSH hop, is what makes one
machine enough.

Tracewake v0 was built around a two-machine topology (ADR 0003, ADR 0006): a
controller hosting the Selector, the Journal, and the Window, reaching across an
SSH hop to a dedicated box where agent Iterations run inside microVM Execution
Boundaries (`sbx`).

That physical separation exists because the orchestrator historically holds wide
infrastructure reach (vault tokens, database passwords, fleet SSH keys), whereas
the Loop's box holds only three base credentials plus one repository token per
target (ADR 0009 as amended). The SSH hop kept unreviewed agent execution off
the machine holding company secrets.

For an operator with one machine - a developer laptop, an isolated workstation,
or a dedicated cloud instance with no other duties - provisioning a second
droplet and managing an SSH hop is pure overhead. This ADR records when one
machine is enough, and why the credential inventory is the gate.

## When one machine is enough

One machine is enough when:

1. **The machine holds no production reach.** It holds no vault tokens, no
   database credentials, no DigitalOcean tokens, no metered API keys, and no
   fleet SSH keys.
2. **The Execution Boundary is intact.** The local machine runs `sbx` directly,
   so each agent Iteration still executes inside a fresh microVM with an
   isolated root filesystem, memory limits, and egress policy. Attendedness (ADR
   0003) is preserved: the agent never runs unconfined on the host.
3. **Dispatch is local.** The Selector invokes `box-sources/local.sh` in place of
   `box-sources/ssh.sh` (ADR 0004), dispatching the Run directly on the
   controller with no SSH hop.

When those three conditions hold, the SSH hop adds no isolation that the
credential inventory and the Execution Boundary do not already provide.

## Why the inventory is the gate

The seam between the controller and the box is not about physical distance; it is
about credential blast radius.

A machine holding production credentials - such as an orchestration host holding
database passwords or vault tokens - must never run unattended agent loops on
itself. If a model was tricked by prompt injection into attempting host
escalation or environment exfiltration, having live production credentials on the
host turns an isolation bug into a fleet compromise.

Therefore, `box-sources/local.sh` makes the credential inventory
(`loop/assert-credentials.sh`) an unavoidable preflight gate:

- Before any git branch is fetched or checked out, and before `run.sh` is
  started, `local.sh` runs `assert-credentials.sh`.
- If the credential inventory reports any violation (a forbidden credential found
  in the environment, a shell profile, or a configuration file; or an allowed
  credential missing), local dispatch is refused immediately, naming every
  violation on stderr and exiting 2.
- A machine holding production credentials will never pass this check. That is the
  point: the controller may run Runs on itself only while it passes the exact same
  cleanliness check the separate box passes.

## Contract parity

`box-sources/local.sh` is a drop-in sibling to `box-sources/ssh.sh` under ADR
0004. Both obey the exact same contract:

- Accept `<branch> <task-ref>` as arguments.
- Require `SELECTOR_BOX_REPO` and refuse by name when it is absent.
- Fetch origin and check out the run branch into the target checkout.
- Pass the target's repository token (`LOOP_GITHUB_TOKEN_FILE`) and guest image
  (`LOOP_GUEST_TEMPLATE`) to the Run, and omit empty exports.
- Execute `run.sh --repo <repo> --task-ref <task-ref> --propose --notify`.
- Yield the Run's stdout summary report (`LOOP_RUN_*`) and exit code.

One test suite (`selector/tests/test_box_source.py`) drives both sources through
this shared contract, so neither source can drift from what `dispatch.py`
expects.
