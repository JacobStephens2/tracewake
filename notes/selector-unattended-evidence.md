# Unattended operation: what was verified on the real box

*2026-08-27, issue #156. The offline suites carry the behaviour
(`selector/tests/test_unattended.py`, plus the strip's twelve HTTP-level tests
in `lab/webapp/tests/test_loop_page.py`). What is written down here is what
only this VM and the real Loop box could say: that the unit systemd holds is
the unit that was written, that the alert wiring registered, that the exec
path works end to end under systemd, and what the box actually answers.*

## The failure hookup, verified rather than read

`OnFailure=` is a `[Unit]` directive. In `[Service]` systemd ignores it and
logs one line nobody reads, so a unit whose alert is dead looks exactly like a
unit whose alert is fine. This box has had that failure before - and it turned
out it still had it, in the copy that ships:

```
$ awk '/^\[/{s=$0} /^OnFailure=/{print FILENAME" "s}' scripts/*.service
scripts/gsc-etl.service [Unit]
scripts/nonprod-migration-drift.service [Service]     <- the repo's copy
scripts/parent-engagement-backup-pull.service [Unit]
scripts/requirements-record-commit.service [Unit]
scripts/requirements-site-regenerate.service [Unit]
```

The *installed* copy of `nonprod-migration-drift.service` was fixed by hand on
2026-08-17; the repository's was not. Since `ansible/roles/timers` installs
`scripts/*.service` wholesale, the next apply would have reinstated the dead
version. Fixed here, in the same ticket that adds a sixth unit to that list,
because the sweep that found it is the one this ticket had to run anyway.

Both units, installed and reloaded:

```
$ systemctl show selector-cycle.service -p OnFailure --value
notify-unit-failure@selector-cycle.service.service
$ systemctl show nonprod-migration-drift.service -p OnFailure --value
notify-unit-failure@nonprod-migration-drift.service.service
```

Empty output is what a dead hookup looks like. Neither is empty.

## The exec path, under systemd, as conductor

The 203/EXEC trap on this box is SELinux: the fcontext rule that labels this
repository's scripts `bin_t` matches `scripts/*.sh` only, so an interpreter
under `lab/` is `var_t` and systemd refuses to exec it with no traceback. The
Selector's venv was in exactly that state:

```
$ ls -Z lab/single-user-factory/selector/.venv/bin/python
unconfined_u:object_r:var_t:s0 ...
```

`ansible/roles/selinux_labels` now declares the rule and
`ansible/roles/selector_cycle` applies it after building the venv. With it
applied, the unit's exact `ExecStart` run under systemd as `conductor`:

```
$ sudo systemd-run --uid=conductor --gid=conductor --wait --pipe \
    --property=WorkingDirectory=/srv/orchestration/lab/single-user-factory/selector \
    /srv/orchestration/scripts/with-orchestration-env.sh \
    .../selector/.venv/bin/python .../selector/cycle.py --dry-run
considered   27
eligible     none
  skipped    14 x blocked-by-open-dependency
  skipped    13 x missing-section
pick         none (none-eligible)
budget       0/4 dispatches in the last 24h
Finished with result: success
Service runtime: 3.331s
```

That is the whole chain the timer will use: the op-inject wrapper resolving
`GH_TOKEN` into an otherwise empty environment, the labelled interpreter, the
real tracker. Three seconds, and the queue state it found was the one the
first live dry-run recorded: 14 issues blocked by open dependencies, and 13
otherwise-ready ones missing the `Owning area` section the label promised at
the time.

**That reading is what dropped the requirement.** 13 of the 13 otherwise-ready
issues on 2026-08-27 lacked the section, as had all 10 of the 10 the first
dry-run found on 2026-08-26 - the count moved because more issues were labeled
in between, not because any of them gained the section. Thirteen of thirteen
is not a queue with a few underspecified issues in it; it is a contract nobody
was writing to, whose only effect would have been to hand the whole queue back
on the first cycle. `Owning area` became optional the same day (see the Selector
README's "The issue contract", and the amendment note in ADR 0014), and an
issue without it is scoped to its own title. The re-measured dry-run after the
change is below.

**The timer is installed and deliberately left disabled.** Enabling it is what
turns unattended dispatch on, and with the section no longer required the
first live cycle would seed and dispatch a real Run rather than hand issues
back - which is the whole point, and is the operator's call to make rather
than a step in a ticket. `sudo systemctl enable --now selector-cycle.timer`,
or `ansible-playbook site.yml`, does it.

## The queue, re-measured after the amendment

Same command, same day, `Owning area` no longer required:

```
considered   27
eligible     [471, 645, 647, 648]
  skipped    14 x blocked-by-open-dependency
  skipped    3 x has-open-sub-issues
  skipped    6 x missing-section
pick         #471 (dry run - not dispatched)
budget       0/4 dispatches in the last 24h
```

Nothing eligible became four eligible, and the loud skip fell from thirteen to
six - which is the number the first dry-run already recorded as also lacking
`Acceptance criteria`. So the six that are still handed back are the ones that
really are underspecified: the requirement that survived is the one that was
doing work.

The first cycle after the timer is enabled would seed and dispatch **#471**.

## What the box answers

`box-sources/facts.sh` against the real box, first run:

```
$ ./box-sources/facts.sh
LOOP_BOX_SCRIPTS_HASH=4b703cac941d
LOOP_BOX_AGENT=claude
LOOP_BOX_AGENT_VERSION=2.1.221 (Claude Code)

real    0m0.467s
```

Half a second, three facts - and one absence that is the card working rather
than failing. `LOOP_BOX_GUEST_TEMPLATE` is missing because the box's copy of
`agents/claude.sh` predates the `--guest-template` flag this ticket adds, and
the card renders `not reported` rather than filling it in from the
controller's copy.

That absence is the same drift the hash exists to expose, and it is real
rather than hypothetical:

```
$ ssh root@loop.etadventures.com "su - loop -c 'ls /home/loop/loop'"
README.md  agents  assert-credentials.sh  check-inventory.sh  contract.sh
pr-sources  propose.sh  run.sh  seed-run.sh  task-sources  tests
```

`notify-sources/` is in this repository and not on the box. Nothing said so
before this card, because the box's Loop is placed by an ansible apply rather
than by a merge, and nothing compares the two. An operator who reads
`4b703cac941d` on `/loop` and the same hash after an apply knows the box is
holding what was reviewed; a hash that moves without an apply is the finding.

## The lock, and why the test drives a real second cycle

Two cycles overlapping is the ordinary case here, not a race to reason about:
the timer fires every thirty minutes and a dispatch holds the process for the
length of the Run. The only moment two cycles can actually meet is *while* a
Run is in flight, so that is where the test puts the second one - the fake box
runs a real `cycle.py` from inside the dispatch it is pretending to be, and
the suite asserts the second one stood down, journaled `cycle-in-progress`,
and exited 0.

Exit 0 is the part worth stating: with a ninety-minute Run and a
thirty-minute timer, this happens two or three times per Run. A timer whose
`OnFailure` paged for it would page for the Selector working, two or three
times per Run, until the operator stopped reading the alerts.
