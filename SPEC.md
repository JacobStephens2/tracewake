# Tracewake Specification

Tracewake turns an issue into a reviewable pull request unattended, under an
operator-governed Termination Contract.

This specification defines the architecture, operational boundaries, and invariants
of Tracewake v0. It states Tracewake's design bets in the same terms as OpenAI's
Symphony specification, providing a direct, side-by-side comparison of two divergent
approaches to autonomous, issue-driven software engineering.

Terminology and domain models are defined in [CONTEXT.md](CONTEXT.md). Architectural
decisions are recorded in [docs/adr/](docs/adr/).

---

## Architectural Comparison: Symphony and Tracewake

Both Symphony and Tracewake address the same core objective: translating issues in a
tracker into merged code without continuous human supervision. However, they make
diametrically opposite architectural bets across all eight primary dimensions:

| Dimension | OpenAI Symphony | Tracewake |
| --- | --- | --- |
| **Work Discovery / Polling** | Continuous polling (default 30s) across active tracker workflow states (`Todo`, `In Progress`). | Scheduled Cycle (30m idle cadence or draining queue) filtering on an explicit Handover label (`ready-for-agent`) placed by an allowlisted operator. |
| **Workspace Model** | Persistent workspaces reused across runs for the same issue; dirty working trees preserved. | Ephemeral microVM / container boundary per Iteration. LLM context discarded between Iterations; state lives strictly in git (`Plan` and `Progress Log`). |
| **Concurrency & Capacity** | Concurrent agent swarms (default 10 parallel agents globally). | Serial execution within a Target; up to K concurrent Dispatches across Targets (default 1). Throughput bounded by human Review Cap (`review_cap`), not thread count or daily spend. |
| **Stall & Fault Handling** | Inactivity timeout (default 5m) triggering worker restart with exponential backoff. | Termination Contract with five predeclared bounds (no-ops, iteration clock, run clock, iteration cap, turn cap). Structural no-op detection. Clean exit 0 on planned end. |
| **What Done Means** | Agent drives issue to workflow-defined terminal state or automated acceptance. | Proposal-Only Output. Model completion claims are advisory. Done means a draft PR exists with verified CI checks, ready for human review. |
| **Who Lands the Work** | Orchestrator or agent lands code into main branch upon passing checks. | The human operator exclusively. Nothing merges itself. The Selector ensures proposal freshness against base. |
| **Blocked Issues** | Best-effort metadata collection (`blocked_by`); resolution left to agent prompt. | Deterministic server-side Eligibility predicate. Native blockers, sub-issues, open proposals, and spent attempts are filtered before dispatch. Incomplete tasks trigger a Loud Skip. |
| **Configuration Architecture** | In-repo `WORKFLOW.md` template with YAML front matter versioned inside the target codebase. | Out-of-repo configuration: instance environment (`tracewake.env`) and target declarations (`targets.toml`) on the controller. Target repository carries zero Tracewake files. |
| **Human Review & Interaction** | Workflow handoff states; user input signals surfaced per implementation. | Handover at the label; Queue Board (`/`) with five live columns; email notifications for finished runs; non-actionable issues routed to `ready-for-human`. |

---

## 1. Work Discovery: What it Polls

### Symphony's Approach
Symphony polls an issue tracker (such as Linear) at a high-frequency interval (default:
`polling.interval_ms = 30000`, 30 seconds). It selects candidate issues matching a
configured set of active workflow states (e.g., `Todo`, `In Progress`) and optional
required labels. Any issue matching the state filter is treated as eligible work for
an agent.

### Tracewake's Specification
Tracewake does not continuously poll tracker state transitions. Instead:

1. **The Cycle Cadence**: The Selector executes in discrete **Cycles** triggered by a
   systemd timer (default: `OnCalendar=*:00/30`, every 30 minutes). When active work
   exists, the Selector executes as a **Draining Cycle** (ADR 0021, ADR 0027),
   processing eligible issues until the queue is exhausted or capacity holds, with
   at most one Run per Target and up to K concurrent Dispatches across Targets.
2. **Handover at the Label**: Work discovery is governed by the **Handover** (ADR 0010,
   ADR 0014). An issue is only considered if it carries the target's configured
   `ready` label (default: `ready-for-agent`).
3. **Provenance & Allowlist Gating**: The Handover requires trust. The Selector inspects
   the issue timeline: the most recent application of the `ready` label must have been
   performed by an operator listed in the target's `labeler_allowlist` (configured in
   `targets.toml`). An issue labeled by an unknown user is skipped with reason
   `labeler-not-allowlisted`.
4. **The Issue Contract**: The issue body must satisfy the contract:
   - `## Acceptance criteria` (**required**): A markdown section containing bulleted
     criteria. This section is parsed at Seeding and copied verbatim into the agent's
     Plan. An issue lacking this section triggers a **Loud Skip** (`missing-section`):
     the Selector posts an explanatory comment, swaps the label to `needs-info`, and
     returns the issue to the author.
   - `## Owning area` (*optional*): A phrase constraining the scope of the Run. If
     omitted, the Run is scoped to the issue's title.
   - `## Check` (*optional*): A command executed inside the microVM to grade progress.

---

## 2. Workspace Model: Isolation & Ephemeral Boundaries

### Symphony's Approach
Symphony creates a workspace per issue on the host filesystem under a deterministic
path. Workspaces are **persistent across runs**: if an agent is stopped, crashes, or is
retried, the existing directory and its accumulated mutations are preserved and reused.
Workspaces are only deleted when an issue reaches a terminal tracker state.

### Tracewake's Specification
Tracewake rejects persistent workspaces and ambient agent state, implementing the
**Ralph technique** within strict **Execution Boundaries** (ADR 0003, ADR 0011):

1. **Ephemeral MicroVM Boundary**: Each Iteration executes within an isolated
   microVM or container created by the Execution Boundary runtime (e.g., Docker
   Sandboxes `sbx`).
2. **Context Discard**: When an Iteration finishes (or is terminated by its turn or clock
   bound), the agent process exits, the microVM is destroyed, and the LLM context
   window is completely discarded.
3. **State on Disk in Git**: State across Iterations lives exclusively as tracked files
   in the repository branch:
   - **The Plan** (`.agents/`): Generated at Seeding from the issue's acceptance criteria
     and updated by the agent at each Iteration.
   - **The Progress Log** (`PROGRESS.md`): An append-only log recording what each
     Iteration attempted, verified, and discovered.
4. **Boundary Destruction Guarantee**: The boundary microVM is destroyed unconditionally
   at the end of every Iteration - whether the agent exited 0, failed with a runtime
   error, or was killed by a timeout bound. No two Iterations ever share a microVM.
5. **No Host Credential Exposure**: Host credentials, fleet private keys, and
   metered API tokens are never mounted or exposed inside the boundary (ADR 0009,
   ADR 0020).

---

## 3. Concurrency: Serial Execution and Review Capacity

### Symphony's Approach
Symphony is designed around concurrent agent execution. It supports up to 10 concurrent
agent instances by default (`agent.max_concurrent_agents = 10`), with optional limits
partitioned by tracker state (`max_concurrent_agents_by_state`).

### Tracewake's Specification
Tracewake enforces **strictly serial execution** per target, with an instance-wide
cap on how many Targets may run at once:

1. **Single-Run Serialization**: Exactly one Run executes on the box at any given time
   for a target. An in-flight Journal event (`run.dispatched` without a matching
   `run.outcome`) prevents a second Dispatch on that Target. Across Targets a Cycle
   may hold up to K concurrent Dispatches (ADR 0027); K is instance configuration
   defaulting to 1, which is today's serial drain. A Postgres advisory lock still
   keeps one Selector process at a time.
2. **The Draining Cycle**: A single Cycle drains the queue (ADR 0021). It reads the
   tracker, picks the lowest-numbered eligible issue, seeds the branch, dispatches
   the Run, observes completion, routes the issue, updates open proposal branches,
   and loops to pick the next eligible task. With K greater than 1, Dispatches on
   different Targets overlap; within a Target they stay serial.
3. **Review Capacity Replaces Spend Caps**: Unattended throughput is bounded by the
   human operator's ability to review code, not by artificial daily quotas or thread
   counts (ADR 0022). Each target declares a `review_cap` in `targets.toml` (default: 20).
4. **Halting on Capacity**: Before each dispatch within a drain pass, the Selector reads
   the number of open Proposals currently in the `awaiting-review` state. If this count
   equals or exceeds `review_cap`, dispatch halts with reason `review-cap-reached`.
   Throughput resumes immediately once the operator reviews and merges or closes
   pending proposals.

---

## 4. Stall Handling: How a Stall Ends

### Symphony's Approach
Symphony monitors active agent runs using an inactivity timer (`stall_timeout_ms`,
default 300,000 ms / 5 minutes). If no activity occurs within this window, the worker is
killed and retried using exponential backoff:
`delay = min(10000 * 2^(attempt - 1), max_retry_backoff_ms)`.

### Tracewake's Specification
Tracewake treats run termination as a contract rather than an exceptional timeout. It
operates under a formal **Termination Contract** (ADR 0007):

1. **Five Predeclared Bounds**: Every Run executes under five explicit numbers:
   - `iteration-cap`: Maximum total Iterations in a Run (default: 10).
   - `run-clock`: Maximum total wall-clock seconds for the entire Run (default: 1800s / 30m).
   - `consecutive-noops`: Maximum consecutive Iterations where git `HEAD` remains
     unchanged (default: 3).
   - `turn-cap`: Maximum tool interactions/turns within a single Iteration (default: 25).
   - `iteration-clock`: Maximum wall-clock seconds for a single Iteration (default: 600s / 10m).
2. **Structural No-op Detection**: Tracewake detects stalls structurally rather than
   heuristically. If an agent completes an Iteration without committing changes to git,
   it counts as a No-op Iteration. Three consecutive no-ops trigger immediate
   termination under the `consecutive-noops` bound.
3. **Honest Exit Codes & Planned Ends** (ADR 0007): The Run is honest about whether it
   worked. Reaching the `iteration-cap` with no killed or failed iterations is the
   Run's planned end and exits 0. A Run cut short by another bound exits non-zero
   naming the bound: `run-clock` exits 2, `consecutive-noops` exits 3, and an iteration
   fault exits 5. Reaching a bound is the Termination Contract functioning as designed
   and is distinguished from an unhandled process crash.
4. **Retry Semantics**: If a Run is cut short by `run-clock`, `consecutive-noops`, or
   agent failure on its first attempt, the Selector leaves the issue in the queue with
   its `ready` label intact. On the next cycle, it is dispatched again on the **same
   branch**, preserving previous progress in `PROGRESS-earlier.md`.
5. **Two-Attempt Limit**: If a second attempt also terminates without a successful
   proposal, the issue is routed to `ready-for-human`. Nothing is dispatched a third
   time (`attempts-exhausted`). Blind exponential retry loops are barred.

---

## 5. What Done Means

### Symphony's Approach
In Symphony, an agent can declare an issue "Done" or drive it to a workflow-defined
terminal state. If configured, Symphony can automatically land and merge pull requests
once CI checks pass.

### Tracewake's Specification
Tracewake enforces **Proposal-Only Output** (ADR 0013):

1. **Advisory Completion Promises**: A model's completion signal (e.g. emitted text
   claiming the task is finished) is purely advisory. It carries no authority and never
   terminates a Run or marks work as complete.
2. **The Proposal Artifact**: The sole external output of a completed Run is a draft pull
   request (the **Proposal**) opened against the target repository's default branch.
3. **Automated Verification**: When a Run finishes, the Selector queries the forge for
   the status of CI checks on the Proposal branch (`SELECTOR_CHECKS_TIMEOUT_SECONDS`,
   default 900s).
   - If CI checks pass (**green**), the Selector routes the issue to `awaiting-review`.
   - If CI checks fail (**red**), never settle, or no checks ran, the Selector posts a
     comment detailing the failure and routes the issue to `ready-for-human`.
4. **Definition of Done**: Work is "Done" only when a human reviewer has inspected the
   pull request, verified the diff and test coverage, and merged the branch.

---

## 6. Who Lands the Work

### Symphony's Approach
Symphony supports automated landing: the orchestrator itself merges accepted PRs directly
into the upstream branch without requiring human interaction on every unit of work.

### Tracewake's Specification
In Tracewake, **the human operator exclusively lands work**:

1. **No Autonomous Merges**: Tracewake holds no credentials or permissions allowing it
   to merge pull requests or push directly to protected default branches.
2. **Proposal Freshness on Drain**: To prevent open Proposals from rotting while
   awaiting human review, the Selector checks all open Proposals during each drain pass
   (ADR 0023). Any Proposal that is behind its base branch and mergeable is
   automatically rebased/updated via `gh pr update-branch` and journaled once
   (`proposal.updated`).
3. **Conflict Visibility**: If a Proposal encounters merge conflicts (`CONFLICTING` or
   `DIRTY`), Tracewake refuses to force-update it. The Proposal is flagged on the Queue
   Board with a visual conflict badge.
4. **Reconcile Runs** (ADR 0030): A conflicting Proposal is not left to rot. During
   each drain pass, the Selector dispatches a reconcile Run for it on the box: the base
   branch is merged into the Proposal branch inside the microVM boundary, conflicts are
   resolved there, the merged branch is verified against the owning issue's Check
   suite, and only then is the Proposal branch pushed - never force-pushed, and never
   merged to the default branch. Success is journaled once (`proposal.reconciled`).
   A reconcile that cannot resolve the conflicts or reds the suite leaves the Proposal
   un-merged and escalates the owning issue to `ready-for-human`
   (`proposal.reconcile-failed`).

---

## 7. How Blocked Issues Are Treated

### Symphony's Approach
Symphony collects issue dependency relationships as best-effort metadata (`blocked_by`).
However, it does not enforce dependency logic server-side. It delegates blocker
evaluation to the agent's prompt template, relying on the agent to decide whether it can
proceed.

### Tracewake's Specification
Tracewake enforces a strict, deterministic **Eligibility Predicate** on the controller
before any work can be dispatched (ADR 0014):

1. **Server-Side Blocker Evaluation**: The Selector queries native tracker issue links
   via the tracker source (`gh`). An issue is ineligible if it has open blocking
   dependencies (`blocked-by-open-dependency`) or open sub-issues (`has-open-sub-issues`).
   Freeform text like "Blocked by #12" in the markdown body is deliberately ignored to
   prevent ambiguous natural-language parsing.
2. **Quiet Skips vs. Loud Skips**:
   - **Quiet Skip**: Issues that are blocked by open dependencies, have open proposals,
     exceed retry budgets, or were labeled by non-allowlisted accounts are skipped
     silently and journaled in `cycle.finished`. A blocked issue is not spammed with
     recurring comments every 30 minutes.
   - **Loud Skip**: If an issue is otherwise eligible but violates the Issue Contract
     (missing `## Acceptance criteria`), the Selector comments on the issue naming the
     exact missing section, swaps `ready-for-agent` to `needs-info`, and journals
     `issue.returned`.
3. **Parent Specs**: A parent tracking issue with open child tickets is never dispatched
   as a unit of work (`has-open-sub-issues`). Only leaf issues can be dispatched.

---

## 8. What is Configurable

### Symphony's Approach
Symphony configures workflow policy through an in-repository `WORKFLOW.md` file located at
the root of the target project. The target repository versions the agent's system prompt,
tool configurations, tracker settings, and runtime parameters alongside its source code.

### Tracewake's Specification
Tracewake keeps configuration strictly **outside the target repository**:

1. **Zero Target Repository Pollution**: The target codebase contains zero Tracewake
   configuration files, zero workflow templates, and zero CI actions. The target
   repository remains completely agnostic of Tracewake's existence.
2. **Two-Tier External Configuration**:
   - **Instance Configuration** (`tracewake.env`): Instance-wide environment variables
     defining the database Journal DSN, box execution command (SSH or local), window URL,
     notification command, guardrail write-protection rules, and operational timeouts.
   - **Target Declarations** (`targets.toml`): A structured TOML file defining one
     `[[target]]` stanza per repository worked:
     - `repo`: Tracker repository (`owner/name`).
     - `work_repo`: Controller checkout path for Seeding.
     - `box_repo`: Box checkout path where the Run executes.
     - `token_file`: Path on the box holding the fine-grained repository token.
     - `guest_template`: MicroVM template image (e.g. `loop-php:1`, `loop-python:1`).
     - `labeler_allowlist`: List of trusted GitHub usernames whose Handover is valid.
     - `labels`: Mapping for `ready`, `needs_info`, `review`, and `human` labels.
     - `review_cap`: Maximum open proposals awaiting review (default: 20).
     - `landing`: Output policy (`propose`).
3. **Refusal Preflight (No Defaults for Instance Facts)**: The codebase contains no
   hardcoded defaults for hostnames, IP addresses, person names, or repositories
   (ADR 0003). Any required configuration missing from `tracewake.env` or `targets.toml`
   aborts the cycle immediately at preflight with the missing key named.

---

## 9. How a Human is Asked

### Symphony's Approach
Symphony supports workflow handoff states (e.g., `Human Review`) but defines no unified
human-interaction protocol. Runtime questions from agents are handled in an
implementation-specific manner (failing the run, prompting an operator terminal, or
auto-resolving).

### Tracewake's Specification
Tracewake establishes five clear, predictable interaction surfaces across the
**Attendedness Spectrum** (ADR 0003, ADR 0016):

1. **The Handover**: The operator specifies acceptance criteria and adds `ready-for-agent`.
   No further human intervention is required until the Run completes.
2. **The Queue Board** (`/` on the web window): A five-column real-time board
   representing the entire queue state:
   - `eligible`: Issues meeting all criteria, ordered lowest-number first.
   - `blocked`: Issues gated by open dependencies, sub-issues, or missing sections.
   - `in-flight`: Currently executing Runs and unrouted proposals.
   - `awaiting-review`: Finished proposals with green CI checks waiting for human review.
   - `ready-for-human`: Runs that failed checks, hit consecutive stalls, or require
     manual operator resolution.
   Beside the queue, the Host's live headroom (CPU, memory, and the disk this
   checkout lives on) and the count of Runs in flight, derived from the
   Journal's in-flight predicate. The figures ride the board's live region.
   Per-Run resource attribution is out of scope.
3. **Run Notifications**: When a Run finishes, the Selector invokes
   `SELECTOR_NOTIFY_COMMAND` (e.g., an email or messaging bridge), delivering a
   structured notification containing the outcome bound, duration, iteration count, and
   link to the Proposal PR.
4. **Pause Control**: An admin can toggle the pause flag directly on `/loop`. A
   paused Selector continues to observe and journal cycles, but suspends new dispatches
   without killing in-flight Runs or disabling the timer.
5. **Human Escalation**: If an agent cannot solve a problem within its Termination
   Contract and retry budget, the issue is routed to `ready-for-human` with the failing
   checks or error reasons posted as a comment. The human operator is never asked
   mid-run interactive questions while the agent runs unattended.
