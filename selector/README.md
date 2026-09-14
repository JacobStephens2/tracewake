# The Selector

The Selector (issue #151's spec; vocabulary in `../CONTEXT.md`) drains a
target repository's Handover queue unattended. This directory is its home.

What exists is **a cycle that picks, dispatches and does the bookkeeping**
(issues #153, #154, #155) on top of the **Selector Journal** (ADR 0015, issue
#152), **run unattended by a timer** (#156), with the **Iteration watcher**
(#157) reading the box's Progress Log while a Run is in flight so that the
activity is visible while it happens, a **pause flag** on `/loop` that
stops new Dispatches without stopping the timer (#161), and an **email
notifier** (#280, ADR 0018) that reads the Journal as it is written and is
the Loop's only channel to an operator who is not looking at the page.

## The cycle

```bash
cd selector
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

**Caps.** One Run in flight per Target, at most `SELECTOR_DRAIN_CONCURRENCY`
(default 1) Dispatches across Targets, and the target's `review_cap` (default
20) Proposals awaiting review. A cap that halts a cycle still lets it reason
and journal first, so the Journal answers "what would it have picked?" as well
as "what did it?".

**Proposal freshness** (ADR 0023). During each drain, every open Proposal that
is behind its base and mergeable is brought up to date via `SELECTOR_ISSUE_COMMAND`
`update-branch` and journaled once (`proposal.updated`). Conflicting Proposals
are not updated and are displayed as conflicting on the board. A forge refusal
is journaled (`proposal.update-failed`) without failing or halting any dispatch.

**Unenrolled-Target warning** (issue #39). Each Cycle also runs one owner-wide
search for the Handover label, diffs the repositories found against the
declared Targets, and journals the gap as `target.unenrolled`. A newly
appearing gap is one notice through the existing notification surface; a
standing gap journals without mailing again. The search is warn-only: it
never enrolls a Target and never writes to any tracker. `SELECTOR_SEARCH_OWNER`
is required instance configuration; there is no default that names an account.

**Pause.** `/loop` is the flag's only writer. A paused Selector still runs its
timer, reads the queue, applies Eligibility and journals the cycle; it stops
before picking or dispatching and writes `halted: paused` on `cycle.finished`.
That is why the next cycle card explains the quiet instead of disappearing.
Resuming lets the next ordinary cycle dispatch again; a Run already in flight
is not cancelled.

The singleton `selector.control` row is deliberately outside
`journal.events`. The flag is control input. The Journal records the paused
decision downstream, but it does not become a work source (ADR 0015).

## Configuration

An instance is **an env file plus a targets file**, and nothing in the code
knows the name of a company, a host, a person or a repository (issue #3).
INSTALL.md shows the Single-Host shape of both files. The product ships no
filled-in Instance. `tests/test_configuration.py` grades every default in the
shipping tree by shape: an email, a hostname, or an `owner/name` slug is
refused.

**The targets file** (`TRACEWAKE_TARGETS_FILE`, TOML) declares one stanza per
repository this instance works. A second target is a second stanza - not a
second controller, a second timer or a second Journal.

| key | default | what it is |
| --- | --- | --- |
| `repo` | required | `owner/name` on the tracker |
| `work_repo` | required | the controller's own clone, where Seeding happens |
| `box_repo` | required | the box's clone, where the Run works |
| `token_file` | required | the fine-grained token for THIS repository, on the box |
| `guest_template` | required | the `sbx` image each Iteration's microVM is built from |
| `labeler_allowlist` | required | whose labelling counts as a Handover |
| `labels.ready` | `ready-for-agent` | the Handover |
| `labels.needs_info` | `needs-info` | where a loud skip sends an issue |
| `labels.review` | `awaiting-review` | a green Proposal waiting on the operator |
| `labels.human` | `ready-for-human` | a give-up, or red checks |
| `review_cap` | `20` | how many Proposals may wait in review |
| `landing` | `propose` | what a finished Run does with its work |

A required value that is absent stops the cycle **at preflight** - before the
tracker is read - with the value named, and journals `cycle.failed`. For a
target's value the file and the stanza's position are named too. Both halves
are checked, and the instance's first: an absent `SELECTOR_BOX_HOST` is not
caught by the script that reads it until the cycle has already read the queue,
seeded a branch and pushed it, because `observe_box` treats a box it cannot
read as a status failure and carries on by design. That is the point of the move: a loader that filled a
gap in with something plausible would put the old problem back with an extra
file in front of it, and the first sign would be a comment on a stranger's
issue.

`cycle.py` works every declared target, each with its own
`cycle.started`/`cycle.finished` pair; `--target owner/name` works one.
With `SELECTOR_DRAIN_CONCURRENCY` greater than 1 (default 1), Dispatches on
different Targets overlap up to that cap; within a Target the drain stays
serial (ADR 0027).

**The instance file** is environment, and the values that name a host, an
address or a command have no default at all:

| variable | default | what it is |
| --- | --- | --- |
| `TRACEWAKE_TARGETS_FILE` | required | the targets file |
| `SELECTOR_BOX_HOST` | required | where the box is, as `ssh` takes it |

| `SELECTOR_LOOP_URL` | required | where this instance publishes its window |
| `SELECTOR_NOTIFY_COMMAND` | required | the mail surface (see below) |
| `SELECTOR_PROTECTED_REPO` | required | the repository holding the guardrail's rules |
| `SELECTOR_PROTECTED_REF` | required | the ref the executed paths are deployed from |
| `SELECTOR_SEARCH_OWNER` | required | the GitHub user or organization whose repositories a Cycle searches for a Handover label with no Target stanza |
| `SELECTOR_TRACKER_COMMAND` | `tracker-sources/github.sh` | the labeled queue, read |
| `SELECTOR_SEARCH_COMMAND` | `search-sources/github.sh` | the owner-wide Handover search, read |
| `SELECTOR_JOURNAL_DSN` | `dbname=selector` | |
| `SELECTOR_WORK_REMOTE` | `origin` | |
| `SELECTOR_BRANCH_PREFIX` | `loop/` | |
| `SELECTOR_SEED_COMMAND` | `../loop/seed-run.sh` | the Loop's own seed step |
| `LOOP_PROGRESS_LOG_PATH` | `PROGRESS.md` | the Progress Log kept aside before Seeding |
| `LOOP_RUN_HEADING` | `## Run started` | what marks a Progress Log as recording a Run |
| `SELECTOR_BOX_COMMAND` | `box-sources/local.sh` | the box, and the Run on it. `box-sources/ssh.sh` is the remote-Box substitute |
| `SELECTOR_ISSUE_COMMAND` | `issue-sources/github.sh` | comments and label swaps |
| `SELECTOR_COMMAND_TIMEOUT_SECONDS` | `300` | git, Seeding, tracker writes |
| `SELECTOR_DISPATCH_TIMEOUT_SECONDS` | `7200` | backstop for a wedged Run |
| `SELECTOR_CHECKS_TIMEOUT_SECONDS` | `900` | how long a Proposal's checks may stay pending |
| `SELECTOR_CHECKS_POLL_SECONDS` | `30` | how often they are re-read while pending |
| `SELECTOR_BOX_FACTS_COMMAND` | `box-sources/facts.sh` | the box, read for the status card |
| `SELECTOR_BOX_FACTS_TIMEOUT_SECONDS` | `60` | how long that status read may take |
| `SELECTOR_GUARDRAIL_COMMAND` | `guardrail-sources/protection.sh` | the write protection over the executed paths, read |
| `SELECTOR_GUARDRAIL_TIMEOUT_SECONDS` | `30` | how long that read may take |
| `SELECTOR_BOARD_TIMEOUT_SECONDS` | `10` | how long one of the queue board's tracker reads may take |
| `SELECTOR_DRAIN_CONCURRENCY` | `1` | how many Dispatches a Cycle may hold at once; serial within a Target |
| `SELECTOR_SSE_KEEPALIVE_SECONDS` | `20` | how long an open `/loop` stream may say nothing before a keepalive |

The last two are deliberately the **Loop's** names rather than `SELECTOR_*`
ones. `contract.sh` declares both because `seed-run.sh` and `run.sh` have to
agree about them, and the Selector is now the third reader: one that spelled
either out would quietly stop finding the Progress Log it is supposed to keep,
and meet Seeding's refusal on the other side of that silence.

Two timeouts because the two waits are nothing like each other: everything
except the Run should answer in seconds, and giving a comment or a fetch the
Run's budget would let one wedged call hold a cycle open for two hours.

`box-sources/ssh.sh` reads four more: `SELECTOR_BOX_HOST` (required),
`SELECTOR_BOX_USER` (`loop`), `SELECTOR_BOX_REPO` (the target's, set from its
stanza) and `SELECTOR_BOX_LOOP` (`/home/loop/loop`). It also carries two
per-target values across the hop rather than reading them -
`LOOP_GITHUB_TOKEN_FILE` and `LOOP_GUEST_TEMPLATE` - because what consumes
them is the Run. `box-sources/local.sh` (ADR 0019) shares `SELECTOR_BOX_REPO`,
`SELECTOR_BOX_LOOP`, `LOOP_GITHUB_TOKEN_FILE` and `LOOP_GUEST_TEMPLATE`, requires
no `SELECTOR_BOX_HOST`, and gates dispatch on `loop/assert-credentials.sh`.
`box-sources/facts.sh` shares the first, second and fourth of ssh.sh's,
and adds `SELECTOR_BOX_AGENT` (`claude`) - which adapter it asks for the guest
template.

`guardrail-sources/protection.sh` shares none of those - it reaches GitHub and
this checkout rather than the box - and reads five of its own:
`SELECTOR_PROTECTED_REPO` (required), `SELECTOR_PROTECTED_REF` (required, the
ref the executed paths are deployed from), `SELECTOR_PROTECTED_REMOTE`
(`origin`), `SELECTOR_PROTECTED_TREE` (the repository the script itself is
deployed in) and `SELECTOR_PROTECTED_PATHS` (`guardrail-sources/paths.txt`).

Widening a target's allowlist is one entry in its stanza plus a note in ADR
0014, which is what story 35 asks for.

## Dispatch

A cycle that picked something and was not asked for a dry-run dispatches it.
Five steps, in this order, all of them through substitutable commands:

1. **The branch.** `git fetch`, then `loop/<number>-<owning-area>` from the
   base branch - or from the branch itself when the remote already has one,
   so a retry continues what the first attempt committed instead of resetting
   it away. A branch a previous attempt actually ran on carries that attempt's
   `PROGRESS.md`, and Seeding refuses to overwrite one; it is appended to
   `PROGRESS-earlier.md` and committed here, so step 2 meets a branch with no
   live Progress Log on it and the record it would have discarded is still on
   the branch and still in the Proposal (#301).
2. **Seeding.** The Loop's own `seed-run.sh`, unchanged, with `--area` and
   `--check` taken from the issue's sections. It still runs HERE rather than
   on the box: ADR 0010's enforcement is that the box's token holds no Issues
   permission, and ADR 0014 moved the Handover to the label without moving
   Seeding. Its refusal - a task whose acceptance criteria it cannot read -
   ends the dispatch rather than being worked around.
3. **The push.** The Plan reaches the box as a commit, like everything else.
4. **The Run.** `box-sources/local.sh` (the product default) puts the box's
   checkout on the branch and runs `run.sh --repo ... --propose --notify`. An
   instance that splits the Box off this Host substitutes `box-sources/ssh.sh`.
   Either command blocks for the length of the Run, which is the point: the
   box persists no record of one, so the only moment the summary exists
   anywhere is while something is holding the process.
5. **The outcome.** The `LOOP_RUN_*` block is parsed and journaled - the bound
   that ended the Run, its exit code, its Iterations, its faults and its
   Proposal URL.

`run.dispatched` is journaled **before** step 4 and `run.outcome` after it.
That gap is the in-flight lock every later cycle reads, so the ordering is the
concurrency control rather than bookkeeping: a dispatch journaled only on
success would leave the ninety minutes a Run takes unguarded. Its
`kept_progress` field names the file step 1 moved an earlier Progress Log
into, and is null when there was none to move - a file that moved on the Run's
branch is not something to do silently.

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
record it has not journaled before (story 26), plus one `run.contract` for the
Run's own terms (story 25, and the discipline skills of #162 - below).
Configuration:

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

**The Contract comes across the same hop.** The bounds a Run executes under
are the box's - the environment `run.sh` was started with - and the only place
they cross to this side is the summary the Run writes into its log before its
first Iteration. The watcher journals that summary as one `run.contract` row
per Run, so the current-Run panel shows the terms THAT Run was given rather
than this side's configuration read back at the operator (story 25), and the
discipline skills the Iterations were told to invoke come with it (#162). The
same three properties hold: complete or nothing, this Run's or none, and
exactly one row however many times the cumulative log is re-read
(`journal.contract_seen` is what makes a restarted watch idempotent too).

**A target's work checkout is not created for you.** Seeding commits the Plan,
so the checkout needs an identity to commit as - the operator's, because that
is whose Handover this is. Where the controller's git config already carries
one, signing key included, the clone is the whole of the setup:

```bash
git clone <the target repository> <the stanza's work_repo>
```

It should deliberately **not** be a checkout anybody works in. A shared tree -
one an operator has open in their own editor - is one a dispatch would switch
the branch of out from under them, which is the Selector reaching through the
glass.

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
a live GitHub queue on 2026-08-27: about three seconds for all three,
which is what a `/loop` request now costs, and why the bound is ten seconds
rather than the minute a cycle would allow.

**The in-flight column has two sources, deliberately.** One is the tracker's:
an issue with an open Proposal, which is `cycle.eligibility`'s `proposal-open`.
The other is the Journal's: a dispatch with no outcome, which is the Selector's
own in-flight lock and exists *before* any Proposal does. Per-issue Eligibility
has no in-flight concept at all - the cycle halts on the lock rather than
skipping an issue for it - so a board built from the predicate alone would draw
the issue a Run is working right now as the next thing to pick, which is the
one card on the page an operator would act on wrongly. This is the page reading
one more Journal fact, not the page deciding work: nothing about the column
changes what any cycle does, and ADR 0015's rule is that the tracker remains
the only work source, not that the window may only read one source.

The columning is `cycle.eligibility`, imported. Two of the things it decides
on are not on the tracker at all - the retry budget and the in-flight lock are
Journal facts - so the page hands the board the same `Spend` its budget cell
is read from. When the Journal is unreachable the board still renders and says
which two columns it is now reading blind, because an issue whose attempts are
spent looks Eligible without it and that is the one card an operator would act
on.

Note what this needs of the process serving the page: `gh` on its `PATH`, and
`gh`'s own auth. `tracewake-web.service` sets the PATH and nothing else - the
tracker read is authenticated by conductor's `~/.config/gh/hosts.yml`, which
is the operator's read of the operator's tracker (ADR 0014). It deliberately
does not get the vault environment the Selector's own unit gets: that would
hand a page-serving process every production credential on the box to get a
queue listing.

`tracewake-web.service` is not installed by Ansible - the tracked copy under
`deploy/systemd/` is the source and the live unit is a hand-installed copy of
it. So unlike every other change to this page, the board needs a deploy step
and not just a restart:

```bash
sudo install -m 0644 /srv/tracewake/deploy/systemd/tracewake-web.service \
    /etc/systemd/system/tracewake-web.service
sudo systemctl daemon-reload && sudo systemctl restart tracewake-web
```

Skip it and the page still serves - with five columns of "tracker command
could not be run", which is the failure this paragraph exists to make
findable.

## Unattended

A cycle is one command, and nothing about it needed a human to begin with -
so what makes the Selector unattended is a timer, a lock, and enough on the
page to tell a Selector that is quiet from one that is dead (#156).

**The timer.** `deploy/systemd/tracewake-selector-cycle.timer` fires
`deploy/systemd/tracewake-selector-cycle.service` every thirty minutes, around
the clock. Installing them is the instance's job today - ETA's `timers` role
copies units wholesale out of its own tree - and giving this repository a role
that installs its own units is the units-and-roles ticket. The venv the unit
execs is built by `deploy/ansible/roles/selector_cycle`, and on an SELinux box
the fcontext that makes it executable by systemd is declared in the instance's
`selinux_labels`; without it the unit dies 203/EXEC with no traceback.

**Installed is not enabled, and that is on purpose.** `tracewake-selector-cycle` is the
one timer deliberately absent from `orchestration_timers`. Every other timer
on this box reads something and reports; this one *spends* - each cycle can
seed a branch, start a Run on the box, and comment on and relabel issues on
somebody else's tracker, around the clock. Enabling that must not be a side
effect of applying the playbook for an unrelated reason, so it is gated on one
declared variable:

```yaml
# ansible/roles/timers/defaults/main.yml
selector_dispatch_enabled: true
```

Flipping it is a one-line reviewable change - the shape ADR 0014 already asks
for when widening the labeler allowlist - and the play prints which way it left
the timer. Out of band it is `sudo systemctl enable --now tracewake-selector-cycle.timer`.
Setting it back to `false` stops a running timer, not merely a future one.

**The gate is flipped and the timer is running** (#261, 2026-08-30): it fired
unattended at 23:30:52 the same evening, journalled the cycle and declined to
dispatch, because the Selector is paused. What it waited on, what was read to
check it, and what is still owed - the box has to be logged in before a resume
means anything - are in
[`../notes/selector-timer-on-evidence.md`](../notes/selector-timer-on-evidence.md),
which is the one place any of it is written down.

`systemctl is-active` is what answers "is it on", not `is-enabled`. A timer
enabled and never started is inert and reads as healthy, which this box has
already had once: `certbot-renew.timer` was enabled and not active on a machine
that never reboots, and an expired certificate is how anyone found out. The
`/loop` strip is the same question without a shell - its timer cell shows the
next firing while the timer is active, and says `timer not running` with the
state in brackets when it is not.

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

**The review cap holds across cycles and within a drain** because it reads
the target's awaiting-review column from the tracker on each pass. When the
number of awaiting-review issues reaches the target's configured `review_cap`,
further dispatches halt before the box is reached, and the refusal is
journaled: `cycle.finished` with `halted: review-cap-reached` and the count it
read.

The refusal is journaled on `cycle.finished` rather than as an event of its
own, unlike the lock refusal above. The asymmetry is not an oversight: a cycle
refused by the cap still *ran* - it read the queue, applied Eligibility to all
of it and journaled the lot - so its record is the cycle summary, with
`halted` naming the cap. A cycle refused by the lock reasoned about nothing
and has no summary to carry the reason, so it needs a row of its own.

**The box card.** Once per cycle - not in a dry run, which still reaches the
tracker and nothing else - `box-sources/facts.sh` reads four facts off the
box over SSH and they are journaled as `box.observed`:

| fact | why it is on the card |
| --- | --- |
| loop scripts hash | the box's copy of the Loop is placed by an ansible apply, not by a merge, so it drifts from the reviewed copy in this repository silently and nothing else would say so (story 34) |
| guest template | the `sbx` template every Iteration's microVM is built from - the Execution Boundary's identity (ADR 0003). Story 33 did change it: it reads `loop-php:1` since #164, the vendor's image plus PHP and Composer, and the version in the tag is what makes a rebuild visible here rather than only in an apply's output |
| agent version | the Termination Contract's five numbers are calibrated against a Run, and a Run by another agent version is a Run against another calibration |
| credential expiry | the first three say what the box *is*; this says whether it can currently do anything (#260). The subscription login lapses eight hours after a human mints it, so a box that passes every other check can still be sixteen hours a day unable to start a Run - which is what Run 645 hit, visible only in that Run's own Progress Log |

The template and the expiry are *asked of* the agent adapter (`agents/claude.sh
--guest-template`, `--credential-expiry`) rather than read out of it, so the
answer comes from the file that makes the choice - and for the expiry, from
the one file that knows the vendor's credential layout (ADR 0004). A fact the
box does not report is left blank on the card rather than filled in from the
controller's copy: the box's Loop can be older than this repository's, and a
page that guessed would be asserting something nothing observed.

The expiry is journaled as an **absolute instant**, never as a remaining time.
The read happens once a cycle and the page is viewed whenever, so a duration
recorded here would be wrong by however long the page sat open - and wrong in
the reassuring direction. `/loop` does that arithmetic against its own clock,
and an expired credential takes the headline off the scripts hash. Renewing it
is ADR 0017 and happens on the box, in the adapter, at every Iteration.

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
doing, when the timer fires next, `review capacity N remaining`, and the box card. The
review budget is read through `cycle.review_budget` - the same function the cap
is enforced with - because two readings that could disagree would be a
strip that reassures about a cap it is not the one reading. The next-cycle
cell reads `systemctl show` on the timer through `SELECTOR_TIMER_COMMAND`
(default: `systemctl show selector-cycle.timer`; the one substitutable command
the page owns rather than `cycle.py`, and the seam the dashboard suite drives)
and says so loudly when the timer is not active. So does the Selector cell
itself, which reads `stopped - nothing will start a cycle` rather than `idle`:
a Selector with nothing to do and a Selector whose timer was never enabled
look identical from the Journal, and that is the failure the strip exists for.

## Write protection (#165)

The Selector spends Runs against somebody else's tracker every thirty minutes
with nobody watching, and the Loop's scripts it dispatches are copied to the
box out of this working tree. So the standard is story 34's: **the unattended
executor must be no easier to change than the repository a Run makes Proposals
against.** tourbot is PR-gated; this had to be too.

**What is protected.** `guardrail-sources/paths.txt`, one path per line: the
Loop's scripts, the Selector, and the unit, timer and env wrapper that invoke
it. The test for inclusion is narrow - does a timer with nobody watching, or
model output, execute this? The rest of the repository is a working area, and
a guardrail that went red for a report page is one nobody could keep green.
The declaration lives inside one of the paths it declares, so widening it is
itself a reviewed change.

**Two halves, because either alone can be green over unreviewed code.**

| half | what it reads | the failure it catches |
| --- | --- | --- |
| the rules | `gh api repos/<repo>/rules/branches/master` | the ruleset comes off, or loses `pull_request`, and a push straight to master is accepted again |
| the tree | `git diff` and `git ls-files --others` against `refs/remotes/origin/master` | this checkout is shared and group-writable, and systemd execs what is sitting in it - a rule on a branch says nothing about the bytes about to run |

The rules required are `pull_request`, `non_fast_forward` and `deletion`: a
review a force-push can replace is not a review, and neither is one on a
branch that can be deleted and re-created. They are named rather than counted
so the chip can say which one went missing.

The tree half compares against `refs/remotes/origin/master` **as the checkout
already holds it** - no fetch, because a status read should not write to
somebody else's working tree. Staleness can only make it stricter: a reviewed
change the local remote-ref has not seen is not in the tree either.

**Where the verdict is made.** `guardrail-sources/protection.sh` reports and
grades nothing; `cycle.py`'s `guardrail_verdict` decides, because which rules
are required is a policy and belongs in reviewed Python. Unknown is not green:
a half the command did not answer counts against, so a command that quietly
stopped reporting shows red rather than reassuring. An absent `UNREVIEWED`
line and an empty one are different answers - "the comparison did not run" and
"nothing differs" - and reading the first as the second would report green for
the one state this exists to catch.

Read once per cycle and journaled as `guardrail.observed` (or
`guardrail.unreadable`), beside the box card and for the same reasons: the
page is a window, and not in a dry run. A guardrail that cannot be read does
**not** fail the cycle - it is a status read, and a Selector that stopped
working because GitHub would not answer a question about its own rules would
be a queue stopped by a dashboard.

**It does not gate dispatch, deliberately.** A red chip is a thing to fix, not
a reason to stop draining the queue: the protection is about who can change
the Selector, and refusing to work would hand an unprotected repository a way
to switch itself off. The gate is the chip and the operator reading it.

**A reading goes stale.** The chip is a claim about the present and is only as
good as the cycle that took it, so a reading older than `GUARDRAIL_MAX_AGE`
(90 minutes - two firings of a thirty-minute timer) reports its own silence
instead of the answer it is holding. Without the bound a Selector whose timer
died would keep asserting protection it had not checked since, which is the
failure the next-cycle cell already guards against, one panel along.

**What it does not defend against.** `protection.sh`, `paths.txt` and
`cycle.py` are themselves inside the declared paths, and it is the *deployed*
copies that run - so anybody who can write to the shared tree can edit the
checker to report green, exactly as they could edit the thing it checks. This
is a detector of drift and accident, not a defence against an operator with
write access; that boundary is ADR 0014's, and the accounts with write access
here are the operator's own. Nor does it recreate the ruleset: `Protect
master` lives in GitHub's settings and is declared in no file here, so the
guardrail can report its removal and cannot undo it.

**The chip** is the strip's fifth cell, and the one cell coloured when nothing
is wrong - it asserts something ("this cannot change without a review") rather
than reporting a number, and an assertion in the same grey as a timestamp
reads as another fact. Green names the ref, the commit it compared against,
the rules in force and how many declared paths it checked; red carries the
verdict's own sentence, which names the missing rule or the differing path.

The push that proves it is in
`notes/selector-write-protection-evidence.md`: a direct push to `master`
touching a protected path, rejected by GitHub, run rather than assumed.

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

**A retry re-seeds, and keeps what it re-seeds over.** The first attempt's
Progress Log is moved to `PROGRESS-earlier.md` and committed before Seeding
runs (`keep_previous_progress`). Three routes were open and this is the one
taken: `--reseed` would have been one flag, but `seed-run.sh` refuses for a
reason - the Progress Log is the only account of what a Run did, and the second
attempt reads the branch it is continuing - and seeding a fresh branch would
abandon the first attempt's commits, which is what `prepare_branch` exists to
avoid.

`PROGRESS-earlier.md` is **per branch**. A branch the remote already has
continues its own file, so a third attempt keeps the second without discarding
the first; a branch cut from the base starts one, so a kept log that reached
the base by being merged is not inherited and appended to by the next issue,
which would leave one file growing for the life of the repository. Nothing is
lost either way - a merged file's history is on the base branch.

Until this was fixed, three shapes reached no Run at all: a retry inside
one Handover, a fresh Handover of an issue worked before, and - once the first
Proposal was merged - **every** dispatch, because a Run proposes its Progress
Log with its work and so a merge puts one on the base branch. That last one is
what `tourbot#646` was in: tourbot master carries the log of the Run merged as
its #711, so a first attempt off master met the refusal too.

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

**Undecided is not red either.** A run that has completed but carries no
conclusion yet - the Actions API reports that transition briefly - is pending,
not red, because red is *terminal*: it comments and swaps to `ready-for-human`,
and only pending is polled again. A conclusion GitHub reports but this script
does not recognise is still red; the distinction is between a verdict this
script cannot read and a verdict GitHub has not given.

**Green is read from workflow runs, not from check runs.** A fine-grained PAT
cannot read check runs at all, and that is the token type rather than a grant
anyone forgot: GitHub's fine-grained tokens offer no Checks permission, so the
check-runs endpoint and the GraphQL rollup `gh pr checks` walks both answer 403
whatever the token holds (#273). So `checks` asks `gh pr view` for the
Proposal's head commit and then reads `actions/runs?head_sha=`, which the
Actions permission the token already holds does cover, and which carries the
same status and conclusion fields.

The cost is bounded and worth naming: **this sees only Actions-based checks.**
A required check posted by some other GitHub app would be invisible to the
Selector and a Proposal waiting on it could read green. Every one of tourbot's
checks is Actions today (five check runs on the first Proposal, all
`github-actions`). The alternatives - a classic PAT with `repo` scope, or
minting installation tokens from a GitHub App with Checks: Read - trade a broad
credential or new moving parts for that edge case. See
`../notes/selector-outcome-evidence.md`.

Each route appends its own Journal row - `issue.retrying`, `issue.given-up`,
`issue.awaiting-review`, `issue.handed-to-human` - so the Journal is greppable
by outcome, and `/loop` reads them to badge each Run card with the label the
issue actually carries. Bookkeeping the tracker refused is journaled as
`issue.route-failed` and exits the cycle non-zero, so the timer's `OnFailure`
pages (story 31). The `run.outcome` row is written before any of this, so a
label swap GitHub rejected never erases the Journal's record that a Run ran.

## Telling the operator (#280)

ADR 0013 left the Loop with one notification surface: a finished Run comments
on its own Proposal, and GitHub's email carries it. ADR 0018 added the second,
and put it **here rather than on the box** - the box's whole external reach
stays "the repository", with no mail host on its egress allowlist and no fifth
credential in its inventory (ADR 0009), while this VM already holds mail
infrastructure and its credentials for the status dashboard.

Since 2026-08-31 it is not a second surface but the **only** one. GitHub's
"include your own updates" setting, which ADR 0013's email depended on, is
account-global: turned on, it delivered every agent session's activity
everywhere, so it is off for good. The comment still goes on the Proposal as
the on-PR record; the Selector is what mails.

`selector-notifier.service` holds a LISTEN on `journal_events` - the channel
`schema.sql`'s insert trigger already NOTIFYs, the same one `/loop` pushes
from - and mails on four kinds of row:

| event | the row | what it replaces |
| --- | --- | --- |
| a Run finished with a Proposal | `run.outcome` | GitHub's email off ADR 0013's comment |
| a Run ended without one | `run.outcome` | silence: no Proposal, so nothing to comment on |
| a dispatch or preflight failed | `run.outcome` (`dispatch-failed`), `cycle.failed` | a `systemctl --failed` nobody is at a keyboard to read |
| the box's credential is close to expiring | `box.observed` | Runs cancelled by a login that lapsed overnight |

Green and not-green are decided with the vocabulary's own `RUN_FAILURE_BOUNDS`
and `outcome_name` (`events.py`) rather than a second predicate, for the
reason `board.py` imports `eligibility`. Two things would go wrong without
that. A Run cut short by its Contract can still be holding a Proposal, and an
email calling that green would make the Selector say two things about one Run.
And a `run.outcome` row carries the *raw* bound under `ended_by` - the rename
to `no-proposal` happens later, on the issue's own row, the one place a
derived `outcome` name is journaled - so a Run that reached its cap with
nothing to show would otherwise be mailed as "cut short by iteration-cap",
which is the opposite of what happened.

What the *checks* make of the Proposal is not in the mail at all - CI is read
afterwards, by the routing step, and the label the issue ends up carrying is
what says whether it passed.

Three pieces of state, each with a failure behind it:

- **The cursor** (`selector.notifier.notified_through`) is written after the
  mail surface accepted a notice, so a restart resumes where *delivery* got
  to. Deliberately at-least-once: a process killed between the send and the
  write repeats a message, which is a smaller failure than losing the only
  notice a silent Run ever produces.
- **`notified_through` starts NULL**, and a first start takes it to the newest
  row and sends nothing. Zero would mean "replay the Journal", which on
  install day is every Run there has ever been, in one inbox.
- **`selector.notifier_sent`** holds the notices that are about a thing rather
  than about a row. The credential is the only one: the box is observed every
  thirty minutes, so without it one expiry would mail four times on its way
  out. It is keyed on the expiry instant, so renewing is what makes the next
  warning sendable.

A notifier that was down for days replays what it missed, and rows past
`SELECTOR_NOTIFY_MAX_AGE_HOURS` (72 hours - a weekend of downtime still gets
reported Run by Run) are collected into **one** summary message naming every
notice and the span of Journal rows it covers, rather than dropped. #280 exists
to end silences, and a quietly discarded backlog would be a new one.

A refused delivery exits the process non-zero with the cursor untouched, so
`Restart=always` *is* the retry, and ten failures in ten minutes ends the unit
in `failed` - where its `OnFailure` hands the alarm to the dashboard's own
notifier, a different process with a different copy of the credentials. That
is the one alert on this box that must not go through this one.

Reading what it would have said, which changes nothing and writes nothing:

```bash
.venv/bin/python notifier.py --once --dry-run --since 0
```

Deliberately silent, so that each silence is a decision rather than an
oversight: `issue.route-failed` and `issue.return-failed` (the tracker
refusing bookkeeping already exits the cycle non-zero, and the unit's own
alert names it), `box.unreachable` (the dispatch behind it pages on its own -
one outage, one page), and every row `/loop` exists to show. Mail is for what
happens while nobody is watching.

Notifying on a *stuck* Run and on board-state transitions are both out until
their prerequisites exist - stuck-detection, and the derived Needs-you facet.
Detection precedes notification, or the mail is a guess.

## Files

- `cycle.py` - the cycle above: Eligibility, ordering, caps, and the
  journaling of every decision. Deterministic code, never an agent (ADR 0014).
- `dispatch.py` - the five mechanical steps between a pick and a started Run.
  It decides nothing and journals nothing: `cycle.py` journals what it
  returns, so the Journal's shape is settled in one file rather than two.
- `control.py` - the pause flag's whole interface: read before a Cycle picks,
  write only from `/loop`. Its singleton table is not Journal history.
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
- `guardrail-sources/protection.sh` - the default
  `SELECTOR_GUARDRAIL_COMMAND`: one `gh api` read of the rules on the ref the
  executed paths are deployed from, and one `git` comparison of the deployed
  tree against that ref, printed as `SELECTOR_GUARDRAIL_*=value` lines. It
  grades nothing - the verdict is `cycle.py`'s - and it reports an
  unprotected ref as a fact rather than as a failure.
- `guardrail-sources/paths.txt` - the declaration of what runs unattended,
  one path per line. Inside a path it declares, so widening it is reviewed.
- `watcher.py` - the Iteration watcher: the Progress Log parser, and the
  thread that polls the box for the length of a Run. It journals
  `run.iteration`, `run.contract` and `run.watch-failed` and nothing else, and
  it never raises into the dispatch it is watching.
- `box-sources/progress.sh` - the default `SELECTOR_BOX_PROGRESS_COMMAND`: one
  SSH hop that `cat`s the box checkout's Progress Log. Read-only, holds no
  credential, touches no working tree.
- `box-sources/local.sh` - the default `SELECTOR_BOX_COMMAND` (ADR 0029):
  executes a Run on this Host without an SSH hop, gated by
  `loop/assert-credentials.sh` (ADR 0019). Refuses dispatch naming the
  violation if the machine holds any forbidden credentials.
- `box-sources/ssh.sh` - the remote-Box `SELECTOR_BOX_COMMAND`: one SSH hop
  that puts the box's checkout on the Run's branch and runs `run.sh --propose
  --notify`, printing what the Run reported. It holds no credential of its
  own and starts nothing else. An instance substitutes this; INSTALL does
  not describe that topology.
- `events.py` - the Journal Event vocabulary (CONTEXT.md): every kind, one
  constructor and one reader each, and the naming rules - `outcome_name`, the
  failure bounds, the retry budget, the route names, the checks states.
  Closed for writers, so shipping code cannot invent a kind or misspell a
  key; total for readers, so a row from any era - the dual-spelled bound, a
  sparse seed row, a hand append - normalizes in one place. Pure on purpose:
  no psycopg, no clock, no import from `cycle`, which is what lets the page
  and the notifier import the meaning of a row without the machinery around
  it. Its suite holds the sweep: no kind literal ships outside this file.
- `notices.py` - which Journal rows are worth an email and what each one says
  (#280). Pure: a row in through `events.py`'s readers, a `Notice` or `None`
  out, no database and no clock of its own, so the wording and the choice of
  event are drivable directly - and the purity is a checked property, not a
  claim (its suite imports it in a subprocess and fails if the dispatcher
  comes too).
- `notifier.py` - the process around it: the LISTEN, the cursor that makes
  delivery survive a restart, the once-per-thing record, and the failure that
  loses nothing. `--once` drains and exits; `--dry-run --since <id>` prints
  what the Journal would have said and writes nothing.
  `SELECTOR_NOTIFY_COMMAND` is the mail surface: one command, subject on argv
  and body on stdin. It has no default - which relay, whose credentials and to
  whom are facts about an instance, not about Tracewake - so an unset one stops
  the notifier by name before it has read a row, rather than letting it advance
  its cursor past a backlog it could not deliver. Email only, never SMS (ADR
  0018).
- `schema.sql` - the pause flag and the append-only `journal.events` table in
  local Postgres. NOTIFY on insert and the append-only guard both live in
  schema triggers, so every append path behaves the same, including a hand
  `psql` INSERT. Idempotent; re-apply freely without resetting Pause.
- `journal.py` - the whole write surface (`append`) plus the reader
  (`events`, newest first). No update, no delete. `SELECTOR_JOURNAL_DSN`
  overrides the DSN (tests do); default is `dbname=selector`, peer auth over
  the unix socket, zero credentials.
- `testdb.py` - the shared throwaway-test-database harness: create a random
  database, apply `schema.sql`, drop it afterwards. Used by this suite and by
  `web/tests/` (the window's side). Needs a Postgres role matching
  the OS user with CREATEDB. It also keeps the books on its own residue, for
  the reason in **Throwaway databases that outlive their run** below.
- `tests/` - suites at their boundaries. `test_events.py` drives the
  vocabulary directly - every constructor's key set, every reader's
  normalization of every era of row, the naming rules - and holds the sweep
  that fails when a kind literal ships outside `events.py`.
  `test_journal.py` is the
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
  `test_notices.py` and `test_notifier.py` are #280's two levels: which rows
  are worth an email and what they say, driven directly with no database and
  a fixed clock, and then the delivery around that - a first start that mails
  nothing, a row delivered exactly once across a restart, a refused delivery
  that loses nothing - driven as the real process against a real Journal with
  the mail surface scripted.
  `test_guardrail.py` and `test_protection_source.py` are #165's two levels:
  what the cycle journals about the write protection, against a scripted
  guardrail command, and what that command itself reports - `gh` faked on
  PATH and a throwaway repository with a real `origin/master` in it, so
  neither the network nor the state of the checkout the suite runs in can
  change the answer.
  `test_testdb.py` is #178 at the harness's own boundary: what the throwaway
  database harness leaked, and the sweep for what a killed run left - both
  against real databases on the real instance, including a second process
  driven through `throwaway_db()` to stand in for a concurrent run.

The window is `web`'s `/loop` page, which imports `journal.py` from
here and renders at request time.

### Throwaway databases that outlive their run (#178)

`throwaway_db()` drops what it creates, and the drop is robust: `WITH (FORCE)`
terminates whatever is still attached, retried past the two errors FORCE
itself can raise - a backend on its way out (`ObjectInUse`) and an autovacuum
worker owned by another role, which this connection is not allowed to signal
(`InsufficientPrivilege`).

What no `finally:` can cover is a run that does not reach it: pytest killed,
a `SIGKILL`, the box rebooted. That leaves a `selector_test_*` database with
nobody left to drop it, and for a while nothing said so - 23 of them, 173 MB,
found by an operator running a count while scoping something else. So two
things now report:

- **At the end of a run**, both suites print the databases *that run* created
  and failed to drop, by name. It is a report and not a failure; the drop
  raising is what fails a run.
- **A sweep** clears the residue of runs that are no longer around to ask:

      .venv/bin/python testdb.py --sweep            # and --sweep --dry-run

  It drops only `selector_test_*` databases with **no session attached**, and
  it drops without FORCE - FORCE is right in `_drop`, where the only backends
  on the database are the test's own, and wrong here, where they are somebody
  else's. One that attaches between the listing and the drop raises
  `ObjectInUse` and is skipped rather than terminated. The live `selector`
  Journal and #175's `selector_staging` are outside the prefix and are never
  candidates.

  "No session attached" is only a safe rule because `throwaway_db()` holds one
  connection open for the life of the database. It did not always: the
  connection that applies `schema.sql` used to close before the DSN was handed
  over, so a live throwaway database routinely had **nothing** attached to it
  between one statement and the next - a test whose subject connects from a
  subprocess may have nothing attached for most of its life - and the sweep
  would have taken a concurrent run's database out from under it. That is what
  the keeper connection is for, and `sweep-takes-a-live-run` in
  `tests/selector-mutations.py` is what keeps it.

The counterpart trap, and the reason the end-of-run report tracks names rather
than counting rows: a bare `SELECT count(*) ... WHERE datname LIKE
'selector\_test\_%'` at the end of a run reports a *concurrent* suite's live
database as this run's leak. The tally on #178 read 25 when the leak was 23,
for exactly that reason.

### The push (#159)

The page does not need reloading. `journal.listen` holds a LISTEN on
`journal_events` - the channel `schema.sql`'s insert trigger already NOTIFYs -
and `/loop/events` frames what it yields as Server-Sent Events. The event
carries a signal, not content: the browser dispatches a `journal` event on the
body, and HTMX re-fetches `/loop/live`, the whole live region, rendered by the
same handler that rendered it into the page. One region rather than a panel
per event kind, because one row moves several panels at once (a `run.outcome`
changes the Run card, the strip's state cell, the budget count and the event
table), and the trigger carries a `delay:` so a cycle writing a dozen rows in
a second costs one re-render rather than a dozen tracker reads.

Three things about it are easy to get wrong and are pinned by
`web/tests/test_liveness.py`:

- **`ready` before anything else.** The LISTEN is established inside the
  handler, after the response headers are out. A client that acted the moment
  it was connected could write a row into the gap and wait forever. The
  `ready` frame is written after the LISTEN, so it is the promise that the
  next row will be seen - and it is what the suite waits for before inserting.
- **Never query the connection from inside `notifies()`.** That generator
  holds the connection's lock while it is being iterated, so the re-read of
  the row it names deadlocks if it is issued from the loop body. `listen`
  takes one notification (`stop_after=1`), leaves the generator, then reads
  every row since the last one sent - which also means a burst of ten arrives
  in one read.
- **`Last-Event-ID` is honoured.** Rows written while a connection was down
  fired their NOTIFY at nobody. EventSource reconnects on its own and sends
  the header; the endpoint replays by id. Without it a page could sit stale
  until the next row happened to land, and after the last row of a Run none
  ever does.

Those tests run against a real uvicorn on a loopback port rather than the
`TestClient` the rest of that suite uses: its transport runs the app to
completion and buffers the body, so a stream that never ends never returns.

**The proxy.** Caddy fronts this app with `encode gzip` + `reverse_proxy`, and
a proxy that buffers would hold every event until the page closed - a failure
indistinguishable from a Selector that never runs. Caddy flushes a
`text/event-stream` without being asked; measured through that exact directive
pair with the client requesting gzip, the events arrived the moment their rows
did. The `X-Accel-Buffering: no` the endpoint sends is for an nginx-family
proxy, which does not, and is inert here.

**What a live page costs.** The live region contains the queue board, and the
board is a tracker read - so a Journal row now costs one `gh` call per label,
where before it cost one per page load. The `delay:` collapses a cycle's burst
into a single re-render; the drip it cannot collapse is the watcher's, roughly
one Iteration a minute while a Run is in flight. That is the price of #159's
"the board counts update the moment a Journal row lands", and it is bounded
rather than unbounded: each label's read is capped by
`SELECTOR_BOARD_TIMEOUT_SECONDS` and a failed one renders as a column error,
so a slow or rate-limited tracker degrades the board rather than the stream.

### Run history (#160)

`/loop/history` is the page `/loop` cannot be. `/loop`'s live region contains
the queue board, and the board is a tracker read - so `/loop` is only as
available as GitHub. The history reads the Journal and nothing else, which is
also why a Run stays on it after the branch it worked on has been merged and
deleted: the forge's copy of that work is gone, and the Journal's - the issue,
the title, the bound the Run ended on, the Proposal URL - is not. The Proposal
link is the durable one and is rendered as a link; the branch is named but
never linked, because a link to a deleted branch is a 404 dressed up as a
working one.

One thing it computes rather than replays:

- **Duration**, the gap between the `run.dispatched` row and the `run.outcome`
  row. Nothing reports it - the box persists no record of a finished Run at
  all - so those two timestamps are the only account of how long the operator
  waited. Two units at most (`1h 40m`, `42s`).

It shows ended Runs only. A Run still going has no bound, no duration and no
Proposal, and `/loop` already shows it live on the panel built for it.

**It counts in Runs, not in rows.** `/loop` reads the newest 200 Journal rows,
which is the right window for "recent" and the wrong one for "past activity
remains inspectable": a Run's rows are interleaved with every cycle summary
and skip written since, so a row cap is a Run cap of no fixed size and a busy
fortnight would silently push the oldest Runs off the page. `journal.run_window`
finds the `run.dispatched` row of the 50th-newest Run and `journal.events`
reads from that floor - every row of every Run above it, and no cap. A
dispatch is the right floor because every other row of a Run is written after
it. What falls below the window is counted and said on the page, not dropped:
`psql -d selector` still has all of it.

The two pages share `_runs.html` (the Run card) and `terminal_base.html`
(the shell and the stream script), and differ in the `runs` list they pass in
and the region each re-fetches -
`/loop/history/live` rather than `/loop/live`, because a history page pointed
at the other one would swap in a queue board it never rendered and reach the
tracker to build it. `web/tests/test_run_history.py` is the suite;
its first assertion is that no tracker command was asked anything.

**Seeing it move in a preview.** An Attended Preview reads `selector_staging`,
and nothing writes to that database on its own - the page will connect, say
`live`, and then sit still, which is correct and looks like a bug. To watch it
move, append to it: `./preview-cycle.sh` runs a whole cycle against the faked
edges, or a single `psql -d selector_staging -c "INSERT INTO journal.events
(kind, payload) VALUES ('run.iteration', '{\"issue\":9001,\"iteration\":1}')"`
lands one row.

## Host setup

Three roles in `site.yml`, in this order: `selector_journal` (the Postgres
state below), `selector_cycle` (the venv the timer's unit execs, correctly
labelled), and `timers` (which installs and enables the timer itself, plus
`selector-notifier.service`).

The notifier is enabled unconditionally, unlike the cycle timer beside it,
which is gated on `selector_dispatch_enabled`. It only reads the Journal and
sends mail: with dispatch off there are no Runs to report and it sits idle,
and with dispatch on it is the only thing that says a Run failed. Gating it
would mean the switch that turns unattended work on also has to remember to
turn the alarm on. It runs from this working tree, so a code change lands with
`sudo systemctl restart tracewake-selector-notifier`.

`deploy/ansible/roles/selector_journal` owns the Selector state: PostgreSQL 16,
socket-only (`listen_addresses = ''`), a peer-auth `conductor` role with
CREATEDB, the `selector` database, schema applied. A rebuilt VM gets the
Journal and an active-by-default pause flag back from the playbook.

## Running the tests

```bash
cd selector
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

Tests skip (not fail) when the local Postgres is unreachable.

Then the mutation check, which is the suite's own grade:

```bash
tests/mutation-check.sh          # one suite run per mutation
```

**Budget hours, not minutes.** This said "~6 minutes" when there were a dozen
mutations and the suites ran in seconds. There are now 119 entries in
`tests/selector-mutations.py`, and the suite each
one re-runs takes one to two minutes, so a whole run is measured in hours -
long enough that a build usually runs the entries it added and their
neighbours by hand, and the whole set is a thing to start and walk away from.
Doing that by hand means copying the loop out of this script, which is a gap
worth closing (a `--only <name>...` argument) and one nobody has closed yet.

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

`tracewake-web.service` runs from the instance's checkout on its default
branch, so without a preview the only way to see a branch's `/loop` is to merge
it. ADR 0016 rules
on the alternative and names it an **Attended Preview**: a second instance at
the instance's staging URL, serving one unmerged branch, permissible on
this VM because somebody is looking at it.

```bash
web/lab-preview.sh --help
web/lab-preview.sh feat/queue-board
```

One preview at a time, held by a lease the banner renders (branch, short SHA,
who started it, uptime). The script refuses a live lease unless you pass
`--force`, and forcing means the operator holding it is now looking at your
branch without knowing. The unit carries `RuntimeMaxSec=4h` and is deliberately
**not** enabled, so a preview exists only while somebody has started one.

### Running a cycle inside a preview

```bash
selector/preview-cycle.sh --dry-run
```

**Use the wrapper, not `python cycle.py`.** The containment ADR 0016 builds is
on the `labstage` account that runs the web process: no `CONNECT` on the live
`selector` database, no SSH key, no vault environment. It does not extend to
your shell. You are `conductor`, which holds the token, the SSH key and write
on the live Journal, so a bare `cycle.py` in the preview tree reads the
instance's real queue and dispatches a real Run against it.

`preview-cycle.sh` points every outward reach at `preview-sources/` - a canned
queue of three issues covering eligible, blocked and missing-section; a box
that starts nothing and reports a plausible ended Run; a box status read that
answers fixture facts rather than opening an SSH session (#156); Seeding that
fetches nothing; issue bookkeeping that writes nothing - and the Journal at
`selector_staging`. It refuses outright if `SELECTOR_JOURNAL_DSN` resolves to
the live Journal, because a cycle appends and those appends would be permanent.

**"Every outward reach" includes two that are not commands**, and they are the
easy ones to miss. `TRACEWAKE_TARGETS_FILE` names the instance's real targets,
and a target's `work_repo` is a checkout `dispatch.py`'s `push()` runs
`git push --set-upstream origin <branch>` against *before* the box is ever
reached - so faking the tracker and the box while pointing at the instance's
own stanzas still puts a branch on the real repository.
`SELECTOR_SEED_COMMAND` defaults to the Loop's real `seed-run.sh`. The wrapper
writes its own targets file and redirects the seed command, at a throwaway
work repo whose `origin` is a local bare repo under `/var/lib/lab-preview/work`,
and
`tests/test_preview_cycle.py::test_no_outward_reach_is_left_on_its_default`
fails if a new one is ever added and left alone.

### The staging Journal

`selector_staging` is built from `schema.sql` plus `seed.sql`, and is
disposable. Note before you read the strip against it: the budget cell says
`8 of 4`, because the fixture packs every outcome state into the last hours
and each needs its own dispatch. Unreachable in life - the cap refuses the
fifth - and explained where the fixture packs them. Every seeded row wears the
shape today's writer writes: `web/tests/test_staging_seed.py` grades
each payload's key set against `events.py`'s constructors, and the seeded
kinds must cover the whole vocabulary, so the fixture cannot drift from the
writer it impersonates.



```bash
dropdb selector_staging && createdb -O conductor selector_staging
psql -d selector_staging -f schema.sql -f seed.sql
```

`seed.sql` is not decoration. The live Journal has never held a
`run.dispatched` or a `run.outcome` row, so a preview reading real data shows
a page with no Run cards and proves nothing about a branch that changed how
Run cards look. The fixture covers every state `/loop` and `/loop/history`
render, and `web/tests/test_staging_seed.py` fails when it stops
covering one - **adding a Journal-backed state to either page means adding it
to `seed.sql` in the same change.** Pause is the exception because it is
control state and can be exercised directly through the button.

The preview runtime may read the staging Journal and may update exactly the
staging pause column. It still cannot append Journal rows or connect to the
live `selector` database, so clicking Pause in a preview exercises the real
UI without changing the live Selector.
