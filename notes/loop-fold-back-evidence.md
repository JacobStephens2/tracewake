# Folding the Termination Contract back to the ETA Factory

Issue #85, the last of the Loop spike's tickets. The spike was authorized on the
argument that what it learned about ending an unattended run would fold back to
the ETA Factory, which has the same problem and a 300-second hard kill standing
in for a solution. This is where that argument was paid.

**It is a decision record, not an integration.** No code path connects the two
systems and none is proposed. What crossed is a shape, a set of corrected
numbers, and a list of things that turned out not to be true.

## What was written, and where

Following the Factory's own governance rather than this repository's. The
Factory's constitution forbids an agent editing its own policy except through a
human-reviewed PR, and the decision log is policy - so this repository's
standing authorization to commit and push does not reach there.

| What | Where |
| --- | --- |
| Ruling | **A155**, `docs/decision-log.md` in `Educational-Travel-Adventures/factory` |
| Evidence page | `docs/termination-contract.md`, indexed in `docs/README.md` |
| Vocabulary | a Run termination section in that repository's `CONTEXT.md`, each term naming the Loop term it is *not* |
| Delivered as | [factory#328](https://github.com/Educational-Travel-Adventures/factory/pull/328), a draft PR |
| Status | **A proposal until Jacob merges it.** The ruling's Source column says so on its face |

A155 rather than A154: `research/model-rates/2026-08-24-openai-xai-token-rates.md`
in that repository already cites A154 as an in-flight ruling whose row is not in
the log yet, and A152 is a gap that was left alone.

## What the ruling decides

The clauses are A155's to state and are not restated here. In summary: the five
bounds are adopted **as roles, not as numbers**, at the Factory's units -
attempts within a request, of which the Factory has one; the 300 seconds stays
and is read as the attempt bound it has always been, with the rename of
`run_max_seconds` directed as a follow-up rather than made in a decision record;
raising it is refused, because one attempt at most 300 seconds with no residue
is what makes teardown and the `BOUNDARY_TAINTED` marker mean anything; a
completion claim is telemetry and the exit code and the ending bound are two
signals, which is ADR 0007 in the Factory's vocabulary; and a green
deterministic gate is coverage rather than correctness.

The numbers explicitly do not transfer. Three of five bounds fired across four
Runs, one was corrected, and the two wall clocks have never been reached by a
real Run, so the Factory prices its own values against its own attempts.

## The tension the ticket asked to be addressed directly

Whether the Factory should raise its ceiling or run many short runs. The ruling
answers *many short attempts* and then says what that costs, which is the half
that matters: the Loop's short attempts work because a Plan and a Progress Log
on disk carry what a fresh process has forgotten. Lift the shape without the
state and every attempt after the first starts blind, the no-progress bound
fires on the technique rather than on the task, and the fold-back would have
handed the Factory a worse version of what it already has.

So the recommendation is conditional, and the condition is named: A43's artifact
store is where per-request state belongs, and until it carries some, the
Factory's one attempt plus one repair loop is the honest configuration.

## What crossed that this project would rather not have learned

The ticket asked for the findings that did not survive contact, not only the
ones that worked, and they are the more useful half. All are in the Factory's
evidence page with their consequences; in short:

- **The permission mode is not the boundary.** `acceptEdits` auto-approves file
  edits and still gates Bash, so no Iteration could `git add` its own work and
  the technique could not work at all under it.
- **Backpressure an Iteration cannot reach is not backpressure.** The
  completeness check was named in the Plan and sat outside the sandbox's mounted
  workspace for two Runs.
- **Host-side credential injection holds for neither agent**, for two
  independent reasons.
- **The metered-key guard is five names plus a config-file door**, not one
  environment variable.
- **An egress allowlist read off a binary was short by a host**, and reading the
  *deny* log found one nobody had declared.
- **A property belongs to an agent until two agents have shown it.** Both agents
  exit 1 on the turn bound; only the message tells it apart from a failure, and
  the messages differ.
- **A Run still does not tell the operator it has finished.** Open here, and
  named as open there.

## What this does not establish

- **That the Factory will adopt any of it.** The PR is a draft and unmerged; a
  ruling is Jacob's to accept.
- **Anything about spend.** Both agents bill against subscriptions. Nothing here
  measures cost, which is why the Contract is the whole cost control on both
  sides.
