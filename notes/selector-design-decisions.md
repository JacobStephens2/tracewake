# Selector design decisions

*Grilling session, 2026-08-25/26. Every decision below was put to the operator
and answered; the trust ruling is ADR 0014, the vocabulary is in CONTEXT.md.
This note is the record the spec and tickets get written from.*

## What is being built

The Loop already works tourbot issues one at a time, seeded and started by
hand. The extension is everything between the label and the Run: a **Selector**
on the orchestration VM that drains the `ready-for-agent` queue unattended,
plus the dashboard that shows it happening. Five gaps close: selection,
unattended Seeding, Run-branch creation, initiation, and issue bookkeeping.

## Trust and selection (ADR 0014)

- Applying `ready-for-agent` is the Handover. Labeler allowlist: exactly
  `JacobStephens2` for now; widening to `vsto-eta` is one allowlist line.
- The box is untouched: token keeps no Issues permission; the Selector runs
  off-box as deterministic code, never an agent.
- Eligible = labeled by an allowlisted operator AND
  `issueDependenciesSummary.blockedBy == 0` AND no open sub-issues AND no open
  Proposal AND retry budget unspent. Native edges are the only blocking
  signal - prose "Blocked by" sections no longer count.
- The label now promises machine-readable sections: `Acceptance criteria`
  (seed-run.sh already refuses without), `Owning area` (feeds `--area`),
  optional `Check` (feeds `--check`). Missing section: skip loudly - comment
  naming the gap, swap to `needs-info`.
  **Amended 2026-08-27 (issue #156): `Owning area` is optional.** An issue
  without it is scoped to its own title. The measurement that decided it: of
  the 10 issues the first live dry-run found otherwise ready on 2026-08-26, 10
  lacked the section - a contract nobody was writing to, whose only effect
  would have been to hand the queue back on the first unattended cycle. See
  ADR 0014 and `selector/README.md`, "The issue contract".
- Accepted residuals recorded in ADR 0014: post-label body edits; the
  read-write skills mount (edits surface in the Proposal diff).

## Mechanics

- Selector: systemd timer on this VM, every 30 min, 24/7. At most one Run in
  flight (serial; concurrency stays a later knob - branch-per-issue already
  permits it). Cap: 4 Runs/day. Ordering: lowest eligible issue number.
- Dispatch: create Run branch, `seed-run.sh`, push, `ssh` the box,
  `run.sh --repo ~/tourbot --task-ref ... --propose --notify`; the Selector
  captures the stdout summary (today it evaporates).
- Done = draft Proposal open AND checks green. Then the issue gets
  **`awaiting-review`** (new label). Merge closes it via a `Closes #n` line in
  the Proposal body. Merging and deploying stay human; review-comment
  iteration is deliberately out of v1.
- Run fails (agent-failed, run-clock, consecutive-noops): one automatic retry
  on the same branch, then swap to `ready-for-human` with a comment saying
  what happened. Never a third attempt, never silence.
- Checks red on a clean Run: no automated repair in v1 - comment the failing
  check names, swap to `ready-for-human`. (CI-feedback repair Runs are a
  possible v2; the agent cannot see CI, so feedback would have to be committed
  into the branch.)
- Selector's own failures page through the existing
  `notify-unit-failure@` machinery (`OnFailure=` in `[Unit]`).
- Run Notification stays the PR comment (ADR 0013). No new surface.

## Skills (Pocock orientation)

- tourbot already vendors 25 skills at `.agents/skills/` with committed
  `.claude/skills/` symlinks; the microVM workspace mount exposes them.
  Updates arrive as reviewed re-vendor commits, never live-sync.
- The Run occupies Pocock's user-invoked slot, so the Iteration prompt absorbs
  `/implement`'s checklist and names the model-invocable discipline skills:
  `/tdd` for code work, `/diagnosing-bugs` for bugs, `/code-review` before
  commit. No frontmatter forks - the user-invoked/model-invoked axis is his
  load-bearing rule and stays intact.
- v1 attempts a PHP-capable sandbox guest (PHP 8 + composer) so `/tdd` is
  real; pre-agreed fallback to the stock image if the template work exceeds
  about a day. This is where the build risk lives.

## Dashboard and Journal

- New pages in `lab/webapp` (FastAPI + Jinja2 + HTMX + vendored
  **terminal.css**, the factory dashboard's own aesthetic). The factory's
  Rust dashboard contributes its lessons - scribe and window, zero business
  logic (A53) - not its code.
- **Selector Journal in PostgreSQL** on this VM (operator's call: least
  painful part of the factory build). Local instance, peer auth over the unix
  socket - zero new credentials, nothing changes on the box. Append-only by
  discipline. GitHub stays the only work source; the Journal is write-only
  downstream.
- `LISTEN/NOTIFY` -> SSE to the browser. v1 NOTIFY drives SSE **only**;
  triggering work from the dashboard ("Run this now") is deferred with its
  seam ready. Control flow stays single-sourced in the timer, plus a pause
  flag the page may toggle.
- Liveness: a watcher on this VM SSH-reads the box checkout's PROGRESS.md
  (~60s) during a Run and journals Iteration records; SSE makes delivery
  instant, freshness is bounded by that poll. Transcript tailing stays out -
  deleting transcripts is a Loop design decision, not an accident.
- v1 views: queue board (label kanban with blocking edges), current Run
  panel, Run history (from the Journal, which outlives merged branches),
  daily-cap budget. Nothing else.

## Tracker hygiene (executed and verified 2026-08-26)

Run by the operator via `tasks/tourbot-tracker-hygiene-20260826.sh` (kept for
its verify block; idempotent):

- native dependency edges added: 646<-645, 649<-648, 650<-648, 651<-648 -
  each now reports exactly 1 open blocker, as it should;
- broken `- #` prose repaired on 474-478 (474/475 -> #473, 476/477/478 -> #474);
- #547 closed (21/21 sub-issues complete);
- `awaiting-review` label created (#8250DF, "Agent proposal open with green
  checks, awaiting human review").

Native edges and prose now agree on all 29 issues: eligibility is a single
GraphQL predicate with no text parsing.


## Build-phase tasks

Alongside the Selector itself:

- ~~**Write protection for the unattended-executed paths.**~~ Done in #165.
  A direct push to this repo's master could change `loop/` and the Selector's
  code, while tourbot - the repo a Run makes Proposals against - was PR-gated:
  the code that runs unattended had less protection than the code it changes.
  (From the from-zero repo-home exercise, 2026-08-26; the repo-home answer
  itself was: stay in orchestration.)

  Both halves of the option named here turned out to be needed, and neither
  is a ruleset over paths. The `Protect master` ruleset gates the whole
  branch - `pull_request`, `non_fast_forward`, `deletion` - and a *path*-scoped
  push rule would have been the wrong instrument anyway: it blocks pushes to
  every branch, which is how the Selector's own Run branches reach GitHub. And
  a ruleset alone proves nothing about what runs, because this checkout is
  shared and group-writable and systemd execs what is sitting in it. So the
  guardrail reads both: the rules on the deployment ref, and whether the
  deployed tree still matches it. See `selector/README.md` (Write protection)
  and `notes/selector-write-protection-evidence.md`.
- Add `awaiting-review` to tourbot's `docs/agents/triage-labels.md`.

## Deferred, explicitly

- Review-comment iteration on Proposals; CI-feedback repair Runs.
- "Run this now" dashboard button (first NOTIFY-consumer candidate).
- Concurrent Runs; the orchestration repo's own queue (needs a second
  repo in the box token - a deliberate decision, not a config tweak).
- Transcript persistence/tailing (would reverse a recorded design stance).
- Widening the labeler allowlist to `vsto-eta`.
