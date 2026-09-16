# Dry-run Cycle evidence for #61

Issue [#61](https://github.com/JacobStephens2/tracewake/issues/61), under
[#51](https://github.com/JacobStephens2/tracewake/issues/51), run on the Host
on 2026-09-16 against the Instance configured in #60
(`notes/instance-60-evidence.md` on `main`).

No product source changed: the deployed checkout (`/srv/tracewake` at
`721c71d`) already carries the dry-run path, and a dry-run reaches only the
tracker, the owner-wide search (both read-only), and the Journal.

## Command

As `conductor`, with the installed Instance environment, timers disabled:

```bash
set -a; . /etc/tracewake/tracewake.env; set +a
cd /srv/tracewake/selector
/srv/tracewake/selector/.venv/bin/python cycle.py --dry-run
```

The one human step this needed: `conductor` had no GitHub auth, so the
tracker's `gh api graphql` reads would have failed. The operator ran
`sudo -u conductor -i gh auth login` on the Host (authenticated as
`JacobStephens2`); no token transited any agent session. This matches
INSTALL.md Step 5 and `selector/README.md` (tracker reads authenticate via
conductor's `~/.config/gh/hosts.yml`).

## Result — exit 0

```text
target       JacobStephens2/vaulted-agent
considered   1
eligible     [92]
pick         #92 (dry run - not dispatched)
budget       0/20 awaiting review
target       JacobStephens2/career
considered   7
eligible     [81, 83, 84]
  skipped    3 x blocked-by-open-dependency
  skipped    1 x has-open-sub-issues
pick         #81 (dry run - not dispatched)
budget       0/20 awaiting review
```

The picks agree with the #51 proving order: the first Dispatch will be
vaulted-agent#92 (first Target, lowest Eligible), and career#81 is Eligible
after that.

## Journal — a started/finished pair per Target

`journal.events` (previously empty) after the run:

| id | kind              | cycle |
|----|-------------------|-------|
| 1  | target.unenrolled | —     |
| 2  | cycle.started     | —     |
| 3  | cycle.picked      | 2     |
| 4  | cycle.finished    | 2     |
| 5  | cycle.started     | —     |
| 6–9| issue.skipped     | 5     |
| 10 | cycle.picked      | 5     |
| 11 | cycle.finished    | 5     |

- Cycles 2 (vaulted-agent) and 5 (career) each open with `cycle.started`
  carrying their Target's repo and close with `cycle.finished`.
- Both `cycle.finished` payloads carry `"dry_run": true`, `"dispatches": []`,
  `"picked": 92` / `"picked": 81`, `"returned": []`.
- Row 1 journals the owner-wide Handover search (story 38): nine unenrolled
  repos carry `ready-for-agent`, including `tracewake` itself (#61, #62 —
  expected, Tracewake is deliberately not a Target). Journaled with
  `"dry_run": true`; dry-run gaps never mail.

## No dispatch, seed, or label side effects

- The Journal holds no `run.*`, route, or return rows — only the reasoning
  rows above.
- `JacobStephens2/vaulted-agent#92`: 0 comments, labels unchanged
  (`bug`, `ready-for-agent`), latest timeline event 2026-09-03.
- `JacobStephens2/career#81`: 0 comments, labels unchanged
  (`ready-for-agent`), latest timeline events 2026-09-08.
- No work checkouts were touched (they still do not exist — see below).

## Test verification

- `tests/test_cycle.py` + `tests/test_configuration.py` on the Host as
  `conductor` (throwaway `/tmp` venv, throwaway test databases):
  **64 passed**.
- Full Selector suite on the Host as `conductor` (throwaway `/tmp` venv
  `/tmp/selector-test-venv`, throwaway test databases): **432 passed,
  6 failed, 2 skipped** (two passes, identical). All 6 failures are
  environmental, same class as #60's findings — none touches the dry-run
  path (`test_cycle`, `test_configuration`, `test_dispatch`,
  `test_targets`, `test_journal`, `test_events` all pass):
  - 5 × `FileNotFoundError: 'ansible-playbook'` — the binary is not on
    `conductor`'s `PATH` (`test_box_role` ×2, `test_controller_role` ×2,
    `test_host_play` ×1). Ansible-syntax coverage, not Selector behaviour.
  - 1 × `test_failure_hookup_honoured_by_systemd_directly` — writes a
    transient unit via `sudo tee /run/systemd/system/`, and `conductor`
    has no NOPASSWD rule for it (`sudo: a password is required`).
- No new test seam or typechecked source was introduced; the repo carries no
  configured typecheck gate for `selector/` (no mypy/pyright/ruff config).

## Still open for the proving Run (#62)

Unchanged from the #60 handoff; the dry-run path does not reach any of these:

- Declared work checkouts (`/var/lib/conductor/work/*`) and the career Box
  checkout (`/home/loop/career`) do not exist.
- Default Box facts/progress commands use SSH; `SELECTOR_BOX_HOST=local`
  does not make them local.
- The Cycle runs as `conductor`; Box credentials belong to `loop`.
- `tracewake-selector-notifier.service` is failed; the first live cycle may
  also queue gap mail for the nine unenrolled repos in row 1.
