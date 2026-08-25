#!/usr/bin/env bash
#
# The scripted fake Execution Boundary, for the agent adapters' offline suites
# (#83, #84).
#
# `sbx` reached through LOOP_SBX_COMMAND is the seam. Pointing it here lets the
# suites assert what an Iteration would do to the boundary - that a microVM is
# created for it, that what goes inside is what should and nothing that should
# not, and that it is destroyed afterwards even when things go wrong - without a
# hypervisor, an image pull, or a model.
#
# It serves BOTH adapters, because both drive the same three verbs and the
# things worth asserting are the same for each. What differs is what the guest
# runs: `claude`, provided by the template, versus a `grok` installed inside at
# a pin. Both are recognised below, and the turn bound speaks each vendor's own
# words - which is the one thing the two adapters must not share.
#
#   FAKE_SBX_STATE       a scratch path. Every invocation is appended to
#                        "<path>.calls" as one line, argv joined by spaces.
#   FAKE_SBX_BEHAVIOUR   ok | create-fails | agent-fails | agent-hangs |
#                        turn-bound | install-fails
#   FAKE_GROK_VERSION    what the guest's agent reports for --version. Defaults
#                        to whatever the adapter pinned, so the happy path needs
#                        no fixture; set it to something else to stand for an
#                        installer that ignored the pin.
#
# `exec` is the one that has to do something rather than record something: the
# real one runs what it is given, so this one carries that command's status.

set -euo pipefail

state="${FAKE_SBX_STATE:?FAKE_SBX_STATE must be set}"
printf '%s\n' "$*" >>"${state}.calls"

# The agent's own invocation, told apart from the boundary preparation `exec`s -
# the mkdir and chmod that place credentials, the install, the version check -
# by the turn bound, which is the one argument only the agent run carries.
is_the_agent() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == --max-turns ]] && return 0
    done
    return 1
}

# Which vendor is being impersonated, so the turn bound can speak its words.
# By the guest binary's path, which is the only thing in the invocation that
# names one.
is_grok() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == */grok ]] && return 0
    done
    return 1
}

# The installer the Grok adapter pipes into a shell inside the guest.
is_the_install() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == *install.sh* ]] && return 0
    done
    return 1
}

is_the_version_check() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == --version ]] && return 0
    done
    return 1
}

# The pin the adapter asked the installer for, recovered from the install
# invocation the suite recorded a moment ago. Reported by the version check, so
# a happy-path test needs no fixture and a test about the pin can override it -
# rather than a hardcoded number here that would have to be edited in step with
# the adapter every time the pin moved.
installed_version() {
    if [[ -n ${FAKE_GROK_VERSION:-} ]]; then
        printf '%s' "${FAKE_GROK_VERSION}"
        return 0
    fi
    sed -n 's/.*bash -s \([0-9][0-9.]*\).*/\1/p' "${state}.calls" | tail -n1
}

case "${1:-}" in
    create)
        if [[ ${FAKE_SBX_BEHAVIOUR:-ok} == create-fails ]]; then
            printf 'fake-sbx: no boundary for you\n' >&2
            exit 1
        fi
        ;;
    exec)
        if is_the_install "$@"; then
            if [[ ${FAKE_SBX_BEHAVIOUR:-ok} == install-fails ]]; then
                printf 'fake-sbx: curl: (7) Failed to connect to x.ai port 443\n' >&2
                exit 1
            fi
            exit 0
        fi
        if is_the_version_check "$@"; then
            printf 'grok %s (5115b46bc9)\n' "$(installed_version)"
            exit 0
        fi
        if is_the_agent "$@"; then
            case "${FAKE_SBX_BEHAVIOUR:-ok}" in
                agent-fails) exit 3 ;;
                agent-hangs) exec sleep 300 ;;
                # What each vendor does on reaching its own turn bound: a
                # message, and the same exit status a broken invocation uses.
                # Telling the two apart is the adapter's job (ADR 0004), which is
                # what this exercises - and the words are not the same, which is
                # why the knowledge lives there rather than in contract.sh.
                turn-bound)
                    if is_grok "$@"; then
                        printf 'Max turns reached\n\nError: max turns reached\n' >&2
                    else
                        printf 'Error: Reached max turns (40)\n' >&2
                    fi
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
