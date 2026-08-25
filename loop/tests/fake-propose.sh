#!/usr/bin/env bash
#
# The scripted fake proposal command.
#
#   fake-propose.sh --repo <path> [--task-ref ...] [--ended-by ...] [--exit ...]
#
# Satisfies exactly the contract propose.sh satisfies. Pointing
# LOOP_PROPOSE_COMMAND here lets the suite drive a real Run through --propose
# with no remote, no token and no network - the same substitution the agent gets.
#
#   FAKE_PROPOSE_STATE      a scratch path. Every argument it was given is
#                           written there, so a test asserts what the Run asked
#                           for rather than what this fake did about it.
#   FAKE_PROPOSE_BEHAVIOUR  ok | fail

set -euo pipefail

state="${FAKE_PROPOSE_STATE:?FAKE_PROPOSE_STATE must be set}"
printf '%s\n' "$@" >"${state}"

if [[ ${FAKE_PROPOSE_BEHAVIOUR:-ok} == fail ]]; then
    printf 'fake-propose: the push failed\n' >&2
    printf 'LOOP_PROPOSE_RESULT=push-failed\n'
    exit 2
fi

printf 'LOOP_PROPOSE_RESULT=proposed\n'
printf 'LOOP_PROPOSE_URL=https://github.com/owner/name/pull/999\n'
