# The Loop's Execution Boundary: standing-it-up evidence

`sbx` (Docker Sandboxes) v0.39.0 on `loop.etadventures.com`, 2026-08-24
(issues #78 and #99, spec issue #73).
ADR 0003 makes this the one subsystem Attendedness
re-earns: nobody is watching a Run, so the boundary is the only control left
standing.

This note is evidence, not documentation of how the Loop works. It records what
was installed, what was observed by running commands rather than reading pages,
and - the part that matters most for anything built on top of it - what the
boundary does **not** guarantee.

## What is on the box

| | |
|---|---|
| Package | `docker-sbx` `0.39.0-1~ubuntu.24.04~noble` from `download.docker.com/linux/ubuntu noble stable` |
| Binary | `/usr/bin/sbx`, `v0.39.0 def8cb0523a77e757bdd6ef52b459fe374f3783e` |
| Pin | held - `apt-mark showhold` returns `docker-sbx`, `dpkg -l` shows state `hi` |
| Installed by | `ansible/loop.yml`, role `loop_execution_boundary` |
| Started by | `sandboxd.service`, a systemd **user** unit at `/etc/systemd/user/`, wanted by `default.target` and reached by `loginctl enable-linger loop`. Same role (#99) |
| Runs as | `loop`, the unprivileged account a Run executes as. Not root, no sudo |
| Guest VMM | `/usr/libexec/containerd-shim-nerdbox-v1` (shipped in `docker-sbx`) |

Applied, then applied again:

```
loopbox : ok=14  changed=5  unreachable=0  failed=0
loopbox : ok=14  changed=0  unreachable=0  failed=0
```

Two things #77 deliberately left for this ticket, both now settled by
observation:

- **`loop` needs the `kvm` group.** `/dev/kvm` is `root:kvm 0660`, so the
  account a Run executes as cannot open it otherwise. After the role,
  `id loop` is `groups=1000(loop),993(kvm)` and `sbx diagnose` reports
  `Virtualization — supported · /dev/kvm is accessible`. That is the whole of
  what `loop` gained: it is still not in `sudo`.
- **The package is installed without recommends**, which is measured rather
  than stylistic. `docker-sbx` hard-depends on `gnome-keyring` and therefore on
  GTK 3 and 4. (It does not use it on this box - see the #99 section: headless,
  no keyring daemon, so the session goes to encrypted files instead. The
  dependency is hard regardless.) With
  recommends that is 115 packages - GStreamer, mesa Vulkan drivers, librsvg,
  three icon themes - on a box whose design property is holding as little as
  possible. Without them, 69. The only recommend with a security argument,
  `apparmor`, is already on the stock Ubuntu image, so nothing was given up.

One implementation detail worth knowing on a rebuild: the signing key is
fetched with `curl` where `ansible.builtin.get_url` belongs. The orchestration
VM runs ansible-core 2.14 on Python 3.9, whose HTTP modules pass a `cert_file`
keyword that Python 3.12 removed, and the Loop's box is Python 3.12 - so
`get_url` fails there before it reaches the network. Revisit when ansible-core
on the orchestration VM reaches 2.16.

## The login: a human step, captured as a walkthrough

`sbx` refuses every command that touches a sandbox until it holds a Docker
identity, and the sign-in it documents opens a browser. This box is headless,
so that is one of the two human-only steps spec #73 names.

It is a runnable walkthrough rather than prose:
`lab/single-user-factory/wizards/loop-sbx-login.sh`. Five stages - preflight,
create a read-only Docker personal access token in a browser, pipe it over SSH
into `sbx login --password-stdin`, read back the egress policy, boot a microVM.
Nothing is written to disk on the orchestration VM: not the token, not the
username. The token is revocable on its own from the page that issued it.

`sbx login --username ... --password-stdin` is what makes this possible at all -
it is the non-browser route the CLI supports, so the browser step happens on the
operator's own machine and the box never needs one. `--password-stdin` rather
than an argument for the same reason CLAUDE.md gives for `MYSQL_PWD` over
`mysql -p<pass>`: an argument is visible in `ps`.

After the walkthrough, `sbx diagnose` is 12 passed, 0 failed - including
`Authentication — authenticated` and `Daemon — healthy`.

## A command demonstrably executes inside a microVM

An agent-less sandbox (`sbx create shell`, which is also the configuration the
Grok experiment needs), one host directory mounted:

```
$ sbx create --name boundary-evidence shell /home/loop/evidence
     sandbox    boundary-evidence
     agent      shell
     workspace  /home/loop/evidence (rw)
     image      docker/sandbox-templates:shell-docker
     cpu        2
     memory     1.9 GiB
   ✓ Created sandbox boundary-evidence
```

**The kernel is a different kernel.** This is the assertion; everything else is
corroboration. A container shares the host's kernel and would report the host's
release.

| | host | inside the sandbox |
|---|---|---|
| `uname -r` | `6.8.0-124-generic` | `7.0.12` |
| `/proc/sys/kernel/random/boot_id` | `3fa01d93-e50c-480d-ad9f-0f81d5036e44` | `e7b48a89-68aa-4a51-b74e-0a7668c2d6e4` |
| `ps -e` | the box's full process table | 11 processes |
| user | `loop` (uid 1000) | `agent` (uid 1000) |

**The kernel is running on KVM, and the host proves it.** While a command was
executing in the guest, the shim owned by `loop` held live KVM descriptors:

```
$ ls -l /proc/$(pgrep -f containerd-shim-nerdbox)/fd | grep -i kvm
13 -> anon_inode:kvm-vm
27 -> anon_inode:kvm-vcpu:0
28 -> anon_inode:kvm-vcpu:1
```

`anon_inode:kvm-vm` is a file descriptor the kernel hands back from
`KVM_CREATE_VM`, and the two `kvm-vcpu` descriptors are its virtual CPUs.
Nothing about a namespace-isolated container produces those. The droplet
evidence note proved the kernel *would* create a guest; this is the boundary
actually holding one.

**The host is not visible from inside.** The mounted workspace appears at the
same path and carries its marker file; the paths that matter do not exist:

```
/home/loop/.local/state/sandboxes  absent   (sbx's own state, incl. the session)
/root/.ssh                         absent
/etc/apt/keyrings/docker.asc       absent
```

**Do not use `systemd-detect-virt` as the test.** It answers `kvm` on the host
(a DigitalOcean droplet) and `none` inside the guest, which reads backwards.
The microVM exposes no DMI for it to read. The kernel release and the `kvm-vm`
descriptor are the honest checks.

## Egress: observed, not assumed

The research behind spec #73 could not read Docker's default-posture page - it
is script-rendered. Running the tool answers better than the page would have:

**There is no implicit default.** On a fresh box `sbx policy ls` answers
`global network policy has not been initialized` and names three profiles -
`deny-all`, `balanced`, `allow-all`. One has to be chosen, by
`sbx policy init <profile>` or `sbx daemon start --policy <profile>`. This box
is on **`balanced`**, the vendor's middle setting, chosen so that the posture
written down here is the one an operator gets by reflex rather than one tuned
first.

`balanced` is **193 allowed hosts in six rules**, plus filesystem read and
write `allow **` inside the guest. Full dump:
[`loop-sbx-balanced-policy-2026-08-24.txt`](loop-sbx-balanced-policy-2026-08-24.txt).

| rule | hosts |
|---|---|
| `default-ai-services` | 23 |
| `default-package-managers` | 56 |
| `default-code-and-containers` | 33 |
| `default-cloud-infrastructure` | 35 |
| `default-cert-validation` | 30 |
| `default-os-packages` | 16 |

**Deny-by-default outside that list is real.** From inside the sandbox:

```
api.anthropic.com   404   (reached the real host; 404 is its answer to GET /)
example.com         403   (blocked by the proxy)
ifconfig.me               Blocked by network policy: domain ifconfig.me:443
api.telegram.org    403
pastebin.com        403
```

**And the list is wide enough to exfiltrate through.** 44 of the 193 entries
are wildcards. Docker's security page warns the defaults "include broad
wildcards" and names `*.googleapis.com`; the ones that matter here are the
object stores, and they are not theoretical - each of these answered from
inside the boundary:

```
s3.amazonaws.com             307     (**.amazonaws.com:443)
storage.googleapis.com       400     (**.googleapis.com:443)
gist.githubusercontent.com   301     (**.githubusercontent.com:443)
```

A 307/400/301 is the service answering, not the proxy refusing - compare the
403s above. `**.blob.core.windows.net`, `**.public.blob.vercel-storage.com`,
`**.gitlab.com` and `**.gcr.io` are on the same list and were not probed.

So: **`balanced` is a boundary against a runaway agent, not against a
motivated one.** It stops `rm -rf ~` and it stops an arbitrary callback host.
It does not stop an agent that writes a repository into an S3 bucket or a
public gist. Spec #73's user story 26 - "egress restricted to what the agent
actually needs" - is not satisfied by `balanced` and is not satisfied by this
ticket. The narrowing belongs to #79/#80, which will know which hosts the agent
actually needs; `deny-all` plus explicit `sbx policy allow` is the shape, and
it should become a declared ansible task rather than a remembered command once
that list exists.

## What the boundary guarantees, per agent

This is the section not to skim. The isolation property and the
credential-injection property are **different axes**, and the credential one
does not hold uniformly.

**Isolation - identical for both candidate agents.** The microVM, the separate
kernel, the workspace mount and the egress proxy are properties of the sandbox,
not of what runs inside it. Everything in the two sections above holds for
Claude Code and for Grok Build alike, because the evidence above was gathered
in an agent-less `shell` sandbox - the exact configuration the Grok experiment
uses.

**Agent templates - Claude yes, Grok no.** `sbx create` offers, verbatim:

```
claude  codex  copilot  cursor  docker-agent  droid  gemini  kiro  opencode  shell
```

Grok is absent. The Claude configuration is `sbx create claude <path>`; the
Grok configuration is `sbx create shell <path>` with the agent installed inside
at a pinned version, which is what spec #73 already assumes.

**Credential injection - keyed to the service, not to the agent template, and
the spec is too strong here.** `sbx secret set` lists its services verbatim:

```
anthropic  cursor  droid  github  google  groq  mistral  nebius  openai
openrouter  xai
```

`xai` is on that list. The proxy "uses stored secrets to authenticate API
requests on behalf of the agent. The secret is never exposed directly" - and
that is a property of the proxy plus a stored service secret, not of whether an
agent has a template. Spec #73 says "the Claude Code configuration ... retains
host-proxy credential injection; the Grok configuration does not". On the
evidence that is not established: what is established is that Grok has no
*template*, while xAI *is* a supported *service*.

**So do not inherit either claim.** Whether a Grok CLI running inside a `shell`
sandbox actually picks up proxy-injected xAI credentials end-to-end is
unverified, and #80 must verify it rather than assume it in either direction.
What is certain is the fallback: `sbx create --env` / `--env-file` puts the
value **inside the VM**, where the agent can read it, print it, or send it to
any of the 193 allowed hosts. Any configuration that reaches for `--env` has
given up the credential-isolation property, whichever agent it is running.

Two further observations for #81:

- `sbx secret set --ref` accepts **1Password `op://` references** (and AWS
  Secrets Manager ARNs), resolved on the host with the `op` CLI. That is the
  same reference form as `/etc/vaulted-agent/manifests/orchestrator-all.env.tpl`,
  and it is the obvious way to hold the Loop's model credential without a
  plaintext copy on the box - if a service-account token is ever put there,
  which is its own decision.
- `sbx secret import` imports secrets **detected in host environment
  variables**. That is exactly the shape of the collision spec #73's user story
  32 wants made impossible - a stray `ANTHROPIC_API_KEY` becoming the thing
  that bills. Whatever #81 does about that assertion should account for this
  path as well as the agent's own.

## The fourth credential

Spec #73 says the box holds **exactly three** credentials: the agent's model
credential, a repo-scoped GitHub token, and a dedicated signing key. It now
holds a fourth, and this is a stated deviation rather than an oversight.

`sbx` will not create a sandbox without a Docker identity, so the boundary
itself requires one. What is on the box is a **Docker personal access token,
read-only scope**, stored under `~loop` in
`.config/com.docker.sandboxes/com.docker.sandboxes-auth/` - encrypted files,
not the keyring, because nothing on this headless box runs one. What it can
reach:
Docker Hub, as a reader. What it cannot do: push an image, touch any ETA
system, or authenticate to anything else. It is revocable on its own, from
`app.docker.com`, without touching the other three, and revoking it stops the
boundary rather than degrading it - which is the right failure direction.

It is not in either shared secret manifest, consistent with how spec #73 treats
the GitHub token: it belongs to the box.

#81 should either fold this into the credential inventory as a fourth line or
record why it does not count. It should not be quietly left at "exactly three".

## What this does not establish

- **That a Run is safe to leave alone.** The boundary is one bound of five;
  the Termination Contract is the other four and does not exist yet (#79).
- **That the egress posture is right.** It is `balanced`, observed and written
  down. It is demonstrably wide enough to exfiltrate a repository through.
- ~~**That `sandboxd` survives a reboot.**~~ Settled on #99, below: the daemon
  does not survive, the boundary does, and it is now supervised by a user unit
  either way.
- **Anything about the agents.** No agent is installed on this box, and no
  model credential is on it. `sbx create claude` would work; nothing has asked
  it to.
- **That skills do not leak between sandboxes.** Docker's own security page
  states a host-side store is mounted read-write at the agent's skills
  directory unless opted out, so one sandbox can modify what another later
  reads. Not observed here - no agent sandbox was created - and it matters the
  moment #79 creates one per Iteration.

## What a reboot actually does (issue #99)

Four reboots of `loop.etadventures.com` on 2026-08-24. The first with no unit
installed, to see what a reboot really costs; the rest after each change to how
`loop_execution_boundary` starts the daemon, because the criterion #99 set was
that the proof be a reboot rather than a green `systemctl`. Two of the findings
below exist only because the later reboots were actually run.

### The premise was wrong: the boundary comes back on its own

#99 was written expecting a reboot to leave the boundary down until a human ran
`sbx daemon start`. It does not. On the freshly rebooted box, with no unit and
no lingering (`ls /var/lib/systemd/linger/` empty), the daemon was indeed gone -

```
$ pgrep -a -f sandboxd            # nothing
$ sbx daemon status
Status: stopped
Socket: /home/loop/.local/state/sandboxes/sandboxes/sandboxd/sandboxd.sock (not connected)
```

- and the very next `sbx` command started it, unprompted:

```
$ sbx policy ls
Starting sandboxd daemon...
POLICY         SOURCE   APPLIES TO   SUMMARY
local-policy   local    all          network: 193 allow; filesystem read: 1 allow; ...
```

The daemon it starts is a detached `/usr/bin/sbx daemon start` reparented to
init, and it outlives the shell that triggered it. So an unattended Run on a
just-rebooted box would have started its own boundary and never noticed. That
is the opposite of the failure the ticket assumed, and it is recorded here
rather than quietly fixed, because the wrong version is the one that would have
been inherited.

### Both things that could have come up missing survived

The two questions #99 asked, answered by running the box rather than reasoning
about it. **Both survive a reboot.** Neither needs a re-run of
`wizards/loop-sbx-login.sh`, and the walkthrough's re-run stage stays a
*rebuild* step only.

| | survives a reboot | survives a rebuild | where it lives |
|---|---|---|---|
| Docker sign-in | **yes** | no | `~loop/.config/com.docker.sandboxes/com.docker.sandboxes-auth/`, encrypted files, not the keyring |
| Egress profile (`balanced`) | **yes** | no | `~loop/.cache/sandboxes/sandboxes/policykit/governor.db`, sqlite |

`sbx diagnose` after the first reboot, before anything was configured:
`12 passed`, including `Authentication - authenticated` and `Daemon - healthy`.

The sign-in one is worth a second look, because the role's own comments say
`docker-sbx` keeps the Docker session in `gnome-keyring`. On this headless box
it does not: there is no keyring daemon to hold it, and `sbx` falls back to
encrypted files under `~loop/.config`. That fallback is why the sign-in
survives, and it is also the reason the credential is a file on disk rather
than something that dies with a session - which is the right trade for an
unattended box and worth naming rather than assuming.

The profile living under `~/.cache` is the one to watch. `~/.cache` is by
convention the directory anything is free to delete; a cache-cleaning cron or a
disk-pressure sweep that took `governor.db` would leave the boundary running
with no egress policy, and `sbx` refuses to boot a sandbox until one is chosen.
That is a loud failure rather than a silent widening, and the role now
re-chooses the profile if nothing has - but it is Docker's choice of directory,
not ours, and a `deny-all` posture that lives there is a posture one `rm -rf`
from being absent.

### So what the unit is for: supervision, not availability

Given auto-start, a unit does not buy availability. It buys three things
auto-start does not:

- **`Restart=on-failure`.** Auto-start only fires when a command is typed. An
  Iteration is one long `sbx exec`; if `sandboxd` dies during it, nothing brings
  it back until the next invocation, which is after the Run has already failed.
- **A known cgroup and a journal.** The unit's daemon runs under
  `user@1000.service/app.slice/sandboxd.service` with its output in the
  journal, rather than reparented to init from whatever first ran `sbx`.
- **A start moment that is not inside the Run.** Daemon startup and the first
  policy fetch happen at boot instead of inside the Run's first command.

`WantedBy=default.target` plus `loginctl enable-linger loop` is the shape, both
declared in `ansible/roles/loop_execution_boundary`. Two details of the
declaration are not cosmetic and are explained where they live, in the unit's
own header: the enablement symlink is root's rather than `loop`'s, and
`ConditionUser=loop` is what keeps that from starting a daemon under every other
account on the box.

### Verified by rebooting, not by `is-enabled`

The acceptance criterion was explicit about this, and for a good reason on
record: `certbot-renew.timer` on the orchestration VM was `enabled` and never
`active` for months, and an expired certificate is how anyone found out. So the
check is a reboot, and the first command run against the box afterwards. This
transcript is the second reboot; the last one, after `ConditionUser` was added,
looks the same but with `Main PID: 921` and no root daemon beside it:

```
$ ps -ef | grep '[s]bx daemon start'      # before any human command
loop  915  855  5 22:09 ?  00:00:00 /usr/bin/sbx daemon start

$ systemctl --user status sandboxd.service
● sandboxd.service - Docker Sandboxes daemon (sbx) - the Loop's Execution Boundary
     Loaded: loaded (/etc/xdg/systemd/user/sandboxd.service; enabled; preset: enabled)
     Active: active (running) since Mon 2026-08-24 22:09:20 UTC; 8s ago
   Main PID: 915 (sbx)
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/sandboxd.service
             └─915 /usr/bin/sbx daemon start
```

`855` is loop's user manager, started at boot by lingering with nobody logged
in. The `Loaded:` path is not a second install location: Ubuntu ships
`/etc/xdg/systemd/user` as a symlink to `../../systemd/user`, and systemd names
the search-path entry it walked rather than the resolved file. `ls -l
/etc/systemd/user/sandboxd.service` is the same file ansible wrote.

And the boundary is not merely up, it works - a microVM booted and answered with
a kernel that is not this box's:

```
$ sbx exec reboot-probe uname -r
7.0.12
$ uname -r
6.8.0-124-generic
```

`sbx diagnose` after that reboot: `12 passed`, `Authentication - authenticated`.
The play re-applies clean afterwards - `ok=19 changed=0`.

### Four things found on the way, all now fixed

**`ansible_become: false` in the inventory was silently disabling every
`become: true` in the play.** It was set on the `loop` group with the reasonable
comment that the connection is already root so there is nothing to escalate to.
But `ansible_become` is a *connection variable*, and connection variables
outrank task keywords - so the three tasks that escalate DOWNWARD, to prove a
tool runs as the unprivileged account a Run uses, were running as root and
proving nothing. `sbx version` and `bats --version` pass as root, so nothing
ever failed. It surfaced only because `systemctl --user` cannot be faked: the
new task died with `Failed to connect to bus: Operation not permitted`, which
was root asking for loop's bus. The line is gone, and the inventory now says
why it must not come back.

Fixing it changed behaviour beyond this ticket, and the change was checked
rather than assumed: `loop_shell_suite`'s `bats --version` and
`shellcheck --version` now genuinely run as `loop` and still pass, on the same
applies that produced `ok=19 changed=0`.

**A user-scope `systemd` task needs two environment variables, not one.**
`XDG_RUNTIME_DIR` alone is not enough; `systemctl --user` reads
`DBUS_SESSION_BUS_ADDRESS` and does not derive it from the runtime directory.
An ansible task is not a login session and has neither by default.

**`systemctl --user enable` writes its symlink where the Run can delete it, and
the root-owned fix started a second boundary.** Two findings, one after the
other, and the second only appeared because the box was rebooted again after
the first was fixed.

The obvious way to enable a user unit - `enabled: true` on the `systemd` task -
puts the `default.target.wants` symlink in `~loop/.config/systemd/user/`, under
the account a Run executes as. The unit file itself is root's, deliberately, so
that a Run cannot rewrite its own boundary; leaving the enablement in ~loop
would have left it able to *disable* it instead, with the loss showing up only
at the next boot. So the role links
`/etc/systemd/user/default.target.wants/sandboxd.service` as root instead, and
removes the per-user copy if one is there.

But a unit wanted by `default.target` in `/etc/systemd/user` is wanted by
**every** user manager on the box. The next reboot showed two daemons:

```
loop   873  827 15 22:22 ?  00:00:00 /usr/bin/sbx daemon start
root   997  984 71 22:22 ?  00:00:00 /usr/bin/sbx daemon start
```

Root's had built its own state directory under `/root/.local/state/sandboxes`,
which is precisely the "second boundary owner with a different state directory"
the unit's own header gives as the reason it is a user unit. `ConditionUser=loop`
in `[Unit]` is the fix, and it is what makes the root-owned symlink safe:

```
Condition: start condition unmet at Mon 2026-08-24 22:24:48 UTC
           └─ ConditionUser=loop was not met
```

`/root/.local/state/sandboxes`, `/root/.config/sandboxes` and
`/root/.cache/sandboxes` were removed. They held no sandbox and no credential -
lock files, a daemon log and an empty containerd store - but the two oldest
directories were stamped 19:40 on install day, which is the `ansible_become`
bug above leaving its own trace: `sbx version` ran as root, and `sbx` made root
a state directory on the way past.

### Known gaps, with owners

- **A hand-started daemon makes the unit a no-op.** If `sandboxd` is already
  running when the unit starts, `sbx daemon start` prints `Daemon is already
  running` and exits 0; systemd records a clean exit and shows the unit
  `inactive (dead)` while the unsupervised daemon keeps going. Not reachable at
  boot, where nothing runs first. Reachable by hand, and a green `systemctl`
  is not proof the unit owns the process - `Main PID` is. Owner: this role;
  revisit if `sbx` ever grows a foreground flag that fails on a live socket.
- **The egress profile lives in `~/.cache`.** See above. The role re-chooses it
  when it is absent, which covers deletion between applies but not deletion
  between a Run's start and its first sandbox. Owner: the egress ticket, which
  is already changing this posture from `balanced` to a narrow allowlist.
- **Nothing alerts if the unit fails.** `Restart=on-failure` retries forever
  with no notification, and this box has no equivalent of the orchestration
  VM's `notify-unit-failure@.service`. A boundary that is restart-looping is
  indistinguishable from one that is fine until a Run fails. Owner: the
  Termination Contract ticket, which is where the Run's own failure signalling
  belongs.

## Rebuilding

`ansible-playbook loop.yml` installs and pins the boundary, and now also starts
it: the play declares a `sandboxd` user unit and `loginctl enable-linger loop`,
so a rebuilt box brings the daemon up at boot with no command. It also chooses
the egress profile if nothing has. What it still cannot do is sign in, so run
the walkthrough - the Docker sign-in does not survive a rebuild (it does survive
a reboot; see above), and the policy choice the play makes is `balanced`:

```
sudo -u conductor -i
cd /srv/orchestration/lab/single-user-factory/wizards && ./loop-sbx-login.sh
```

Moving the pin is one line in
`ansible/roles/loop_execution_boundary/defaults/main.yml`, and the apt hold does
not have to be lifted by hand - but only because the install task carries
`allow_downgrade` and `allow_change_held_packages`. It did not, at first, and
the play failed twice on the way to learning it:

```
E: Packages were downgraded and -y was used without --allow-downgrades.
E: Held packages were changed and -y was used without --allow-change-held-packages.
```

apt refuses both by default and ansible passes neither. Verified by moving the
pin to 0.38.0 and back:

```
loopbox : ok=14  changed=2   # 0.39.0 -> 0.38.0, sbx version: v0.38.0
loopbox : ok=14  changed=2   # 0.38.0 -> 0.39.0, sbx version: v0.39.0
loopbox : ok=14  changed=0   # and clean again
```

Overriding the hold in that one task is the point rather than a loophole: the
hold exists to stop an unattended `apt upgrade` moving the boundary with no
commit recording it, and that task is the commit. It is re-asserted immediately
afterwards - `apt-mark showhold` still returns `docker-sbx`.
