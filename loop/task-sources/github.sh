#!/usr/bin/env bash
#
# The task source: one chosen task, fetched from GitHub.
#
#   github.sh <owner/repo> <number>
#
# The default LOOP_TASK_SOURCE_COMMAND. Prints the task as JSON on stdout and
# exits non-zero if it could not be fetched. Substitutable (ADR 0004): a task
# living somewhere other than GitHub Issues is a different script here and no
# change to seed-run.sh.
#
# Two positional arguments and nothing else, deliberately. There is no search,
# no list, no filter, and no "next open issue" - this command can fetch exactly
# the one task it is told to, which is what makes seeding a setup step rather
# than issue intake (ADR 0010).
#
# It runs as the operator, with the operator's own GitHub identity, off the
# Loop's box. The box's fine-grained token holds Contents and Pull requests and
# NOT Issues, so a Run cannot fetch a task even if something told it to; that is
# ADR 0010's enforcement and this script is not it.

set -euo pipefail

task_repo="${1:?usage: github.sh <owner/repo> <number>}"
number="${2:?usage: github.sh <owner/repo> <number>}"

command -v gh >/dev/null 2>&1 || {
    printf 'github.sh: gh is not installed - the seed step runs as the operator, not on the Loop'"'"'s box\n' >&2
    exit 1
}

exec gh issue view "${number}" --repo "${task_repo}" \
    --json number,title,url,state,body
