# Codex runs an Iteration

ADR 0004 made the agent one substitutable command (`LOOP_AGENT_COMMAND`), and
ADR 0012 established that properties observed under one agent belong to that
agent until two have shown them. ADR 0024 extracted the structural boundary
lifecycle into `loop/boundary-harness.sh`, leaving vendor-specific facts in lean
adapter leaves (`loop/agents/claude.sh` and `loop/agents/grok.sh`).

Issue #12 introduces OpenAI Codex as the third agent adapter to run an Iteration
inside the Execution Boundary.

## Context and Findings

Codex runs as an agent adapter leaf (`loop/agents/codex.sh`) sourcing
`boundary-harness.sh`. Adding it proves the claim made in ADR 0012 and ADR 0024:
introducing a third agent is writing a leaf, declaring its egress, and adding a
sibling test suite, with no changes required to the boundary lifecycle harness or
the Loop's orchestration mechanics.

1. **Guest template and sandbox kit**: Unlike Grok (which has no `sbx` template
   and requires per-Iteration in-guest installation), Codex is offered by `sbx`
   as a supported kit (`codex`). The adapter creates the microVM using
   `adapter_create_args=(-t "${guest_template}" codex)` with the default
   `loop-php:1` guest template. Files placed into the guest live under
   `/home/agent`, so `adapter_privileged_put=false`.

2. **Credential placement and isolation**: Codex uses a subscription session
   stored on the host in `~/.codex/auth.json`. Like Grok (and unlike Claude
   Code's environment token), the subscription credential is staged into the
   guest at `/home/agent/.codex/auth.json` (mode 0600). The GitHub token stays
   strictly outside the boundary (Proposal-Only Output). Commits made by Codex
   are signed using the operator's dedicated SSH signing key staged into
   `/home/agent/.ssh/`, with `.gitconfig` rewritten to point to guest paths.

3. **Metered-key collision guard**: Codex accepts `OPENAI_API_KEY` and
   `CODEX_API_KEY` in its environment, in `auth.json`, and in `config.toml`. A
   metered key takes precedence over the subscription session and would silently
   move billing. The adapter checks for both:
   - It declares `metered_env_names=(OPENAI_API_KEY CODEX_API_KEY)` which
     `boundary-harness.sh` and `run.sh` refuse before building a boundary.
   - `adapter_check_credentials` inspects `${codex_home}/auth.json` and
     `${codex_home}/config.toml`, refusing to run if an API key is configured.

4. **Credential inventory without restatement**: Prior to this work,
   `loop/assert-credentials.sh` hardcoded `OPENAI_API_KEY` in the
   `metered-model-key` family as an unclaimed vendor name. With `agents/codex.sh`
   now claiming it, `assert-credentials.sh` leaves `metered-model-key` bare and
   dynamically folds in the names queried from all executable
   `agents/*.sh --metered-env-names`.

5. **Egress allowlist and missing host naming**: Under the boundary's `deny-all`
   network policy, Codex requires three hosts declared in
   `deploy/ansible/roles/loop_execution_boundary/defaults/main.yml`:
   - `api.openai.com:443`: subscription model inference and completions.
   - `auth.openai.com:443`: OAuth token refresh during an Iteration.
   - `chatgpt.com:443`: session routing and subscription backend APIs.
   If an Iteration fails because a required host was blocked or refused by
   network policy, `adapter_run_agent` inspects stderr, names the blocked host,
   and references the egress allowlist in `defaults/main.yml`.

6. **Turn bound detection**: Codex reports turn bounds as `turn limit reached`
   or `max turns reached`. `adapter_is_turn_bound` matches these vendor phrases,
   allowing `boundary-harness.sh` to map the agent's exit status to
   `LOOP_AGENT_TURN_BOUND_EXIT` (33) rather than recording an agent failure.

7. **Sibling test suite**: Per ADR 0012, `tests/boundary-codex.bats` asserts both
   shared boundary properties and Codex-specific properties independently rather
   than parameterising an existing suite. `tests/boundary-codex-mutations.py`
   verifies that 14 deliberate breaks to `agents/codex.sh` are caught by the
   suite, registered in `tests/mutation-check.sh`.

## Decision

Ship `loop/agents/codex.sh` as a leaf adapter, declare its egress hosts under
`loop_execution_boundary_egress_by_agent.codex`, remove the hardcoded
`OPENAI_API_KEY` from `loop/assert-credentials.sh`, and assert all properties in
an independent sibling suite `tests/boundary-codex.bats` with mutation checks.

## Consequences

- Swapping the Loop to run under Codex requires only pointing
  `LOOP_AGENT_COMMAND` to `loop/agents/codex.sh` and setting `loop_agent_name: codex`.
- The credential inventory dynamically checks all adapter metered names without
  duplication.
- Commits made by Codex inside the Execution Boundary remain signed and Verified.
- Any missing host during a Codex Run is identified in the failure diagnostic.
