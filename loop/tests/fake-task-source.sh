#!/usr/bin/env bash
#
# The scripted fake task source - what keeps the seed step's suite offline.
#
#   fake-task-source.sh <owner/repo> <number>
#
# Satisfies exactly the contract task-sources/github.sh satisfies, because the
# task source is one substitutable command for the same reason the agent is
# (ADR 0004), and that substitution point is the test seam. Pointing
# LOOP_TASK_SOURCE_COMMAND here lets the suite drive the REAL seed step against
# every shape of task with no network, no GitHub token, and no rate limit.
#
# Driven by three environment variables:
#
#   FAKE_TASK_JSON   a path holding the JSON to emit. Absent means a minimal
#                    well-formed task, so a test that does not care about the
#                    task's content does not have to construct one.
#   FAKE_TASK_RC     the exit code. Non-zero is a fetch that failed - a wrong
#                    number, a token that cannot see the repository, no network.
#   FAKE_TASK_STATE  a scratch path. The arguments of every invocation are
#                    appended to it, one line each, so a test can assert that
#                    exactly one task was fetched and which one - which is the
#                    externally observable form of "this is not issue intake".

set -euo pipefail

task_repo="${1:?usage: fake-task-source.sh <owner/repo> <number>}"
number="${2:?usage: fake-task-source.sh <owner/repo> <number>}"

if [[ -n ${FAKE_TASK_STATE:-} ]]; then
    printf '%s %s\n' "${task_repo}" "${number}" >>"${FAKE_TASK_STATE}"
fi

rc="${FAKE_TASK_RC:-0}"
if ((rc != 0)); then
    printf 'fake-task-source: pretending the fetch failed\n' >&2
    exit "${rc}"
fi

if [[ -n ${FAKE_TASK_JSON:-} ]]; then
    cat -- "${FAKE_TASK_JSON}"
    exit 0
fi

cat <<JSON
{
  "number": ${number},
  "title": "A task the operator wrote",
  "url": "https://github.com/${task_repo}/issues/${number}",
  "state": "OPEN",
  "body": "## What to build\n\nSomething small.\n\n## Acceptance criteria\n\n- [ ] the something is small\n- [ ] nothing else changed\n"
}
JSON
