# The Loop's box: configuration evidence

`loop.etadventures.com` configured by `ansible/loop.yml` on 2026-08-24 (issue
#77, spec issue #73). Companion to `loop-droplet-evidence.md`, which covers the
droplet underneath: that note proves the box can exist and can run a hypervisor;
this one proves the box's contents are automation rather than remembered steps.

## What the play does, and what it deliberately does not

Three roles, run over SSH as `root` from the orchestration VM:

| role | effect |
|---|---|
| `loop_base` | `git 1:2.43.0-1ubuntu7.3`, `curl`, `ca-certificates`, `jq 1.7.1-3ubuntu0.24.04.2` |
| `loop_account` | user `loop`, uid 1000, `/home/loop` at 0750, `/bin/bash`, no sudo, no authorized key |
| `loop_shell_suite` | `bats 1.10.0-1`, `shellcheck 0.9.0-1`, then runs both as `loop` |

Not installed, and each for a stated reason rather than an oversight:

- `sbx` and anything the Execution Boundary turns out to need, including whether
  the `loop` account wants `kvm` group membership. That is issue #78's
  observation to make; guessing it here would put dead configuration on the box
  if #78 finds the boundary wants something else.
- The Loop's three credentials and its signing key (#81). The key is generated
  on the box, not moved onto it, so there is nothing for this play to place.
- The Loop scripts and their Termination Contract (#79).
- `bats-support` / `bats-assert`. Core `bats` carries `run`, `$status` and
  `$output`, which is the whole idiom the suite needs. If #79 finds it wants the
  helper libraries, they are one line in `loop_shell_suite_packages`.

## Applying it twice is clean the second time

Packages purged and the account deleted first, so the first run below is a real
first run rather than a re-run of an already-configured box:

```
$ ssh root@loop.etadventures.com 'apt-get purge -y bats shellcheck parallel; userdel -r loop'
$ ansible-playbook loop.yml
TASK [loop_account : The Loop's unprivileged account] ***
changed: [loopbox]
TASK [loop_shell_suite : Install the shell test harness and linter] ***
changed: [loopbox]
loopbox : ok=7  changed=2  unreachable=0  failed=0  skipped=0

$ ansible-playbook loop.yml
loopbox : ok=7  changed=0  unreachable=0  failed=0  skipped=0
```

`changed=0` on the second apply is the assertion. Note `ok=7` both times: the
two read-only checks - the Ubuntu assertion and running `bats`/`shellcheck` -
report `ok`, not `changed`, so they never inflate a re-apply.

## The orchestration play is unaffected

The new inventory group is a sibling of `orchestration`, and `site.yml` is
`hosts: orchestration`, so the new host cannot enter it. Verified rather than
argued - the same dry run, before and after the change:

```
$ ansible-playbook site.yml --check --diff   # at the branch base
orchestrate : ok=55  changed=5  unreachable=0  failed=0  skipped=20

$ ansible-playbook site.yml --check --diff   # with loop.yml and the new group
orchestrate : ok=55  changed=5  unreachable=0  failed=0  skipped=20

$ diff <before task list> <after task list>   # exit 0
```

Identical task list, identical recap. The `changed=5` is pre-existing drift on
the orchestration VM in both runs, not an effect of this change.

## The suite runs as the account a Run uses

Installed by `root`, but a Run executes as `loop`, so the check runs as `loop`:

```
$ sudo -u loop bats --version
Bats 1.10.0
$ id loop
uid=1000(loop) gid=1000(loop) groups=1000(loop)
$ stat -c '%a %U:%G' /home/loop
750 loop:loop
```

`groups=1000(loop)` and nothing else is the point: no sudo, no `docker`, no
`kvm`. Every privilege the Loop turns out to need has to be added deliberately
by the ticket that establishes it needs one.

## What a dry run does and does not prove

`ansible-playbook loop.yml --check --diff` reports the packages and the account
it would create. It does not prove the tools work: ansible skips `command`
tasks in check mode, so the step that runs `bats` and `shellcheck` does not
execute under `--check`. Proof is an apply. There is no CI here and there will
not be - the repository has no workflows, and #77 says so explicitly.

## Rebuilding

`./tofu.sh apply` from `tofu/hosts`, then `ansible-playbook loop.yml` from
`ansible/`. Both from a `va` session on the orchestration VM; the play needs
`~conductor/.ssh/id_ed25519`, which is the key `loop.tf` attaches at create time.
