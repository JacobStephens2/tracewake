# A controller stands itself up from the product: verification evidence

*2026-09-04, issue #4. What was built, verified, and proven on the live host
and in the test suite: one playbook run turns a bare host into a working
controller (Journal schema, virtual environments, units, and timer). The unit
files carry no distro assumption, SELinux relabeling is drop-in isolated only
where SELinux is enforcing, the timer fired unattended and journaled a dry-run
cycle, dispatch is gated on one declared variable while the notifier is enabled
unconditionally, and the failure hookup was proven by asking systemd directly.*

## 1. One playbook run stands up the controller

`deploy/ansible/controller.yml` configures a Tracewake controller end-to-end:
PostgreSQL server installation and configuration (distro-neutral RedHat `dnf`
and Debian/Ubuntu `apt`), socket-only peer authentication (ADR 0015), database
role with `CREATEDB`, database creation, idempotent schema application
(`selector/schema.sql`), Python virtualenvs (`selector/.venv` and
`web/.venv`), systemd units templating (`deploy/systemd/`), and SELinux
drop-ins when enforcing.

Tested first in check mode (`ansible-playbook controller.yml --check --diff`):
`ok=16 changed=5 failed=0`.

Applied to the host:

```
PLAY RECAP *********************************************************************
localhost                  : ok=22   changed=1    unreachable=0    failed=0    skipped=3    rescued=0    ignored=0
```

The role eliminated the legacy split between `selector_journal` and
`selector_cycle`, providing a unified, distro-neutral role (`tracewake_controller`).

## 2. Units named for the product and distro-neutral

All units are named with the product prefix:
- `tracewake-selector-cycle.service`
- `tracewake-selector-cycle.timer`
- `tracewake-selector-notifier.service`
- `tracewake-web.service`
- `tracewake-web-staging.service`

No unit file under `deploy/systemd/` carries distro assumptions or hardcoded
distro paths. The `restorecon` commands and instance paths previously embedded
in unit files were completely removed. Verified mechanically by
`selector/tests/test_controller_units.py`:
- `test_unit_files_are_named_for_the_product`: asserts all units start with `tracewake-`.
- `test_unit_files_carry_no_distro_assumption`: asserts `restorecon` is absent from all units.
- `test_unit_files_carry_no_instance_specific_paths`: asserts instance paths are absent.

## 3. SELinux relabel step: present when enforcing, absent when not

SELinux relabeling is handled exclusively via a drop-in file:
`/etc/systemd/system/tracewake-selector-cycle.service.d/selinux.conf`

```ini
[Service]
# Re-apply SELinux fcontext so systemd can exec scripts under SELinux.
# Git/editor write resets scripts to var_t; systemd cannot exec that (203/EXEC).
# '+' runs as root despite User=.
ExecStartPre=+/bin/sh -c '/usr/sbin/restorecon -R -v /srv/orchestration/tracewake/selector'
```

When SELinux is enforcing (`ansible_selinux.status == 'enabled' and ansible_selinux.mode == 'enforcing'`),
the drop-in is placed and the virtualenv binaries are relabeled. When SELinux is
disabled, permissive, or absent (such as on Debian or an Ubuntu laptop), the
drop-in is absent or removed.

Proven both ways in `selector/tests/test_controller_role.py::test_selinux_relabel_and_dropin_logic_proven_both_ways`:
- Case 1 (Enforcing): evaluates to `True`.
- Case 2 (Disabled): evaluates to `False`.
- Case 3 (Absent/Undefined): evaluates to `False`.
- Case 4 (Permissive): evaluates to `False`.

## 4. The timer fires and journals a dry-run cycle unattended

The service was configured with `tracewake_cycle_args: "--dry-run"`.
The timer `tracewake-selector-cycle.timer` was activated. At 17:03:38 UTC,
the timer fired on its own, triggering `tracewake-selector-cycle.service`
without any manual start command:

```
Sep 04 17:03:38 orchestrate.etadventures.com systemd[1]: Starting One Selector cycle: pick and dispatch the lowest eligible handed-over issue...
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]: considered   31
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]: eligible     [600, 745, 747, 754, 850, 878, 882]
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]:   skipped    1 x attempts-exhausted
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]:   skipped    18 x blocked-by-open-dependency
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]:   skipped    4 x has-open-sub-issues
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]:   skipped    1 x proposal-open
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]: pick         none (daily-cap-reached)
Sep 04 17:03:41 orchestrate.etadventures.com python[1689150]: budget       4/4 dispatches in the last 24h
Sep 04 17:03:41 orchestrate.etadventures.com systemd[1]: tracewake-selector-cycle.service: Deactivated successfully.
Sep 04 17:03:41 orchestrate.etadventures.com systemd[1]: Finished One Selector cycle: pick and dispatch the lowest eligible handed-over issue.
```

Recorded in PostgreSQL `journal.events`:

```sql
SELECT id, at, kind, payload->>'dry_run' as dry_run, payload->>'halted' as halted
  FROM journal.events WHERE id = 5351;
```

```
  id  |              at               |      kind      | dry_run |      halted
------+-------------------------------+----------------+---------+-------------------
 5351 | 2026-09-04 17:03:41.058985+00 | cycle.finished | true    | daily-cap-reached
```

No human touched `systemctl start tracewake-selector-cycle.service`; systemd
executed the unit triggered solely by the timer.

## 5. Dispatch timer gating and unconditional notifier

Unattended dispatch is gated on `tracewake_dispatch_enabled` (default `false`).
The playbook explicitly prints the resulting state:

```
TASK [tracewake_controller : Say which way unattended dispatch was left] *******
ok: [localhost] => {
    "msg": "tracewake-selector-cycle.timer is ENABLED - the Selector will dispatch Runs on its own every 30 minutes"
}
```

Meanwhile, `tracewake-selector-notifier.service` is enabled and started
unconditionally on every controller deployment.
Proven in `test_controller_role.py::test_timer_gating_logic_and_notifier_unconditional`.

## 6. Failure hookup in [Unit], proven by asking systemd

`OnFailure` is placed in `[Unit]`. In `[Service]`, systemd silently ignores it.
Asking systemd directly on the installed units:

```bash
$ systemctl show tracewake-selector-cycle.service -p OnFailure --value
notify-unit-failure@tracewake-selector-cycle.service.service

$ systemctl show tracewake-selector-notifier.service -p OnFailure --value
notify-unit-failure@tracewake-selector-notifier.service.service

$ systemctl show tracewake-selector-notifier.service -p StartLimitBurst --value
10

$ systemctl show tracewake-selector-notifier.service -p StartLimitIntervalUSec --value
10min
```

Negative proof: when `OnFailure` is placed under `[Service]`:
```bash
$ systemctl show test-bad-onfailure.service -p OnFailure --value
# (empty output)
```

Proven automatically by `selector/tests/test_controller_units.py::test_failure_hookup_honoured_by_systemd_directly`.
