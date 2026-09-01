# Single-User Factory

The language of a lightweight software factory with one trusted operator, and of
the unattended loop that resolves its requests. Sibling to
[ETA Factory](../../eta-factory/CONTEXT.md), which is company-governed and
defends a different threat model.

The evidence behind the loop terms and the trust and boundary terms is
[the Ralph research](research/2026-08-24-ralph-loop-and-isolation.md), a
source-cited investigation of both published Ralph sources, Docker Sandboxes,
`sandcastle`, ETA's preview slots, and the ETA Factory's Firecracker workers.

## The loop

**Ralph**:
The published technique: a shell loop that re-invokes a *fresh* agent process per
Iteration, keeping all state on disk in the repo. Huntley's and Matt Pocock's, not
ETA's.
_Avoid_: the Loop, our Ralph

**Loop**:
ETA's single-operator instantiation of Ralph: the Run, its Termination Contract,
the box it executes on, and the Proposal-Only Output it produces. Sibling to the
ETA Factory, which solves the same problem at a company threat model.
_Avoid_: Ralph, the Ralph loop, the agent

**Iteration**:
One agent process, launched and exited. Context is discarded at its boundary, and
that discard is the point of the technique.
_Avoid_: turn, cycle, pass

**Run**:
An ordered sequence of Iterations executing under one Termination Contract.
_Avoid_: session, job, loop

**Plan**:
The file holding scope and the task list, read at the start of every Iteration and
edited as work proceeds. It is what a fresh context reads to learn where it is.
_Avoid_: PRD, prd.json, fix_plan.md, spec

**Progress Log**:
The append-only record of what each Iteration did, what it decided and why, and
what is blocked. Initialized by Seeding and appended to from then on.
Session-scoped, deleted when the Run's work ends; not permanent documentation.
A second Run on the same branch gets a fresh one: the Selector moves the
previous Run's into `PROGRESS-earlier.md` before Seeding, because Seeding
refuses to overwrite a log that records a Run and that record is the only
account of what the Run did (#301).
_Avoid_: progress.txt, changelog, journal

**Seeding**:
The setup step that turns one task the operator chose, fetched by number, into a
Plan and an initialized Progress Log. It is a human handing over a task he
authored, which is what makes ADR 0003's content-trust collapse valid - and it is
emphatically *not* issue intake, which would have the Loop read text the operator
never saw. ADR 0010. Since ADR 0014 the Handover happens at the label, and the
Selector performs Seeding mechanically for a task the operator has labeled.
_Avoid_: intake, ingestion, triage, importing an issue

**Handover**:
The operator's attestation that he authored or read a task and authorizes an
unattended Run on it. ADR 0010 located it at the seed command; ADR 0014 moves it
to applying `ready-for-agent`, which is why the Selector may seed with nobody at
a keyboard.
_Avoid_: approval, sign-off, triage

**Selector**:
The deterministic automation, off the box, that turns a Handover into a started
Run: it reads the tracker as the operator, applies Eligibility, performs
Seeding, and dispatches. It decides which task and when; it never does the work,
and it is not an agent - no model output executes in it (ADR 0014).
_Avoid_: scheduler, dispatcher, intake

**Cycle**:
One execution of the Selector: read the tracker, apply Eligibility to the whole
labeled queue, order, apply the caps, journal the reasoning, and dispatch at
most one Run. The timer's unit of work, and the Journal's unit of grouping -
every event of one Cycle carries its id. It is emphatically not an Iteration,
which is why "cycle" is a word the Iteration entry above tells you to avoid:
a Cycle chooses work and an Iteration does it.
_Avoid_: tick, sweep, poll, pass

**Dispatch**:
The Selector's act of turning one picked task into a started Run: the Run
branch, Seeding, the push, and the start on the box with propose-and-notify.
Mechanical and holding no judgement - what to work is the Cycle's decision and
how to work it is the Run's. A Dispatch blocks for the length of its Run,
because the box persists no record of one and the summary exists only while
something is holding the process.
_Avoid_: trigger, kick off, schedule, launch

**Loud Skip**:
Skipping a labeled task by handing it back rather than by passing over it: a
comment naming what the task is missing, a swap to `needs-info`, and a
journaled skip. Reserved for a task that broke the promise the label makes -
today, a missing `Acceptance criteria` section. Every other
skip is quiet, because a blocked task commented on every half hour is a queue
nobody reads.
_Avoid_: rejection, bounce, failing an issue

**Route**:
What the Selector does to the issue once its Run has ended: the swap to
`awaiting-review` (a Proposal with green checks), to `ready-for-human` (red
checks, checks that never settled, or a second failed Run), or the deliberate
non-swap of a first failure, which leaves the task in the queue to be picked
again. Decided from two facts only - the Run's ending bound and the Proposal's
checks - and journaled under its own event kind, so the Journal is readable by
outcome. A Route is bookkeeping, not judgement: it moves a card, it never
decides whether the work is good.
_Avoid_: triage, verdict, disposition, grading the Run

**Eligible**:
The predicate a labeled task passes before the Selector may seed it: labeled by
an allowlisted operator, no open blocking dependency - native tracker edges
only - no open sub-issues (a parent spec is not a unit of work), no open
Proposal, retry budget unspent.
_Avoid_: unblocked, ready (the labels already own that word)

**Selector Journal**:
The append-only record of the Selector's decisions and of Run outcomes: what was
eligible, what was picked, what was skipped and why, how each Run ended, and
where its Route put the task. Read by the dashboard and by nothing that decides
work - the tracker remains the only work source. It is also where the retry
budget is counted from, which is why a Route that GitHub refused still leaves a
task that cannot be dispatched a third time.
_Avoid_: ledger, log, queue

**Queue Board**:
The tracker's whole labeled queue as five columns on `/loop`, read when the page
is requested rather than replayed from the Selector Journal: Eligible, blocked,
in flight, `awaiting-review`, `ready-for-human`. Its columning is the Selector's
own Eligibility predicate, imported - a board that decided for itself which
tasks were Eligible would be a second Selector, and their first disagreement
would be a bug in whichever one you did not read.
_Avoid_: kanban, backlog, dashboard (the page is the dashboard; this is one
panel on it)

**Run History**:
Every Run that has ended, on `/loop/history`, replayed from the Selector
Journal and from nothing else: its Bound, how long it took, and its Proposal.
Reading nothing but the Journal is the point rather than an economy - it is
what lets a Run stay inspectable after the branch it worked on has been merged
and deleted, and what keeps the record readable when the tracker cannot be
reached. The Run in flight is not history and is not on it.
_Avoid_: log, audit trail, archive

**Owning Area**:
The one part of a task a single Run is scoped to, named in the Plan at Seeding.
The rest of the task is out of scope for that Run and is not remaining work: an
Iteration that starts on it has left the Plan rather than found more of it. The
same word the Inventory groups by.
_Avoid_: slice, chunk, batch, phase

**Termination Contract**:
The predeclared set of conditions that end a Run, and the conditions that end a
single Iteration within it. It is the sole cost control when the agent
authenticates against a subscription rather than a metered credential.
_Avoid_: exit condition, iteration cap

**Bound**:
One condition of the Termination Contract. There are five, and a Run names the
one that ended it: `iteration-cap`, `run-clock`, `consecutive-noops`,
`agent-failed`. So "iteration cap" is the right name for one bound and the wrong
name for the whole Contract, which is what the entry above is guarding.
_Avoid_: limit, guardrail, timeout

**Discipline Skills**:
The model-invocable skills an Iteration is told to invoke for itself - `/tdd`
for code work, `/diagnosing-bugs` for something broken, `/code-review` before
every commit - because a Run occupies the slot a person would have invoked
`/implement` from. Declared once in `contract.sh` and written into the Progress
Log with the Contract summary, so the record of a Run says what discipline it
was asked for. Not a Bound: naming them ends nothing.
_Avoid_: rules, guidelines, workflow

**No-op Iteration**:
An Iteration after which `HEAD` is unchanged. Consecutive No-op Iterations are the
project's non-progress signal; neither published Ralph source has one.
_Avoid_: failed iteration, stall

**Completion Promise**:
A string the model emits to claim the work is finished. It is advisory evidence
only and never ends a Run on its own, because nothing verifies it and it is
documented lying.
_Avoid_: completion signal, done marker

## The first task's grade

**Completeness Check**:
The mechanical grade on a task that has no test suite: it derives every
Occurrence from the checkout, reads the Inventory, and exits non-zero when the
Inventory does not account for one. Backpressure while a Run is happening and
acceptance afterwards; the same script in both roles.
_Avoid_: the linter, the validator, the audit script

**Occurrence**:
One line of tracked application code naming the symbol under audit. Line
granularity, not match granularity: a line naming the table twice is one thing to
decide about. The count of them is the Completeness Check's denominator, derived
every run and never read from the task (ADR 0008).
_Avoid_: hit, match, reference

**Inventory**:
The document the first task produces: every Occurrence classified, grouped by the
area that owns it, each entry carrying the rationale its classification requires.
A document a human reviews, not a data file - the Completeness Check picks the
entries out of it.
_Avoid_: the audit, the list, the classification file

## Trust and boundaries

**Attendedness**:
Whether a human is present to notice and intervene while the agent acts. It is a
fourth trust axis alongside blast radius, reversibility, and trust model, and
unlike those three it does *not* collapse at single-operator scale.
_Avoid_: supervision, HITL

**Attended Preview**:
Unreviewed code running where a human is looking at it: one branch served from
its own instance so it can be evaluated without being merged first. Permissible
on the VM that holds production credentials only because Attendedness holds -
which is what separates it from model output executing unattended, and why the
name carries the reason rather than the deployment (ADR 0016).
_Avoid_: staging, staging instance, preview slot, dev instance

**Execution Boundary**:
The mechanism the agent cannot cross while unattended. The one subsystem that
Attendedness re-earns after the single-operator collapses have deleted the rest of
the isolation apparatus.
_Avoid_: sandbox, container, isolation stack

**Executed Path**:
A path in this repository that a timer, or model output, runs with nobody
watching: the Loop's scripts, the Selector, and the unit and wrapper that invoke
it. Declared in `selector/guardrail-sources/paths.txt` and named as a set because
the standard applies to the set - the unattended executor must be no easier to
change than the repository a Run makes Proposals against. Distinguished from the
rest of the repository, which is a working area.
_Avoid_: protected file, the code, the scripts

**Guardrail**:
The check that the Executed Paths cannot change without a review, and the chip on
`/loop` that reports it. Two halves, because either alone can be green over
unreviewed code: the rules GitHub holds over the ref those paths are deployed
from, and whether the deployed tree still matches that ref. Read once per Cycle
and journaled; unknown is never green. It reports, and does not gate Dispatch.
_Avoid_: branch protection, the ruleset (one half of it), lock

**Proposal-Only Output**:
The invariant that a Run's sole external effect is a draft pull request a human
merges. No push to a protected branch, no merge, no deploy, no `apply`, no write to
a live third-party API. A Run Notification is a comment on that same pull request
and so is inside the invariant rather than an exception to it.
_Avoid_: PR-per-iteration, output contract

**Run Notification**:
How a finished Run reaches the operator who walked away from it: the Selector's
email, sent off the Journal's outcome row, with the Run's comment on its own
proposal as the on-PR record - naming the bound that ended the Run, the exit
code and the proposal. Per Run rather than per box, and unable to change what
the Run did - it moves no exit code and is written into no Progress Log
(ADR 0013; carrier moved to the Selector by ADR 0018, amended).
_Avoid_: alert, page, report

**Agent Adapter**:
The one substitutable command a Run launches per Iteration: a prompt file and a
turn bound in, the agent's exit status out. Everything a vendor does differently -
its template or lack of one, its egress hosts, the words it prints on reaching the
turn bound, the environment names that supersede its subscription - lives here and
nowhere else, which is what makes the agent a variable rather than a decision
(ADR 0004).
_Avoid_: the agent, the backend, the driver

**Structural Property**:
Something true of the Execution Boundary and the Loop's shape, so true whatever
agent runs. Distinguished from a **per-agent property**, which is true of one
vendor and must be re-established for the next. A property observed under one
agent is per-agent until a second has shown it (ADR 0012).
_Avoid_: guarantee, invariant (both are used for the Loop's own invariants)

**Verified Commit**:
A commit signed by a key registered to the operator's GitHub account. In this
context the badge asserts that the operator *caused* the commit, not that they
hand-wrote it - a deliberate reinterpretation, recorded in ADR 0005.
_Avoid_: hand-authored, human-written, trusted commit
