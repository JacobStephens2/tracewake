# Reconcile Runs bring conflicting Proposals up to date

During a drain, every open Proposal behind its base that can be cleanly
updated is fast-forwarded by the forge, and one that cannot is flagged
conflicting on the board (ADR 0023). A flagged Proposal rots: the base keeps
moving and the reviewer inherits the merge. The reconcile Run is what meets
those conflicts instead of leaving them.

## The decision

When the drain meets an open Proposal the forge cannot cleanly merge, the
Selector dispatches a reconcile Run for it on the box, and journals the
outcome. Five rules govern it:

1. **Detect off the same read.** The drain already fetches the Handover and
   review queues for the freshness pass; conflicting Proposals are detected
   off those records, each carried with its owning issue - the escalation
   target and the bearer of the Check section the merged branch is verified
   against. The boundary is the queues': a conflicting Proposal whose owning
   issue carries neither label is out of sight until it is labeled, exactly
   like a Proposal the freshness pass cannot see.
2. **A Run, not a fast-forward.** The reconcile goes through the box surface
   under its own verb and executes `loop/reconcile.sh` on the box: fetch the
   base, merge it into the Proposal branch as a merge commit, resolve
   conflicts through the agent adapter inside the Execution Boundary, verify
   the merged branch, and push the Proposal branch. The push is never forced
   and the base is never written to, so Proposal-Only Output holds.
3. **Verified before pushed.** The owning issue's Check section is the suite.
   It runs on the merged branch and a red suite stops the push: a reconcile
   that resolved the conflicts but reds the suite is journaled as the failure
   it is, never as reconciled. With no Check section there is nothing to run,
   and merge-cleanliness is the whole of the verification - the vacuous pass
   is stated rather than smuggled.
4. **One reconcile per Proposal per drain.** Reconciled and failed Proposals
   are deduplicated across the drain's passes like the freshness sets, and an
   escalation removes the owning issue from its queue so the next pass no
   longer sees it. A paused Selector starts no reconcile, and a Target
   already holding a Run stays serial - a reconcile Run is a Run.
5. **Failure is escalation, not retry.** A reconcile the box could not finish
   leaves the Proposal un-merged and moves the owning issue to
   `ready-for-human` with the reason said on the issue and journaled as
   `proposal.reconcile-failed`. A second Run would meet the same conflicts,
   so there is no second Run under this Handover.

## Consequences

- **Conflicts resolve unattended when they can.** The board's conflict badge
  clears because the branch is current, not because anyone stopped looking.
- **Unresolvable conflicts arrive as work, not as rot.** The operator reads
  what the Run tried and why it stopped, on an issue that will not be picked
  again.
- **The drain stays bounded.** One reconcile per Proposal per drain, serial
  within a Target, never while paused - the same serialization the ordinary
  dispatch keeps.
