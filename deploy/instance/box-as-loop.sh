#!/usr/bin/env bash
#
# Instance-owned dispatch wrapper: run the box command as the Run account.
#
#   box-as-loop.sh <branch> <task-ref>
#
# Lives at /etc/tracewake/box-as-loop.sh on the Host (NOT in the deployed
# product tree), selected by SELECTOR_BOX_COMMAND in tracewake.env. A copy is
# kept in this repository at deploy/instance/box-as-loop.sh; the two must
# stay byte-identical (compare with `cmp` after any edit).
#
# Why this exists (issue #62): the Cycle runs as conductor - the operator's
# identity, holding the tracker credential the Handover checks need - while
# a Run executes as loop, the unprivileged account holding the target token,
# the agent subscription and the box checkouts. box-sources/local.sh runs as
# its invoker and does not switch Unix users, so dispatching through it
# directly leaves the Run unable to read its token or write its checkout.
#
# What it does: when already the Run account, exec local.sh directly. Else
# re-exec it under `sudo -u <run-account> -g <run-account> -H`, so the Run
# inherits loop's home (agent credential, token default) and nothing of
# conductor's environment except what the sudoers env_keep allows (the
# LOOP_*, SELECTOR_* and TRACEWAKE_* names - notably NOT GH_TOKEN or any
# metered key, which sudo's env_reset strips).
#
# What it does NOT do, on purpose:
#
# - No SETENV in sudoers: conductor cannot smuggle arbitrary environment
#   (LD_PRELOAD and friends stay stripped) past the transition.
# - No fallback to running as the invoker: without sudo the dispatch fails
#   loudly here instead of starting a Run as the wrong account.
# - No mail involvement: the SMTP secret (/etc/tracewake/smtp-password) is
#   conductor-only, and this transition runs TOWARD loop, so a Run that
#   reaches for the relay finds the secret unreadable (story 20).
#
# The credential inventory gate still runs - inside local.sh, as loop,
# against loop's home - which is what assert-credentials.sh asks for
# ("run it on the box, as the account a Run executes as").
#
# Sudoers companion (/etc/sudoers.d/conductor-loop, validated with visudo):
#
#   Defaults:conductor env_keep += "LOOP_*"
#   Defaults:conductor env_keep += "SELECTOR_*"
#   Defaults:conductor env_keep += "TRACEWAKE_*"
#   conductor ALL=(loop:loop) NOPASSWD: /srv/tracewake/selector/box-sources/local.sh *
#
# Configuration, all environment:
#
#   SELECTOR_BOX_USER   the Run account (default loop)
#
# Exit codes: local.sh's own, or 1 when the transition itself is unavailable.

set -euo pipefail

die() {
    printf 'box-as-loop.sh: %s\n' "$*" >&2
    exit 1
}

branch="${1:?usage: box-as-loop.sh <branch> <task-ref>}"
task_ref="${2:?usage: box-as-loop.sh <branch> <task-ref>}"

box_user="${SELECTOR_BOX_USER:-loop}"
# Validated before reaching sudo: anything else fails closed at the
# sudoers rule (conductor-loop pins loop:loop), but the script's own error
# says what was wrong instead of sudo's.
[[ "${box_user}" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "unknown Run account '${box_user}'"
local_sh="/srv/tracewake/selector/box-sources/local.sh"

[[ -x "${local_sh}" ]] || die "local.sh not executable or not found at ${local_sh}"

if [[ "$(id -un)" == "${box_user}" ]]; then
    exec "${local_sh}" "${branch}" "${task_ref}"
fi

command -v sudo >/dev/null 2>&1 || die "sudo is required to reach the Run account ${box_user}"
exec sudo -u "${box_user}" -g "${box_user}" -H -- "${local_sh}" "${branch}" "${task_ref}"
