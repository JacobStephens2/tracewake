#!/usr/bin/env bats
#
# The notification surface's offline suite (#110).
#
# Seamed and tested on its own rather than only through a Run, for the same
# reason the pull-request surface is: this is the one place where "the operator
# is told" stops being an argument and becomes a request over the wire, and a
# Run that drives it through a fake proves the Run's half only.
#
# `curl` is the seam. A fake ahead of the real one on PATH means the suite can
# assert the method, the path and the JSON with no token, no network, and no
# comment left on a real pull request.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    NOTIFY="${LOOP_SRC}/notify-sources/github-pr-comment.sh"

    FAKE_CURL_STATE="${BATS_TEST_TMPDIR}/curl"
    export FAKE_CURL_STATE
    export FAKE_CURL_BEHAVIOUR=none

    BIN="${BATS_TEST_TMPDIR}/bin"
    mkdir -p "${BIN}"
    ln -s "${LOOP_SRC}/tests/fake-curl.sh" "${BIN}/curl"
    export PATH="${BIN}:${PATH}"

    TOKEN_FILE="${BATS_TEST_TMPDIR}/github-token"
    printf 'github_pat_11EXAMPLE\n' >"${TOKEN_FILE}"
    chmod 0600 "${TOKEN_FILE}"
    export LOOP_GITHUB_TOKEN_FILE="${TOKEN_FILE}"

    BODY="${BATS_TEST_TMPDIR}/body.md"
    printf 'The Run has finished.\n\n- Ended by: agent-failed\n"quoted" and \\backslashed\n' >"${BODY}"

    PR_URL="https://github.com/owner/name/pull/999"
}

notify() {
    run "${NOTIFY}" "Run ended: agent-failed (exit 4)" "${BODY}" "${PR_URL}"
}

payload_field() {
    jq -r "$1" <"${FAKE_CURL_STATE}.payload"
}

# --- What it sends -----------------------------------------------------------

@test "the comment is posted to the proposal's own thread" {
    notify
    [ "$status" -eq 0 ]
    grep -q 'POST .*/repos/owner/name/issues/999/comments' "${FAKE_CURL_STATE}.calls"
}

@test "the body reaches the comment intact, quotes and backslashes included" {
    notify
    [[ "$(payload_field .body)" == *'"quoted" and \backslashed'* ]]
    [[ "$(payload_field .body)" == *"Ended by: agent-failed"* ]]
}

@test "the subject leads the comment, so the operator reads the outcome first" {
    notify
    [[ "$(payload_field .body)" == "Run ended: agent-failed (exit 4)"* ]]
}

@test "where the comment landed is what is printed" {
    notify
    [ "$output" = "https://github.com/owner/name/pull/999" ]
}

# --- What it refuses ---------------------------------------------------------

@test "a refused comment fails rather than reporting a notification nobody got" {
    FAKE_CURL_BEHAVIOUR=refuse-post notify
    [ "$status" -ne 0 ]
}

@test "no token is a refusal before anything is sent" {
    : >"${TOKEN_FILE}"
    notify
    [ "$status" -ne 0 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "a URL that is not a pull request is refused rather than guessed at" {
    PR_URL="https://github.com/owner/name/issues/999"
    notify
    [ "$status" -ne 0 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "a URL somewhere other than github.com is refused" {
    PR_URL="https://example.invalid/owner/name/pull/999"
    notify
    [ "$status" -ne 0 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "a body file that is not there is refused rather than sent empty" {
    BODY="${BATS_TEST_TMPDIR}/absent.md"
    notify
    [ "$status" -ne 0 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}
