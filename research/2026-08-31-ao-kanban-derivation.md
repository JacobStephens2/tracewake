# AO's fact-derived Kanban and "Needs you"

Agent Orchestrator (AO) derives every card's board placement on read, from persisted facts, and never stores a column: the daemon's reducer takes session facts (agent activity from hook callbacks, terminated flag, signal timestamps) plus per-PR facts (draft/merged/closed, GitHub's own `statusCheckRollup` state, `reviewDecision`, mergeability, AO-vs-external review authorship) and returns a `KanbanColumn` plus a `DisplayStatus`, with the contract file stating outright that the column "is never persisted" (`backend/pkg/contract/kanban.go`) and the status "is never stored" (`backend/internal/domain/status.go`). "Needs you" is not a column in the current desktop board at all: it is the label of attention zone `action`, a five-bucket grouping (`merge`/`action`/`pending`/`working`/`done`) computed as a pure function of the derived session status, where `action` is the union {`needs_input`, `exited`, `no_signal`, `ci_failed`, `changes_requested`, `unknown`} — i.e. every status whose next turn is provably a person's. The newer desktop board renders four delivery lanes instead (`building`/`validating`/`needs_review`/`ready`, archive off-board), whose central idea is a *whose-turn* split: the same review-feedback loop is `validating` while AO is turning it (its review pass running, auto-fixing CI, auto-addressing comments) and `needs_review` when the next turn is a person's. Staleness is handled twice: a 90-second `noSignalGrace` downgrades a silent-but-signal-expected session from "idle" to `no_signal` ("AO cannot tell whether the agent is working or stuck"), and the SCM observer polls PR/CI facts every 30s with a 5-minute bounded-staleness forced refetch. Every claim in the secondary doc checked out: the repo exists, Apache-2.0, 10,765 stars on 2026-08-31, README claims exactly 26 supported agents (26 logo rows; 27 non-infrastructure adapter directories), and the monorepo has both a `cloud/` directory (Apache-licensed source of a "private control-plane service") and a `private/` directory (an empty submodule placeholder pointing at a 404-private repo).

Studied at commit 5cb411aca2c080fc7050884ec2ceebcdce270672 (2026-08-31), shallow clone. Spec input only; the product was not run.

## A. The repo, verified

github.com/Untrivial-ai/agent-orchestrator, via the GitHub API on 2026-08-31:

| claim in the secondary doc | verdict |
| --- | --- |
| Apache-2.0 | **Confirmed.** Root `LICENSE` is Apache-2.0; no per-directory license anywhere, including `cloud/`. |
| ~10.8k stars | **Confirmed.** 10,765 stars, 1,498 forks, 860 open issues; created 2026-02-13, pushed same day as the check. |
| 26 supported coding agents | **Confirmed as the repo's own claim.** README: "26 coding agents supported"; exactly 26 logo rows. `backend/internal/adapters/agent/` holds 27 adapter directories after excluding shared infrastructure (`activitystate`, `hooksjson`, `registry`, `fake`, …) — the off-by-one is which dirs count as an "agent", not a false README. |
| optional `cloud/` and `private/` directories | **Confirmed, with a sharper reading — see D.** |

It is a desktop app (Electron frontend + Go daemon), one session = one task/agent/worktree, GitHub and GitLab SCM adapters, SQLite persistence. The board is the daemon's opinion; clients render what it sends.

## B. Two board vocabularies, one derivation chain

AO has *two generations* of grouping, both alive in the code, both pure functions over the same derived facts:

1. **Attention zones** (`attentionZone()` in `packages/product-ui/src/session-presentation.ts`): `SessionStatus → {merge, action, pending, working, done}`, labeled "Ready to merge" / **"Needs you"** / "In review" / "Working" / "Terminated". The README's board bullets and the mobile app (`packages/mobile/lib/agentsView.ts`) use this vocabulary; mobile orders sections action-first and drops empty zones.
2. **Kanban delivery lanes** (`derivePRKanbanColumn()` in `backend/pkg/contract/kanban.go`): `building → validating → needs_review → ready`, plus `archive` rendered as a sheet, not a lane. The current desktop `SessionsBoard.tsx` renders these (`boardKanbanColumnOrder`). The daemon sends `kanbanColumn`; the client's `toKanbanColumn()` falls back to mapping status→zone→lane only for a daemon that predates the field — an explicit mixed-version compatibility rule, commented as such.

The derivation chain: durable facts → `SessionStatus` (one word) → attention zone, and in parallel durable facts → `KanbanPresentation{Column, DisplayStatus}`. Both derivations read the clock **once**, from the same instant, because "two reads could put them either side of its grace period and have the card contradict its own status" (`backend/internal/service/session/service.go`, `toSessionWithFacts`).

## C. "Needs you", exactly

`zone.action`, English label "Needs you" (`session-presentation.ts`). Its membership, from `attentionZone()`:

| status | fact behind it |
| --- | --- |
| `needs_input` | agent activity is `waiting_input` (a `permission-request` hook fired) or `blocked` |
| `exited` | the agent process exited |
| `no_signal` | signal expected, none ever delivered, quiet past the 90s grace |
| `ci_failed` | aggregate rollup of the PR's checks is failing |
| `changes_requested` | a human review requested changes, or unresolved review comments exist |
| `unknown` | unrecognized status (defensive) |

README's gloss matches the code: "blocked sessions, missing input, failed CI, requested changes, or lost signals." Nothing in this zone is a card someone dragged; every member is a predicate over a session or provider fact. Mobile folds the same statuses into its `action` section and notes in a comment that desktop "files `ci_failed` and `changes_requested` under 'Needs you' rather than giving review its own column."

In the newer lane vocabulary, "Needs you" has no direct lane; its work splits between `needs_review` (person's turn in the review loop) and the `Blocked`/`Exited`/`No signal` display statuses shown *inside* whatever lane the card is in.

## D. `cloud/` and `private/`: the licensing boundary

- `cloud/` is real source in the Apache-2.0 tree: a Go control-plane (28-table Postgres schema, WorkOS auth, org RLS, GitHub App webhooks) plus a Next.js Cloud UI. Its README calls it a "**Private** AO control-plane service" — "private" describes the deployment/product, not the license; there is no separate LICENSE, so the root Apache-2.0 grant covers it.
- `private/` contains exactly one thing: `private/ao-cloud`, a git **submodule** (`.gitmodules`: `url = https://github.com/Untrivial-ai/ao-cloud.git`, `update = none`) whose target returns 404 to unauthenticated API calls. In a public clone it is an empty directory. So the proprietary piece is fenced off by repo access, not by a license file inside the monorepo. What `ao-cloud` contains is UNVERIFIED (inaccessible); the naming overlap with the in-tree `cloud/` suggests a successor or deployment overlay, but that is inference.

## E. `SessionStatus`: the status reducer

`contract.DeriveStatus(session, prs, now, noSignalGrace)` in `backend/pkg/contract/status.go`. Precedence, top wins:

1. `IsTerminated` → `merged` if any PR merged, else `terminated`.
2. Activity: `active` → `working`; `exited` → `exited`; `waiting_input` or `blocked` → `needs_input`. (Live agent trouble outranks any PR fact.)
3. SCM status over **open** PRs, worst-first. Per PR (`prPipelineStatus`): CI failing → `ci_failed`; draft → `draft`; changes requested *or* unresolved review comments → `changes_requested`; mergeable → `mergeable`; review required → `review_pending`; merge blocked → `pr_open`; approved → `approved`; default `pr_open`. Aggregation picks the **worst by a fixed severity order** (`ci_failed` < `changes_requested` < `draft` < `review_pending` < `pr_open` < `approved` < `mergeable`). Stacked PRs: `BuildStacks` marks a PR blocked when its target branch is another open PR's source branch; a blocked child only contributes if its signal is actionable (`ci_failed`/`draft`/`changes_requested`) — an upstack PR's "review pending" cannot drown the stack.
4. `silentPastGrace`: `SignalExpected && !HasSignal && now-LastActivityAt > 90s` → `no_signal`. `SignalExpected` is per-harness (hook-capable agent, not chat mode); `HasSignal` is "a hook callback has ever arrived this spawn".
5. Else `idle`.

`noSignalGrace = 90 * time.Second` (`backend/internal/service/session/status.go`), sized to cover TUI boot plus the gap to the first activity-bearing hook.

## F. `KanbanColumn`: the delivery-lane reducer

`contract.DeriveKanbanPresentation` in `backend/pkg/contract/kanban.go`. Inputs: session facts **plus policy flags** (`AutoReview`, `AutoInjectReview`, `AutoInjectCI`), per-PR facts **plus** `KanbanReviewRunFacts` (AO's own review passes, pre-filtered to the PR's *current head* — "a stale run can never decide the column") and `KanbanExternalReviewFacts` (provider reviews with AO's own excluded by review id, because GitHub's aggregate `reviewDecision` "cannot tell whose turn the review-feedback loop is on").

Per-PR column, first match wins:

1. merged or closed → `ready`
2. draft → `validating`
3. externally approved (aggregate `reviewDecision == approved` **and** a surviving non-AO approval) or provider-mergeable → `ready`
4. `aoOwnsNextStep` → `validating`: AO's review pass on the current head is running, or (`AutoInjectReview` and AO's pass requested changes), or (`AutoInjectCI` and CI failing)
5. `AutoReview && !approvedByAO` → `validating` (auto review owns the head until its own pass approves; failed/cancelled/not-yet-run passes all count as "not approved")
6. fallthrough → `needs_review` — "the PR is in its review cycle and no AO loop is turning it, so the next turn is a person's"

Session level: terminated → `archive`; no PRs → `building`. With several PRs, terminal (merged/closed) PRs are excluded while anything is live, then each PR's column is ranked (`ready` most actionable, then `needs_review`, `validating`, `building`) with ties broken by `UpdatedAt` then URL "so the board never flickers between equally ranked PRs" — and the winning PR is the one whose facts the display status reads.

`DisplayStatus` is then derived *inside* the chosen column from only the facts that column cares about ("so a session never shows a phrase belonging to a stage it is not in"): building shows Working/Blocked/Exited/No signal/Awaiting PR; validating shows Fixing CI failures / Addressing comments / Reviewing / Review scheduled / Draft / …; needs_review shows CI failing / Changes requested / Commented / Needs human review; ready shows Merged / Closed without merge / Mergeable / Approved. A blocked or exited worker outranks the loop it was running even inside `validating`.

## G. Stored vs recomputed; refresh

**Facts are stored; placements are recomputed on every read.** SQLite persists the `pr` table (draft/merged/closed flags, provider mergeability, `reviewDecision`, head SHA, semantic hashes `metadata_hash`/`ci_hash`/`review_hash`, `observed_at` timestamps — migration `0004_scm_observer_schema.sql`), `pr_checks`, `pr_comment` (only unresolved human threads), review runs, and session activity records. `SessionStatus`, `SCMStatus`, `KanbanColumn` and `DisplayStatus` are computed in `toSessionWithFacts` per API response and are in no table. A CDC migration (`0006_pr_session_changed_cdc.sql`) drives change propagation to clients.

Fact freshness:

- **SCM observer** (`backend/internal/observe/scm/observer.go`): PR/CI poll every **30s** (`DefaultTickInterval`), review-thread fetch at most every **2m** per PR, ETag caches, per-identity rate-limit cooldown 30s–10m, batches of 25, and `DefaultPRMaxAge = 5m` — a "bounded-staleness backstop, not a distrust of the ETag": a merged PR whose head SHA never changed can otherwise read stale forever.
- **CI** is not AO's own aggregation over checks: it maps GitHub's `statusCheckRollup.state` (`SUCCESS`→passing, `FAILURE`/`ERROR`→failing, `PENDING`/`EXPECTED`→pending, else unknown — `mapRollupState`, `backend/internal/adapters/scm/github/provider.go`). Individual check rows are stored for display and fix loops.
- **Merge readiness** (`domain.MergeReadiness.ReadyToMerge`, `backend/internal/domain/pr.go`): unknown or pending CI is a **blocker** — "AO only claims readiness it can actually prove"; changes-requested or any unresolved comment blocks; then `Mergeability == mergeable` decides.

## H. "Agent is working" vs "agent is stuck"

Three mechanisms, none of them a stored "stuck" status:

1. **Hook-driven activity** (`backend/internal/adapters/agent/activitystate/`): `session-start`/`user-prompt-submit` → active, `stop` → idle, `permission-request` → `waiting_input`. Claude Code, Codex and Droid have payload-aware derivers.
2. **`no_signal`** is the honest-uncertainty state: hooks expected, none ever arrived, quiet > 90s — commented as "AO cannot tell whether the agent is working or stuck (broken hook pipeline, blocked interactive prompt). Rendered instead of a confident idle." It lands in "Needs you".
3. **Stale-active reconciliation** (`backend/internal/observe/activity/observer.go` + per-adapter `DetectTerminalActivity`): every **30s**, sessions whose hook state has been quiet past **2m** (`DefaultStaleAfter`) get their last 40 terminal lines screen-inspected; Claude Code's detector reports idle only when the rendered surface *positively* shows an idle composer, because "a turn that dies without emitting its Stop hook (auth expiry, CLI crash, network loss) leaves the session stuck active forever." Hooks stay authoritative while they flow; the screen only demotes a stale `active`.

The mobile client also recognizes `stuck`/`errored` status strings in a fallback path (`packages/mobile/lib/sessionStatus.ts`), but no daemon code emits them — UNVERIFIED whether the private cloud service does.

## What the sources do not answer

- What `private/ao-cloud` contains, and whether the in-tree `cloud/` is its predecessor, mirror, or open core — the submodule target is private (404).
- Whether any AO surface distinguishes "agent is making progress" from "agent is running but looping" — nothing measures progress (diff growth, token spend, repeated output); the only stuck detectors are the signal-absence and stale-active ones above.
- How GitLab's derivation differs in detail (an adapter exists; not read).
- Real-world latency between a GitHub event and the card moving (30s poll + CDC push in code; no telemetry examined, product never run).
- Why the README still describes the zone board while desktop renders delivery lanes — recent refactor is the obvious inference, but the shallow clone has one commit and no history was read.

## Sources

- https://github.com/Untrivial-ai/agent-orchestrator — commit 5cb411aca2c080fc7050884ec2ceebcdce270672 (2026-08-31); metadata via api.github.com same day (10,765 stars, Apache-2.0, pushed 2026-08-31).
- `backend/pkg/contract/kanban.go` — column + display-status reducers, `KanbanReviewRunFacts`, `aoOwnsNextStep`.
- `backend/pkg/contract/status.go` — `DeriveStatus`, severity order, stacks, `silentPastGrace`.
- `backend/internal/service/session/status.go`, `kanban.go`, `service.go` — `noSignalGrace = 90s`, single clock read, derivation on read.
- `backend/internal/domain/status.go`, `pr.go` — "never stored", `MergeReadiness.ReadyToMerge`.
- `backend/internal/observe/scm/observer.go` — 30s tick, 2m review interval, 5m `DefaultPRMaxAge`, rate-limit cooldowns.
- `backend/internal/observe/activity/observer.go`, `backend/internal/adapters/agent/activitystate/activitystate.go`, `.../claudecode/terminal_activity.go` — hook mapping, stale-active screen-proof.
- `backend/internal/adapters/scm/github/observer_provider.go`, `provider.go` — `statusCheckRollup`, `reviewDecision`, `mapRollupState`.
- `packages/product-ui/src/session-presentation.ts` — attention zones, "Needs you", `toKanbanColumn` fallback; `frontend/src/renderer/components/SessionsBoard.tsx`; `packages/mobile/lib/agentsView.ts`, `sessionStatus.ts`.
- `backend/internal/storage/sqlite/migrations/0004_scm_observer_schema.sql`, `0006_pr_session_changed_cdc.sql`.
- `README.md`, `cloud/README.md`, `.gitmodules`.

## Spec input for the Loop's board

Rules worth copying, mapped to what the Selector already has:

1. **Derive on read; persist only facts.** AO stores PR/CI/review observations and computes every column per response. The `/loop` live board already does this ("the tracker as it is right now, columned by the same Eligibility predicate") — keep it; never add a stored column, and treat the Journal as AO treats SQLite: the fact store, not the placement store.
2. **Column first, then the phrase, from only that column's facts.** AO picks the lane from lifecycle facts and then derives the card's phrase inside the lane, so a card never wears a stage it is not in. The Loop's card `reason`/`detail` pair should follow the same discipline: an `awaiting-review` card's detail speaks only in check/review vocabulary, an eligible card's only in Eligibility vocabulary.
3. **"Needs you" as a computed union, not a label.** AO's `action` zone is a predicate: needs_input ∪ exited ∪ no_signal ∪ ci_failed ∪ changes_requested. The Loop's `ready-for-human` is today a *written* label (give-up or red checks). Worth adding: a derived "needs you" facet on top of the existing columns — an `awaiting-review` Proposal whose checks have gone red or that carries changes-requested belongs to the operator *now*, without waiting for anything to relabel it. The label records what the Selector concluded; the derived facet shows what GitHub says this second.
4. **The whose-turn split.** AO's `validating` vs `needs_review` is one review loop viewed by whose turn it is; policy flags (auto-retry CI, auto-address comments) decide the side. The Selector's analog is `attempts-exhausted`: a red-check Proposal with a retry remaining is the Selector's turn (validating); one with attempts spent is the operator's (`ready-for-human`). Deriving that from the Journal's attempt count plus live check state would give the board the same honesty.
5. **Current-head discipline.** AO excludes review runs recorded for an earlier head before any derivation, and separates its own approvals from humans' by review id, "because the aggregate cannot tell whose turn the loop is on." When the Loop reads checks or reviews on a Proposal, key them to the current head SHA and never let the agent's own artifacts count as external approval.
6. **Only claim readiness you can prove.** Unknown or pending CI blocks AO's `ready`; there is no optimistic default. The Loop's `awaiting-review` (green Proposal) should treat missing or pending check data as *not green*.
7. **Silence has its own state.** `SignalExpected && !HasSignal && quiet > grace → no_signal` instead of a confident idle, with a deliberately small grace (90s) sized to boot time. The Iteration watcher already reads the box's Progress Log while a Run is in flight; give it the same rule — an in-flight card whose log is expected and silent past a grace renders "no signal", not "in flight" — and back it with AO's second trick: a cheap positive proof (AO screen-scrapes the terminal; the Loop can stat the log or the branch) that only ever *demotes* a stale active reading, never promotes one.
8. **Bounded staleness even with delta mechanisms.** AO forces a re-fetch of any open PR every 5 minutes because its change guards don't track a PR's own merge/close. Whatever caching the board's GitHub reads grow, keep a max-age backstop.
9. **Worst-of severity with a fixed order; deterministic tie-breaks.** Multi-fact cards (or an issue with several Proposals) aggregate by an explicit severity list, ties broken by updated-time then URL so the board never flickers. One clock read per card so two related derivations cannot straddle a grace boundary.
