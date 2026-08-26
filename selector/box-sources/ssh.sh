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
#   SELECTOR_BOX_HOST   root@loop.etadventures.com
#   SELECTOR_BOX_USER   loop            the account a Run executes as
#   SELECTOR_BOX_REPO   /home/loop/tourbot
#   SELECTOR_BOX_LOOP   /home/loop/loop
#
# Exit codes: the Run's own, or 1 when no Run was started.

set -euo pipefail

die() {
    printf 'box-sources/ssh.sh: %s\n' "$*" >&2
    exit 1
}

branch="${1:?usage: ssh.sh <branch> <task-ref>}"
task_ref="${2:?usage: ssh.sh <branch> <task-ref>}"

box_host="${SELECTOR_BOX_HOST:-root@loop.etadventures.com}"
box_user="${SELECTOR_BOX_USER:-loop}"
box_repo="${SELECTOR_BOX_REPO:-/home/loop/tourbot}"
box_loop="${SELECTOR_BOX_LOOP:-/home/loop/loop}"

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
exec %q --repo %q --task-ref %q --propose --notify' \
    "${box_repo}" \
    "${box_repo}" "${branch}" "origin/${branch}" \
    "${box_loop}/run.sh" "${box_repo}" "${task_ref}")"

# BatchMode: an unattended dispatch must fail rather than sit at a prompt.
# No ConnectTimeout on the session itself - the Run is ninety minutes long and
# the Selector's own SELECTOR_DISPATCH_TIMEOUT_SECONDS is what bounds it.
exec ssh -o BatchMode=yes -o ServerAliveInterval=60 -o ServerAliveCountMax=10 \
    "${box_host}" \
    "su - $(printf '%q' "${box_user}") -s /bin/bash -c $(printf '%q' "${remote_command}")"
