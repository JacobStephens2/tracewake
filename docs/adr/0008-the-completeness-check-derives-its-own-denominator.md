# The completeness check derives its own denominator, and declares what it left out

The Loop's first task is a classification exercise (Tourbot issue 648): every
occurrence of `tblEmailMessage` in application code marked as a read that should
include notes, a read that is about email by design, or a write. A
classification exercise has no test suite, so the Run has no backpressure and
the operator has no acceptance unless something mechanical is built to be both.

The ticket states the size of the job: "78 files and 276 occurrences". That
number was true when it was written. Against `tourbot` master as of 2026-08-25
the same search answers 359 occurrences across the tracked tree and 303 in
application code - the codebase moved. The command and its full output are in
`../notes/loop-completeness-check-evidence.md`. A check that compared an
inventory against the number in the ticket would therefore pass on an inventory
that had missed everything added since the ticket was written, which is the
exact failure it exists to catch.

So the check derives its denominator from the checkout on every run, and never
reads a count from the task or from the inventory. `git grep` over tracked files
is the source; one occurrence is one line; the symbol matches case-insensitively
because SQL identifiers do.

The corollary is that a derived denominator is only honest if what was subtracted
from it is visible. Two things subtract. **Exclusions** - schema files,
documentation, vendored code - are declared in the script rather than passed by
the caller, because a denominator whose exclusions come from the call site is a
denominator the caller can shrink until the inventory looks complete; `--exclude`
can add to the declared list and cannot remove from it. **Scope** narrows a run to
one owning area, which is how the first Run is sized. Both are printed in the
report with the count each removed, so the number can be audited rather than
taken.

Three further consequences of treating the check as the honest signal rather
than as a helper:

- **A denominator of zero is an error, not a pass.** A wrong `--symbol`, a
  mistyped `--scope`, a checkout that failed to clone: all of them produce a
  check with nothing to check, and all of them would otherwise report success.
  It exits 1.
- **A stale entry fails as loudly as a missing one.** An inventory naming a line
  that no longer holds the symbol was written against an older tree, and the
  classification it carries has not been reviewed against what is there now.
- **The report names every unaccounted occurrence, with the line that produced
  it.** "Six occurrences are missing" is a number; the six paths and the six
  lines are what an Iteration can act on without re-deriving the search.

One thing the check asserts is not a count. Issue 648 makes the rationale an
acceptance criterion - a should-include-notes entry names the user-visible
symptom "so the fix is gradeable", an emails-only-by-design entry carries a
one-line reason - and without a floor, "- x" satisfies both. The floor is twelve
characters, declared at the top of the script beside the classifications. It is
a first value in the same sense as the Termination Contract's five numbers: a
guess whose correction is the first Run's output, changed in one line.

## Consequences

The same script is the Run's backpressure and the operator's acceptance, which
is what issue #80 asks for, and it needs no argument to change between the two
roles. It reaches nothing: no network, no model, no write to the checkout. On
`deny-all` egress inside the Execution Boundary it behaves exactly as it does on
the orchestration VM, so it cannot fail inside the boundary and pass outside it -
the worst failure shape available to a component whose whole job is to be the
honest signal.

Line granularity is the coarseness that has to be lived with. A line naming the
table twice is one entry, and an occurrence inside a comment has to be
classified like any other. Both err towards asking for more classification than
strictly necessary, which is the safe direction for a completeness check.

The check is generic over `--symbol` and ships pointed at `tblEmailMessage`. The
mechanism is not specific to one table, and the next audit of this shape should
not have to write it again.
