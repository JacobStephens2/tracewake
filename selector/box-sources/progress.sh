#!/usr/bin/env bash
#
# The box surface, read-only: the Progress Log of the Run in flight.
#
#   progress.sh <branch>
#
# The default SELECTOR_BOX_PROGRESS_COMMAND. Prints the box checkout's
# Progress Log on stdout and exits 0 when it could be read.
#
# This is the one thing a Run makes visible while it is happening. run.sh
# persists nothing and prints its summary only when it ends, but every
# Iteration appends its record to this file and commits it - so a read of it
# about once a minute is what lets /loop show Iterations landing during a Run
# rather than five of them arriving at once ninety minutes later (#157).
#
# The branch is an argument and is deliberately NOT checked out or fetched:
# the box is running a Run in that checkout, and a watcher that touched its
# working tree would be a window reaching through the glass. It is passed so
# that a box reached some other way - a copy, an artifact store, a different
# host - has what it needs, and so that the log this printed can be attributed
# to a branch by whatever reads it.
#
# It holds no credential, starts nothing, writes nothing: one SSH hop, one
# `cat`. Substitutable (ADR 0004) like every other command the Selector
# reaches through; the offline suite answers it with a file that grows.
#
# Configuration, all environment, shared with box-sources/ssh.sh:
#
#   SELECTOR_BOX_HOST            required        where the box is
#   SELECTOR_BOX_USER            loop            the account a Run executes as
#   SELECTOR_BOX_REPO            required        the target's checkout on the box
#   SELECTOR_BOX_PROGRESS_PATH   PROGRESS.md     relative to the checkout
#
# Exit codes: 0 when the log was read, non-zero when it could not be.

set -euo pipefail

# The shared refusal. Sourced rather than restated: an instance value has no
# default anywhere in the product, and the sentence that says so belongs in
# one place (issue #3).
# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"

branch="${1:?usage: progress.sh <branch>}"
: "${branch}"  # named for the reader and for a substitute; unused here.

require SELECTOR_BOX_HOST
require SELECTOR_BOX_REPO
box_host="${SELECTOR_BOX_HOST}"
box_user="${SELECTOR_BOX_USER:-loop}"
box_repo="${SELECTOR_BOX_REPO}"
box_progress="${SELECTOR_BOX_PROGRESS_PATH:-PROGRESS.md}"

command -v ssh >/dev/null 2>&1 || {
    printf 'box-sources/progress.sh: ssh is required to reach the box\n' >&2
    exit 1
}

# A missing log is a failure rather than empty output: before the first
# Iteration the file exists (seed-run.sh wrote it and committed it), so its
# absence means the box is not where this thinks it is - which is worth
# saying once rather than rendering as a Run with no Iterations.
remote_command="$(printf 'set -euo pipefail
log=%q
[ -f "${log}" ] || { printf "no Progress Log at %%s\\n" "${log}" >&2; exit 1; }
exec cat "${log}"' "${box_repo}/${box_progress}")"

# BatchMode and a connect timeout: this is a read on a cycle that is holding a
# Run open, so a box that is not answering must fail fast rather than pile
# reads up behind each other. watcher.py bounds the whole call as well.
exec ssh -o BatchMode=yes -o ConnectTimeout=10 \
    "${box_host}" \
    "su - $(printf '%q' "${box_user}") -s /bin/bash -c $(printf '%q' "${remote_command}")"
