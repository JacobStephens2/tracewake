#!/usr/bin/env bash
#
# The Loop's box, faked for an Attended Preview (ADR 0016).
#
#   box.sh <branch> <task-ref>
#
# Same contract as box-sources/ssh.sh: print the Run's own LOOP_RUN_* report
# on stdout and exit with the Run's exit code.
#
# This is the edge that would start a real unattended Run, on real code, under
# the operator's identity. ADR 0016 rejects letting a preview do that on its
# own logic: you are attended for the dispatch and then nobody watches for
# thirty to forty-five minutes, which is the premise ADR 0003 says fails.
#
# It reports a plausible ended Run so that the outcome and routing paths can
# be exercised, and it reaches nothing outside this VM.
set -euo pipefail

branch="${1:?usage: box.sh <branch> <task-ref>}"
task_ref="${2:?usage: box.sh <branch> <task-ref>}"

printf 'preview: no Run was started - %s on %s reaches no box\n' \
    "$task_ref" "$branch" >&2

cat <<REPORT
LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_NOTIFIED=sent
LOOP_RUN_PROPOSAL=https://example.invalid/pull/9001
REPORT
