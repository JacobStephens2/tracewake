# The operator hands over at the label, and the Selector seeds off the box

ADR 0010 made Seeding a human setup step: the operator picks one task he
authored, fetches it by number under his own identity, and the box cannot read
issues at all - enforced by a token that holds no Issues permission.
`seed-run.sh`'s header names the alternative and refuses it: a Loop that read
arbitrary issues would feed an unattended agent text the operator never saw,
"a different trust model and it needs its own decision."

This is that decision. The `ready-for-agent` queue on the tourbot tracker -
29 open issues at the time of writing - drains unattended, without the operator
starting each Run.

**Applying `ready-for-agent` is the handover.** The operator labels a task he
authored or read; that act carries the attestation ADR 0010 located at the seed
command. A deterministic Selector - a systemd timer on the orchestration VM,
acting on the tracker as the operator - reads the label, applies Eligibility,
performs Seeding, creates the Run branch, and starts the Run on the box over
SSH. The box is unchanged: its token still holds no Issues permission, a Run
still cannot fetch a task even if something inside it tried, and ADR 0010's
enforcement survives verbatim.

## Eligibility

The Selector seeds a task only when all of these hold:

- labeled `ready-for-agent` by an allowlisted operator - the timeline actor is
  checked, and the allowlist is currently exactly `JacobStephens2`;
- no open blocking dependency, read from the tracker's native edges
  (`issueDependenciesSummary.blockedBy == 0`). Native edges are the single
  blocking signal; a prose "Blocked by" section no longer counts;
- no open sub-issues - a parent spec is not a unit of work;
- no open Proposal for it - an open Proposal means in flight;
- retry budget unspent - one automatic retry, then the give-up swap.

## What the label now promises

`ready-for-agent` already meant "fully specified, ready for an AFK agent." It
now promises that mechanically: an `Acceptance criteria` section (`seed-run.sh`
refuses without one) and current native dependency edges. A labeled task
missing it is skipped loudly - a comment naming the gap and a swap to
`needs-info` - never guessed at.

Two sections are optional: `Owning area` supplies `--area`, and `Check`
supplies `--check`.

**Amended 2026-08-27 (issue #156).** `Owning area` was required here as well,
and is no longer. The requirement was sound on its own terms - `--area` is the
Loop's scope fence, and how much of a large task one Run is for is a judgement
an Iteration should not make - but it cost the operator a second step on every
Handover, which is the friction this whole ruling exists to remove. It was
measured before it was dropped: on the day it went in, every labeled task in
the queue lacked the section, so its effect was to hand the queue back rather
than work it. A task with no `Owning area` is now scoped to its own title; a
task that really is bigger than one Run still says so by carrying the section.

## Why the Selector may live on the orchestration VM

The VM holds what the box was built to be away from: production grants, the
vault token, SSH reach. The Selector is acceptable there because it is
deterministic code the operator reviews, not an agent - no model output
executes on this VM. The agent still runs only inside the Execution Boundary,
and the Selector widens nothing inside it.

## Accepted residuals

- A task body edited *after* labeling reaches the prompt unseen. Accepted:
  write access to the tracker is org-trusted staff. Recorded rather than
  engineered around.
- The workspace mount is read-write, so an Iteration can edit the vendored
  skills it runs under. Accepted: any such edit lands in the Proposal diff the
  operator reviews.

## Alternatives rejected

- **Widening the box token to Issues: Read** and scheduling on the box - ADR
  0010 already says this would reopen the subsystem ADR 0003 closed.
- **A GitHub Actions trigger** on the label - an inbound execution surface and
  a third trust domain, for latency nobody needs.
- **Repo-as-queue** - the Selector pushes only a seeded branch and the box
  polls Contents for seeded-but-unrun branches. Preserves the credential wall
  equally well and is worth remembering, but costs two pollers and a branch
  protocol for no additional property while the SSH path already exists.

## Consequences

- Seeding stays a pure function of the task; the Selector merely runs it.
- Issue bookkeeping is Selector work under the operator identity - the swap to
  `awaiting-review` (Proposal open, checks green), `ready-for-human` (given
  up), or `needs-info` (underspecified), and the closing `Closes #n` line in
  the Proposal so a merge retires the task. The box still cannot touch an
  issue.
- A second labeler is one allowlist entry plus a note here; `vsto-eta` is the
  expected first.
