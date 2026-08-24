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
what is blocked. Session-scoped, deleted when the Run's work ends; not permanent
documentation.
_Avoid_: progress.txt, changelog, journal

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

**No-op Iteration**:
An Iteration after which `HEAD` is unchanged. Consecutive No-op Iterations are the
project's non-progress signal; neither published Ralph source has one.
_Avoid_: failed iteration, stall

**Completion Promise**:
A string the model emits to claim the work is finished. It is advisory evidence
only and never ends a Run on its own, because nothing verifies it and it is
documented lying.
_Avoid_: completion signal, done marker

## Trust and boundaries

**Attendedness**:
Whether a human is present to notice and intervene while the agent acts. It is a
fourth trust axis alongside blast radius, reversibility, and trust model, and
unlike those three it does *not* collapse at single-operator scale.
_Avoid_: supervision, HITL

**Execution Boundary**:
The mechanism the agent cannot cross while unattended. The one subsystem that
Attendedness re-earns after the single-operator collapses have deleted the rest of
the isolation apparatus.
_Avoid_: sandbox, container, isolation stack

**Proposal-Only Output**:
The invariant that a Run's sole external effect is a draft pull request a human
merges. No push to a protected branch, no merge, no deploy, no `apply`, no write to
a live third-party API.
_Avoid_: PR-per-iteration, output contract

**Verified Commit**:
A commit signed by a key registered to the operator's GitHub account. In this
context the badge asserts that the operator *caused* the commit, not that they
hand-wrote it - a deliberate reinterpretation, recorded in ADR 0005.
_Avoid_: hand-authored, human-written, trusted commit
