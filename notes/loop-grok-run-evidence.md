# The same task under a second agent: what transferred, and what did not

Issue #84. Everything below was run on 2026-08-25, against
`loop.etadventures.com` and the Loop as committed. #83 ran the Loop end to end
under Claude Code and produced [tourbot#665](https://github.com/Educational-Travel-Adventures/tourbot/pull/665);
this is the same task, the same Plan, the same Termination Contract and the same
box, with `LOOP_AGENT_COMMAND` pointed at a different file.

The ticket's reason for existing is in its first line: *so that what the spike
learned is about the technique rather than about one vendor*. What follows is
therefore organised around which of the first Run's results survived the swap.

## The swap, as it was actually made

Two lines, in two places, for two different jobs:

```
LOOP_AGENT_COMMAND=/home/loop/loop/agents/grok.sh      the Loop's half
ansible-playbook loop.yml -e loop_agent_name=grok      the box's half
```

Nothing in `run.sh`, `contract.sh`, `propose.sh`, `seed-run.sh` or the boundary
role's structure moved. `tests/boundary-grok.bats` asserts the first half
directly - `run.sh` names an adapter on exactly one line of code, and
`contract.sh` on none - so the claim is a test rather than a sentence.

The second line does two things that have to agree, which is why it is one
variable rather than two: `loop_agent` installs the agent the box logs in with,
and `loop_execution_boundary` allows that agent's hosts through the egress
proxy. A box configured to install one vendor and allow the other's hosts fails
as a Run that builds a boundary, installs an agent, and cannot reach a model.

## The Run

Seeded off the box by the operator, from the same task, onto a fresh branch off
`origin/master` - so this is the same starting point #83 had rather than a
continuation of what it produced:

```
$ seed-run.sh --repo <checkout> --task 648 \
      --task-repo Educational-Travel-Adventures/tourbot \
      --area 'dashboards and reports' \
      --check "/home/loop/loop/check-inventory.sh --checkout . \
               --inventory documentation/tblEmailMessage-inventory.md \
               --scope 'mtourbot/reports/*'"
LOOP_SEED_RESULT=seeded
LOOP_SEED_CRITERIA=5
```

Then, unattended, with one environment variable different from #83's command:

```
$ LOOP_AGENT_COMMAND=/home/loop/loop/agents/grok.sh \
      run.sh --repo ~/tourbot --task-ref Educational-Travel-Adventures/tourbot#648 --propose
LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_PROPOSAL=proposed
LOOP_PROPOSE_URL=https://github.com/Educational-Travel-Adventures/tourbot/pull/670
LOOP_PROPOSE_BRANCH=loop/648-dashboards-and-reports-grok
LOOP_PROPOSE_BASE=master
```

**Twenty minutes, five Iterations, exit 0 on the first attempt.** ADR 0007 makes
`0` the only code meaning the Run executed its planned Iterations and every one
of them ran to its own end. #83 took three Runs to produce it; this took one,
which is the clearest single statement that what #83 corrected was the Loop
rather than an agent.

```
- Ended by: iteration-cap
- Iterations: 5 (committed 5, no-op 0, killed 0, turn bound 0)
- Completion Promises recorded: 1
- Faults: none
- Exit code: 0
```

**Five Iterations, five commits, no No-ops.** The corrected Contract held under a
different vendor without being re-tuned: at 100 turns the turn bound did not
fire, and no Iteration reached its wall clock. One Completion Promise was
recorded and did not end the Run - the same behaviour user story 12 asks for,
demonstrated a second time.

### What was verified about it

| | |
|---|---|
| Each Iteration inside its own microVM | yes - `sbx ls` during the Run showed one `shell` sandbox at a time (`loop-2483851-… shell running /home/loop/tourbot, /home/loop/loop:ro`), a different name each Iteration, and `No sandboxes found` after |
| Commits attributed and Verified | all 13 on the branch: `JacobStephens2`, `verification.verified: true`, reason `valid` - the agent's own work included, signed inside a microVM with a key copied in for the Iteration and destroyed with it |
| Draft pull request, referencing the task | [tourbot#670](https://github.com/Educational-Travel-Adventures/tourbot/pull/670), `draft: true`, `loop/648-dashboards-and-reports-grok` → `master` |
| Progress Log reads as a narrative | 574 lines, one section per Iteration |
| Nothing merged, deployed, applied, or written to a live third-party service | yes - three files, 949 additions, **zero deletions**, no application code touched |
| The box's credential inventory, after | `CREDENTIALS_RESULT=clean`, 4 held, 0 violations |

### The grade, and the comparison it makes possible

```
CHECK_RESULT=complete
CHECK_OCCURRENCES=128
CHECK_ACCOUNTED=128
CHECK_MISSING=0        CHECK_UNCLASSIFIED=0   CHECK_AMBIGUOUS=0
```

128 of 128, against a denominator the check derived from the checkout on the day
it ran (ADR 0008). Both Runs pass the same mechanical check on the same
denominator - **and they do not agree on the answer**:

| | #83, Claude Code | #84, Grok Build |
|---|---|---|
| `should-include-notes` | 14 | 12 |
| `emails-only-by-design` | 113 | 116 |
| `write` | 1 | 0 |
| total | 128 | 128 |

That is the most interesting number this ticket produced and it is worth being
careful about what it does and does not say. It does **not** say one agent is
more accurate: nothing here adjudicates the disputed occurrences, and the check
grades coverage rather than correctness - which is exactly what ADR 0008 says it
is for. What it says is that the completeness check cannot tell the two apart,
so a Run's grade is evidence that the work was *done* and not evidence that it
was done *right*. The operator reviewing #670 against #665 is what decides which
classification is correct, and that review is the thing neither Run replaces.

## The agent is inside the boundary, not provided by it

`sbx create` offers `claude codex copilot cursor docker-agent droid gemini kiro
opencode shell` and nothing for Grok, so the boundary is a `shell` one and
`agents/grok.sh` installs the agent within it at a pinned version, per
Iteration:

```
$ cd ~/proof && ~/loop/agents/grok.sh /tmp/proofprompt 2
Installing Grok 1.0.5 (linux-x86_64)...
  Downloading grok 1.0.5...
Grok 1.0.5 installed to /home/agent/.grok/bin/grok
ready
RC=0
real    0m30.849s
$ sbx ls
No sandboxes found.
```

Thirty-one seconds for a microVM, an install, one inference turn and a teardown;
the install itself is about six of them. The sandbox was destroyed, as it is
under the other adapter.

**A copy of the host's binary would have been cheaper and is deliberately not
what happens.** A copy makes the guest's agent whatever the host is holding, and
the reason to pin at all is that an agent whose version changes between
Iterations is an agent whose behaviour nobody has observed. The pin is asked for
*and read back* - `install-unpinned` and `pin-not-verified` are both mutations,
and both are caught - because an installer that quietly fell back to a channel
pointer would otherwise put an unobserved version inside an unattended Run.

The pin lives in the adapter and `ansible/roles/loop_agent` asks it for the
number (`grok.sh --pinned-version`) rather than carrying a second copy. A host
and a guest pinned by two files is exactly the drift a pin exists to prevent.

## The egress hosts, discovered rather than guessed

#100 narrowed the boundary to `deny-all` plus two hosts, and #78's note listed
the Grok set as *read off the shipped binary, not verified against a Run*. It
was two hosts and it needed three.

| host | how it was established |
|---|---|
| `cli-chat-proxy.grok.com:443` | Inference. The binary's default `GROK_CLI_CHAT_PROXY_BASE_URL`, and confirmed against `sbx policy log` as what a Run actually reached |
| `auth.x.ai:443` | The OIDC issuer in `~/.grok/auth.json`. The credential expires six hours after it is minted, so this is the refresh DURING a Run, not only at login |
| `x.ai:443` | **New.** The install. #100 left this host off as a cosmetic changelog fetch; installing the agent inside the boundary is what made it required |

### What the Run actually reached

`sbx policy log` after the Run, counted by host. This is the instrument, and it
is why "discovered rather than guessed" is a claim about evidence rather than
about care:

```
== allowed
   12  api.anthropic.com:443          <- #83's Runs, still in the log
    9  mcp-proxy.anthropic.com:443    <- #83's Runs
   10  mcp-gateway.docker.internal:80
    8  x.ai:443                       <- the install, once per Iteration
    6  cli-chat-proxy.grok.com:443    <- INFERENCE. Observed, not read off a binary
    1  api.github.com:443             <- the proposal

== blocked
   21  archive.ubuntu.com:80
   21  security.ubuntu.com:80
   21  download.docker.com:443
    9  http-intake.logs.us5.datadoghq.com:443
    7  api.x.ai:443                   <- the metered path, refused every Iteration
    1  storage.googleapis.com:443     <- the installer's GCS fallback, refused
    1  auth.x.ai:443                  <- the pre-allowlist probe
    1  cli-chat-proxy.grok.com:443    <- the pre-allowlist probe
    1  x.ai:443                       <- the pre-allowlist probe
```

`cli-chat-proxy.grok.com` is the only inference host an Iteration reached: no
`api.x.ai`, no vendor fallback, no second endpoint. `auth.x.ai` appears only in
the pre-allowlist probe row, which is a finding rather than an omission - this
Run was shorter than the token's remaining six hours, so the refresh never
fired. It stays on the list for the Run that is not.

**One host nobody declared, found by reading this log.** Something inside the
sandbox posts to `http-intake.logs.us5.datadoghq.com:443`, nine times, and
`deny-all` refused it every time. It is not Grok's - it appears alongside the
`archive.ubuntu.com` / `download.docker.com` boot traffic that every sandbox
generates, so it is the boundary vendor's own telemetry. Recorded because an
allowlist is only trustworthy if somebody has read what it turned away, and this
is the first time anyone has.

Before the allowlist gained them, every one of them was the proxy's `403` from
inside a sandbox - which is what `deny-all` looks like when it is working:

```
x.ai                    -> 403
storage.googleapis.com  -> 403
cli-chat-proxy.grok.com -> 403
auth.x.ai               -> 403
api.x.ai                -> 403
```

After: `sbx policy ls --json` holds five global allow rules and no more -
`github.com`, `api.github.com`, `x.ai`, `auth.x.ai`, `cli-chat-proxy.grok.com` -
reconciled by the play rather than added by hand.

### Two hosts the agent reaches for and does not get

- **`api.x.ai:443`.** The `sbx policy log` recorded it BLOCKED during the proof
  run above: `no applicable policies for op(action=net:connect:tcp,
  resource=net:domain:api.x.ai:443)`. That is the metered API path, and ADR 0004
  forbids it for the same reason story 32 forbids the key. The agent reached for
  it, was refused, and worked anyway.
- **`storage.googleapis.com:443`.** The installer's fallback artifact host.
  `x.ai/cli` serves the channel pointer and the artifact alike, so a pinned
  install never needs it. It stays off deliberately: a GCS bucket reachable from
  inside an unattended agent's microVM is the exfiltration path #100 closed, and
  an x.ai outage becoming an install that fails loudly is the right direction to
  fail.

One host arrives without being declared anywhere: the `shell` kit attaches
`openrouter.ai` to a `shell` sandbox, the way the `claude` kit attaches
Anthropic's six. Choosing a template chooses hosts, whichever template it is.

## The metered key, which has five doors and a file

Spec #73's story 32, and the failure shape that broke Remote Control on the
orchestration VM. Under this vendor it is not theoretical and it is not one
name. The vendor's own shipped README says it outright:

> The API key takes precedence over browser credentials.

and gives the full resolution order:

> `api_key` -> `env_key` -> cached `auth_provider` token -> session token ->
> `XAI_API_KEY`

So the guard is a list, it runs before the boundary is built, and it names every
key that is set rather than the first:

```
XAI_API_KEY  GROK_CODE_XAI_API_KEY  GROK_DEPLOYMENT_KEY
GROK_AUTH_PROVIDER_ACCESS_TOKEN  GROK_AUTH_PROVIDER_COMMAND
```

`TOURBOT_PREVIEW_XAI_API_KEY` already exists in both of the orchestration VM's
manifests, so the realistic way one of these arrives is somebody copying a
working command across.

**And a file door, which the environment cannot show you.** A per-model
`api_key` or `env_key` in `~/.grok/config.toml` resolves *ahead* of the session
token, and that file is copied into the guest. `agents/grok.sh` refuses an
Iteration while one is there.

**One list, three enforcement points, and none of them restates a name.** The
names are declared in the adapter - where ADR 0004 puts vendor knowledge - and:

1. `run.sh`'s **preflight** asks the adapter and refuses to start the Run. This
   is the ticket's "asserted absent at Run start" literally: no Iteration, no
   boundary, no model time. The Loop does not know a vendor name and must not;
   `--metered-env-names` is a query, which is why it is part of the adapter's
   contract alongside its two arguments.
2. The **adapter** checks again at its own first act, before `sbx create`. Not
   redundant: the preflight covers the Run's environment and this covers a box
   whose environment changed between the two.
3. `assert-credentials.sh` grades the **box** - the process environment, every
   shell profile and `/etc/environment`, and `sbx secret` - against the union of
   what every adapter answers.

An adapter that answers nothing stops the Run and fails the inventory rather
than shortening either check. Five mutations cover it - `metered-key-unguarded`,
`metered-key-partial`, `config-key-door-open`, `metered-key-preflight-dropped`,
`metered-names-optional`, plus `adapter-names-not-folded-in` and
`silent-adapter-tolerated` on the inventory side - and all are caught.

## Whether the vendor's terms permit leaving it alone

Checked before the Run, because the ticket asks for it checked before running
unattended, and the answer is qualified rather than clean.

**What was found.** xAI's own CLI documentation describes headless mode as being
for exactly this: "Automate tasks - CI/CD pipelines, pre-commit hooks, cron
jobs", and its headless page instructs "When using headless mode (`-p`) or ACP
in scripts, CI, or other automated environments, pass `--no-auto-update`."
Unattended, scripted invocation is the documented use of the interface the Loop
drives, not a workaround of it. The consumer Terms of Service' "What you cannot
do" section prohibits modifying, reselling, reverse-engineering, competing
model development, and "Disrupting, interfering, or unauthorized access to the
Service or its safety systems" - no clause about automation, bots or scripted
access.

**What could not be read from here, and is stated rather than papered over.**
`x.ai/legal/acceptable-use-policy` and the current `x.ai/legal/terms-of-service`
both answer `403` to this box, to `curl` and to a headless Chromium alike -
Cloudflare refusing a datacentre IP. The ToS text above is from an archived
copy of a previous version, and the AUP was not read at all. So: nothing found
prohibits it, the vendor documents the use, and one document was not readable
from this host. An operator who wants certainty should open the AUP in a browser.

**One thing the docs say and this version does not have.** The
`--no-auto-update` flag the documentation names is not in `grok 1.0.5 --help`.
The adapter uses `GROK_DISABLE_AUTOUPDATER=1`, which the shipped binary does
honour, alongside `GROK_TELEMETRY_ENABLED=0` and `GROK_CHANGELOG_OFFLINE=1` -
each turning off something that would otherwise reach a host `deny-all` blocks.

## The credential, and the property that does not transfer

**`sbx` host-proxy credential injection does not hold here, and must not be
inherited from the first Run.** ADR 0011 already found it holds for neither
configuration, and this one fails it for a second, independent reason: Grok's
subscription credential is an auto-refreshing OAuth token in `~/.grok/auth.json`,
not a key the proxy could present. `xai` *is* a supported `sbx secret` service,
so an injected configuration is possible - but only with an API key, which is
the one thing story 32 exists to prevent. So the token is copied inside, where
the agent can read it, and what bounds that is the egress allowlist, the sandbox
dying after every Iteration, and a session that expires on its own.

It expires in six hours, which is a difference worth naming rather than a
detail: the other agent's credential does not, and a Run left overnight under
this one depends on `auth.x.ai` being reachable mid-Iteration.

**How the credential got onto the box, recorded because it is a departure.** The
login is a browser step and the box is headless; `wizards/loop-grok-login.sh`
exists to walk an operator through `grok login --device-auth`. It was not used
for this Run. The operator instead authorised copying his existing
`~/.grok/auth.json` from the orchestration VM to `~loop/.grok/auth.json`, mode
0600 - the same subscription, the same person, and a model credential is a class
`assert-credentials.sh` already permits on that box. It is still a live OAuth
refresh token moved between hosts without a browser exchange, which is a
departure from this project's own "the login is human-only" doctrine, and it is
written down here rather than left to be inferred from a timestamp.

## A template difference, found by running it

The signing key and the allowed-signers file go into the guest at their **host**
paths, because the git config that names them is the host's and a copy landing
elsewhere would produce commits that are not Verified. Under `sbx create claude`,
`/home/loop` inside the guest is owned by `agent` and a plain `mkdir -p` works.
Under `sbx create shell` it is `root:root` mode 0755:

```
$ sbx exec <claude-sandbox> ls -ld /home/loop
drwxr-xr-x 3 agent agent 4096 /home/loop
$ sbx exec <shell-sandbox>  ls -ld /home/loop
drwxr-xr-x 3 root  root  4096 /home/loop
```

So the Grok adapter prepares the directory through the guest's passwordless
`sudo` and hands it back, and the other adapter does not need to. Nothing
offline would have found this, and the failure it produces is late and
unhelpful: a Run that builds a boundary, installs an agent, and dies placing a
credential. `guest-dir-unprivileged` is now a mutation, and it is caught.

## The turn bound says something else

The single clearest piece of evidence for ADR 0004 keeping vendor knowledge in
the adapter. Both agents exit `1` on reaching `--max-turns`; only the message
tells the bound apart from a broken invocation, and the messages are not the
same:

```
Claude Code   Error: Reached max turns (40)
Grok Build    Max turns reached
              Error: max turns reached
```

Observed, not read off a page: `grok --prompt-file … --max-turns 1` on a prompt
needing several turns prints both lines and exits 1. The adapter matches
case-insensitively because the phrase appears twice in two casings, and matching
the one that happens to carry `Error:` would turn on which of the two the vendor
keeps.

`contract.sh` declares only `LOOP_AGENT_TURN_BOUND_EXIT=33`, the number the two
sides share. `turn-bound-wrong-vendor` - the mistake a copied adapter would make -
is a mutation, and it is caught.

## The offline suites

`tests/boundary-grok.bats` is a sibling of `tests/boundary.bats` rather than a
parameterisation of it, and that is ADR 0012's reasoning rather than
duplication: a shared suite would have to be written in the vocabulary the two
agents have in common, and that vocabulary is exactly where the differences hide.
The shared properties are asserted twice on purpose.

```
bats tests/boundary-grok.bats                             37 tests, all green
bats tests/loop.bats                                      44 tests, all green
bats tests/*.bats                                         all green
tests/mutation-check.sh --only agents/grok.sh             20 mutations, all caught
tests/mutation-check.sh --only assert-credentials.sh      20 mutations, all caught
tests/mutation-check.sh --only run.sh                     all caught
```

## What this Run does not establish

- **That either classification is right.** Both Runs pass the completeness check
  on the same 128 occurrences and disagree on four of them. The check grades
  coverage, not correctness (ADR 0008), and the operator reviewing #670 against
  #665 is what settles it.
- **That the credential refresh works mid-Run.** Twenty minutes is well inside
  the token's six hours, so `auth.x.ai` was never reached. It stays on the
  allowlist for the Run that outlives a token, and that Run has not happened.
- **That five Iterations is the right cap for this agent.** The cap ended the
  Run with the Plan not obviously exhausted, exactly as it did under the other
  agent. One Run corrects at most the bounds that fired, and none did.
- **That the two agents cost the same.** Nothing here measures spend. Both bill
  against a subscription, which is why the Termination Contract is the whole
  cost control and why neither Run can produce a number.
