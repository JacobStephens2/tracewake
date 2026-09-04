#!/usr/bin/env bash
#
# The box surface: put the Loop's box on the Run's branch and start the Run.
#
#   ssh.sh <branch> <task-ref>
#
# The default SELECTOR_BOX_COMMAND. Prints the Run's own stdout report - the
# LOOP_RUN_* block run.sh ends with - and exits with the Run's exit code.
#
# It blocks for the length of the Run, and that is the point. The box persists
# no record of a Run: run.sh prints its summary and exits, so the only moment
# the outcome exists anywhere is while something is holding the process. This
# command is that something, and dispatch.py captures what it prints (spec
# #151, story 13).
#
# Two accounts, one hop. Ansible reaches the box as root because that is the
# only account the droplet was created with; a Run executes as the
# unprivileged `loop` account, which is where the agent's login, the signing
# key and the GitHub token live. So this drops to `loop` with a login shell -
# the same shape the credential inventory is run with.
#
# What it does NOT do is as important as what it does: it starts run.sh and
# nothing else. It does not seed (that happens off the box, as the operator -
# ADR 0010), it does not push (propose.sh does, on the box, after every agent
# process is gone), and it holds no credential of its own.
#
# Substitutable (ADR 0004): a box reached some other way is a different script
# here and no change to dispatch.py, and the offline suite drives the real
# dispatch sequence through a scripted fake in its place.
#
# Configuration, all environment:
#
#   SELECTOR_BOX_HOST         required   where the box is, as ssh takes it
#   SELECTOR_BOX_USER         loop       the account a Run executes as
#   SELECTOR_BOX_REPO         required   the target's checkout on the box
#   SELECTOR_BOX_LOOP         /home/loop/loop
#   LOOP_GITHUB_TOKEN_FILE    optional   the target's repository token, on the box
#   LOOP_GUEST_TEMPLATE       optional   the target's guest image
#
# The last three are per-target and are set by the Selector from the target's
# stanza (issue #3); the last two are carried across the hop rather than read
# here, because what consumes them is the Run.
#
# Exit codes: the Run's own, or 1 when no Run was started.

set -euo pipefail

die() {
    printf 'box-sources/ssh.sh: %s\n' "$*" >&2
    exit 1
}

# The shared refusal. Sourced rather than restated: an instance value has no
# default anywhere in the product, and the sentence that says so belongs in
# one place (issue #3).
# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"

branch="${1:?usage: ssh.sh <branch> <task-ref>}"
task_ref="${2:?usage: ssh.sh <branch> <task-ref>}"

require SELECTOR_BOX_HOST
require SELECTOR_BOX_REPO
box_host="${SELECTOR_BOX_HOST}"
box_user="${SELECTOR_BOX_USER:-loop}"
box_repo="${SELECTOR_BOX_REPO}"
box_loop="${SELECTOR_BOX_LOOP:-/home/loop/loop}"

# Carried across the hop, not read here. `ssh` forwards no environment, so a
# per-target token file or guest image set on this side would simply be absent
# on the box - which is the shape of failure that ends with every target's Run
# using the first target's credential.
#
# Assembled into one block HERE, with the line breaks in the block rather than
# in the format string below. Command substitution strips trailing newlines,
# so a `$(printf 'export ...=%q\n')` passed as a `%s` argument arrives with
# its newline gone and runs into whatever follows it:
#
#     export LOOP_GITHUB_TOKEN_FILE=/home/loop/tokexec /home/loop/loop/run.sh
#
# which is a dispatch that starts nothing, on every Run that has a token.
# `$'\n'` is a literal newline in the variable and survives.
exports=""
if [[ -n ${LOOP_GITHUB_TOKEN_FILE:-} ]]; then
    exports+="$(printf 'export LOOP_GITHUB_TOKEN_FILE=%q' "${LOOP_GITHUB_TOKEN_FILE}")"$'\n'
fi
if [[ -n ${LOOP_GUEST_TEMPLATE:-} ]]; then
    exports+="$(printf 'export LOOP_GUEST_TEMPLATE=%q' "${LOOP_GUEST_TEMPLATE}")"$'\n'
fi

command -v ssh >/dev/null 2>&1 || die "ssh is required to reach the box"

# Assembled with printf %q so that a branch name or a task reference reaches
# the remote shell as one word whatever is in it. The remote is bash, which is
# what %q quotes for.
#
# `fetch` then `checkout -B` from the remote ref rather than a plain checkout:
# the branch was pushed from this VM a moment ago and the box has never seen
# it. No reset and no clean - a checkout that fails because the box's tree is
# dirty is a box that needs a human, and discarding whatever is in it
# unattended is not this script's call.
remote_command="$(printf 'set -euo pipefail
git -C %q fetch --prune origin
git -C %q checkout -B %q %q
%sexec %q --repo %q --task-ref %q --propose --notify' \
    "${box_repo}" \
    "${box_repo}" "${branch}" "origin/${branch}" \
    "${exports}" \
    "${box_loop}/run.sh" "${box_repo}" "${task_ref}")"

# BatchMode: an unattended dispatch must fail rather than sit at a prompt.
# No ConnectTimeout on the session itself - the Run is ninety minutes long and
# the Selector's own SELECTOR_DISPATCH_TIMEOUT_SECONDS is what bounds it.
exec ssh -o BatchMode=yes -o ServerAliveInterval=60 -o ServerAliveCountMax=10 \
    "${box_host}" \
    "su - $(printf '%q' "${box_user}") -s /bin/bash -c $(printf '%q' "${remote_command}")"
