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
: "${LOOP_MAX_TURNS:=40}"

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

# The string an agent emits to claim the work is finished. Recorded in the
# Progress Log as advisory evidence and never terminal on its own: nothing
# verifies it and Pocock documents his agent lying with it.
: "${LOOP_COMPLETION_PROMISE:=LOOP: WORK COMPLETE}"

# How much of a faulting Iteration's agent output is quoted into the Progress
# Log. Not a bound - a Run nobody watched has to be diagnosable from the file it
# leaves behind.
: "${LOOP_FAULT_OUTPUT_LINES:=20}"

# --- Where the Run's state lives, relative to the repository ----------------
: "${LOOP_PLAN_PATH:=PLAN.md}"
: "${LOOP_PROGRESS_LOG_PATH:=PROGRESS.md}"

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
