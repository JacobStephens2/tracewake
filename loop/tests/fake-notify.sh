#!/usr/bin/env bash
#
# The scripted fake notification command.
#
#   fake-notify.sh <subject> <body-file> [<proposal-url>]
#
# Satisfies exactly the contract notify-sources/github-pr-comment.sh satisfies.
# Pointing LOOP_NOTIFY_COMMAND here lets the suite drive a real Run through
# --notify with no token, no network, and no comment left on a real pull
# request - the same substitution the agent and the proposal get.
#
#   FAKE_NOTIFY_STATE      a scratch path. The subject, the body it was given
#                          and the proposal URL are written there, so a test
#                          asserts what the Run said rather than what this fake
#                          did about it.
#   FAKE_NOTIFY_BEHAVIOUR  ok | fail | hang

set -euo pipefail

state="${FAKE_NOTIFY_STATE:?FAKE_NOTIFY_STATE must be set}"

{
    printf 'NOTIFY_SUBJECT=%s\n' "${1:-}"
    printf 'NOTIFY_URL=%s\n' "${3:-}"
    if [[ -f ${2:-} ]]; then
        cat -- "$2"
    fi
} >"${state}"

if [[ ${FAKE_NOTIFY_BEHAVIOUR:-ok} == fail ]]; then
    printf 'fake-notify: GitHub refused the comment\n' >&2
    exit 1
fi

# A surface that never answers. Longer than any LOOP_NOTIFY_TIMEOUT_SECONDS a
# test sets, so what the test observes is the Run abandoning it rather than this
# script finishing.
if [[ ${FAKE_NOTIFY_BEHAVIOUR:-ok} == hang ]]; then
    sleep 6
fi

printf 'https://github.com/owner/name/pull/999#issuecomment-1\n'
