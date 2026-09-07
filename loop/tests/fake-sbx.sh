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

# --- The guest's filesystem, and who may write where ------------------------
#
# Added for #268. Until it existed, every `exec` here succeeded, so the offline
# suite could not tell a placement the guest permits from one it refuses - and
# `agents/claude.sh` placed the signing key at its HOST path, which the guest
# permits only by accident.
#
# The accident, measured on loop.etadventures.com against `loop-php:1` on
# 2026-08-30: `sbx` synthesises the parent directories of a bind mount and owns
# them by depth. A workspace one level under $HOME (`/home/loop/tourbot`) leaves
# `/home/loop` as `agent:agent 755` and `mkdir -p /home/loop/.ssh` works; two
# levels (`/home/loop/work/tourbot`) leaves it `root:root 755` and the same call
# is "mkdir: Permission denied".
#
# So this models the GUARANTEE rather than the measurement: writable is the
# guest's own home and the workspace mount, both of which `sbx` owns to `agent`
# at every depth. It deliberately refuses the depth-one case too, even though a
# real guest would allow it - modelling the luck is what let this hide, and an
# adapter that needs the luck is the bug. `sudo` reaches around it, as the Grok
# adapter's `put` does and as a real guest's passwordless sudo would.
guest_home=/home/agent
workspace_file="${state}.workspace"

# The workspace the boundary was built around, off the create that built it.
#   create --quiet --name <name> -t <template> <agent> <workspace> [<mount>:ro]
record_workspace() {
    local -a rest=()
    while (($#)); do
        case "$1" in
            create | --quiet) shift ;;
            --name | -t) shift 2 ;;
            *)
                rest+=("$1")
                shift
                ;;
        esac
    done
    printf '%s\n' "${rest[1]:-}" >"${workspace_file}"
}

granted_file="${state}.granted"

guest_writable() {
    local path="$1" workspace="" granted
    [[ -f ${workspace_file} ]] && workspace="$(cat -- "${workspace_file}")"
    case "${path}" in
        "${guest_home}" | "${guest_home}"/*) return 0 ;;
    esac
    if [[ -n ${workspace} ]]; then
        case "${path}" in
            "${workspace}" | "${workspace}"/*) return 0 ;;
        esac
    fi
    # And anywhere an adapter has already reached around it with `sudo` and
    # handed the directory to the guest account.
    if [[ -f ${granted_file} ]]; then
        while IFS= read -r granted; do
            [[ -n ${granted} ]] || continue
            case "${path}" in
                "${granted}" | "${granted}"/*) return 0 ;;
            esac
        done <"${granted_file}"
    fi
    return 1
}

# Root inside the guest, which may write anywhere. The Grok adapter's `put`
# takes this route on purpose; this one recognises it so that adapter's suite
# still passes through the same filesystem model.
is_privileged() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == sudo ]] && return 0
    done
    return 1
}

# What the boundary-preparation `exec`s would do to the guest's filesystem, in
# the guest's own words. `mkdir -p <dir>` and `chmod <mode> <path>`; anything
# else is left alone, because nothing else in either adapter writes a path.
refuse_unwritable_write() {
    local -a argv=("$@")
    local i target=""
    if is_privileged "$@"; then
        # `sudo chown agent:agent <dir>` is how an adapter buys the write it
        # did not have; from here on that directory is the guest account's.
        for ((i = 0; i < ${#argv[@]}; i++)); do
            [[ ${argv[i]} == chown ]] || continue
            printf '%s\n' "${argv[${#argv[@]} - 1]}" >>"${granted_file}"
        done
        return 0
    fi
    for ((i = 0; i < ${#argv[@]}; i++)); do
        case "${argv[i]}" in
            mkdir) target="${argv[${#argv[@]} - 1]}" ;;
            chmod) target="${argv[${#argv[@]} - 1]}" ;;
            *) continue ;;
        esac
        guest_writable "${target}" && return 0
        printf '%s: Permission denied\n' "${argv[i]}" >&2
        exit 1
    done
    return 0
}

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

is_codex() {
    local arg
    for arg in "$@"; do
        [[ ${arg} == codex || ${arg} == */codex ]] && return 0
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
        record_workspace "$@"
        ;;
    cp)
        # What actually crossed the boundary, not just that a copy happened.
        # `.calls` records the paths, and paths are the same whether the file
        # behind them was renewed or stale - so a test that the renewed
        # credential is the one that goes in (#260) has nothing to assert
        # against without this.
        #
        # Keyed by destination, because more than one file crosses in an
        # Iteration - the credential, the signing key, the git identity - and a
        # flat append would let a test asserting something about the credential
        # be satisfied by the contents of the signing key. `$3` is
        # `<sandbox>:<path>`; the path after the colon is what names it.
        # A copy into a directory the guest account cannot write fails as the
        # real one would, so a placement that skipped the mkdir is not quietly
        # counted as having landed.
        if ! guest_writable "$(dirname -- "${3##*:}")"; then
            printf 'cp: Permission denied\n' >&2
            exit 1
        fi
        if [[ -f ${2:-} ]]; then
            printf '%s\n' "$(cat -- "$2")" >>"${state}.copied.$(basename -- "${3##*:}")"
        fi
        ;;
    exec)
        refuse_unwritable_write "$@"
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
                missing-host)
                    printf 'fake-sbx: connection to api.openai.com:443 failed: Blocked by network policy\n' >&2
                    exit 1
                    ;;
                # What each vendor does on reaching its own turn bound: a
                # message, and the same exit status a broken invocation uses.
                # Telling the two apart is the adapter's job (ADR 0004), which is
                # what this exercises - and the words are not the same, which is
                # why the knowledge lives there rather than in contract.sh.
                turn-bound)
                    if is_codex "$@"; then
                        printf 'Error: turn limit reached\n' >&2
                    elif is_grok "$@"; then
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
