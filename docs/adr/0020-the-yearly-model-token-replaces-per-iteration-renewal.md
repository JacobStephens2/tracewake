# The yearly model token replaces per-Iteration renewal

The box's subscription login in `~/.claude/.credentials.json` expired eight hours
after being minted, requiring ADR 0017's per-Iteration renewal mechanism on the
host. That renewal introduced an unattended model process running on the host
outside the execution boundary, ran network calls during Iteration setup, and
remained vulnerable to silent refresh lapses (such as Run 645).

Claude Code provides long-lived setup-tokens (`claude setup-token`,
`CLAUDE_CODE_OAUTH_TOKEN`) that last for one year.

## The decision

The box's model credential is replaced with a yearly setup-token carried via
`CLAUDE_CODE_OAUTH_TOKEN`, and the per-Iteration renewal mechanism (ADR 0017) is
retired.

Four architectural changes follow:

1. **Environment injection, no credential file in the guest (amending ADR 0011).**
   `loop/agents/claude.sh` injects `env CLAUDE_CODE_OAUTH_TOKEN="${token}"` into
   the `sbx exec` command launching Claude inside the microVM. No credential
   file (`.credentials.json`) is copied into the guest. The token is resolved
   from `CLAUDE_CODE_OAUTH_TOKEN`, falling back to the accessToken in
   `.credentials.json` or a token file on the box (`~/.config/loop/model-token`).
2. **Cost control preserved.**
   An Iteration still refuses to start if any metered key (`ANTHROPIC_API_KEY`,
   `CLAUDE_API_KEY`) is present in the environment or files. The subscription
   model remains the cost control.
3. **Leak protection at the push boundary.**
   Because the token is carried in the guest's environment, an agent could
   inadvertently write it to disk and commit it. `loop/propose.sh` checks the git
   diff between the base branch and proposal branch for the setup-token pattern
   (`sk-ant-oat01-`). If found, it refuses to push, reports
   `LOOP_PROPOSE_RESULT=push-failed`, and exits 2.
4. **Consolidated yearly credentials and early warning.**
   One consolidated wizard (`wizards/loop-credentials.sh`) mints all three box
   credentials (Docker PAT, GitHub PAT, and Model setup-token) and prints the
   date they share (one year from creation). The box card reports the nearest of
   the box's expiries as an absolute instant, and the operator is mailed once, a
   fortnight (336 hours) before it expires.

## Consequences

- **Zero model processes on the host.** No agent process runs outside the
  microVM during Iteration startup.
- **Push refusal is deterministic.** Any proposal diff containing the token
  pattern fails immediately at the push stage before reaching the remote
  repository.
- **Maintenance is annual.** The operator runs one wizard per year rather than
  managing daily or session-level credential renewals.
