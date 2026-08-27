# Progress Log

Task: Educational-Travel-Adventures/tourbot#648 - Audit every tblEmailMessage read and classify it
Owning area: dashboards and reports

See PLAN.md for the task, its acceptance criteria, and what remains.

This log is the Run's memory. Every Iteration is a fresh process with no
recollection of the one before it, so what is not written here did not happen.
Record decisions and blockers and not only completed tasks: a later Iteration
reads this instead of relitigating a settled choice or repeating exploration
that has already been done.

Seeded by seed-run.sh. No Iteration has run yet.

## Run started 2026-08-25T16:32:48Z

Task: Educational-Travel-Adventures/tourbot#648

Termination Contract:

[... the agent's own narrative, trimmed ...]

```

Exit 2: CHECK_OCCURRENCES=128, CHECK_ACCOUNTED=45, CHECK_MISSING=83,
CHECK_STALE=0, CHECK_UNJUSTIFIED=0. The 45 funnel entries are clean. Do not
re-run a full re-grep of the 45; the missing 83 are the remaining batches.

### BLOCKED

None. `gh issue view 648` failed (HTTP 401); the task text in PLAN.md was
enough.


### Iteration 1 - 2026-08-25T16:32:48Z

- Agent exit: 0
- Turn bound: 100
- Head: f0f4d749e966 -> 06d7a82a8309
- Completion Promise: not recorded

## Iteration 2

Classified all 32 `mtourbot/reports/sales_dashboard.php` occurrences as
emails-only-by-design. No application behaviour changes.

### DECIDED

- Last-comm / days-stale lines that `GREATEST()` `MAX(tblEmailMessage.MessageDate)`
  with `MAX(tblCommunicationNote.MessageDate)` are **emails-only-by-design**,
  not should-include-notes. The occurrence is the email half of last contact;
  notes are already in from the other table, so the read is not under-counting.
