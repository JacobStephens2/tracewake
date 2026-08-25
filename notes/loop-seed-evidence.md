# Seeding a Run: what was verified, and how

Issue #82. Everything below was run on the orchestration VM on 2026-08-25
against `loop/seed-run.sh` as committed. The seed step is where a task the
operator chose becomes text an unattended agent reads, so what is recorded here
is what the Plan ends up saying and what a second seeding does.

## Seeding the first Run's task

Tourbot issue 648, scoped to one owning area, into a throwaway repository:

```
$ seed-run.sh --repo . --task 648 --task-repo Educational-Travel-Adventures/tourbot \
      --area 'dashboards and reports' \
      --check "check-inventory.sh --checkout . --inventory docs/tblEmailMessage-inventory.md \
               --scope 'mtourbot/reports/*'"
LOOP_SEED_RESULT=seeded
LOOP_SEED_TASK=Educational-Travel-Adventures/tourbot#648
LOOP_SEED_TASK_STATE=OPEN
LOOP_SEED_AREA=dashboards and reports
LOOP_SEED_CRITERIA=5
LOOP_SEED_PLAN=PLAN.md
LOOP_SEED_PROGRESS=PROGRESS.md
```

Five acceptance criteria, lifted verbatim into their own section of the Plan.
That count is reported rather than assumed for the same reason
`check-inventory.sh` prints its denominator: "the Plan carries the acceptance
criteria" is a claim the operator should be able to see rather than take.

The Plan that came out is the whole of issue 648 - the criteria in one section,
the rest of the body under `## The task, as written` with its headings demoted a
level so they nest - plus two things the task does not contain: the owning area
this Run is scoped to, and the check command that grades it.

**One thing the Plan deliberately carries a warning about.** Issue 648's body
says "78 files and 276 occurrences", and that number reaches the Plan because
the body reaches the Plan. An Iteration reading it could take 276 as the
denominator, which is exactly the failure ADR 0008 exists to prevent - the same
search answers 303 in application code today. So the Plan says, above the body,
to read it as the request and not as fact about the codebase, and points at the
check. Stripping the number instead was the alternative and is worse: it would
mean the Plan quietly editing the task, and an operator comparing the two would
find them different for no visible reason.

## Re-running it

```
$ seed-run.sh --repo . --task 648 ...        # identical arguments
LOOP_SEED_RESULT=unchanged
  result        already seeded from this task and area; nothing changed
$ git rev-list --count HEAD
2
```

Two commits: the fixture's own, and one seeding. The second seeding wrote
nothing and committed nothing. Neither file carries a timestamp, which is what
makes that true - a generated file with a "seeded at" line in it would commit
every time it ran and turn "re-run the setup step" into "accumulate another
commit".

## Refusing to discard a Run

With a Run's record in the Progress Log:

```
$ seed-run.sh --repo . --task 648 --area 'alerts and background jobs'
seed-run.sh: PROGRESS.md already records 1 Run(s) and 1 Iteration(s).
Seeding rewrites it. Pass --reseed to discard that record, or seed a fresh checkout.
$ echo $?
2
```

Both files are written whole rather than appended to - that is what stops state
accumulating - and the cost of that choice is that a re-seed would destroy the
only account of what an unattended agent did. So it exits 2 and names the flag.
`--reseed` does it and says how much it discarded.

## The suite

Thirty-seven tests, `bats tests/seed-run.bats`, about ten seconds. The task
source is one substitutable command for the same reason the agent is (ADR 0004),
and `tests/fake-task-source.sh` is what the suite substitutes: no GitHub token,
no network, no rate limit. Every test asserts on what the seed leaves behind -
the Plan's contents, the Progress Log's contents, the git history, the exit code
and the reported result - and none names an internal function of `seed-run.sh`.

Two of them are the seam to the rest of the Loop: a Run started against a
repository this step prepared reaches its iteration cap and exits 0, and the
prompt an Iteration is handed points at the Plan the seed wrote. Those matter
because `run.sh` and `seed-run.sh` agree about where the Plan lives only through
`contract.sh`, and a suite that tested each alone would not notice them
disagreeing.

## Mutation check

```
$ tests/mutation-check.sh --only seed-run.sh
seed-run.sh
  criteria-not-required        caught,  5 red
  prose-counts-as-criteria     caught,  2 red
  criteria-dropped-from-plan   caught,  2 red
  area-not-required            caught,  2 red
  run-history-overwritten      caught,  2 red
  plan-appended-not-rewritten  caught,  2 red
  reseeding-never-unchanged    caught,  2 red
  fenced-headings-counted      caught,  3 red
  headings-not-demoted         caught,  2 red
  task-number-unvalidated      caught,  2 red
  wrong-task-accepted          caught,  2 red
  unrelated-work-swept-in      caught,  2 red
  plan-path-hardcoded          caught,  2 red

All 13 mutations caught.
```

`wrong-task-accepted` is the one worth naming. It removes the check that the
task the source answered with is the task that was asked for, and the failure it
guards against is not a wrong number - it is the Plan carrying a task the
operator never chose, which is the thing ADR 0010 says must not be possible.
`prose-counts-as-criteria` is the second: an acceptance criteria section that
gestures at criteria without listing any would otherwise seed a Run whose Plan
looks complete and defines nothing.

## What is asserted, and what is not

**Asserted.** That the seed fetches exactly one task and exactly the one it was
given; that a task with no readable acceptance criteria does not seed at all;
that the Plan names one owning area and says the rest of the task is out of
scope; that seeding twice is byte-identical and commits nothing; that a Progress
Log holding a Run stops a re-seed; that the seed stages the Plan and the Progress
Log by path and does not sweep an operator's unrelated work into its commit.

**Not asserted here.** That the box's token cannot read issues - the enforcement
ADR 0010 rests on. It cannot be asserted from the orchestration VM, which does
not hold that token. It is probed where the token is:
`wizards/loop-github-credentials.sh` stage 4 now asks GitHub whether the box's
token can list the target repository's issues, alongside the existing probe for a
second repository, and refuses to continue quietly if it can. That probe has not
been run yet - the wizard is a human walkthrough and the token exists - so this
is a check that is in place rather than a result, and running it is the first
thing #83's operator sees.

**Not built.** Issue intake. Not because it was hard but because it would
invalidate ADR 0003's reasoning, which is ADR 0010.
