#!/usr/bin/env bash
#
# The scripted fake pull-request command.
#
#   fake-pr-source.sh <owner/repo> <head> <base> <title> <body-file>
#
# Satisfies exactly the contract pr-sources/github.sh satisfies. Pointing
# LOOP_PR_COMMAND here lets the suite drive the real propose.sh end to end with
# no token, no network, and no draft pull request left behind on a real
# repository every time the suite runs.
#
#   FAKE_PR_STATE      a scratch path. What it was asked for is written here as
#                      NAME=value lines, so a test asserts what the Run proposed
#                      rather than what this fake did about it. The body is kept
#                      alongside it as "<path>.body".
#   FAKE_PR_BEHAVIOUR  ok | fail | existing

set -euo pipefail

state="${FAKE_PR_STATE:?FAKE_PR_STATE must be set}"

{
    printf 'PR_REPO=%s\n' "${1:-}"
    printf 'PR_HEAD=%s\n' "${2:-}"
    printf 'PR_BASE=%s\n' "${3:-}"
    printf 'PR_TITLE=%s\n' "${4:-}"
    printf 'PR_BODY_FILE=%s\n' "${5:-}"
} >"${state}"
if [[ -f ${5:-} ]]; then
    cp -- "$5" "${state}.body"
fi

case "${FAKE_PR_BEHAVIOUR:-ok}" in
    ok) printf 'https://github.com/%s/pull/999\n' "${1}" ;;
    existing) printf 'https://github.com/%s/pull/17\n' "${1}" ;;
    fail)
        printf 'fake-pr-source: GitHub refused\n' >&2
        exit 1
        ;;
    *)
        printf 'fake-pr-source: unknown behaviour %s\n' "${FAKE_PR_BEHAVIOUR}" >&2
        exit 64
        ;;
esac
