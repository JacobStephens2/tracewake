#!/usr/bin/env bash
#
# The conductor-to-Run transition, sourced rather than restated.
#
# A local box read runs in the cycle's process, as the instance's
# unprivileged user - while the answers live with the Run account: the model
# credential's expiry files under its home, the checkout it writes, the
# adapter it executes. A read taken as the wrong account would not fail; it
# would silently report `not reported` for the one fact the card headlines,
# which is the reassurance rather than the check. So a local read becomes
# the Run account first, through the same `sudo -u` transition
# box-as-loop.sh makes for a dispatch, and reads nothing until it has.
#
# Usage, at the top of the script after its own argument handling:
#
#   become_run_user ${1+"$@"}
#
# When already the Run account this returns and the script reads directly.
# Otherwise it re-execs the calling script under `sudo -u <account> -g
# <account> -H`, so the read inherits the Run account's home and nothing of
# the invoker's environment except what the sudoers env_keep allows. The
# `${1+"$@"}` is the empty-argv guard older bash needs under `set -u`.
#
# Sudoers companion (/etc/sudoers.d/, validated with visudo), beside the
# conductor-loop rule box-as-loop.sh documents - the env_keep lines are the
# same ones, and each local read adds one command line:
#
#   Defaults:conductor env_keep += "LOOP_*"
#   Defaults:conductor env_keep += "SELECTOR_*"
#   Defaults:conductor env_keep += "TRACEWAKE_*"
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/facts-local.sh
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/progress-local.sh *
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/microvms-local.sh
#
# What it does NOT do, on purpose - the same three refusals as box-as-loop.sh:
# no SETENV, no fallback to reading as the invoker, no mail involvement.
become_run_user() {
    local script
    script="$(basename -- "$0")"
    local box_user="${SELECTOR_BOX_USER:-loop}"
    # Validated before reaching sudo: anything else fails closed at the
    # sudoers rule, but the script's own error says what was wrong instead
    # of sudo's.
    [[ "${box_user}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
        printf '%s: unknown Run account %s\n' "${script}" "${box_user}" >&2
        exit 1
    }
    if [[ "$(id -un)" != "${box_user}" ]]; then
        command -v sudo >/dev/null 2>&1 || {
            printf '%s: sudo is required to read as the Run account %s\n' \
                "${script}" "${box_user}" >&2
            exit 1
        }
        exec sudo -u "${box_user}" -g "${box_user}" -H -- "$0" ${1+"$@"}
    fi
}
