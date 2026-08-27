# The Selector

The Selector (issue #151's spec; vocabulary in `../CONTEXT.md`) drains
tourbot's `ready-for-agent` queue unattended. This directory is its home.

What exists is **a cycle that picks, dispatches and does the bookkeeping**
(issues #153, #154, #155) on top of the **Selector Journal** (ADR 0015, issue
#152), **run unattended by a timer** (#156), with the **Iteration watcher**
(#157) reading the box's Progress Log while a Run is in flight so that the
activity is visible while it happens.

## The cycle

```bash
cd lab/single-user-factory/selector
.venv/bin/python cycle.py --dry-run    # reason, journal, change nothing
.venv/bin/python cycle.py              # and act on it
```

One cycle reads the labeled queue through the tracker command, applies
Eligibility to every issue in it, orders what survives lowest-first, applies
the caps, and appends the whole of that reasoning to the Journal. Then, unless
`--dry-run`, it acts: the pick is dispatched, and every issue skipped for a
missing section is handed back to the operator.

The two modes share every line of the deciding, so a dry-run is the cycle that
would have happened rather than a separate approximation of one.

**Eligibility** (ADR 0014). An issue is skipped with the first of these that
holds, and the reason is the string journaled with it:

| reason | what it means |
| --- | --- |
| `labeler-not-allowlisted` | the most recent `ready-for-agent` labeling on the timeline was not by an allowlisted operator - the Handover is the label, so the labeler is who is trusted |
| `blocked-by-open-dependency` | native tracker edges report open blockers. Prose "Blocked by" text is deliberately not read |
| `has-open-sub-issues` | a parent spec is not a unit of work |
| `proposal-open` | an open pull request closes it: the issue is in flight |
| `attempts-exhausted` | already dispatched `MAX_ATTEMPTS` times (one automatic retry) |
| `missing-section` | the label promises an `Acceptance criteria` section and it is absent or empty |

The order is not arbitrary: `missing-section` is the loud skip - see below -
so the cheap, quiet reasons are tested first. An issue that is blocked anyway
is not shouted at for a gap.

**Caps.** One Run in flight, `SELECTOR_DAILY_CAP` (4) dispatches per rolling
24 hours. Rolling rather than calendar: "four a day" is a spend bound, and a
calendar boundary would let eight Runs happen inside three hours across
midnight. A cap that halts a cycle still lets it reason and journal first, so
the Journal answers "what would it have picked?" as well as "what did it?".

**Configuration**, all environment, all with working defaults:

| variable | default | what it is |
| --- | --- | --- |
| `SELECTOR_TRACKER_COMMAND` | `tracker-sources/github.sh` | the labeled queue, read |
| `SELECTOR_TASK_REPO` | `Educational-Travel-Adventures/tourbot` | |
| `SELECTOR_LABEL` | `ready-for-agent` | the Handover |
| `SELECTOR_NEEDS_INFO_LABEL` | `needs-info` | where a loud skip sends an issue |
| `SELECTOR_REVIEW_LABEL` | `awaiting-review` | a green Proposal waiting on the operator |
| `SELECTOR_HUMAN_LABEL` | `ready-for-human` | a give-up, or red checks |
| `SELECTOR_LABELER_ALLOWLIST` | `JacobStephens2` | comma-separated |
| `SELECTOR_DAILY_CAP` | `4` | dispatches per rolling 24h |
| `SELECTOR_JOURNAL_DSN` | `dbname=selector` | |
| `SELECTOR_WORK_REPO` | `/var/lib/conductor/selector-work/tourbot` | the checkout Seeding happens in |
| `SELECTOR_WORK_REMOTE` | `origin` | |
| `SELECTOR_BRANCH_PREFIX` | `loop/` | |
| `SELECTOR_SEED_COMMAND` | `../loop/seed-run.sh` | the Loop's own seed step |
| `SELECTOR_BOX_COMMAND` | `box-sources/ssh.sh` | the box, and the Run on it |
| `SELECTOR_ISSUE_COMMAND` | `issue-sources/github.sh` | comments and label swaps |
| `SELECTOR_COMMAND_TIMEOUT_SECONDS` | `300` | git, Seeding, tracker writes |
| `SELECTOR_DISPATCH_TIMEOUT_SECONDS` | `7200` | backstop for a wedged Run |
| `SELECTOR_CHECKS_TIMEOUT_SECONDS` | `900` | how long a Proposal's checks may stay pending |
| `SELECTOR_CHECKS_POLL_SECONDS` | `30` | how often they are re-read while pending |
| `SELECTOR_BOX_FACTS_COMMAND` | `box-sources/facts.sh` | the box, read for the status card |
| `SELECTOR_BOX_FACTS_TIMEOUT_SECONDS` | `60` | how long that status read may take |
| `SELECTOR_BOARD_TIMEOUT_SECONDS` | `20` | how long one of the queue board's tracker reads may take |

Two timeouts because the two waits are nothing like each other: everything
except the Run should answer in seconds, and giving a comment or a fetch the
Run's budget would let one wedged call hold a cycle open for two hours.

`box-sources/ssh.sh` reads four more: `SELECTOR_BOX_HOST`
(`root@loop.etadventures.com`), `SELECTOR_BOX_USER` (`loop`),
`SELECTOR_BOX_REPO` (`/home/loop/tourbot`) and `SELECTOR_BOX_LOOP`
(`/home/loop/loop`). `box-sources/facts.sh` shares the first, second and
fourth of those, and adds `SELECTOR_BOX_AGENT` (`claude`) - which adapter it
asks for the guest template.

Widening the allowlist is one entry here plus a note in ADR 0014, which is
what story 35 asks for.

## Dispatch

A cycle that picked something and was not asked for a dry-run dispatches it.
Five steps, in this order, all of them through substitutable commands:

1. **The branch.** `git fetch`, then `loop/<number>-<owning-area>` from the
   base branch - or from the branch itself when the remote already has one,
   so a retry continues what the first attempt committed instead of resetting
   it away.
2. **Seeding.** The Loop's own `seed-run.sh`, unchanged, with `--area` and
   `--check` taken from the issue's sections. It still runs HERE rather than
   on the box: ADR 0010's enforcement is that the box's token holds no Issues
   permission, and ADR 0014 moved the Handover to the label without moving
   Seeding. Its refusal - a task whose acceptance criteria it cannot read -
   ends the dispatch rather than being worked around.
3. **The push.** The Plan reaches the box as a commit, like everything else.
4. **The Run.** `box-sources/ssh.sh` puts the box's checkout on the branch and
   runs `run.sh --repo ... --propose --notify`. It blocks for the length of
   the Run, which is the point: the box persists no record of one, so the only
   moment the summary exists anywhere is while something is holding the
   process.
5. **The outcome.** The `LOOP_RUN_*` block is parsed and journaled - the bound
   that ended the Run, its exit code, its Iterations, its faults and its
   Proposal URL.

`run.dispatched` is journaled **before** step 4 and `run.outcome` after it.
That gap is the in-flight lock every later cycle reads, so the ordering is the
concurrency control rather than bookkeeping: a dispatch journaled only on
success would leave the ninety minutes a Run takes unguarded.

A Run that ended on a bound is an outcome, not a failure - the Termination
Contract working is not the Selector failing, and a cycle exits 0 for it. A
box that started no Run is a failure, and the two are told apart by whether
the box reported `LOOP_RUN_ENDED_BY` at all.

**A dropped connection reads as a dispatch that failed.** The summary is
printed when the Run ends, so an SSH session that died at minute forty
produces no `LOOP_RUN_ENDED_BY` and is journaled `dispatch-failed` even though
the Run may have finished on the box and opened its Proposal. Nothing here can
tell the two apart from this side; what narrows the gap is the watcher below,
whose `run.iteration` rows say how far the Run had got.

## The watcher

A Run persists nothing. `run.sh` prints its summary when it ends and exits, so
for the ninety minutes in between the only record of what is happening is the
Progress Log in the box's own checkout - which nothing on this side of the SSH
hop could see. Step 4 above blocks for exactly that long, and the page had
nothing to say for the whole of it.

So while it blocks, a thread reads that log about once a minute through
`box-sources/progress.sh` and journals `run.iteration` for every Iteration
record it has not journaled before (story 26). Configuration:

| variable | default | what it is |
| --- | --- | --- |
| `SELECTOR_BOX_PROGRESS_COMMAND` | `box-sources/progress.sh` | the box's Progress Log, read |
| `SELECTOR_WATCH_INTERVAL_SECONDS` | `60` | how often |
| `SELECTOR_WATCH_TIMEOUT_SECONDS` | `30` | how long one read may take |
| `SELECTOR_WATCH_CLOCK_SKEW_SECONDS` | `300` | how far the box's clock may sit behind this one |

`box-sources/progress.sh` shares `SELECTOR_BOX_HOST`, `SELECTOR_BOX_USER` and
`SELECTOR_BOX_REPO` with `ssh.sh`, and adds `SELECTOR_BOX_PROGRESS_PATH`
(`PROGRESS.md`, relative to the checkout). It is handed the Run's branch and
deliberately does not check it out or fetch it: the box is running a Run in
that checkout, and a watcher that touched its working tree would be a window
reaching through the glass.

Three properties, each of them a way this could have gone wrong unattended:

**It journals what is new, not what it read.** The log is cumulative and every
poll re-reads the whole of it, so a watcher that journaled its reading would
leave a row per Iteration per minute. What is already journaled is read back
out of the Journal when the watch starts and remembered in the process after
that, so neither a later poll nor a re-dispatch can duplicate a row - which
matters more here than elsewhere because the Journal is append-only and a
wrong row can only be contradicted, never corrected.

**It journals this Run's Iterations.** A retry works the branch the first
attempt left behind, so the log it reads opens with that attempt's Run block
and its Iterations - and for the first seconds, while the box is still
fetching and checking out, that block is the newest one in the file. Blocks
are told apart by when the Run started: one that started before this watch did
is over. The skew tolerance above is what keeps two NTP-synced clocks that are
only approximately equal from hiding a live Run's Iterations, and the cost of
it being loose is at worst attributing an attempt that ended in the last five
minutes - which the thirty-minute timer makes impossible in practice, since a
retry is a cycle later and not five minutes later. Shortening that timer below
the skew would mean shortening the skew with it: `iterations_seen` is scoped
by attempt, so an attempt's Iterations read under the next attempt's number
would not be deduplicated away.

**It cannot fail a dispatch.** A watcher is a window. A box that will not hand
over its log is journaled `run.watch-failed` **once** - not once a minute for
ninety minutes - and the Run is untouched; the dispatch fails or succeeds on
its own and pages on its own. The single row is what stops a Run whose log
could not be read from looking like a Run with nothing happening, which is the
opposite fact.

That is the same knowing narrowing of story 31 that `box.unreachable` above
is, and for the same reason: this SSH failure sits underneath a dispatch that
will page on its own if it fails too, and paging twice for one outage is how
an alert stops being read. The difference from `box.unreachable` is that there
is always a dispatch behind this one - a watcher exists only while a Run
does - so the residual that note carries does not apply here.

A half-written record is not a record. The log is read while it is being
appended to, so an Iteration heading with no `Agent exit` line under it yet is
left for the next read. Neither is a Run heading the agent wrote: a block
boundary is only taken from a line carrying a stamp that parses, because one
that did not would discard every record already found and leave the block
undatable - and an undatable block is dropped whole, so the page would show no
Iterations at all for the rest of the Run with nothing saying why.

**Most of a Progress Log is not the Loop's.** The agent writes its own
narrative into it - that is what the log is for - headings and bullet lists
included, so a record is exactly the run of `- ` lines under a `### Iteration
N - <stamp>` heading and ends at the first line that is not one. Reading the
real box's log is what established that, and what it would have cost to get
wrong is in `../notes/selector-watcher-evidence.md`: a parser that ran a
record to the next heading would have reported every Iteration one Iteration
late, which is a watcher that looks like it works.

The last read happens after the Run has ended, on the way out: an Iteration
written in a Run's final seconds has no next poll coming, and would otherwise
be missing from the page for good.

**The work checkout is not created for you.** Seeding commits the Plan, so the
checkout needs an identity to commit as - the operator's, because that is
whose Handover this is. On this VM `conductor`'s global git config already
carries it, signing key included, so the clone is the whole of the setup:

```bash
git clone https://github.com/Educational-Travel-Adventures/tourbot \
    /var/lib/conductor/selector-work/tourbot
```

It is deliberately **not** `/srv/orchestration/tourbot`. That checkout is
shared with the operators who work in it from their own code-server, and a
dispatch that switched its branch out from under one of them would be the
Selector reaching into somebody else's working tree.

## The queue board

`/loop` shows the Journal - what the Selector decided, remembered. The board
at the top of it does not: it is the tracker's own state, read when the page
is requested (story 24). A cycle that has not run for twenty-five minutes
leaves the Journal that stale, and the board is never stale; the gap between
the two is itself worth seeing.

Five columns, and they are the tracker's labels rather than words invented for
the page - so what the board says and what the operator sees on his issue list
are the same thing:

| column | what is in it |
| --- | --- |
| eligible | `ready-for-agent`, and Eligible. Lowest first, so the top card is what the next cycle picks |
| blocked | `ready-for-agent`, and not - each card carrying the reason string the cycle would journal for it, and a blocked card naming the open issues that block it |
| in-flight | an open Proposal, or a dispatch the Selector has journaled no outcome for |
| `awaiting-review` | a green Proposal is open and waiting on the operator |
| `ready-for-human` | the Selector gave up, or the checks went red |

Three reads of the same substitutable tracker command, one per label,
concurrently and each bounded by `SELECTOR_BOARD_TIMEOUT_SECONDS` - this one
happens inside a request, and a tracker that is not answering has to make its
column say so rather than hold the page open. A label whose read fails shows
the failure in its own column; the other four still render. Measured against
the live tourbot queue on 2026-08-27: about three seconds for all three,
which is what a `/loop` request now costs.

The columning is `cycle.eligibility`, imported. Two of the things it decides
on are not on the tracker at all - the retry budget and the in-flight lock are
Journal facts - so the page hands the board the same `Spend` its budget cell
is read from. When the Journal is unreachable the board still renders and says
which two columns it is now reading blind, because an issue whose attempts are
spent looks Eligible without it and that is the one card an operator would act
on.

Note what this needs of the process serving the page: `gh` on its `PATH`, and
`gh`'s own auth. `lab-webapp.service` sets the PATH and nothing else - the
tracker read is authenticated by conductor's `~/.config/gh/hosts.yml`, which
is the operator's read of the operator's tracker (ADR 0014). It deliberately
does not get the vault environment the Selector's own unit gets: that would
hand a page-serving process every production credential on the box to get a
queue listing.

`lab-webapp.service` is not installed by Ansible - the tracked copy under
`deploy/systemd/` is the source and the live unit is a hand-installed copy of
it. So unlike every other change to this page, the board needs a deploy step
and not just a restart:

```bash
sudo install -m 0644 /srv/orchestration/deploy/systemd/lab-webapp.service \
    /etc/systemd/system/lab-webapp.service
sudo systemctl daemon-reload && sudo systemctl restart lab-webapp
```

Skip it and the page still serves - with five columns of "tracker command
could not be run", which is the failure this paragraph exists to make
findable.

## Unattended

A cycle is one command, and nothing about it needed a human to begin with -
so what makes the Selector unattended is a timer, a lock, and enough on the
page to tell a Selector that is quiet from one that is dead (#156).

**The timer.** `scripts/selector-cycle.timer` fires
`scripts/selector-cycle.service` every thirty minutes, around the clock. Both
are installed by `ansible/roles/timers`, which copies every `scripts/*.service`
and `scripts/*.timer` wholesale. The venv the unit execs is built by
`ansible/roles/selector_cycle`, and the fcontext that makes it executable by
systemd is declared in `ansible/roles/selinux_labels`; without it the unit
dies 203/EXEC with no traceback.

**Installed is not enabled, and that is on purpose.** `selector-cycle` is the
one timer deliberately absent from `orchestration_timers`. Every other timer
on this box reads something and reports; this one *spends* - each cycle can
seed a branch, start a Run on the box, and comment on and relabel issues on
somebody else's tracker, around the clock. Enabling that must not be a side
effect of applying the playbook for an unrelated reason, so it is gated on one
declared variable:

```yaml
# ansible/roles/timers/defaults/main.yml
selector_dispatch_enabled: false
```

Flipping it is a one-line reviewable change - the shape ADR 0014 already asks
for when widening the labeler allowlist - and the play prints which way it left
the timer. Out of band it is `sudo systemctl enable --now selector-cycle.timer`.
Setting it back to `false` stops a running timer, not merely a future one.

`OnFailure=notify-unit-failure@%n.service` is in the unit's **`[Unit]`**
section, which is the only section systemd reads it in - in `[Service]` it is
silently ignored, and a sibling unit on this box shipped that way with its
alert dead from the day it was added. It is story 31's requirement, so it is
worth proving rather than reading:

```bash
systemctl show selector-cycle.service -p OnFailure --value
# notify-unit-failure@selector-cycle.service.service   <- registered
#                                                      <- empty means it is NOT
```

`Persistent=false` is the setting that spends money if it is wrong. `true`
would fire a catch-up cycle the moment a box came back from six hours down,
and a catch-up cycle is a real Run against a real repository. Nothing is lost:
the queue is still there in thirty minutes, and the cap is a rolling window
rather than a quota to use up.

**One cycle at a time.** Every cycle takes a Postgres advisory lock on its
Journal connection and a second one stands down - journaling
`cycle.skipped` with reason `cycle-in-progress`, and exiting **0**. Exit 0
matters: with a thirty-minute timer and a ninety-minute Run this happens two
or three times per Run, and a timer whose `OnFailure` paged for it would page
for the Selector working.

A lock the database owns rather than a lock file, because a lock file left
behind by a killed cycle wedges the timer until somebody notices and deletes
it, where a connection that dies releases its lock. It is a different thing
from the in-flight lock in the caps: that one is journaled and is about *Runs*,
this one is about *processes*, and it stops a second cycle before it has read
anything at all.

**The daily cap holds across cycles** because it was always read from the
Journal rather than from memory - which is what makes it survive a process
that runs for ninety seconds every half hour and remembers nothing. The fifth
dispatch of a rolling day is refused before the box is reached, and the
refusal is journaled: `cycle.finished` with `halted: daily-cap-reached` and
the budget it counted.

The refusal is journaled on `cycle.finished` rather than as an event of its
own, unlike the lock refusal above. The asymmetry is not an oversight: a cycle
refused by the cap still *ran* - it read the queue, applied Eligibility to all
of it and journaled the lot - so its record is the cycle summary, with
`halted` naming the cap. A cycle refused by the lock reasoned about nothing
and has no summary to carry the reason, so it needs a row of its own.

**The box card.** Once per cycle - not in a dry run, which still reaches the
tracker and nothing else - `box-sources/facts.sh` reads three facts off the
box over SSH and they are journaled as `box.observed`:

| fact | why it is on the card |
| --- | --- |
| loop scripts hash | the box's copy of the Loop is placed by an ansible apply, not by a merge, so it drifts from the reviewed copy in this repository silently and nothing else would say so (story 34) |
| guest template | the `sbx` template every Iteration's microVM is built from - the Execution Boundary's identity (ADR 0003), and a thing story 33 may change |
| agent version | the Termination Contract's five numbers are calibrated against a Run, and a Run by another agent version is a Run against another calibration |

The template is *asked of* the agent adapter (`agents/claude.sh
--guest-template`) rather than read out of it, so the answer comes from the
file that makes the choice. A fact the box does not report is left blank on
the card rather than filled in from the controller's copy: the box's Loop can
be older than this repository's, and a page that guessed would be asserting
something nothing observed.

A box that cannot be read is journaled `box.unreachable` and **does not fail
the cycle**. The dispatch behind it fails on its own and pages on its own, and
paging twice for one outage is how an alert stops being read.

That is a knowing narrowing of story 31, which lists "SSH failure" among the
Selector's own failures that should page. The residual: with an empty or fully
blocked queue there is no dispatch behind the read, so a box that has been
unreachable for days pages nothing and says so only on the card. Accepted
because the alternative pages every thirty minutes for a box nobody is asking
to do anything.

**The status strip** is the `/loop` half of all of this: what the Selector is
doing, when the timer fires next, `Runs today N of 4`, and the box card. The
budget is read through `cycle.spend` - the same function the cap is enforced
with - because two readings of "Runs today" that could disagree would be a
strip that reassures about a cap it is not the one reading. The next-cycle
cell reads `systemctl show` on the timer through `SELECTOR_TIMER_COMMAND`
(default: `systemctl show selector-cycle.timer`; the one substitutable command
the page owns rather than `cycle.py`, and the seam the dashboard suite drives)
and says so loudly when the timer is not active. So does the Selector cell
itself, which reads `stopped - nothing will start a cycle` rather than `idle`:
a Selector with nothing to do and a Selector whose timer was never enabled
look identical from the Journal, and that is the failure the strip exists for.

## The issue contract

`ready-for-agent` promises **one** section, and everything else a Run needs
either has a default or is optional:

| section | required | what it is |
| --- | --- | --- |
| `## Acceptance criteria` | yes | one list item per criterion, carried into the Plan verbatim. The Run's only definition of done, and what `seed-run.sh` already refuses without |
| `## Owning area` | no | one phrase naming the part of the issue a single Run is scoped to. Without it the Run is scoped to **the issue's title** |
| `## Check` | no | a command that grades the work, run by the Run as it goes |

`Owning area` was required until 2026-08-27, as spec #151's Issue contract and
story 10 wrote it. It is a defensible requirement - `--area` is the Loop's
scope fence, written into the Plan as the line everything else in the issue is
outside of, and deciding how much of a large issue one Run is for is a
judgement an Iteration should not be making. It was dropped anyway, and the
reason is what the Selector is for: it made the Handover two steps instead of
one, and the operator's whole ask was that applying the label be the last
human action.

The measurement settled it. Of the 28 tourbot issues carrying
`ready-for-agent` on 2026-08-26, the day the requirement went in, 10 were
otherwise ready and **all 10** lacked the section - so its practical effect
was to hand the queue back rather than to work it. Re-measured on 2026-08-27
without it: 4 eligible where there had been none.

What replaces it is a default rather than a guess. An issue with no
`## Owning area` is scoped to its own title, which is the honest reading of
"work this issue"; an issue that really is bigger than one Run still says so
by carrying the section, and the fence still holds for it. This is a
deliberate amendment to #151 rather than a reinterpretation of it.

## The loud skip

An issue that passes every other Eligibility clause and is missing
`Acceptance criteria` is not quietly passed over: the
Selector comments on it naming the gap and the way back, swaps
`ready-for-agent` for `needs-info`, and journals `issue.returned`. The comment
goes first - a swap that landed with no comment would take the issue out of
the queue with nothing on it saying why.

It happens for every such issue in the queue and independently of the caps:
handing work back is not spending a Run, and an issue the Selector will never
seed should not wait for a free budget to be told so. A hand-back GitHub
refused is journaled as `issue.return-failed` and exits the cycle non-zero, so
the timer's `OnFailure` pages (story 31).

What was verified on the real box - the failure hookup under `systemctl
show`, the unit's exec path end to end, what `facts.sh` actually answers, and
the drift the scripts hash exposed on its first read - is in
`../notes/selector-unattended-evidence.md`.

Dispatch against the real box - the SSH hop's two-shell quoting, the whole
chain end to end, and the one acceptance criterion that needs the operator -
is written up in `../notes/selector-dispatch-evidence.md`.

The first live dry-run is written up in
`../notes/selector-first-dry-run-evidence.md`, including what it found: no
tourbot issue carried the `Owning area` section the label promised on
2026-08-26, so on that day every otherwise-ready issue in the queue was one
the loud skip above would have handed straight back. **That requirement was
dropped on 2026-08-27** - see "The issue contract" below - so the note records
a rule the Selector no longer applies.

## The outcome

The Run has ended, its summary is in the Journal, and the issue is still
sitting in the agent queue. What happens next is decided from two facts and
nothing else - the Run's ending bound, and the Proposal's checks - and it is
the bookkeeping the box deliberately cannot do (ADR 0010).

| the Run | the checks | what the Selector does |
| --- | --- | --- |
| cut short by `run-clock`, `consecutive-noops` or `agent-failed`, first attempt | not read | nothing to the issue. It keeps `ready-for-agent`, and the next cycle dispatches it again **on the same branch** |
| cut short again, second attempt | not read | comment saying what happened, swap to `ready-for-human` |
| reached its `iteration-cap` with a Proposal | green | swap to `awaiting-review`, no comment |
| reached its `iteration-cap` with a Proposal | red | comment naming the failing checks, swap to `ready-for-human` |
| reached its `iteration-cap` with a Proposal | still pending when the wait is spent | comment saying so, swap to `ready-for-human` |
| reached its `iteration-cap` with a Proposal | no check ran at all | comment saying so, swap to `ready-for-human` |
| reached its `iteration-cap` and proposed nothing | not read | a failed attempt: retried once, then given up |

A few of those deserve their reasoning stated.

**`iteration-cap` is not a failure.** A Run that reached its cap did what it
was designed to do, faults or no faults, and it is judged on the Proposal it
left rather than on the bound that ended it. Everything else is the
Termination Contract cutting a Run short, which is what story 14's retry is
for.

**The retry is the queue working, not a second code path.** A first failure
swaps no label, so the issue is simply picked again by the ordinary route, and
`prepare_branch` continues the branch the first attempt left behind rather than
resetting it - which is what stops a retry proposing an empty diff.

**Nothing is dispatched a third time.** Two guards, deliberately independent:
the give-up swaps the label out of the queue, and Eligibility refuses an issue
whose Journal already holds `MAX_ATTEMPTS` dispatches since it was last
labeled. The second is what holds when GitHub refuses the first.

**Pending checks are not green.** CI starts when the Run pushes, so at the
moment the box hands back its summary the checks have usually only just been
queued; reading once and calling that final would route nearly every green Run
to a human. The Selector polls for `SELECTOR_CHECKS_TIMEOUT_SECONDS` and, if
CI still has not decided, hands the issue to the operator rather than putting
unverified work in the review queue.

**"No check ran" is not "every check passed".** They are opposite facts about
how far a Proposal has been verified, so `none` is its own state rather than a
flavour of green. On a repository that has CI - which tourbot does - it usually
means something went wrong upstream: a workflow file that will not parse,
Actions disabled, a run that never triggered. Sending that to review as though
it had passed would be the same false pass as a permission error read as an
all-clear, reached by a different road.

**Red is the fallthrough.** `issue-sources/github.sh` maps an unrecognised
check state to red, and so does `cycle.py`. An unknown state must never reach
`awaiting-review` as though it had passed.

**The green path needs a token permission it does not yet have.** Reading a
Proposal's checks needs **Checks: Read** on the fine-grained PAT, and the
operator's token does not hold it for tourbot today. Until it does, a clean
Run's route fails loudly - `issue.route-failed`, cycle exit 1, the timer pages
- rather than mislabelling unverified work. The other three routes do not read
checks and are unaffected. See `../notes/selector-outcome-evidence.md`.

Each route appends its own Journal row - `issue.retrying`, `issue.given-up`,
`issue.awaiting-review`, `issue.handed-to-human` - so the Journal is greppable
by outcome, and `/loop` reads them to badge each Run card with the label the
issue actually carries. Bookkeeping the tracker refused is journaled as
`issue.route-failed` and exits the cycle non-zero, so the timer's `OnFailure`
pages (story 31). The `run.outcome` row is written before any of this, so a
label swap GitHub rejected never erases the Journal's record that a Run ran.

## Files

- `cycle.py` - the cycle above: Eligibility, ordering, caps, and the
  journaling of every decision. Deterministic code, never an agent (ADR 0014).
- `dispatch.py` - the five mechanical steps between a pick and a started Run.
  It decides nothing and journals nothing: `cycle.py` journals what it
  returns, so the Journal's shape is settled in one file rather than two.
- `tracker-sources/github.sh` - the default `SELECTOR_TRACKER_COMMAND`: one
  `gh api graphql` call normalized to a flat record per issue (labeler,
  native blocker count and the blockers themselves, open sub-issues, open
  Proposals). Substitutable, and the seam the offline suite drives.
- `board.py` - the queue board `/loop` renders (#158): the same tracker
  command, asked once per label in the lifecycle, columned by `cycle.py`'s
  own `eligibility`. It decides nothing - the predicate is imported, not
  reimplemented, because a page that decided for itself which issues were
  Eligible would be a second Selector and the first disagreement between them
  would be a bug in whichever one you did not read.
- `fixtures.py` - the canned tracker record every suite that drives
  Eligibility builds from, including the dashboard's. Imported by tests and
  by nothing that ships.
- `issue-sources/github.sh` - the default `SELECTOR_ISSUE_COMMAND`: one
  comment, one label swap, or one read of a Proposal's checks, as the
  operator. This is the half of the work
  the box deliberately cannot do - its token holds no Issues permission at
  all - so every write to the tracker happens here.
- `box-sources/facts.sh` - the default `SELECTOR_BOX_FACTS_COMMAND`: one SSH
  hop that reads what the box is holding - the Loop scripts' hash, the guest
  template its adapter would build, the installed agent version - and prints
  them as `LOOP_BOX_*=value` lines. Read-only, holds no credential, starts
  nothing.
- `watcher.py` - the Iteration watcher: the Progress Log parser, and the
  thread that polls the box for the length of a Run. It journals
  `run.iteration` and `run.watch-failed` and nothing else, and it never
  raises into the dispatch it is watching.
- `box-sources/progress.sh` - the default `SELECTOR_BOX_PROGRESS_COMMAND`: one
  SSH hop that `cat`s the box checkout's Progress Log. Read-only, holds no
  credential, touches no working tree.
- `box-sources/ssh.sh` - the default `SELECTOR_BOX_COMMAND`: one SSH hop that
  puts the box's checkout on the Run's branch and runs `run.sh --propose
  --notify`, printing what the Run reported. It holds no credential of its
  own and starts nothing else.
- `schema.sql` - one append-only `journal.events` table in the local
  Postgres. NOTIFY on insert and the append-only guard both live in schema
  triggers, so every append path behaves the same, including a hand `psql`
  INSERT. Idempotent; re-apply freely.
- `journal.py` - the whole write surface (`append`) plus the reader
  (`events`, newest first). No update, no delete. `SELECTOR_JOURNAL_DSN`
  overrides the DSN (tests do); default is `dbname=selector`, peer auth over
  the unix socket, zero credentials.
- `testdb.py` - the shared throwaway-test-database harness: create a random
  database, apply `schema.sql`, drop it afterwards. Used by this suite and by
  `lab/webapp/tests/` (the dashboard side). Needs a Postgres role matching
  the OS user with CREATEDB.
- `tests/` - three suites at three boundaries. `test_journal.py` is the
  Journal against the real engine: NOTIFY asserted by a live listener,
  mutation blocked by the guard triggers, ordering. `test_cycle.py` runs the
  real `cycle.py --dry-run` against canned queue states, reading only the
  commands it issued and the rows it wrote - plus a tripwire PATH (`gh`,
  `git`, `ssh`, `seed-run.sh` shimmed to log and fail) that makes "a dry run
  touches nothing but the Journal" a checked property of every scenario.
  `test_unattended.py` is #156's three properties at the same boundary: a
  second cycle standing down while one is in flight (driven by running a real
  nested `cycle.py` from inside the fake box, which is the only moment two can
  meet), the fifth dispatch of a day refused from Journal history alone, and
  the box's facts read and journaled.
  `test_dispatch.py` runs the same real `cycle.py` with the dispatch commands
  scripted and **git real**, against a bare repository in a tmpdir: the
  branch, the push and the retry case are asserted against what actually
  ended up on a remote. Its fake box also reads the Journal while it is
  "running", which is how the in-flight lock being held for the length of a
  Run is checked rather than assumed.
  `test_watcher.py` is #157's two halves: the parser against Progress Log
  snapshots written the way `run.sh` writes them, and the real `cycle.py`
  dispatching against a box that GROWS A LOG while it runs, with the interval
  collapsed from a minute to a fraction of a second. Each snapshot is held
  still for several polls, so "exactly the new records, never a duplicate" is
  an assertion rather than a coincidence of timing.

The window is `lab/webapp`'s `/loop` page, which imports `journal.py` from
here and renders at request time (SSE via LISTEN/NOTIFY is issue #159).

## Host setup

Three roles in `site.yml`, in this order: `selector_journal` (the Postgres
below), `selector_cycle` (the venv the timer's unit execs, correctly
labelled), and `timers` (which installs and enables the timer itself).

`ansible/roles/selector_journal` owns the Journal: PostgreSQL 16,
socket-only (`listen_addresses = ''`), a peer-auth `conductor` role with
CREATEDB, the `selector` database, schema applied. A rebuilt VM gets all of
it back from the playbook.

## Running the tests

```bash
cd lab/single-user-factory/selector
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

Tests skip (not fail) when the local Postgres is unreachable.

Then the mutation check, which is the suite's own grade:

```bash
tests/mutation-check.sh          # ~6 minutes: one suite run per mutation
```

It breaks one guard at a time - the allowlist, each Eligibility clause, each
cap, the fence-aware section reader, the tracker's failure path, and now each
step of dispatch and each half of the loud skip - and every one must turn its
suite red. A guard whose removal leaves it green is a guard nothing verifies.
Same contract as the Loop's `loop/tests/mutation-check.sh`; adding a guard
means adding its mutation to `tests/selector-mutations.py`, where each entry
names the file it breaks and the suite that has to notice.

## Inspecting the live Journal

```bash
psql -d selector -c "SELECT id, at, kind, payload FROM journal.events ORDER BY id DESC LIMIT 20;"
```

Appending by hand is legitimate (the schema NOTIFYs either way); updating or
deleting raises `journal.events is append-only` unless you deliberately drop
the guard triggers first.

## Previewing a branch (the Attended Preview)

`lab-webapp.service` runs from `/srv/orchestration` on `master`, so without a
preview the only way to see a branch's `/loop` is to merge it. ADR 0016 rules
on the alternative and names it an **Attended Preview**: a second instance at
`lab-staging.etadventures.com`, serving one unmerged branch, permissible on
this VM because somebody is looking at it.

```bash
scripts/lab-preview.sh --help
scripts/lab-preview.sh feat/queue-board
```

One preview at a time, held by a lease the banner renders (branch, short SHA,
who started it, uptime). The script refuses a live lease unless you pass
`--force`, and forcing means the operator holding it is now looking at your
branch without knowing. The unit carries `RuntimeMaxSec=4h` and is deliberately
**not** enabled, so a preview exists only while somebody has started one.

### Running a cycle inside a preview

```bash
lab/single-user-factory/selector/preview-cycle.sh --dry-run
```

**Use the wrapper, not `python cycle.py`.** The containment ADR 0016 builds is
on the `labstage` account that runs the web process: no `CONNECT` on the live
`selector` database, no SSH key, no vault environment. It does not extend to
your shell. You are `conductor`, which holds the token, the SSH key and write
on the live Journal, so a bare `cycle.py` in the preview tree reads the real
tourbot queue and dispatches a real Run against it.

`preview-cycle.sh` points every outward reach at `preview-sources/` - a canned
queue of three issues covering eligible, blocked and missing-section; a box
that starts nothing and reports a plausible ended Run; a box status read that
answers fixture facts rather than opening an SSH session (#156); Seeding that
fetches nothing; issue bookkeeping that writes nothing - and the Journal at
`selector_staging`. It refuses outright if `SELECTOR_JOURNAL_DSN` resolves to
the live Journal, because a cycle appends and those appends would be permanent.

**"Every outward reach" includes two that are not commands**, and they are the
easy ones to miss. `SELECTOR_WORK_REPO` defaults to a real tourbot checkout,
and `dispatch.py`'s `push()` runs `git push --set-upstream origin <branch>`
against it *before* the box is ever reached - so faking the tracker and the
box while leaving that alone still puts a branch on the real repository.
`SELECTOR_SEED_COMMAND` defaults to the Loop's real `seed-run.sh`. The wrapper
redirects both, at a throwaway work repo whose `origin` is a local bare repo
under `/var/lib/lab-preview/work`, and
`tests/test_preview_cycle.py::test_no_outward_reach_is_left_on_its_default`
fails if a new one is ever added and left alone.

### The staging Journal

`selector_staging` is built from `schema.sql` plus `seed.sql`, and is
disposable. Note before you read the strip against it: the budget cell says
`7 of 4`, because the fixture packs every outcome state into one hour and each
needs its own dispatch. Unreachable in life - the cap refuses the fifth - and
explained where the fixture packs them.



```bash
dropdb selector_staging && createdb -O conductor selector_staging
psql -d selector_staging -f schema.sql -f seed.sql
```

`seed.sql` is not decoration. The live Journal has never held a
`run.dispatched` or a `run.outcome` row, so a preview reading real data shows
a page with no Run cards and proves nothing about a branch that changed how
Run cards look. The fixture covers every state `/loop` renders, and
`lab/webapp/tests/test_staging_seed.py` fails when it stops covering one -
**adding a state to the page means adding it to `seed.sql` in the same
change.**
