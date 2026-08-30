# Turning the timer on: what was checked, and what stopped it

*2026-08-30, issue #261. Nothing here is code. The Selector was finished weeks
ago; this is the decision to let it dispatch with nobody watching, and the
checks that make the decision defensible. Three things turned out to be broken
that the ticket did not think to list, and all three could only have been found
by running something on this box or the Loop's - which is the argument for
running the checks rather than reasoning about them.*

## What flipping it means

The risk, in full: `SELECTOR_DAILY_CAP` Runs a day (4), each bounded by the
Termination Contract at ninety minutes, on the operator's subscription, around
the clock, each ending in a draft Proposal on tourbot and a label change on the
issue. The gate is one line - `selector_dispatch_enabled` in
`ansible/roles/timers/defaults/main.yml`, now `true` - and the pause flag on
`/loop` is the only stop that needs no systemd.

## The three blockers, closed

#260 (the credential renews itself), #165 (the executed paths are
write-protected) and #259 (the boundary's own proxy captures the login) are all
closed and merged. That is what made this ticket workable. It is not what made
it *safe*: see the credential below, where the mechanism #260 built is present
and the box it protects is still unable to authenticate.

## Two things found by checking rather than reading

(The third is the box's credential, at the bottom: it is what stopped the
ticket rather than something fixed along the way.)

**The executed tree was not the reviewed tree.** `selector-cycle.service` runs
`/srv/orchestration/lab/single-user-factory/selector/cycle.py` out of the
shared checkout, and the shared checkout was parked on
`issue-248-scripts-selinux-relabel` (0e0f5681) - merged, and well behind
master. Every tracked file under `ansible/`, `scripts/`, `lab/webapp/` and
`selector/` matched that commit exactly, so nothing was locally modified; it
was simply old. Old by exactly the two tickets this one is blocked on:
`guardrail-sources/` did not exist in it and `facts.sh` could not ask the box
about its credential. Enabling the timer against it would have dispatched
pre-#165, pre-#260 code while the ticket recorded both as satisfied. Moved to
master before anything else was measured.

**The pause flag's table had never reached the live database.** The first
cycle run after the checkout moved died before it read anything:

```
$ .venv/bin/python cycle.py --dry-run
cycle.py: journal unavailable: relation "selector.control" does not exist
```

`schema.sql` is idempotent and `ansible/roles/selector_journal` applies it, but
nobody had applied the playbook since #161 added `selector.control` - so the
Journal's own database was a version behind the code that reads it. Every cycle
would have exited 1, and every cycle exiting 1 pages: the alert this ticket
insists on proving would have fired every thirty minutes, around the clock,
starting with the first firing. Applied by hand, exactly as the role does it:

```
$ psql -d selector -v ON_ERROR_STOP=1 -f .../selector/schema.sql
$ psql -d selector -c 'SELECT * FROM selector.control'
 singleton | paused
-----------+--------
 t         | f
```

Both are the same shape of failure and it is worth naming: a deployment where
the reviewed copy and the executed copy are different objects, and nothing
compares them. `hosts/sync.py` does this for other boxes' configuration; the
Selector's own two halves have no such check.

## The dry run, recorded before any cycle could dispatch

Against the live tracker, on the code the timer would actually execute:

```
considered   26
eligible     [471, 647, 648]
  skipped    14 x blocked-by-open-dependency
  skipped    3 x has-open-sub-issues
  skipped    5 x missing-section
  skipped    1 x proposal-open
pick         #471 (dry run - not dispatched)
budget       0/4 dispatches in the last 24h
```

The first cycle allowed to dispatch will seed and dispatch **#471**. The same
numbers came off the stale checkout half an hour earlier, which is worth one
line: the eligible set was not what the stale code got wrong, so re-running it
proved the tree rather than the queue.

## The failure path, proven

`systemctl show` says the directive registered:

```
$ systemctl show selector-cycle.service -p OnFailure --value
notify-unit-failure@selector-cycle.service.service
```

That is what #156 already recorded, and it is not enough: it says systemd
parsed the line, not that anything reaches a human. So the unit was made to
fail on purpose - a drop-in replacing `ExecStart` with `/bin/false`, started,
then removed and the unit confirmed back to its real command:

```
$ sudo systemctl start selector-cycle.service
Job for selector-cycle.service failed because the control process exited with error code.

$ sudo journalctl -u notify-unit-failure@selector-cycle.service.service
22:28:14 Starting Alert the operator that selector-cycle.service failed...
22:28:15 unit-failure notification sent for selector-cycle.service: email, sms
22:28:15 Finished Alert the operator that selector-cycle.service failed.
```

`email, sms` is the notifier naming the channels it actually used, which is
dispatch and not delivery. Delivery was confirmed the only way it can be: the
operator read the mail out - unit, description, `result: exit-code`, `failed
at: Sun 2026-08-30 22:28:14 UTC` - within a couple of minutes of the failure.
The alert reaches a human, which is the claim story 31 makes.

## The pause flag, proven under systemd

Paused through `/loop`'s own control - the page is the flag's only writer - and
then a whole cycle run as the timer runs it, `systemctl start
selector-cycle.service`:

```
$ curl -X POST .../loop/pause      ->  200
$ psql -d selector -tAc 'SELECT paused FROM selector.control'
t
$ sudo systemctl start selector-cycle.service   ->  Result=success

 id  | kind           | cycle | halted
 253 | cycle.finished |   222 | paused
```

Nothing was dispatched and the cycle still exited 0: a paused Selector is not a
failing one. `/loop` reads `paused - dispatch is off`.

Half of the criterion is what was proven. It asks for the flag to stop dispatch
"while the timer stays enabled", and the timer is not enabled yet, so what this
shows is the flag stopping a cycle that systemd started - which is the same
process the timer starts, by the same unit, but started by hand. The other half
costs nothing once the timer is on: a firing lands, journals `halted: paused`,
and dispatches nothing.

**A paused cycle still writes to the tracker, and that surprised this ticket.**
Cycle 222 handed five underspecified issues back before it halted - tourbot
#596, #599, #603, #606 and #626 each got the contract comment and a swap from
`ready-for-agent` to `needs-info`. That is the documented behaviour (the pause
stops *dispatch*, and the loud skip is not a dispatch), and it is right: an
issue that cannot be built from should be handed back whether or not the
operator has the Selector paused. But "paused" reads like "does nothing", and
it is not that. The five hand-backs are correct and were left standing.

Worth being exact about which step wrote them, since the ticket asks for a dry
run *before* anything acts: the dry runs wrote nothing anywhere, and these five
comments and label swaps came from the cycle run afterwards to prove the pause.
Proving the pause needs a real cycle, and a real cycle hands back what it
cannot build from. That is the cost of the proof rather than a dry run that
leaked.

## What is NOT done, and why

**The timer is still `disabled` and `inactive`.** The box's model credential is
dead, and it cannot renew itself:

```
$ box-sources/facts.sh
LOOP_BOX_CREDENTIAL_EXPIRES_AT=1970-01-01T00:00:00Z

$ /home/loop/loop/agents/claude.sh --refresh-credential
claude.sh: the model credential expires 1970-01-01T00:00:00Z and
'claude -p ok --max-turns 1' did not renew it past the 7200s a Run needs -
run wizards/loop-claude-login.sh

$ claude -p ok --max-turns 1
Failed to authenticate: OAuth session expired and could not be refreshed
```

The credential file on the box holds `expiresAt: 0` beside a
`refreshTokenExpiresAt` of 2026-09-27, which is the shape ADR 0017's renewal
was built for and it still cannot use it: the refresh token is inside its
stated life and the vendor refuses it anyway. So #260's machinery is present
and working as designed, and the box is nonetheless sixteen-hours-a-day's worth
of broken - the exact condition this ticket is blocked on, arrived at by a
different route than the one #260 anticipated. It takes a human login
(`wizards/loop-claude-login.sh`) to clear, and until it is cleared, enabling
the timer means four dispatches a day that fail on authentication.

The Selector is therefore left **paused**, which is the state that makes that
safe without hiding it: the timer can be enabled at any time and no Run starts
until somebody resumes.

Owed, in order, and none of them a code change:

1. `wizards/loop-claude-login.sh` - log the box in. `facts.sh` should then
   answer with an instant eight hours out, and `/loop`'s box card stops saying
   `credential has expired`.
2. `sudo systemctl enable --now selector-cycle.timer` (or `ansible-playbook
   site.yml`, now that the gate is `true`). Then `systemctl is-active` - not
   `is-enabled`, which is the check that let `certbot-renew.timer` sit enabled
   and never started on a box that never reboots until a certificate expired.
3. Resume on `/loop`. The next firing dispatches #471.
4. Watch that first unattended cycle land on `/loop` - the cycle card, and the
   `next cycle` cell showing a firing time instead of `timer not running`.

Steps 2 and 3 were refused to the agent session that did the rest of this
work: it was fenced to a worktree, and `systemctl enable` and the playbook run
were both denied to it. Which is the correct outcome for the one step in this
ticket that is a decision rather than a check.
