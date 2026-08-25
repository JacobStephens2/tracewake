#!/usr/bin/env bats
#
# The pull-request surface's offline suite (#83).
#
# Seamed and tested on its own rather than only through propose.sh, for the
# reason spec issue #73's Seam B gives about the completeness check: this script
# is the one place where Proposal-Only Output stops being an argument and
# becomes a request over the wire, and what it sends should not have its
# correctness established only through whatever calls it.
#
# `curl` is the seam. A fake ahead of the real one on PATH means the suite can
# assert the method, the path and the JSON - `draft: true` above all - with no
# token, no network, and no pull request left on a real repository.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    PR="${LOOP_SRC}/pr-sources/github.sh"

    FAKE_CURL_STATE="${BATS_TEST_TMPDIR}/curl"
    export FAKE_CURL_STATE
    export FAKE_CURL_BEHAVIOUR=none

    # The fake, under the name the script calls.
    BIN="${BATS_TEST_TMPDIR}/bin"
    mkdir -p "${BIN}"
    ln -s "${LOOP_SRC}/tests/fake-curl.sh" "${BIN}/curl"
    export PATH="${BIN}:${PATH}"

    TOKEN_FILE="${BATS_TEST_TMPDIR}/github-token"
    printf 'github_pat_11EXAMPLE\n' >"${TOKEN_FILE}"
    chmod 0600 "${TOKEN_FILE}"
    export LOOP_GITHUB_TOKEN_FILE="${TOKEN_FILE}"

    BODY="${BATS_TEST_TMPDIR}/body.md"
    printf 'A proposal.\n\n- Task: owner/name#648\n"quoted" and \\backslashed\n' >"${BODY}"
}

open_a_pull_request() {
    run "${PR}" "owner/name" "loop/run-648" "master" "Loop: a proposal" "${BODY}"
}

payload_field() {
    jq -r "$1" <"${FAKE_CURL_STATE}.payload"
}

# --- What it sends -----------------------------------------------------------

@test "the pull request is opened as a draft" {
    open_a_pull_request
    [ "$status" -eq 0 ]
    [ "$(payload_field .draft)" = "true" ]
}

@test "the head, the base and the title are what the caller asked for" {
    open_a_pull_request
    [ "$(payload_field .head)" = "loop/run-648" ]
    [ "$(payload_field .base)" = "master" ]
    [ "$(payload_field .title)" = "Loop: a proposal" ]
}

@test "the body survives quotes and backslashes intact" {
    open_a_pull_request
    [[ "$(payload_field .body)" == *'"quoted" and \backslashed'* ]]
}

@test "the pull request is opened on the repository it was given" {
    open_a_pull_request
    grep -q 'POST .*/repos/owner/name/pulls' "${FAKE_CURL_STATE}.calls"
}

@test "the URL GitHub answered with is what is printed" {
    open_a_pull_request
    [ "$output" = "https://github.com/owner/name/pull/999" ]
}

# --- Re-running it -----------------------------------------------------------

@test "an open pull request for this branch is reused rather than opened again" {
    FAKE_CURL_BEHAVIOUR=existing open_a_pull_request
    [ "$status" -eq 0 ]
    [ "$output" = "https://github.com/owner/name/pull/17" ]
    ! grep -q '^POST ' "${FAKE_CURL_STATE}.calls"
}

@test "the existing pull request is looked for by head, scoped to the owner" {
    open_a_pull_request
    grep -q 'GET .*head=owner:loop/run-648' "${FAKE_CURL_STATE}.calls"
    grep -q 'GET .*state=open' "${FAKE_CURL_STATE}.calls"
}

# --- Refusals ----------------------------------------------------------------

@test "a refused pull request is a failure that quotes what GitHub said" {
    FAKE_CURL_BEHAVIOUR=refuse-post open_a_pull_request
    [ "$status" -eq 1 ]
    [[ "$output" == *"No commits between"* ]]
}

@test "a refused lookup fails rather than opening a second pull request" {
    FAKE_CURL_BEHAVIOUR=refuse-get open_a_pull_request
    [ "$status" -eq 1 ]
    [[ "$output" == *"Bad credentials"* ]]
    ! grep -q '^POST ' "${FAKE_CURL_STATE}.calls"
}

@test "no token on the box is a failure naming the file, not a silent skip" {
    rm -f "${TOKEN_FILE}"
    open_a_pull_request
    [ "$status" -eq 1 ]
    [[ "$output" == *"${TOKEN_FILE}"* ]]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "an empty token file is treated as no token" {
    : >"${TOKEN_FILE}"
    open_a_pull_request
    [ "$status" -eq 1 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "a body file that is not there is a failure before anything is sent" {
    run "${PR}" "owner/name" "loop/run-648" "master" "title" "${BATS_TEST_TMPDIR}/absent"
    [ "$status" -eq 1 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}

@test "missing arguments are a failure, not a pull request with empty fields" {
    run "${PR}" "owner/name"
    [ "$status" -ne 0 ]
    [ ! -f "${FAKE_CURL_STATE}.calls" ]
}
