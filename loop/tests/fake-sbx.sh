#!/usr/bin/env bash
#
# The scripted fake Execution Boundary, for the agent adapter's offline suite
# (#83).
#
# `sbx` reached through LOOP_SBX_COMMAND is the seam. Pointing it here lets the
# suite assert what an Iteration would do to the boundary - that a microVM is
# created for it, that what goes inside is what should and nothing that should
# not, and that it is destroyed afterwards even when things go wrong - without a
# hypervisor, an image pull, or a model.
#
#   FAKE_SBX_STATE       a scratch path. Every invocation is appended to
#                        "<path>.calls" as one line, argv joined by spaces.
#   FAKE_SBX_BEHAVIOUR   ok | create-fails | agent-fails | agent-hangs |
#                        turn-bound
#
# `exec` is the one that has to do something rather than record something: the
# real one runs the agent, so this one carries the agent's exit status.

set -euo pipefail

state="${FAKE_SBX_STATE:?FAKE_SBX_STATE must be set}"
printf '%s\n' "$*" >>"${state}.calls"

# The agent's own invocation is the one that carries a behaviour, and it is
# distinguished from the boundary preparation `exec`s - the mkdir and chmod that
# place credentials - by the command it is asked to run.
is_the_agent() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == claude ]] && return 0
    done
    return 1
}

case "${1:-}" in
    create)
        if [[ ${FAKE_SBX_BEHAVIOUR:-ok} == create-fails ]]; then
            printf 'fake-sbx: no boundary for you\n' >&2
            exit 1
        fi
        ;;
    exec)
        if is_the_agent "$@"; then
            case "${FAKE_SBX_BEHAVIOUR:-ok}" in
                agent-fails) exit 3 ;;
                agent-hangs) exec sleep 300 ;;
                # What Claude Code does on reaching --max-turns: a message, and
                # the same exit status a broken invocation uses. Telling the two
                # apart is the adapter's job, which is what this exercises.
                turn-bound)
                    printf 'Error: Reached max turns (40)\n' >&2
                    exit 1
                    ;;
            esac
        fi
        ;;
esac

# Explicit, because the last thing above is a conditional: a `case` whose branch
# ends in a test that was false exits non-zero, and every boundary preparation
# step would look like a failure.
exit 0
