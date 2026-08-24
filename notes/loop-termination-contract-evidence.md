# The Loop and its Termination Contract: what was verified, and how

Issue #79. Everything below was observed by running it on the orchestration VM
on 2026-08-24, offline - no model, no network, no spend. The Loop's own box was
not needed and was not touched.

## What was built

`lab/single-user-factory/loop/`:

- `contract.sh` - the five bounds, one file, every value overridable from the
  environment so the suite drives the real entry point with minutes collapsed to
  seconds.
- `run.sh` - one Run. The only executable that is not a declaration or an
  adapter.
- `agents/claude.sh` - the agent as one substitutable command (ADR 0004),
  carrying the vendor-specific concerns: `--permission-mode acceptEdits`, the
  turn bound, and the `ANTHROPIC_API_KEY` guard.
- `tests/` - the bats suite, the scripted fake agent, and the mutation check.

## The suite

Twenty tests, `bats tests/loop.bats`, 17.7 seconds wall clock. bats 1.10.0 -
the same version `ansible/roles/loop_shell_suite` installs on the Loop's box
from Ubuntu noble - fetched to the orchestration VM for this work because the
orchestration VM has `shellcheck` (0.11.0) but no bats.

All twenty assert only what a Run externally produces: its exit code, the bound
it names on stdout, the Progress Log's contents, and the git history. None names
an internal function of `run.sh` or depends on the order of steps inside it.

## The mutation check, and what it found

`tests/mutation-check.sh` breaks one bound at a time and runs the whole suite
against the broken copy. Eight mutations, all caught:

| Mutation | Tests turned red |
| --- | --- |
| run clock never checked | 1 |
| consecutive No-ops never abort | 3 |
| Iteration wall clock removed | 1 |
| Completion Promise ends the Run | 2 |
| agent's non-zero exit ignored | 1 |
| head compared after the Loop's own commit | 16 |
| iteration cap off by two | 7 |
| turn bound not passed to the agent | 2 |

Three minutes twenty-eight seconds for the whole check.

Two of these are worth reading rather than counting. **`head-after-bookkeeping`**
turning sixteen tests red is the shape of the design: No-op detection is the one
signal several bounds are built on, so comparing the head at the wrong moment
does not fail one test, it fails most of them. And **the Iteration wall clock was
the one bound the suite originally did not catch** - removing `timeout` made the
Run hang rather than fail, and a hung suite reports nothing. That is now fixed in
the harness rather than in the Loop: `run_the_loop` puts a sixty-second ceiling,
unrelated to any Contract value, above the Run. Without it, the bound that exists
to stop a hang was itself verified by hanging.

## Departures from the ticket worth knowing about

**Exit 0 exists.** Spec issue #73 asks the Run to exit non-zero "whenever any
bound fired", and separately that a Completion Promise never end a Run. Together
those mean every Run ends on a bound and every Run exits non-zero, which makes
the exit code carry nothing. ADR 0007 records the split: which bound ended the
Run is reported separately and always, on stdout and in the Progress Log; the
exit code answers only whether anything went wrong. Reaching the cap with nothing
killed and nothing failed is the planned end and exits 0.

**The Iteration wall clock is clamped to what is left of the Run's.** Checking
the Run clock only at the Iteration boundary would let a Run overshoot its cap by
a whole Iteration timeout - fifteen unattended minutes at the shipped values.

**A faulting Iteration's agent output is quoted into the Progress Log**, last
twenty lines, only on a fault. "The agent exited 3" with nothing behind it is not
a diagnosis, and a Run nobody watched is diagnosed from the file it leaves
behind.

**The `ANTHROPIC_API_KEY` guard lives in `agents/claude.sh`, not in `run.sh`.**
Spec issue #73 asks for it to be asserted at Run start. It is a property of one
agent - Claude Code prefers that variable over the subscription login - and
putting it in the loop would make the loop know something about a vendor that
ADR 0004 exists to keep out of it. It fires before the first turn of every
Iteration, which is strictly more often than Run start.

## What is still missing before a Run can happen

- **Nothing puts this directory on `loop.etadventures.com`.** `ansible/loop.yml`
  installs the box's packages, account and Execution Boundary and deliberately
  stops there. The first end-to-end Run (#83) needs the scripts on the box.
- **Nothing runs the agent inside the Execution Boundary yet.** That is a change
  to `agents/claude.sh` and to nothing else, which is the point of the seam.
- **Nothing seeds the Plan** (#82); `run.sh` refuses to start without one.
- **Nothing pushes or opens a pull request** (#83).

Also still open from #78, recorded on the ticket and not addressed here:
`sandboxd` does not survive a reboot, and the box's egress posture is `balanced`
- 193 allowed hosts - which is wide enough to exfiltrate a repository through.
Neither is in this ticket's acceptance criteria; both must be settled before a
Run is genuinely left alone.
