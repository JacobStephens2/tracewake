# The Cycle owns the loop

A Cycle decides and does in one loop: reading the tracker, applying
Eligibility, holding the caps, and acting - commenting, refreshing
Proposals, starting Runs, routing them. `--dry-run` was branches inside
that loop and a conjunct of the concurrency policy, so a new act could be
added and forgotten on the dry-run path.

## The decision

The Cycle owns the loop, and the doing is handed to it. What the Cycle
needs of the world arrives as four operations: observe the Guardrail; keep
Proposals current; return a task to the operator; work a pick. A dry run
is an adapter at that seam that does nothing and journals nothing;
production wraps today's acts, including the write-before-act that makes
`run.dispatched` the in-flight lock. The Cycle still journals its
decisions - started, each skip, each pick, finished - and still carries
`dry_run` as a payload fact, not as control flow. Concurrency policy does
not mention dry runs.

The four per-drain Proposal sets are the doing's own state. Slot acquire
stays an argument of the Cycle, between the decision and the journaled
pick. The Guardrail is read once when the Cycle starts; the box is read
before each Dispatch.

## Considered options

- **A pure per-pass decision function with the loop left around it.**
  Rejected: the re-pick guard, the skip dedup, first-considered against
  last-eligible, the pause re-check after a slot wait, and which
  conditions break would all have stayed in an untested caller. A pure
  per-pass decision may exist inside the Cycle as an internal seam.
- **One seam for tracker reads and the doing.** Rejected: they vary
  independently. A dry run is real reads with a null doing; a test is
  canned reads with a recorder.
