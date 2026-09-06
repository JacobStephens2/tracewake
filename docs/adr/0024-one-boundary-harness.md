# One boundary harness, with the vendor facts in the leaf

ADR 0004 made the agent one substitutable command (`LOOP_AGENT_COMMAND`), and
ADR 0012 established that properties observed under one agent belong to that
agent until two have shown them. Running the same task under Grok Build (#84)
demonstrated that the Execution Boundary lifecycle is structural rather than
per-agent: creating and destroying a microVM per Iteration, mounting the
workspace and the Loop's scripts read-only, placing signing keys and git identity,
isolating credentials (no GitHub token inside the boundary), guarding against
metered API keys, and detecting turn bounds.

However, because the two adapters were authored sequentially, that structural
lifecycle was duplicated across `loop/agents/claude.sh` and `loop/agents/grok.sh`.
Each file declared the adapter contract (`<prompt-file> <max-turns>`,
`--metered-env-names`, `--guest-template`, `--credential-expiry`), managed its own
`sbx` sandbox creation and destruction trap, set up `put()` helpers, and handled
turn bound exits. Duplicating the harness meant a structural fix or contract
addition had to be made in two places, or risk drift the day a third vendor leaf
is added.

## Decision

Extract the shared Execution Boundary lifecycle into a single harness:
`loop/boundary-harness.sh`. Both `loop/agents/claude.sh` and `loop/agents/grok.sh`
become lean vendor leaves that declare vendor facts and source the harness.

1. **The adapter contract is declared in exactly one place.** `boundary-harness.sh`
   dispatches the 4-part adapter contract:
   - `<adapter> <prompt-file> <max-turns>`: one Iteration inside the boundary
   - `<adapter> --metered-env-names`: one name per line
   - `<adapter> --guest-template`: the image the boundary is built from
   - `<adapter> --credential-expiry`: when the model credential expires (ISO 8601 UTC)
   Leaf-specific query options (such as `--pinned-version` on Grok) are handled via
   the `adapter_query` hook before falling back to usage error.

2. **The harness owns structural boundary mechanics:**
   - Preflight verification that `sbx` is present on PATH
   - Metered key refusal before building a boundary (`refuse_metered_keys`)
   - Unique sandbox naming (`loop-$$-$(date +%s)`) and cleanup trap (`EXIT INT TERM`)
   - Read-only mounting of the Loop's directory (`${loop_dir}:ro`) and workspace mount
   - MicroVM creation from the declared template (`${guest_template}`) and arguments
   - File staging and placement into the guest (`put()`), supporting privileged
     staging via `adapter_privileged_put=true` where root owns guest paths
   - Pipeline execution with `pipefail` disabled during the agent run to capture
     transcripts without short-circuiting on non-zero exit
   - Turn bound detection dispatch to `adapter_is_turn_bound` and exit mapping to
     `LOOP_AGENT_TURN_BOUND_EXIT` (33)

3. **Leaves declare only what their vendor does differently:**
   - `metered_env_names`: vendor-specific environment variable names that supersede subscriptions
   - `guest_template` and `adapter_create_args`: image and creation arguments
   - `adapter_check_credentials`: vendor credential presence and config checks
   - `adapter_prepare_guest`: guest filesystem preparation, agent installation (if unbundled), and credential placement
   - `adapter_run_agent`: vendor invocation arguments, disabled telemetry, and bypass permissions
   - `adapter_is_turn_bound`: matching vendor-specific turn bound messages in the transcript
   - `adapter_credential_expiry`: credential expiry extraction if the vendor uses a time-bounded token

## Consequences

**Structural properties remain asserted in both suites.** `tests/boundary.bats` and
`tests/boundary-grok.bats` continue to test their respective adapters independently.
Per ADR 0012, shared properties are asserted twice on purpose to guarantee that
structural invariants hold under each leaf's configuration and guest template.

**Mutation entries follow the code that moved.** Structural mutations
(`sandbox-leaked`, `missing-credential-skipped`, `pipefail-kills-detection`,
`check-not-mounted`, `check-mounted-writable`, `metered-key-unguarded`,
`create-fails-ignored`) move to `loop/tests/boundary-harness-mutations.py` and are
tested against `boundary-harness.sh`. Vendor leaves retain mutations only for the
vendor facts they declare (12 in `boundary-mutations.py` for Claude, 14 in
`boundary-grok-mutations.py` for Grok).

**Adding a third agent is writing a leaf.** A new vendor requires declaring only its
specific leaf hooks and sourcing `../boundary-harness.sh`. The boundary lifecycle,
staging mechanism, error handling, contract options, and cleanup traps do not need
to be rewritten or maintained across multiple files.
