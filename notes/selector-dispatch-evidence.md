# The Selector's first dispatch: what was verified, and how

*2026-08-26, issue #154. The offline suites are the bulk of the evidence and
they are in the repository; what is written down here is what only a real box
could say, plus the one acceptance criterion this ticket could not close and
why.*

## The SSH hop, against the real box

`box-sources/ssh.sh` is two shells deep - this VM's `ssh` builds a command
string, the box's login shell parses it, and `su - loop -c` parses it again -
so its quoting is the part no offline test can grade. Driven against
`loop.etadventures.com` with a throwaway target (`/tmp/probe`), a fake
`run.sh`, and no model:

```
$ SELECTOR_BOX_REPO=/tmp/probe/repo SELECTOR_BOX_LOOP=/tmp/probe/loop \
      ./box-sources/ssh.sh "loop/999-a-probe" "acme/widgets#999"
Switched to and reset branch 'loop/999-a-probe'
run.sh got: --repo /tmp/probe/repo --task-ref acme/widgets#999 --propose --notify
LOOP_RUN_ENDED_BY=iteration-cap
...
on branch: loop/999-a-probe
exit=0
```

Three things at once: the hop lands as `loop` and not as root, the branch is
fetched and checked out before the Run starts, and `--propose --notify` is
what the Run is asked for. The summary came back on stdout, which is the whole
mechanism by which an outcome survives a box that persists none.

## The whole chain, end to end

Then the real `cycle.py` with only the tracker and the issue command faked -
real `seed-run.sh`, real `git push` over SSH, the real `box-sources/ssh.sh`,
the real Journal:

```
$ .venv/bin/python cycle.py
considered   1
eligible     [999]
pick         #999
branch       loop/999-the-probe-area
run          iteration-cap (exit 0, 5 iteration(s), faults none)
proposal     https://example.invalid/pull/1
budget       0/4 dispatches in the last 24h
```

Five Journal rows, in the order the design requires - `run.dispatched` before
the box was reached and `run.outcome` after, which is the in-flight lock
rather than bookkeeping:

```
 id |      kind      | issue |         branch          |    outcome
  1 | cycle.started  |       |                         |
  2 | cycle.picked   |       |                         |
  3 | cycle.finished |       |                         |
  4 | run.dispatched | 999   | loop/999-the-probe-area |
  5 | run.outcome    | 999   | loop/999-the-probe-area | iteration-cap
```

And on the box afterwards:

```
loop/999-the-probe-area
427083a Loop: Seed the Run from acme/widgets#999 (The probe area)
c679980 first
PLAN.md  PROGRESS.md  README.md
```

The Plan reached the box as a commit, on the Run's branch, written by the
Loop's own seed step running here rather than there - which is ADR 0010's
property holding through the automation. Everything above was torn down
afterwards; nothing of it is left on the box.

## What the offline suites cover

Twenty-five tests drive the real `cycle.py` in dispatch mode against scripted
commands and a **real** bare git repository, asserting the sequence, the
captured summary, the retry's branch handling, the loud skip's comment and
label swap, and the two failure shapes. Thirty mutations - each Eligibility
clause, each cap, each dispatch step, each half of the loud skip - are all
caught. The Loop's own 287-test suite is green with `propose.sh`'s new
`Closes #n` line (run as `loop` on the box; two tests fail as root, because
root can read the unreadable files two of them construct).

## The criterion this ticket did not close

> One supervised live dispatch works a real tourbot issue end to end.

**Not done, and not for a reason in the code.** A live dry-run on the same
day:

```
$ .venv/bin/python cycle.py --dry-run
considered   27
eligible     none
  skipped    14 x blocked-by-open-dependency
  skipped    3 x has-open-sub-issues
  skipped    10 x missing-section
pick         none (none-eligible)
```

Nothing in tourbot's queue is Eligible. Fourteen are blocked and three are
parent specs - both correct. The other ten are missing `## Owning area`, the
section `ready-for-agent` began promising on 2026-08-26, and the owning area
is the operator's decision by design: it is how much of an issue one Run is
for, which is the one judgement `seed-run.sh` has always refused to make and
which the Selector must not make either.

So there are two operator acts left, in this order:

1. **Add `## Owning area` to the issues that are otherwise ready** - #471,
   #596, #597, #598, #599, #606, #626, #645, #647, #648. Four of them (#471,
   #645, #647, #648) already carry acceptance criteria and need only the area.
2. **Run one live cycle, watched.** A cycle run today would also hand back
   every remaining issue in that list of ten - a comment and a `needs-info`
   swap each - which is the design working, and is worth being ready for
   rather than surprised by.

Neither was done unattended. Both write to the operator's tracker under his
identity, and the first is a decision this ticket has no standing to make.
