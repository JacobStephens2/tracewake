#!/usr/bin/env bash
#
# The box surface, local: execute a Run directly on the controller without SSH.
#
#   local.sh <branch> <task-ref>
#
# Sibling to box-sources/ssh.sh for single-host instances (ADR 0019). Prints the
# Run's own stdout report - the LOOP_RUN_* block run.sh ends with - and exits
# with the Run's exit code.
#
# It blocks for the length of the Run, capturing the outcome exactly as ssh.sh
# does, with no SSH hop.
#
# Gated by the credential inventory (ADR 0019). The controller may run Runs on
# itself only while it passes the exact same check the separate box passes:
# `loop/assert-credentials.sh`. If any forbidden credential (vault token,
# database password, fleet SSH key, DigitalOcean token, metered API key) or
# missing required credential is reported, dispatch is refused immediately,
# naming the violation. A machine holding production credentials will never
# pass this check, which is the point.
#
# Substitutable (ADR 0004): a box reached locally is a different script here
# and no change to dispatch.py, driven through the exact same contract.
#
# Configuration, all environment:
#
#   SELECTOR_BOX_REPO         required   the target's checkout on this machine
#   SELECTOR_BOX_LOOP         optional   the Loop directory (default ../../loop)
#   LOOP_GITHUB_TOKEN_FILE    optional   the target's repository token
#   LOOP_GUEST_TEMPLATE       optional   the target's guest image
#   SELECTOR_BOX_HOME         optional   home directory for credential check (default $HOME)
#   SELECTOR_BOX_SBX          optional   sbx command for credential check
#   SELECTOR_BOX_SYSTEM_ROOT  optional   system root prefix for testing
#   SELECTOR_ASSERT_CREDENTIALS_COMMAND optional override for credential check
#
# Exit codes: the Run's own, 2 when credential check fails with violations,
# or 1 when no Run was started.

set -euo pipefail

die() {
    printf 'box-sources/local.sh: %s\n' "$*" >&2
    exit 1
}

# The shared refusal. Sourced rather than restated: an instance value has no
# default anywhere in the product, and the sentence that says so belongs in
# one place (issue #3).
# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"

branch="${1:?usage: local.sh <branch> <task-ref>}"
task_ref="${2:?usage: local.sh <branch> <task-ref>}"

require SELECTOR_BOX_REPO
box_repo="${SELECTOR_BOX_REPO}"
box_loop="${SELECTOR_BOX_LOOP:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../loop" && pwd)}"

# The credential inventory gate (ADR 0019).
# Runs assert-credentials.sh before touching git or starting the Run.
assert_script="${SELECTOR_ASSERT_CREDENTIALS_COMMAND:-${box_loop}/assert-credentials.sh}"
[[ -x "${assert_script}" ]] || die "assert-credentials.sh not executable or not found at ${assert_script}"

local_home="${SELECTOR_BOX_HOME:-${HOME:?HOME is required}}"
assert_args=(--home "${local_home}")

if [[ -n "${LOOP_GITHUB_TOKEN_FILE:-}" ]]; then
    assert_args+=(--token-file "${LOOP_GITHUB_TOKEN_FILE}")
fi
if [[ -n "${TRACEWAKE_TARGETS_FILE:-}" && -f "${TRACEWAKE_TARGETS_FILE}" ]]; then
    assert_args+=(--targets "${TRACEWAKE_TARGETS_FILE}")
fi
if [[ -n "${SELECTOR_BOX_SBX:-}" ]]; then
    assert_args+=(--sbx "${SELECTOR_BOX_SBX}")
fi
if [[ -n "${SELECTOR_BOX_SYSTEM_ROOT:-}" ]]; then
    assert_args+=(--system-root "${SELECTOR_BOX_SYSTEM_ROOT}")
fi

cred_output=""
cred_status=0
cred_output="$("${assert_script}" "${assert_args[@]}" 2>&1)" || cred_status=$?

if (( cred_status == 2 )); then
    printf 'box-sources/local.sh: credential inventory reported violations:\n%s\n' "${cred_output}" >&2
    exit 2
elif (( cred_status != 0 )); then
    printf 'box-sources/local.sh: assert-credentials.sh failed to run (exit %d):\n%s\n' "${cred_status}" "${cred_output}" >&2
    exit 1
fi

command -v git >/dev/null 2>&1 || die "git is required on the local machine"
[[ -x "${box_loop}/run.sh" ]] || die "run.sh not executable or not found at ${box_loop}/run.sh"

git -C "${box_repo}" fetch --prune origin
git -C "${box_repo}" checkout -B "${branch}" "origin/${branch}"

if [[ -n "${LOOP_GITHUB_TOKEN_FILE:-}" ]]; then
    export LOOP_GITHUB_TOKEN_FILE
else
    unset LOOP_GITHUB_TOKEN_FILE || true
fi
if [[ -n "${LOOP_GUEST_TEMPLATE:-}" ]]; then
    export LOOP_GUEST_TEMPLATE
else
    unset LOOP_GUEST_TEMPLATE || true
fi

exec "${box_loop}/run.sh" --repo "${box_repo}" --task-ref "${task_ref}" --propose --notify
