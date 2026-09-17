#!/usr/bin/env bash
#
# The box surface, read-only, local: the Progress Log of the Run in flight.
#
#   progress-local.sh <branch>
#
# The Single-Host SELECTOR_BOX_PROGRESS_COMMAND. Sibling to
# box-sources/progress.sh for Single-Host instances (ADR 0019): dispatch runs
# through box-sources/local.sh with no SSH hop, so the watcher's read must
# not open one either. Prints the box checkout's Progress Log on stdout and
# exits 0 when it could be read.
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
# that the log this printed can be attributed to a branch by whatever reads
# it.
#
# It holds no credential, starts nothing, writes nothing: one `cat`.
# Substitutable (ADR 0004) like every other command the Selector
# reaches through; the offline suite answers it with a file that grows.
#
# Configuration, all environment, shared with box-sources/local.sh:
#
#   SELECTOR_BOX_REPO            required        the target's checkout on this machine
#   SELECTOR_BOX_PROGRESS_PATH   PROGRESS.md     relative to the checkout
#
# Exit codes: 0 when the log was read, non-zero when it could not be.
#
# Requires no SELECTOR_BOX_HOST: there is no hop to address. A Single-Host
# instance that left the default progress.sh in place would SSH to whatever
# that names - INSTALL.md's `local` does not resolve - and journal
# `run.watch-failed` on every poll of every Run while the dispatch beside it
# worked perfectly.

set -euo pipefail

# The shared refusal. Sourced rather than restated: an instance value has no
# default anywhere in the product, and the sentence that says so belongs in
# one place (issue #3).
# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"

branch="${1:?usage: progress-local.sh <branch>}"
: "${branch}"  # named for the reader and for a substitute; unused here.

require SELECTOR_BOX_REPO
box_repo="${SELECTOR_BOX_REPO}"
box_progress="${SELECTOR_BOX_PROGRESS_PATH:-PROGRESS.md}"

log="${box_repo}/${box_progress}"

# A missing log is a failure rather than empty output: before the first
# Iteration the file exists (seed-run.sh wrote it and committed it), so its
# absence means the box is not where this thinks it is - which is worth
# saying once rather than rendering as a Run with no Iterations.
[ -f "${log}" ] || {
    printf 'box-sources/progress-local.sh: no Progress Log at %s\n' "${log}" >&2
    exit 1
}

exec cat "${log}"
