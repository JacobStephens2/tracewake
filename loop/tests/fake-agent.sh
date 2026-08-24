#!/usr/bin/env bash
#
# The scripted fake agent - the offline suite's whole reason for existing.
#
#   fake-agent.sh <prompt-file> <max-turns>
#
# Satisfies exactly the contract agents/claude.sh satisfies, because ADR 0004
# made the agent one substitutable command and that substitution point is the
# test seam. Pointing LOOP_AGENT_COMMAND here lets the suite drive the REAL
# entry point through every branch of the Termination Contract with no model, no
# network, and no spend.
#
# Driven by two environment variables:
#
#   FAKE_AGENT_BEHAVIOURS  whitespace-separated, one per Iteration. The last
#                          entry repeats if the Run outlasts the list.
#   FAKE_AGENT_STATE       a scratch path. The invocation counter lives here;
#                          "<path>.turns" collects the turn bound each Iteration
#                          was given, so a test can assert the bound reached the
#                          agent rather than assert on the Loop's internals.
#
# Behaviours:
#
#   commit        do work, log a decision and a blocker, commit
#   noop          do nothing at all, exit 0
#   dirty         change a file and do NOT commit it
#   promise       commit, and record the Completion Promise in the Progress Log
#   promise-noop  emit the Completion Promise on stdout and commit nothing
#   hang          outlive the Iteration's wall clock
#   fail          exit 3 the way a broken invocation does
#   slow:N        take N seconds, then commit

set -euo pipefail

prompt_file="${1:?usage: fake-agent.sh <prompt-file> <max-turns>}"
max_turns="${2:?usage: fake-agent.sh <prompt-file> <max-turns>}"
state="${FAKE_AGENT_STATE:?FAKE_AGENT_STATE must be set}"
promise="${LOOP_COMPLETION_PROMISE:-LOOP: WORK COMPLETE}"

read -r -a behaviours <<<"${FAKE_AGENT_BEHAVIOURS:-commit}"

count=0
if [[ -f ${state} ]]; then
    count="$(cat -- "${state}")"
fi
count=$((count + 1))
printf '%s\n' "${count}" >"${state}"
printf '%s\n' "${max_turns}" >>"${state}.turns"
# The prompt the Loop handed this Iteration, kept so a test can assert what the
# Loop asked for rather than what this fake happens to do about it.
cp -- "${prompt_file}" "${state}.prompt"

index=$((count - 1))
if ((index >= ${#behaviours[@]})); then
    index=$((${#behaviours[@]} - 1))
fi
behaviour="${behaviours[index]}"

# Proves to a test that the Iteration was given a prompt at all, and that it was
# a fresh process handed the Plan by file rather than a carried-over context.
printf 'fake-agent: iteration %d, behaviour %s, turns %s, prompt %d bytes\n' \
    "${count}" "${behaviour}" "${max_turns}" "$(wc -c <"${prompt_file}")"

# The three committing behaviours differ only in their commit message, so the
# work itself lives in one place.
do_work() {
    printf 'work from iteration %d\n' "${count}" >>work.txt
    log_work
    git add -A
    git commit --quiet --message "$1"
}

log_work() {
    cat >>PROGRESS.md <<ENTRY

Agent, Iteration ${count}: did task ${count}.
Decided: task ${count} takes the simple approach, because the alternative needs
a decision the operator has not made.
Blocked: nothing.
ENTRY
}

case "${behaviour}" in
    commit)
        do_work "Agent: task ${count}"
        ;;
    noop) ;;
    dirty)
        printf 'uncommitted from iteration %d\n' "${count}" >>work.txt
        ;;
    promise)
        printf '\n%s\n' "${promise}" >>PROGRESS.md
        do_work "Agent: task ${count}, and the work is done"
        ;;
    promise-noop)
        printf '%s\n' "${promise}"
        ;;
    hang)
        exec sleep 300
        ;;
    fail)
        printf 'fake-agent: pretending the invocation is broken\n' >&2
        exit 3
        ;;
    slow:*)
        sleep "${behaviour#slow:}"
        do_work "Agent: task ${count}, slowly"
        ;;
    *)
        printf 'fake-agent: unknown behaviour %s\n' "${behaviour}" >&2
        exit 64
        ;;
esac
