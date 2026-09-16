# This Tracewake's Instance files

Issue [#60](https://github.com/JacobStephens2/tracewake/issues/60), under
[#51](https://github.com/JacobStephens2/tracewake/issues/51), configured on the
Host on 2026-09-16. Product checkout: `721c71d7ebc62bec21753583acf3f2e56f41795f`.

The configuration lives on the Host in `/etc/tracewake/tracewake.env` and
`/etc/tracewake/targets.toml`. This note records the applied result; it is not
a shipping Instance template. No product defaults or execution scripts changed.

## Applied configuration

- Public origin: `https://tracewake.stephens.page` (`SELECTOR_LOOP_URL`, also
  used for invitation links).
- Search owner: `JacobStephens2`.
- Targets, in file order: `JacobStephens2/vaulted-agent`, then
  `JacobStephens2/career`. Both allow Handover by `JacobStephens2`, use the
  standard lifecycle labels, a review cap of 20, and `landing = "propose"`.
  Tracewake is not a Target.
- Each Target names its own existing token under `/home/loop/.config/loop/`.
  Both select the installed `docker.io/docker/sandbox-templates:shell-docker`
  guest template. The existing vaulted-agent Box checkout is
  `/home/loop/workspace`; career is declared at `/home/loop/career`.
- Box command: `/srv/tracewake/selector/box-sources/local.sh`. Loop directory:
  `/srv/tracewake/loop`. Agent: `grok`, with
  `LOOP_AGENT_COMMAND=/srv/tracewake/loop/agents/grok.sh`.
- Guardrail: `JacobStephens2/tracewake`, `refs/heads/main`, deployed tree
  `/srv/tracewake`, and its `selector/guardrail-sources/paths.txt` declaration.
  That covers `loop`, `selector`, and the Cycle, timer, and notifier units.
- Mail commands retain the Host shims installed for #59:
  `/usr/local/bin/tracewake-notify` and `/usr/local/bin/tracewake-invite`.
  The Journal DSN is `dbname=selector`.

Both files are `conductor:conductor`, mode `0600`, inside the existing `0700`
directory. The SMTP secret and its permissions were unchanged; the Run account
still cannot read it. The prior environment is backed up on the Host at
`/root/tracewake-before-60.MFh7lh/tracewake.env`. No Targets file existed before.

Applied file SHA-256:

```text
836d2dba7fe23d3fc7738402af9abfc57567ba1caf95c581deb7ac31f8ec4f74  tracewake.env
5281a618cd500fec6a25150522f7c028108ab5f6686f3edb8c6f5ba437f16067  targets.toml
```

## Verification

As `conductor`, with the installed environment exported, the product's
`targets.Instance.from_env()`, `targets.load()`, and
`notifier.NotifierConfig.from_env()` successfully read the Instance, the two
ordered Targets, and the public mail-link origin. The Box, Grok, notify, and
invite command paths are executable. Every declared Executed Path exists and
has no tracked diff against the deployed checkout's `origin/main`.

`bash -n` accepted the environment file. The existing configuration and Target
test files passed together: **46 passed**. This is a configuration-only change;
no new test seam or typechecked source was introduced.

After restarting the Dashboard, its process environment contains the installed
origin, search owner, Targets path, and invite command. Both the loopback and
public HTTPS `/healthz` returned HTTP 200. The Cycle timer remains disabled.
No live Cycle or Run was started, and no invitation or test email was sent.

The full Loop suite passed on Ubuntu: **332 passed**. The full Dashboard suite
on macOS reported **224 passed, 12 failed**; the failures were in the Linux
telemetry and preview tests. Re-running those two files on Ubuntu, in a
temporary test virtualenv, passed all **32 tests**.

The full Selector suite on macOS reported **434 passed, 3 failed, 3 skipped**.
The failures were environmental: the SELinux test's bare `python3` lacked
Jinja2, the controller check-mode test expects Linux service facts, and a
database-sweep assertion expects a local `selector` database that this Mac
does not have. The SELinux test passed when rerun with the Dashboard virtualenv
on `PATH` (which supplies Jinja2). No product source or tests changed in this task.

Separate Standards and Spec reviews found no issues within #60's scope.

## Handoff to the dry-run and proving Run

This verifies #60's configuration, not #61's successful dry-run or the later
live Run. The following pre-existing setup gaps still need resolution there:

- The declared work checkouts under `/var/lib/conductor/work/` and the career
  Box checkout do not yet exist.
- The product's default Box facts and progress commands still use SSH;
  `SELECTOR_BOX_HOST=local` does not make those commands local.
- The Cycle service runs as `conductor`, while Box credentials belong to
  `loop`. The local Box command does not switch Unix users. The eventual
  Dispatch wiring must preserve the credential inventory gate and keep the
  mail secret outside the Run account.
- The notifier was already failed before this change and was not restarted;
  activating it can deliver queued mail. This work verified its configuration
  reader, not mail delivery (which was verified in #59).

Guardrail's declaration and deployed tree were checked here. The live GitHub
protection verdict, tracker authentication, and Cycle Journal outcomes were
not exercised.
