# The completeness check: what was verified, and how

Issue #80. The Loop's first task is Tourbot issue 648, a classification exercise
with no test suite, so this script is the only thing that can say the Run did not
work. Everything below was run on the orchestration VM on 2026-08-25 against
`check-inventory.sh` as committed.

## The denominator, and why the ticket's number is not it

Issue 648 states the size of the job as **78 files and 276 occurrences**. The
same checkout answers differently today:

```
$ check-inventory.sh --checkout /srv/orchestration/tourbot --inventory /tmp/empty.md
CHECK_OCCURRENCES=303
CHECK_EXCLUDED=56

Denominator
  359 occurrences of tblEmailMessage in tracked files
  -32   mysql_files/* (not application code)
  -17   specifications/* (not application code)
  -5    *.sql (not application code)
  -1    notes/* (not application code)
  -1    *.md (not application code)
  =303  to be classified
```

359 occurrences across the tracked tree, 303 of them in application code. An
inventory graded against 276 could omit 27 occurrences and still be called
complete. That is the whole reason this component exists, and it is ADR 0008.

The exclusions are printed with the count each removed, every run, because a
derived denominator is only honest if what was subtracted from it is visible.
They are declared in the script and `--exclude` can only add to them: a
denominator whose exclusions come from the call site is a denominator the caller
can shrink until the inventory looks complete.

## Mutation check, subject side

Spec issue #73 asks specifically that the thing the check guards be broken
deliberately and the check confirmed to fail. Run at real scale against
`/srv/orchestration/tourbot` at master on 2026-08-25, with an inventory generated
mechanically from the checkout so that the starting state is genuinely complete:

```
git -C /srv/orchestration/tourbot grep -I -z -n -i -F -e tblEmailMessage -- . \
  | tr '\0' '\t' | awk -F'\t' '{print "- `" $1 ":" $2 "` - write"}' > full-inventory.md
```

Classifying all 359 as `write` is not the real task's answer - it is the cheapest
way to produce a genuinely complete inventory to break.

| Mutation | Result |
| --- | --- |
| Inventory accounts for all 303 | `CHECK_RESULT=complete`, `CHECK_ACCOUNTED=303`, exit 0 |
| One entry deleted (`mtourbot/reports/agency_360.php:335`) | exit 2, `CHECK_MISSING=1`, names the path and quotes the `EXISTS(SELECT 1 FROM tblEmailMessage em ...)` line |
| One entry added for a line that does not exist (`:99999`) | exit 2, `CHECK_STALE=1`, names it |

The middle row is the one that matters: the report does not say that something is
missing, it says which occurrence and shows the line, which is what an Iteration
can act on without re-deriving the search.

The same three shapes are in the offline suite as fixture tests, plus an
occurrence added to the checkout *after* the inventory was written - the failure
a stale denominator would hide.

## Mutation check, check side

`tests/mutation-check.sh --only check-inventory.sh` removes one guard at a time
and requires the suite to go red. Fifteen breaks, fifteen caught.

```
zero-denominator-passes        caught,  1 red
case-sensitive-symbol          caught,  1 red
untracked-files-counted        caught,  1 red
missing-not-a-fault            caught,  7 red
stale-not-a-fault              caught,  2 red
duplicates-ignored             caught,  1 red
unclassified-accepted          caught,  1 red
ambiguous-accepted             caught,  1 red
rationale-floor-removed        caught,  2 red
scope-ignored                  caught,  2 red
declared-excludes-dropped      caught,  1 red
wrong-shape-inventory-silent   caught,  1 red
subdirectory-silently-rescoped caught,  1 red
unreadable-inventory-graded    caught,  1 red
report-names-nothing           caught,  8 red
```

The harness earned its keep once during this: after the exclusion loop was
refactored, `scope-ignored` no longer matched the line it names and the run
stopped and said so rather than reporting a mutation caught that was never
applied.

`zero-denominator-passes` is the one worth naming. Without that guard a wrong
`--symbol`, a mistyped `--scope` or a checkout that failed to clone produces a
check with nothing to check, and it reports success - a check that passes
hardest when it is most broken. It exits 1 instead.

`report-names-nothing` going eight red is the suite saying that most of what it
asserts is the *content* of the report rather than its counts. That is
deliberate: a completeness check whose output is a number is not usable as
backpressure.

## The suite

Thirty-two tests, fifteen seconds, no model and no network. They drive
`check-inventory.sh` against small fixture checkouts - three files, three
occurrences - and assert only its exit code and its report.

The check is seamed and tested on its own rather than only through a Run because
it is itself the honest failure signal, and a component that is the failure
signal should not have its correctness established only through another
component (spec issue #73, Seam B).

`shellcheck -x check-inventory.sh` is clean.

## One trap worth recording

The first parser looked for a classification anywhere in the entry line. That
reads `- \`x.php:2\` - should-include-notes - the timeline is empty because the
write that produced it went to the note table` as carrying two classifications
at once, and fails a correctly written entry - a false alarm on the component
whose whole value is that its alarms mean something. The classification is now
read from its own slot: everything between the reference and the first " - ".
A rationale can use the word "write" in a sentence; naming a second
classification in the slot is still ambiguous, which is what the suite asserts
both ways.

The awk is POSIX-subset on purpose. `/usr/bin/awk` on the Loop's box is mawk,
not the gawk this was written against.

## Egress: it needs no host

#80 carried a warning that anything the check fetches has to be on the Execution
Boundary's allowlist, or it fails inside the boundary and passes outside it -
the worst failure shape for a component whose job is to be the honest signal.

The check fetches nothing. It reads a checkout and a file, runs `git grep` and
`awk`, and writes to stdout. No network, no model, no write to the checkout -
the last of which the suite asserts, because a check that dirties the tree it is
grading would make the Loop's No-op detection lie. It behaves identically on
`deny-all` and on the orchestration VM, and it adds no line to
`loop_execution_boundary_egress_common`.

## Two failures that would have been silent

Both came out of review, and both are the same shape: an output that means "the
check could not run" being emitted as an output that means "the work is not
done".

**awk exits 2.** So does the check, when the inventory does not account for
everything. An unreadable inventory therefore graded the Run as incomplete
rather than saying it could not be read. It now dies with exit 1, and a test
holds it there.

**An inventory in the wrong shape parses to zero entries.** A markdown table -
a plausible reading of issue 648's "publish the inventory grouped by the area
that owns it" - produced "303 missing" with nothing to say the format was the
problem. It now names the shape it expected. The `CHECK_ENTRIES` count is in the
header for the same reason.

A third, narrower one: `--checkout` pointed at a subdirectory passed
`rev-parse --git-dir`, and `git grep` then reported paths relative to that
subdirectory, so every declared exclusion silently stopped matching. It is
refused, and `--scope` is the way to grade part of a checkout.

## What is not verified

**That a classification is correct.** The check asserts that every occurrence has
been decided about and that the two classifications requiring a rationale carry
one above a length floor. Whether a read genuinely should include notes is a
judgement, and reviewing it is what the draft pull request is for.

**The area grouping.** Issue 648 asks for the inventory grouped by owning area.
`--scope` narrows a Run to one area by path glob and the report prints the glob,
which is how the first Run is sized - but the check does not verify that the
document's headings match the areas the ticket names. That is a reading, not a
count.

**Occurrences in comments.** A comment naming the table is an occurrence and has
to be classified. This asks for slightly more classification than strictly
necessary, which is the safe direction for a completeness check.
