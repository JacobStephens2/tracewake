# Open Proposals stay current

During an active draining Cycle (ADR 0021), multiple Runs may dispatch and merge,
or the base branch may advance upstream while earlier Proposals await human review.
When Proposals rot behind their base branch, reviewers merging a sequence of
completed work are forced to manually resolve conflicts created simply because
the Loop branched subsequent runs from earlier commits.

## The decision

During each drain, every open Proposal that is behind its base branch and can be
cleanly updated is brought up to date automatically; one that cannot is shown
as conflicting on the board rather than silently rotting.

Five architectural rules govern Proposal freshness:

1. **Freshness on drain passes.**
   On each pass of a draining Cycle, the Selector inspects all open Proposals
   associated with issues in the Handover queue and the review queue
   (`awaiting-review`). Any open Proposal that is behind its base and mergeable
   is updated via the forge command.
2. **Once-per-drain deduplication.**
   Each open Proposal is updated and journaled at most once per drain
   (`proposal.updated`). Multi-dispatch drains track updated and failed proposals
   across passes so no Proposal is updated repeatedly within the same Cycle.
3. **Conflicting Proposals marked on the board.**
   A Proposal that cannot be cleanly merged (`mergeable` is `CONFLICTING` or
   `mergeStateStatus` is `DIRTY`) is not updated. The queue board inspects open
   proposals and renders conflicting cards with `.badge-conflicting` and
   `.card-conflicting` styling in the review and in-flight columns, alerting the
   operator to merge conflicts.
4. **Forge refusal isolation.**
   If the forge refuses a branch update, the refusal is recorded in the Journal
   as `proposal.update-failed`. The failure is isolated: it does not halt the
   drain, block subsequent picks, or fail any Run dispatch.
5. **Substitutable forge interface.**
   Branch updating is driven through the substitutable `SELECTOR_ISSUE_COMMAND`
   using the `update-branch <proposal-url-or-number>` verb (ADR 0004), preserving
   offline testability with scripted test doubles. Dry-run cycles reason and
   journal without invoking forge mutations.

## Consequences

- **Review velocity preserved.** Operators merge current, tested Proposals
  without manual rebase or conflict resolution overhead.
- **Immediate conflict visibility.** Merge conflicts are highlighted directly
  on the queue board instead of being discovered at merge time.
- **Drain resilience.** Transient forge refusals or branch update rejections
  are audited in the Journal without interrupting unattended dispatch throughput.
