# The Loop's credentials: what the box holds, and the proof it works

`loop.etadventures.com`, 2026-08-25 (issue #81, spec issue #73).
ADR 0005 governs what a Verified commit from this box means; ADR 0009 records
that the inventory is four lines, not three.

**Status: the two browser steps have not been run yet.** Everything below marked
**[pending]** is what the walkthrough will produce, not what has been observed;
it is written down first so that running it is a comparison rather than a
transcription. Everything not so marked was observed on 2026-08-25.

This note is evidence, not documentation. It records what was generated, what
was registered by hand, what was observed by running commands, and - the part
that outlives the ticket - which of the properties here are asserted by
something that runs and which are asserted by a sentence in a document.

## The inventory

| # | Credential | Where it lives | Placed by | Revokes at |
|---|---|---|---|---|
| 1 | Model credential | not on the box yet | #83 installs the agent | - |
| 2 | GitHub token, fine-grained, `Educational-Travel-Adventures/tourbot`, contents + pull requests **[pending]** | `~loop/.config/loop/github-token`, mode 0600 | `wizards/loop-github-credentials.sh` | github.com/settings/personal-access-tokens |
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
  It is piped into `install -D -m 600 /dev/stdin` on the box, so the file is
  created with the right mode from its first byte rather than existing
  world-readable for however long a `chmod` takes. Every later command that needs
  it reads it from that file inside the same shell that uses it.
- **The key title and the target repository are read from the role's defaults**,
  not carried in the wizard. A walkthrough with its own copy would be a second
  place they are decided, and the two would disagree the first time either moved.

The one trap the walkthrough exists to prevent: GitHub's "New SSH key" form
offers **Authentication Key** and **Signing Key**, and the same key in the wrong
box lets the box log in and leaves every commit Unverified with no error anywhere
saying why.

## The negative probe **[pending]**

The property the whole isolation argument rests on is that the token reaches one
repository. That is asserted by asking GitHub, in both directions:

```
repos/Educational-Travel-Adventures/tourbot          ???   expected 200, reachable
repos/Educational-Travel-Adventures/orchestration    ???   expected 404, invisible
```

The 404 is the one that matters. A token that can see a second repository is
wider than the acceptance criterion, and nothing else in the wizard or on the box
would notice.

Not probed, deliberately: whether the token can push to `master`. Branch
protection there requires a review (`required_pull_request_reviews: 1`), and an
unattended Run that tried would be testing a backstop rather than using it.

## One Verified commit **[pending]**

The stage that makes the other five worth anything: the box makes a commit,
signs it with the registered key, pushes it to a throwaway branch on the target
repository, and GitHub is asked what it thinks of it.

```
author account : ???   expected JacobStephens2
author email   : jstephens@etadventures.com
verified       : ???   expected true (valid)
branch         : loop/verified-proof-<utc timestamp>, deleted immediately after
```

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

`loop/assert-credentials.sh` is the inventory as something that runs. Against the
box, as the account a Run executes as, before the token was placed - the one
violation is the credential the walkthrough exists to install, which is the
script reporting exactly what was true at the time:

```
CREDENTIALS_RESULT=violations
CREDENTIALS_HELD=2
CREDENTIALS_VIOLATIONS=1

Allowed - the box holds these and nothing else:
  [absent]  github-token      fine-grained, repository-scoped, contents + pull requests
  [held]    signing-key       dedicated SSH signing key, registered to the operator
  [held]    docker-identity   read-only Docker PAT the Execution Boundary requires
  [absent*] model-credential  the agent's subscription login (#83 installs the agent)

Forbidden - none of these may be on the box:
  [clear]   vault-token
  [clear]   database-credential
  [clear]   fleet-ssh-key
  [clear]   digitalocean-token
  [clear]   metered-model-key

Violations:
  - github-token: absent - expected at /home/loop/.config/loop/github-token
```

The four forbidden families were already clear at that point, which is the half
of the fifth acceptance criterion that does not depend on the browser steps.
**[pending]** the same run after the walkthrough, which should answer
`CREDENTIALS_RESULT=clean` with `CREDENTIALS_HELD=3`.

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

### Fail closed, not open

Two choices in the script are worth naming because the opposite would be easy and
silent:

- An Execution Boundary that **cannot be asked** is a violation, not a pass. An
  assertion that could not be evaluated is not an assertion that succeeded.
- A private key is identified by its **header**, not its filename, because the
  filename is the one part of a key an operator renames.

`SSH_AUTH_SOCK` is in the fleet-key family rather than with the files: a
forwarded agent is fleet reach that leaves nothing on disk, and `ssh -A` to this
box is the easiest way to hand an unattended agent the operator's whole key ring
for as long as the session lasts.

## The suites

On the box, where `ansible/roles/loop_shell_suite` installs the harness:

```
bats tests/                    91 tests, 91 passed      (36 of them this ticket's)
tests/mutation-check.sh        37 mutations, 37 caught  (12 of them this ticket's)
shellcheck -x *.sh agents/*.sh tests/*.sh
```

The credential script is seamed and tested on its own, not only through whatever
runs it, for the same reason the completeness check is: it is itself an
assertion, and a component that is the assertion should not have its correctness
established only through another component.

The seam is three flags - `--home`, `--system-root`, `--sbx` - so a whole box is
a tmpdir: a home directory, a system root, and a scripted fake `sbx` that can
answer "signed out", "an anthropic secret is stored", or nothing at all. No
network, no box, no model.

The twelve mutations break one credential family or one property of a held
credential each. Two are worth knowing about, because they are the failures that
would otherwise be silent for a whole Run: `signing-configured`, which leaves the
key present and `commit.gpgsign` off - commits land unsigned and nobody finds out
until the pull request is open - and `boundary-unknown`, which turns an
Execution Boundary that cannot be asked into one that answered yes.

## What this does not establish

- **That the key is registered as a signing key rather than an authentication
  key.** No credential on this box can read
  `/user/ssh_signing_keys`, and the orchestration VM's own token cannot either
  (`403 Resource not accessible by personal access token`). The Verified badge on
  a pushed commit is the proof, which is why the walkthrough pushes one rather
  than asking the API what is registered.
- **That the token's permissions are exactly contents + pull requests.** GitHub
  publishes no endpoint that reports a fine-grained token's own permission set.
  What is established is that it can read one repository and cannot see another,
  and that it can push a branch. Pull-request write is proven the first time #82
  opens one.
- **What happens when the token expires.** A Run fails at its push, loudly, with
  everything it did still on the box. That is the intended direction and it has
  not been exercised.
- **Anything about the model credential.** No agent is installed (#83). The
  fourth line of the inventory is `absent`, which is the expected state, and it
  is the one line that does not gate the assertion's exit code.
