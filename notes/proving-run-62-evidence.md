# Proving Run evidence for #62

Issue [#62](https://github.com/JacobStephens2/tracewake/issues/62), under
[#51](https://github.com/JacobStephens2/tracewake/issues/51), run on the Host
on 2026-09-16 against the Instance configured in #60
(`notes/instance-60-evidence.md`), after the dry-run of #61
(`61-dry-run-evidence` branch). Product checkout on the Host: branch `62`
(an unmerged bootstrap branch - see the end of this note), selected through
the operator's SSH session, not through any agent session of its own.

## Result

Two live cycles, both scoped `--target JacobStephens2/vaulted-agent`:

- Cycle 13 picked #92 first and recorded `run.outcome` with
  `ended_by: dispatch-failed`: Seeding refused - the fresh work checkout
  `/var/lib/conductor/work/vaulted-agent` had no git identity.
- Cycle 20 (after the identity fix) seeded, dispatched attempt 2, ran one
  Iteration, and recorded `run.outcome` with `ended_by: agent-failed`,
  `exit: 4`, `proposed: proposed`,
  `proposal: https://github.com/JacobStephens2/vaulted-agent/pull/109`.
  PR #109 is open, draft, head
  `loop/92-installer-auto-detects-binaries-for-the-invoking`, base `main`;
  both CI checks are green (`test (macos-latest)`, `test (ubuntu-latest)`),
  and the Run's `--notify` comment ("Run ended: agent-failed (exit 4)…")
  is on the Proposal.

Routing: attempt 2 spent the per-Handover retry budget (2), so the Selector
took the GIVEN_UP route to `ready-for-human` and posted the give-up comment
on #92 itself. The label swap failed - `ready-for-human` did not exist in
vaulted-agent - leaving #92 at `["bug"]` with the comment overstating the
swap. The operator created the three missing lifecycle labels in
vaulted-agent (`needs-info`, `awaiting-review`, `ready-for-human`) and
applied the journaled route label, so #92 now carries
`["bug", "ready-for-human"]`. No third Run can start for this Handover.

## Criterion-by-criterion

- The first Dispatch is vaulted-agent#92: yes. Both cycles picked #92
  (Journal rows 15/17 and 22/24); it is the first Target and was the only
  Eligible issue in it.
- A draft Proposal exists for that Run: yes, PR #109 (draft, from the Run's
  branch, checks green).
- Routed from the Run's ending bound and the Proposal's checks: the route
  decision is the Selector's (GIVEN_UP from `agent-failed` at attempt 2/2,
  Journal row 27, comment on the issue). The Proposal's checks are green;
  the bound-driven route at a spent budget does not consult them further.
  The mechanical swap was completed by the operator (see above) - that half
  is operator action, recorded here, not Selector action.
- Run History shows the ended Run: yes. Rows 17/18 (attempt 1) and 24/26
  (attempt 2, with proposal) pair by (issue, attempt) through the repo's own
  `journal.run_window` / `journal.events` / `events` record constructors -
  the same layer the `/loop/history` page reads.
- Notify mail arrives at `jacob@stephens.page`: the relay accepted three
  sends, logged by the notifier - `Handover on 9 unenrolled repositories`
  (row 12/19, the first-live notice the dry-run predicted),
  `Dispatch failed: …#92` (row 18), and
  `Run cut short by agent-failed: …#92` (row 26). Inbox receipt is for the
  operator to confirm.

## What the proving Run required (all on the Host, operator-authorized)

- Notifier: the `failed` state was stale - the unit gave up at 02:39 UTC,
  before #60 wrote the env file at 04:16. `reset-failed` + `start` revived
  it; it has been `active` since, cursor from row 11 (nothing stale mailed).
- Dispatch wiring: the Cycle runs as `conductor`; a Run executes as `loop`.
  `/etc/tracewake/box-as-loop.sh` (instance-owned, copy at
  `deploy/instance/box-as-loop.sh`, sha256 identical) re-execs
  `box-sources/local.sh` via `sudo -u loop -g loop -H`; `/etc/sudoers.d/conductor-loop`
  allows conductor exactly that command (NOPASSWD, no SETENV, `env_reset`
  with `env_keep` for `LOOP_*`/`SELECTOR_*`/`TRACEWAKE_*` only - conductor's
  `GH_TOKEN` and any metered key are stripped at the transition).
  `SELECTOR_BOX_COMMAND` now points at the wrapper (prior env backed up at
  `/root/tracewake-before-62.env`). This is strictly less privilege than the
  split-host topology, where the controller holds the fleet key.
- Work checkout: `/var/lib/conductor/work/vaulted-agent` cloned as
  conductor, with the operator git identity (`Jacob Stephens`,
  `jstephens@etadventures.com`) - the missing identity cost attempt 1.
- Credential gate: `assert-credentials.sh --agent` (default `claude`),
  threaded through `local.sh` from `SELECTOR_BOX_AGENT`. The model row was
  Claude-only, so a Grok instance holding no Claude login on purpose (story
  30) refused every dispatch. The Grok row checks the same two facts
  `agents/grok.sh` refuses an Iteration without (auth.json present, config
  free of api_key/env_key). TDD: 8 new bats tests; full gate file green on
  the Host as conductor (67/67), `loop.bats` + `boundary-grok.bats` green
  (99), selector `test_box_source` + `test_cycle` + `test_configuration`
  green (93). An adapter with no credential shape (codex) refuses rather
  than passing.
- Target-repo labels: vaulted-agent carried only `ready-for-agent` of the
  four lifecycle labels. Created the other three; completing the recorded
  route needed `ready-for-human` to exist.

## Still open (not this issue)

- `box.unreachable` / `run.watch-failed`: `facts.sh` and `progress.sh`
  SSH to `SELECTOR_BOX_HOST=local`. Advisory only (never raised), but the
  box card and live progress are dark on Single-Host. Product follow-up.
- `guardrail.unreadable` rows 14/21: the deployed tree is on unmerged
  branch `62` while the Guardrail ref is `main`. Resolves when the gate-fix
  review merges and the Host returns to `main`. Until then the Host stays
  on `62` deliberately - reverting would re-break every dispatch at the
  gate.
- Career: no work checkout, no box checkout (`/home/loop/career`), and its
  repo lacks `needs-info` / `awaiting-review`. The next unscoped cycle will
  repeat this issue's lessons there.
- `notify-unit-failure@…` does not exist, so the cycle units' `OnFailure`
  pages nothing. The two `CycleFailed` exits in this issue mailed nothing
  through that path (the Journal notices above did mail).
- The Cycle timer remains disabled; both proving cycles were manual.
- SMTP secret (`/etc/tracewake/smtp-password`, 0600 conductor) verified
  unreadable by `loop` - the Run account cannot reach the relay (story 20).
