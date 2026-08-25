# The Termination Contract, and the agent it binds.
#
# Sourced by run.sh. Nothing here executes work; this file exists so that the
# five bounds live in ONE place rather than scattered through the loop, because
# spec issue #73 says these numbers are first guesses whose correction is the
# first Run's most valuable output. Correcting a guess should be editing one
# line in one file, not grepping a script for a magic number.
#
# Every value is `: "${VAR:=default}"` so the environment can override it. That
# is not a convenience knob: it is the seam the offline suite drives the real
# entry point through, with a five-minute Iteration timeout collapsed to two
# seconds so the whole Contract is exercised without a model, a network, or a
# minute of waiting.
#
# shellcheck shell=bash

# --- The five bounds -------------------------------------------------------
#
# Four of these exist in no published Ralph source. Huntley's loop is unbounded;
# Pocock's has an iteration cap he guesses at and a Completion Promise he
# documents lying. The reasoning for each, and why four independent bounds are
# cheaper than arguing about which single one would be sufficient, is in spec
# issue #73 - the short version is that the credential is a subscription, so the
# Contract is the entire cost control.

# How many Iterations a Run may execute.
: "${LOOP_MAX_ITERATIONS:=5}"

# How long one Iteration may run before it is killed. A hung agent process
# stalling the whole Run is the failure mode both published sources leave open.
: "${LOOP_ITERATION_TIMEOUT_SECONDS:=900}"   # 15 minutes

# How many turns the agent may take inside one Iteration. Cuts off an agent that
# starts thrashing *inside* the Iteration rather than at its edge.
#
# Was 40, which the first Run found too small (#83): Iteration 1 spent all forty
# reading a 128-occurrence classification task, wrote a file, and ran out before
# it could commit - so a whole Iteration's work landed as an uncommitted diff and
# a No-op. 100 is the wall clock's answer rather than a guess: forty turns took
# about four and a half minutes, so a hundred is roughly eleven, which leaves
# headroom under the fifteen-minute Iteration timeout. The two bounds should not
# fire at the same moment; whichever bites first should bite alone, or a Run
# cannot say which one it was.
: "${LOOP_MAX_TURNS:=100}"

# How long the whole Run may last. Ends a Run where every Iteration runs long
# before the iteration cap would.
: "${LOOP_RUN_TIMEOUT_SECONDS:=5400}"        # 90 minutes

# How many consecutive No-op Iterations abort the Run. A No-op Iteration is one
# after which the repository head is unchanged. Huntley names an agent stuck
# re-reading the same task as the technique's Achilles' heel; this is the bound
# that notices.
: "${LOOP_MAX_CONSECUTIVE_NOOPS:=2}"

# --- The agent -------------------------------------------------------------
#
# ADR 0004: the agent is one substitutable command. Its contract is two
# positional arguments - a file holding a single-turn prompt, and a turn bound -
# executed with the repository as its working directory, exiting non-zero on
# failure. Changing vendor is changing this one line, which is also why the
# offline suite can point it at a scripted fake and drive the real Run.
: "${LOOP_AGENT_COMMAND:=}"

# The exit status an agent command uses to say THE TURN BOUND FIRED, as opposed
# to the agent having failed on its own. They are different events and the first
# Run proved it matters: Claude Code exits 1 on reaching --max-turns, which the
# Loop read as a broken invocation and used to end the whole Run at Iteration 1.
#
# The turn bound is one of the Contract's five. Reaching it ends an ITERATION,
# exactly as the Iteration wall clock does - it is not a fault in the agent and
# it is not a reason to stop. Which vendor message means it is the agent
# adapter's to know (ADR 0004); this number is the vocabulary the two share.
#
# 33 rather than something lower: 1 and 2 are what any broken command exits with,
# 124 and 137 are `timeout`'s, and 64 upwards are conventionally usage errors. A
# code an agent is unlikely to pick for itself is the point.
: "${LOOP_AGENT_TURN_BOUND_EXIT:=33}"

# The string an agent emits to claim the work is finished. Recorded in the
# Progress Log as advisory evidence and never terminal on its own: nothing
# verifies it and Pocock documents his agent lying with it.
: "${LOOP_COMPLETION_PROMISE:=LOOP: WORK COMPLETE}"

# How much of a faulting Iteration's agent output is quoted into the Progress
# Log. Not a bound - a Run nobody watched has to be diagnosable from the file it
# leaves behind.
: "${LOOP_FAULT_OUTPUT_LINES:=20}"

# --- The task source -------------------------------------------------------
#
# How seed-run.sh reaches the one task the operator chose. It is one
# substitutable command for the same reason the agent is (ADR 0004) and with the
# same second benefit: the offline suite drives the real seed step through a
# scripted fake, so the suite needs no GitHub token and no network.
#
# Its contract is two positional arguments - the task's repository as
# owner/name, and the task's number - printing the task on stdout as JSON with
# number, title, url, state and body, and exiting non-zero on failure.
#
# Fetching ONE task the operator chose is a setup step, not issue intake. What
# follows from that, and why building real intake would invalidate ADR 0003's
# reasoning, is ADR 0010. Resolved in seed-run.sh rather than here, like the
# agent command, because the default is a path relative to this checkout.
: "${LOOP_TASK_SOURCE_COMMAND:=}"

# --- Where the Run's state lives, relative to the repository ----------------
: "${LOOP_PLAN_PATH:=PLAN.md}"
: "${LOOP_PROGRESS_LOG_PATH:=PROGRESS.md}"

# The headings run.sh writes into the Progress Log, declared here because
# seed-run.sh reads them: a Progress Log holding a Run is what stops a re-seed
# from discarding one. Two scripts agreeing on a literal string by both spelling
# it out is a seam that breaks silently - the guard would simply stop finding
# anything and the seed would overwrite a Run's record without a word.
: "${LOOP_RUN_HEADING:=## Run started}"
: "${LOOP_ITERATION_HEADING:=### Iteration}"

# Render the Contract for the Progress Log. Written at Run start so that reading
# the log afterwards tells you which bound fired and what it was set to, without
# needing the version of this file that was current at the time.
loop_contract_summary() {
    cat <<SUMMARY
- Iterations per Run: ${LOOP_MAX_ITERATIONS}
- Iteration wall clock: ${LOOP_ITERATION_TIMEOUT_SECONDS}s
- Turns per Iteration: ${LOOP_MAX_TURNS}
- Run wall clock: ${LOOP_RUN_TIMEOUT_SECONDS}s
- Consecutive No-op Iterations that abort: ${LOOP_MAX_CONSECUTIVE_NOOPS}
- Completion Promise: recorded, never terminal
- Agent command: ${LOOP_AGENT_COMMAND}
SUMMARY
}
