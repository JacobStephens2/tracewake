#!/usr/bin/env bash
#
# The box surface, read-only, local: the Progress Log of the Run in flight.
#
#   progress-local.sh <branch>
#
# The Single-Host SELECTOR_BOX_PROGRESS_COMMAND (ADR 0019). Sibling to
# box-sources/progress.sh for the instance whose box is the controller
# itself (ADR 0004): the same contract - print the box checkout's Progress
# Log on stdout, exit non-zero when it could not be read - with no SSH hop,
# so no SELECTOR_BOX_HOST.
#
# Reads as the Run account (SELECTOR_BOX_USER, default loop) - see
# become-run-user.sh, sourced below. The checkout the Run is writing is that
# account's, and the branch is passed through untouched: the box is running
# a Run in that checkout, and a watcher that touched its working tree would
# be a window reaching through the glass.
#
# Configuration, all environment:
#
#   SELECTOR_BOX_REPO            required        the target's checkout, on this
#                                               machine
#   SELECTOR_BOX_USER            loop            the account a Run executes as
#   SELECTOR_BOX_PROGRESS_PATH   PROGRESS.md     relative to the checkout
#
# Exit codes: 0 when the log was read, non-zero when it could not be.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"
# shellcheck source-path=SCRIPTDIR source=./become-run-user.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/become-run-user.sh"

branch="${1:?usage: progress-local.sh <branch>}"
: "${branch}"  # named for the reader and for a substitute; unused here.

require SELECTOR_BOX_REPO
box_repo="${SELECTOR_BOX_REPO}"
box_progress="${SELECTOR_BOX_PROGRESS_PATH:-PROGRESS.md}"

become_run_user ${1+"$@"}

# A missing log is a failure rather than empty output: before the first
# Iteration the file exists (seed-run.sh wrote it and committed it), so its
# absence means the box is not where this thinks it is - which is worth
# saying once rather than rendering as a Run with no Iterations.
log="${box_repo}/${box_progress}"
[ -f "${log}" ] || {
    printf 'box-sources/progress-local.sh: no Progress Log at %s\n' "${log}" >&2
    exit 1
}
exec cat "${log}"
