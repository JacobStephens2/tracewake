# The Loop's credentials: what the box holds, and the proof it works

`loop.etadventures.com`, 2026-08-25 (issue #81, spec issue #73).
ADR 0005 governs what a Verified commit from this box means; ADR 0009 records
that the inventory is four lines, not three.

**Status: complete.** Both browser steps were run on 2026-08-25 and everything
below was observed rather than expected. The Verified commit is
`e974df8e22ea968a193246e404c5cbd2f9125f09`, and it is checkable independently of
this note - see "One Verified commit".

This note is evidence, not documentation. It records what was generated, what
was registered by hand, what was observed by running commands, and - the part
that outlives the ticket - which of the properties here are asserted by
something that runs and which are asserted by a sentence in a document.

## The inventory

| # | Credential | Where it lives | Placed by | Revokes at |
|---|---|---|---|---|
| 1 | Model credential | not on the box yet | #83 installs the agent | - |
| 2 | GitHub token, fine-grained, `Educational-Travel-Adventures/tourbot`, contents + pull requests | `~loop/.config/loop/github-token`, mode 0600 | `wizards/loop-github-credentials.sh` | github.com/settings/personal-access-tokens |
| 3 | SSH signing key, dedicated | `~loop/.ssh/loop_signing_ed25519`, mode 0600 | `ansible/loop.yml`, role `loop_credentials` | github.com/settings/keys |
| 4 | Docker PAT, read-only | `gnome-keyring` under `~loop` | `wizards/loop-sbx-login.sh` (#78) | app.docker.com |

Four rather than three, stated rather than slipped in: `sbx` will not create a
sandbox without a Docker identity, so the Execution Boundary itself requires one.
ADR 0009 has the reasoning. The fourth reads Docker Hub and reaches nothing else,
and revoking it stops the boundary rather than degrading it.

**Neither shared secret manifest holds any of them.** Checked rather than
asserted - `grep -ic loop` answers `0` on both
`/etc/vaulted-agent/manifests/orchestrator-all.env.tpl` and
`/srv/orchestration/env.tpl`. A `va` session on the orchestration VM has no
reference to this box's credentials, which is what spec #73 means by "it belongs
to the box".

## What the play generates, and what it deliberately does not

`ansible/roles/loop_credentials` generates the signing key **on the box** (ADR
0005: the operator's laptop key never leaves the laptop) and configures git to
use it. Applied, then applied again:

```
loopbox : ok=36  changed=6  unreachable=0  failed=0  skipped=5
loopbox : ok=36  changed=0  unreachable=0  failed=0  skipped=5
```

```
256 SHA256:y2o+BwR8wRTlWUnGLdj3W6CzQeH2ARgEfI7cg1hAlE0 loop@loop.etadventures.com (ED25519)
```

The key is generated with `creates:` rather than by a state module, which is the
one non-obvious choice in the role: **regenerating this key would silently
invalidate its registration on GitHub**, and every commit signed afterwards would
be unverifiable with no error anywhere. A key that exists is left exactly as it
is.

Three tasks are guarded by `when: not ansible_check_mode`. Ansible skips
`command` in check mode, so on a first `--check` run the key does not exist and
the tasks that read it would fail on its absence rather than report anything.
#77 already records that a check run proves nothing about this box; this is that,
made explicit rather than fatal.

What the play does **not** do is place the token or register the key. Both are
browser steps, and both are in the walkthrough rather than in prose.

### The box verifies its own signature

`gpg.ssh.allowedSignersFile` is configured and generated from the key's own
public half, so `git verify-commit` answers on the box:

```
a171b1b06c81c6636121b919ff010e2f00fff185 Jacob Stephens <jstephens@etadventures.com> sig=G key=SHA256:y2o+BwR8...
Good "git" signature for jstephens@etadventures.com with ED25519 key SHA256:y2o+BwR8...
```

That matters for cost rather than for correctness: a signature that will not
verify is found on the box in a second, and found on GitHub after a whole Run has
been spent.

### The token is read by a helper, never written into a URL

`~loop/.config/loop/git-credential-loop` reads the token file on `get` and does
nothing on `store` or `erase`. A token in a remote URL ends up in `.git/config`,
in `git remote -v`, and in any error message that quotes the URL. The helper also
prints nothing when the token is absent, so a push fails with an authentication
error rather than prompting - an unattended Iteration handed a prompt it cannot
answer would burn its whole wall clock to say the same thing.

## The human-only steps, as a walkthrough

`lab/single-user-factory/wizards/loop-github-credentials.sh`, six stages:
preflight, register the key **as a Signing Key**, mint the repository-scoped
token and pipe it over SSH, probe what the token can and cannot reach, make and
push one commit and read GitHub's verdict on it, then run the box's own
credential assertion.

Two properties of the walkthrough worth keeping if it is ever rewritten:

- **The token never reaches the orchestration VM's disk, and never comes back.**
  It goes over SSH into a redirect on the box, and every later command that needs
  it reads it from that file inside the same shell that uses it.

  The obvious way to write it, `install -D -m 600 /dev/stdin`, does not work and
  cost a run to find out: under `su - loop` that path is the ssh pipe, still owned
  by root, and `install` fails with `cannot open '/dev/stdin': Permission
  denied`. `cat` with no argument reads the descriptor it was handed rather than
  opening a path, so it does not care who owns it. What replaced it is
  `umask 077 && cat > file.new && mv file.new file`: the temporary file is 0600
  from its first byte and `mv` carries that mode across, so the token is never on
  disk in a mode anyone else could read - not for the instant a `chmod` would
  take, and not on a re-run over a file already there with a wider mode.
- **The key title and the target repository are read from the role's defaults**,
  not carried in the wizard. A walkthrough with its own copy would be a second
  place they are decided, and the two would disagree the first time either moved.

The one trap the walkthrough exists to prevent: GitHub's "New SSH key" form
offers **Authentication Key** and **Signing Key**, and the same key in the wrong
box lets the box log in and leaves every commit Unverified with no error anywhere
saying why.

## The negative probe

The property the whole isolation argument rests on is that the token reaches one
repository. That is asserted by asking GitHub, from the box, with the token the
box holds:

```
repos/Educational-Travel-Adventures/tourbot                    200
repos/Educational-Travel-Adventures/orchestration              404
repos/Educational-Travel-Adventures/tourbot/actions/secrets    403
repos/Educational-Travel-Adventures/tourbot/hooks              403
repos/Educational-Travel-Adventures/tourbot/collaborators      200
orgs/Educational-Travel-Adventures/repos                       200
```

The 404 is the one that matters: a token that could see a second repository would
be wider than the acceptance criterion, and nothing else in the wizard or on the
box would notice. The two 403s are Secrets and Administration, neither of which
was granted.

The last two lines are the honest part of this section, because both are 200 and
neither is a hole. `collaborators` is readable with Metadata, which GitHub adds
to every fine-grained token and does not let you remove. `orgs/…/repos` answers
**2** of the organisation's **52** repositories - `tourbot`, which the token was
granted, and `.github`, which is public and visible to any token at all. So the
token cannot enumerate the organisation; it sees what it was given plus what the
world already sees.

Not probed, deliberately: whether the token can push to `master`. Branch
protection there requires a review (`required_pull_request_reviews: 1`), and an
unattended Run that tried would be testing a backstop rather than using it.

## One Verified commit

The stage that makes the other five worth anything: the box makes a commit,
signs it with the registered key, pushes it to a throwaway branch on the target
repository, and GitHub is asked what it thinks of it.

```
commit         : e974df8e22ea968a193246e404c5cbd2f9125f09
message        : Loop: prove a Verified commit on a throwaway branch
author account : JacobStephens2
author email   : jstephens@etadventures.com
signature      : -----BEGIN SSH SIGNATURE-----
verified       : true (valid)
branch         : loop/verified-proof-20260825-021243
```

Checkable without trusting either the wizard or this note. The branch is gone,
but the repository's activity log records it and the commit answers by SHA:

```
$ gh api /repos/Educational-Travel-Adventures/tourbot/activity
  branch_creation  refs/heads/loop/verified-proof-20260825-021243  02:12:46Z  JacobStephens2
  branch_deletion  refs/heads/loop/verified-proof-20260825-021243  02:12:53Z  JacobStephens2

$ gh api /repos/Educational-Travel-Adventures/tourbot/commits/e974df8e
  .author.login                  JacobStephens2
  .commit.verification.verified  true
  .commit.verification.reason    valid
```

Seven seconds on the branch, which is the whole external effect of this ticket.

It is an **orphan branch built in a fresh repository**, not a clone of
`tourbot`. That repository is 981 MB, and cloning it to make one commit would
cost minutes to prove nothing extra: what is being proved is the box's identity,
its key and its token, and a single commit exercises all three.

Read the badge as ADR 0005 defines it - the operator *caused* this commit, not
that he typed it. The key's distinct title, `Loop - loop.etadventures.com`, is
what keeps a Loop commit distinguishable from a hand-authored one, and it has to
be the discriminator because ETA policy forbids an AI-attribution trailer in a
commit message.

If it comes back `verified=false, reason=unknown_key`, the key was registered as
an authentication key rather than a signing key. That is the one trap the
walkthrough exists to prevent, and it is invisible from the box.

## The credential assertion

`loop/assert-credentials.sh` is the inventory as something that runs, on the box,
as the account a Run executes as. After the walkthrough:

```
CREDENTIALS_RESULT=clean
CREDENTIALS_HELD=3
CREDENTIALS_VIOLATIONS=0
CREDENTIALS_INDETERMINATE=2

Allowed - the box holds these and nothing else:
  [held]    github-token      /home/loop/.config/loop/github-token
  [held]    signing-key       /home/loop/.ssh/loop_signing_ed25519
  [held]    docker-identity   sbx reports authenticated
  [absent*] model-credential  no agent login on the box yet
  * not gating: absent is the expected state until #83 installs the agent.

Forbidden - none of these may be on the box:
  [clear]   vault-token
  [partial] database-credential
  [clear]   digitalocean-token
  [clear]   metered-model-key
  [partial] fleet-ssh-key

Could not be checked - these are not passes:
  - database-credential: /root/.my.cnf could not be checked - /root is not searchable by loop
  - fleet-ssh-key: /root could not be swept - it is not searchable by loop
```

Before the token was placed the same run answered `violations` with exactly one -
`github-token: absent` - which is the script reporting what was true at the time
rather than a check that had not noticed anything yet.

The two `[partial]` lines are the reason the next section exists.

### The collision has three doors, not one

Spec #73's user story 32 wants a metered API key silently superseding the
subscription to be impossible or loudly detected. #78 found that the environment
variable is only the first way in:

1. **`ANTHROPIC_API_KEY` in the Run's environment.** Guarded at Run start by
   `agents/claude.sh`, per-agent, because that collision is a property of the
   agent rather than of the Loop.
2. **The same name exported from a shell profile or `/etc/environment`.** Not in
   this process's environment and in every future login's. `env` would not show
   it; the script reads the files.
3. **A metered secret stored in the Execution Boundary.** `sbx secret import`
   imports secrets *detected in host environment variables*, and `sbx secret set`
   stores one durably for the proxy to inject. A key that reached the boundary's
   store is in no environment and no file at all, so the only way to see it is to
   ask `sbx`.

The script checks all three. On this box `sbx secret ls` answers `No secrets
found`.

Door three was exercised against the real CLI rather than only against the
suite's fake. A throwaway secret was stored, the script was run, and the secret
was removed:

```
$ sbx secret set anthropic --token sk-ant-NOT-A-REAL-KEY-issue81-probe
No keychain detected - this secret will be stored on disk, protected by file permissions
Saved secret for service "anthropic" in scope "(global)"

CREDENTIALS_RESULT=violations
  [found]   metered-model-key
  - metered-model-key: the Execution Boundary stores a 'anthropic' secret, which the proxy would inject

$ sbx secret rm anthropic --force
Deleted secret for service "anthropic" in scope "(global)"
```

`--force` is not incidental: `sbx secret rm` prompts, and the prompt is what
hangs an unattended caller. That is also why both boundary calls in the script
carry a `timeout` - this script is what #83's preflight runs, and a preflight
that blocks forever is worse than one that fails, because the failure is
reported and the block is a Run that never starts and never says so.

### Fail closed, not open

Three choices in the script are worth naming because the opposite would be easy
and silent:

- An Execution Boundary that **cannot be asked** is a violation, not a pass. An
  assertion that could not be evaluated is not an assertion that succeeded.
- A probe that **could not be evaluated** is reported as `[partial]`, never as
  `[clear]`. `[[ -e ]]` is false both for "not there" and for "not allowed to
  look", and those are different answers. Run as the Run account - the
  documented way - `/root` is mode 0700, so every probe under it lands here.
- A private key is identified by its **header**, not its filename, because the
  filename is the one part of a key an operator renames. The one exception is
  `/etc/ssh/ssh_host_*`: the box's own host keys are its identity rather than
  reach into anything, and flagging them would make the family red on every
  correctly-built box, which is how a check stops being read. A key parked in
  `/etc/ssh` under any other name is still caught - which was found the honest
  way, by the root run flagging three host keys.

### It takes two runs, not one

Neither invocation covers the fifth acceptance criterion alone, and the
walkthrough runs both:

| run as | sees | blind to |
|---|---|---|
| `loop` | the environment a Run actually gets, the Run account's home, the boundary's session | `/root` (0700) |
| `root` | `/root`, `/etc/ssh` | the Run account's environment; `sbx` (root has no session, so `docker-identity` reports `unknown`) |

Observed, `loop` then `root`, on 2026-08-25 with the token not yet placed:

```
loop : [clear] vault-token  [partial] database-credential  [clear] digitalocean-token
       [clear] metered-model-key  [partial] fleet-ssh-key
       - database-credential: /root/.my.cnf could not be checked - /root is not searchable by loop
       - fleet-ssh-key: /root could not be swept - it is not searchable by loop

root : [clear] vault-token  [clear] database-credential  [clear] digitalocean-token
       [clear] metered-model-key  [clear] fleet-ssh-key
```

Between them, the four forbidden families are clear across both the Run
account's world and root's.

`SSH_AUTH_SOCK` is in the fleet-key family rather than with the files: a
forwarded agent is fleet reach that leaves nothing on disk, and `ssh -A` to this
box is the easiest way to hand an unattended agent the operator's whole key ring
for as long as the session lasts.

## The suites

On the box, where `ansible/roles/loop_shell_suite` installs the harness:

```
bats tests/                    96 tests, 96 passed      (41 of them this ticket's)
tests/mutation-check.sh        40 mutations, 40 caught  (15 of them this ticket's)
shellcheck -x *.sh agents/*.sh tests/*.sh
```

Scope: `bats tests/` run on `loop.etadventures.com` as the `loop` account,
against this branch's copy of `lab/single-user-factory/loop/`. The harness is on
the box and not on the orchestration VM (`ansible/roles/loop_shell_suite`), so
that is where the numbers come from.

The credential script is seamed and tested on its own, not only through whatever
runs it, for the same reason the completeness check is: it is itself an
assertion, and a component that is the assertion should not have its correctness
established only through another component.

The seam is three flags - `--home`, `--system-root`, `--sbx` - so a whole box is
a tmpdir: a home directory, a system root, and a scripted fake `sbx` that can
answer "signed out", "an anthropic secret is stored", or nothing at all. No
network, no box, no model.

The fifteen mutations break one credential family or one property of a held
credential each. Three are worth knowing about, because each is a failure that
would otherwise be silent for a whole Run: `signing-configured`, which leaves the
key present and `commit.gpgsign` off - commits land unsigned and nobody finds out
until the pull request is open; `boundary-unknown`, which turns an Execution
Boundary that cannot be asked into one that answered yes; and
`indeterminate-silent`, which scores a probe nobody was allowed to run as a pass
and turns the whole fifth acceptance criterion into four families reported
`[clear]` that were never looked at.

One mutation stopped applying when `check_forbidden_files` gained its
unreadable-parent branch, and the harness said so and failed rather than
reporting a false pass - which is the property that makes the mutation set worth
having at all.

## What this does not establish

- **Which keys are registered to the account, or under what titles.** No
  credential on this box can read `/user/ssh_signing_keys`, and the orchestration
  VM's own token cannot either (`403 Resource not accessible by personal access
  token`). What is established is stronger for this ticket and weaker in general:
  `verified: true, reason: valid` on a commit signed by this key proves the key
  is registered *as a signing key*, because an authentication key produces
  `unknown_key`. The title is not checkable from here at all - it is the
  operator's to keep right, and the walkthrough is where he is told what to type.
- **That the token's permissions are exactly contents + pull requests.** GitHub
  publishes no endpoint that reports a fine-grained token's own permission set.
  What is established: it reads one repository and cannot see a second, it cannot
  read Secrets or Administration, and it pushed and deleted a branch - so
  contents write is proven. Pull-request write is proven the first time #83 opens
  one.
- **What happens when the token expires.** A Run fails at its push, loudly, with
  everything it did still on the box. That is the intended direction and it has
  not been exercised.
- **Anything about the model credential.** No agent is installed (#83). The
  fourth line of the inventory is `absent`, which is the expected state, and it
  is the one line that does not gate the assertion's exit code.
