# The first Run end to end: what was verified, and how

Issue #83. Everything below was run on 2026-08-25, against
`loop.etadventures.com` and the Loop as committed. #79 built the Loop and
verified it entirely offline against a scripted fake; this is the first time any
of it met a model, a hypervisor and GitHub at once, so what is recorded here is
what only a real Run could say.

The three things #79's own README named as missing are the three things this
ticket added: the agent runs inside the Execution Boundary, a Run pushes and
opens a draft pull request, and `ansible/loop.yml` puts the Loop on the box.

## What the box holds now

`ansible-playbook loop.yml`, applied and then applied again:

```
loopbox : ok=42  changed=3  unreachable=0  failed=0
loopbox : ok=41  changed=0  unreachable=0  failed=0
```

Two roles are new. `loop_agent` installs Claude Code at a pinned version -
2.1.221, which is what `docker/sandbox-templates:claude-code-docker` shipped on
the same day, so the host and the guest agree. `loop_scripts` copies the Loop
itself to `/home/loop/loop` and clones the work checkout to `/home/loop/tourbot`
with `update: false`, because a play applied while a Run is in flight would
otherwise be a way to lose one.

The agent is on the **host** for exactly one reason: the login. Claude Code's
subscription login opens a browser and a sandbox is destroyed after every
Iteration, so the credential has to be established somewhere that outlives one.
A Run never executes the host's copy - `loop/agents/claude.sh` always goes
through `sbx`, and `tests/boundary.bats` is what holds that rather than a
comment saying so.

### The credential inventory, clean for the first time

`assert-credentials.sh` had four allowed rows and only three of them gating,
because until this ticket no agent was installed and requiring a model
credential would have made the script red on a box that was exactly as the spec
intended. That row gates now, and the box satisfies it:

```
CREDENTIALS_RESULT=clean
CREDENTIALS_HELD=4
CREDENTIALS_VIOLATIONS=0
```

**A finding this ticket produced by existing.** The moment `loop_scripts` put a
work checkout on the box, the fleet-key family went red - `tourbot` carries
three vendor sample keys in phpdocx's examples, and the sweep identifies a
private key by its header rather than by its filename, which is the right rule.
A check that is red on a correctly-built box is a check nobody reads (ADR 0009),
so the sweep now skips a private key that is **tracked content of a git
checkout**: it arrived by clone, it is visible in a diff, and it is the
repository's rather than the box's. The line is at *tracked* - a key dropped
into the checkout by hand is untracked and is still a violation - and what was
skipped is printed with its count, because an exclusion nobody can see is one
nobody can audit.

## The Execution Boundary, per Iteration

`agents/claude.sh` creates a microVM for each Iteration, places what the guest
needs, runs the agent, and destroys the sandbox. The Loop knows none of it:
`run.sh` launches one command per Iteration and compares the repository head
before and after.

Proved twice before a Run was spent on it, because the two claims are different
and the second is the one that matters:

```
$ cd ~/tourbot && LOOP_CLAUDE_CONFIG_DIR=/tmp/dry/.claude agents/claude.sh /tmp/dry/prompt 1
Not logged in · Please run /login
RC=1
$ sbx ls
No sandboxes found.
```

A deliberately empty credential: the sandbox was created, the credential was
copied in, the agent ran inside it, the agent refused, and **the sandbox was
removed anyway**. That is the plumbing, established without a model and without
a login.

Then the same adapter with the real credential:

```
$ cd ~/proof && ~/loop/agents/claude.sh /tmp/proof-prompt 1
ready
RC=0
$ sbx ls
No sandboxes found.
```

The agent answered from inside a microVM and the sandbox was destroyed. Neither
of these is the Run; both are things that would otherwise have been discovered
forty-five minutes into one.

### What goes inside, and what does not

| | inside | why |
|---|---|---|
| model credential | yes | there is nothing to run without it (ADR 0011) |
| signing key | yes | an Iteration commits, and an unsigned commit is not Verified - which is not recoverable afterwards |
| git identity | yes | the author email is what GitHub matches to the operator's account |
| **GitHub token** | **no** | the push and the pull request happen on the host after every agent process is gone |

The token being outside is what makes Proposal-Only Output a property of what is
inside the boundary rather than of what the prompt asked for. `tests/boundary.bats`
asserts it directly, and the mutation that puts the token in is caught.

The signing key being inside is a stated cost: an agent in the guest can read
it. What bounds that is the egress allowlist - `deny-all` plus `github.com` and
`api.github.com` (#100), both of which need the token the guest does not have -
and the key being dedicated to the Loop and revocable on its own (ADR 0005).

### The vendor writes into the agent's context

`sbx create claude <path>` leaves an 18KB `CLAUDE.md` in the **parent** of the
workspace - `/home/loop/CLAUDE.md` inside the guest - which Claude Code
discovers by walking up from its working directory. It is Docker's own guidance
about the sandbox's persistent environment file and shell completions, not
anything hostile.

It is recorded here because it is text the operator did not author reaching the
prompt of an unattended agent, which is the exact shape ADR 0003's content-trust
collapse depends on not happening. The collapse still holds - this is the
boundary vendor describing the boundary, not third-party input - but "nothing
the Loop reads was written by somebody else" is now false as literally written,
and a later reader should know which exception was examined rather than
overlooked.

## Proposal-Only Output

A Run started with `--propose` ends by pushing its branch and opening a draft
pull request. Four things hold the invariant up, and only the first three
survive somebody editing the fourth:

1. The box's fine-grained token holds Contents and Pull requests. It cannot
   merge, cannot administer, and has no Issues permission at all (ADR 0010).
2. The token is not inside the boundary.
3. Branch protection on `master`.
4. `draft: true` - what a reviewer sees, and the weakest: anyone who can see a
   draft can mark it ready.

`run.sh` refuses to start on `origin`'s default branch, at second zero rather
than at the proposal. By the proposal an unattended agent's Iterations have
already committed, and the refusal would arrive after the thing it was meant to
prevent.

Exit code `6` is new: the Run reached a planned end and the proposal failed. It
follows ADR 0007 one step further - a Run that produced nothing reviewable must
not exit `0` - while a Run that already ended on a bound keeps that bound's
code, because which bound ended it is the more useful fact.

## The first Run, which failed in four minutes and was worth every one of them

Seeded off the box, by the operator, from one task he chose (ADR 0010):

```
$ seed-run.sh --repo <checkout> --task 648 \
      --task-repo Educational-Travel-Adventures/tourbot \
      --area 'dashboards and reports' \
      --check "~/loop/check-inventory.sh --checkout . \
               --inventory documentation/tblEmailMessage-inventory.md \
               --scope 'mtourbot/reports/*'"
LOOP_SEED_RESULT=seeded
LOOP_SEED_CRITERIA=5
```

The branch was pushed and the box checked it out, so the Plan reached the box as
a commit like everything else. Then, unattended:

```
$ run.sh --repo ~/tourbot --task-ref Educational-Travel-Adventures/tourbot#648 --propose
LOOP_RUN_ENDED_BY=agent-failed
LOOP_RUN_EXIT=4
LOOP_RUN_ITERATIONS=1
LOOP_RUN_FAULTS=agent-failed
LOOP_RUN_PROPOSAL=proposed
LOOP_PROPOSE_URL=https://github.com/Educational-Travel-Adventures/tourbot/pull/665
```

Four minutes, one Iteration, no work committed, and a draft pull request all the
same. **This is the ticket's most valuable output**, and it is worth being
precise about which of the two things it found was which.

### It found a defect, not a preference

Claude Code exits non-zero on reaching `--max-turns`, printing
`Error: Reached max turns (40)`. The Loop read that as an agent that had failed
on its own, so it ended the whole Run at Iteration 1 - with the Iteration's work
sitting uncommitted in the working tree, and the head unmoved, which the Loop
also recorded as a No-op.

The turn bound is one of the Contract's five. It ends an **Iteration**, exactly
as the Iteration wall clock does; it is not a broken invocation and it is not a
reason to stop. #79 could not have found this: its scripted fake chose its own
exit codes, and nothing offline knows what the vendor does on reaching a bound
the vendor enforces.

The fix keeps the vendor knowledge where ADR 0004 puts it. `agents/claude.sh`
recognises the message and exits `LOOP_AGENT_TURN_BOUND_EXIT`, a status
`contract.sh` declares; `run.sh` treats that status as a bound firing - recorded
as a fault, so a Run reaching its cap having hit it does not exit 0, but not as
an ending bound, so the Run continues and the next Iteration reads what the last
one left behind.

Two things about that fix are worth knowing:

- **A message match, not `--output-format json`.** The JSON carries a structured
  `error_max_turns` and would be more robust to rewording. It would also make
  every faulting Iteration's Progress Log excerpt a blob, and the excerpt is how
  a Run nobody watched gets diagnosed. If the vendor rewords the message, the
  behaviour degrades to what it was - reported as `agent-failed`, with
  "Reached max turns" quoted into the log where a human reads it. Visible, not
  silent.
- **`pipefail` had to come off for that one pipeline.** The adapter now streams
  the agent's output *and* captures it, and with `pipefail` on, `set -e` ended
  the script the moment the agent exited non-zero - before any of the detection
  ran. It is caught by a mutation now, because it is precisely the kind of thing
  that regresses quietly.

### And it corrected a first value

Forty turns is too few. Iteration 1 spent all forty reading a 128-occurrence
classification task, wrote
`documentation/tblEmailMessage-inventory.md`, and ran out before step 6 of its
own prompt - the commit. So a whole Iteration's work landed as an uncommitted
diff.

Forty turns took about four and a half minutes, so the turn bound was biting at
roughly a third of the fifteen-minute Iteration wall clock. **100** puts it at
about eleven minutes, which leaves headroom under the wall clock: the two bounds
should not fire at the same moment, or a Run cannot say which one it was.

The other four values were not exercised. One Run corrects at most the bounds
that fired.

### What the first Run did establish

Everything outside the Loop's own logic worked on the first attempt, which is
the part that could not be tested offline:

| | |
|---|---|
| Iteration inside a microVM | yes - `sbx ls` showed `loop-982971-… claude running /home/loop/tourbot` while it ran, and nothing afterwards |
| Sandbox destroyed | yes, on a faulting Iteration |
| Commits attributed and Verified | `4fc5a7ecd`, `jstephens@etadventures.com`, resolved to `JacobStephens2`, `verification.verified: true`, reason `valid` |
| Push from the box | yes, through the credential helper, with the token never on a command line |
| Draft pull request | [#665](https://github.com/Educational-Travel-Adventures/tourbot/pull/665), `draft: true`, `loop/648-dashboards-and-reports` → `master`, referencing the task |
| Nothing merged, deployed or applied | yes |
| Progress Log readable as a narrative | yes - it names the bound, the exit status, the No-op, the uncommitted work, and quotes the agent's last lines |

The proposal was made on a **failed** Run, which is the behaviour the design
asks for rather than an accident: a Run that went wrong has still produced a
Progress Log saying so, and that record is exactly what is worth reviewing.

## What this does not establish

- **That the Termination Contract's five numbers are right.** They are first
  guesses, and one Run corrects them by at most one bound - whichever fired.
- **That the model does the task well.** That is what review is for, and it is
  not a property the Loop can assert.
- **Anything about a second agent.** #84 swaps to Grok Build, whose egress needs
  are different and which has no `sbx` template.
- **That host-proxy credential injection works for this configuration.** It does
  not, and ADR 0011 records why: the credential is a subscription, and the proxy
  injects API keys.
