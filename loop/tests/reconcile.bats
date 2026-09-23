#!/usr/bin/env bats
#
# The reconcile Run's box-side offline suite (issue #34).
#
# `curl` is the forge seam: a stub ahead of the real one answers the Proposal
# lookup, so what is asserted is the merge, the verification gate and the
# push against a real local remote - with no token and no network. The agent
# is LOOP_AGENT_COMMAND like everywhere else in the Loop; the suite points it
# at a failing fake or at a small resolving one.
#
# What the Selector asserts about the other end of this - dispatch,
# escalation, journaling - lives in selector/tests/test_reconcile.py.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    RECONCILE="${LOOP_SRC}/reconcile.sh"

    WORK="${BATS_TEST_TMPDIR}/work"
    BARE="${BATS_TEST_TMPDIR}/remote.git"
    git init --quiet --bare --initial-branch=main "${BARE}"
    git clone --quiet "${BARE}" "${WORK}"
    git -C "${WORK}" config user.email "box@example.invalid"
    git -C "${WORK}" config user.name "The Box"
    git -C "${WORK}" config commit.gpgsign false

    printf 'line one\n' >"${WORK}/file.txt"
    git -C "${WORK}" add -A
    git -C "${WORK}" commit --quiet -m "First commit"
    git -C "${WORK}" push --quiet -u origin main
    git -C "${WORK}" remote set-head origin --auto

    # The forge stub: answers the pulls lookup with the head the test sets.
    BIN="${BATS_TEST_TMPDIR}/bin"
    mkdir -p "${BIN}"
    cat >"${BIN}/curl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
printf '%s\n' "${url}" >>"${FAKE_CURL_STATE}.calls"
if [[ ${url} == */pulls/* ]]; then
    printf '{"number": 13, "head": {"ref": "%s"}, "base": {"ref": "main"}}\n' \
        "${FAKE_PR_HEAD:-loop/630-the-nightly-sync}"
else
    printf '{}\n'
fi
STUB
    chmod +x "${BIN}/curl"
    export PATH="${BIN}:${PATH}"
    export FAKE_CURL_STATE="${BATS_TEST_TMPDIR}/curl"
    export FAKE_PR_HEAD="loop/630-the-nightly-sync"

    TOKEN_FILE="${BATS_TEST_TMPDIR}/github-token"
    printf '[REDACTED]\n' >"${TOKEN_FILE}"
    chmod 0600 "${TOKEN_FILE}"
    export LOOP_GITHUB_TOKEN_FILE="${TOKEN_FILE}"
    export SELECTOR_TASK_REPO="owner/name"

    # No agent unless a test sets one: a clean merge must never need it.
    # (/bin/false is not this - it does not exist everywhere, and a missing
    # adapter must read as missing rather than as failed.)
    FAILING_AGENT="${BATS_TEST_TMPDIR}/failing-agent.sh"
    printf '#!/usr/bin/env bash\nexit 1\n' >"${FAILING_AGENT}"
    chmod +x "${FAILING_AGENT}"
    export LOOP_AGENT_COMMAND="${FAILING_AGENT}"
}

# A proposal branch behind a base that moved on without touching its files.
behind_branch() {
    git -C "${WORK}" checkout --quiet -b loop/630-the-nightly-sync
    printf 'proposal work\n' >"${WORK}/proposal.txt"
    git -C "${WORK}" add -A
    git -C "${WORK}" commit --quiet -m "Proposal work"
    git -C "${WORK}" push --quiet -u origin loop/630-the-nightly-sync
    git -C "${WORK}" checkout --quiet main
    printf 'line one\nline two\n' >"${WORK}/file.txt"
    git -C "${WORK}" commit --quiet -am "Base moves on"
    git -C "${WORK}" push --quiet origin main
}

# The same, but the base rewrote the line the proposal rewrote: a conflict.
conflicting_branch() {
    git -C "${WORK}" checkout --quiet -b loop/630-the-nightly-sync
    printf 'proposal line\n' >"${WORK}/file.txt"
    git -C "${WORK}" commit --quiet -am "Proposal work"
    git -C "${WORK}" push --quiet -u origin loop/630-the-nightly-sync
    git -C "${WORK}" checkout --quiet main
    printf 'base line\n' >"${WORK}/file.txt"
    git -C "${WORK}" commit --quiet -am "Base moves on"
    git -C "${WORK}" push --quiet origin main
}

remote_branch_sha() {
    git --git-dir="${BARE}" rev-parse "refs/heads/$1"
}

reconcile() {
    run "${RECONCILE}" --repo "${WORK}" --proposal 13 "$@"
}

# --- The clean merge ----------------------------------------------------------

@test "a behind proposal is merged with the base and pushed" {
    behind_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    reconcile

    [ "$status" -eq 0 ]
    echo "${output}" | grep -q "^LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync$"
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" != "${before}" ]
    git -C "${WORK}" merge-base --is-ancestor origin/main HEAD
}

@test "the merge is a merge commit, the forge's own shape" {
    behind_branch

    reconcile

    [ "$status" -eq 0 ]
    [ "$(git -C "${WORK}" rev-list --parents -n 1 HEAD | wc -w)" -eq 3 ]
}

@test "a proposal already containing the base still reports and pushes" {
    behind_branch
    git -C "${WORK}" checkout --quiet loop/630-the-nightly-sync
    git -C "${WORK}" merge --quiet --no-ff -m "already merged" origin/main
    git -C "${WORK}" push --quiet origin loop/630-the-nightly-sync
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    reconcile

    [ "$status" -eq 0 ]
    echo "${output}" | grep -q "^LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync$"
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

# --- The verification gate ----------------------------------------------------

@test "a red suite stops the push" {
    behind_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    reconcile --check "exit 3"

    [ "$status" -eq 3 ]
    # The merge stands and is reported even though nothing is pushed: the
    # Selector's escalation names the branch the work got to.
    echo "${output}" | grep -q "^LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync$"
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

@test "a green suite lets the push through" {
    behind_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    reconcile --check true

    [ "$status" -eq 0 ]
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" != "${before}" ]
}

@test "the suite runs on the merged branch" {
    behind_branch

    reconcile --check "grep -q 'line two' file.txt"

    [ "$status" -eq 0 ]
}

# --- Conflicts -----------------------------------------------------------------

@test "an agent that fails to resolve stops the push" {
    conflicting_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    reconcile

    [ "$status" -eq 2 ]
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

@test "conflicts with no agent at all cannot run" {
    conflicting_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"
    export LOOP_AGENT_COMMAND=/nonexistent/agent.sh

    reconcile

    [ "$status" -eq 1 ]
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

@test "a resolution the agent leaves unmerged stops the push" {
    conflicting_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"
    AGENT="${BATS_TEST_TMPDIR}/lazy-agent.sh"
    printf '#!/usr/bin/env bash\nexit 0\n' >"${AGENT}"
    chmod +x "${AGENT}"
    export LOOP_AGENT_COMMAND="${AGENT}"

    reconcile

    [ "$status" -eq 2 ]
    # The unmerged guard names the files, which the ancestry guard below
    # cannot - and it stops the push on its own rather than falling through
    # to that guard. Both messages would mean the fall-through happened.
    echo "${output}" | grep -q "still unmerged after the agent: file.txt"
    [ -z "$(echo "${output}" | grep "is not an ancestor" || true)" ]
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

@test "a resolution that drops the base stops the push" {
    conflicting_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"
    AGENT="${BATS_TEST_TMPDIR}/dropping-agent.sh"
    cat >"${AGENT}" <<'AGENT'
#!/usr/bin/env bash
set -euo pipefail
git merge --abort
git -c user.email=agent@example.invalid -c user.name="The Agent" \
    commit --quiet --allow-empty -m "not a merge"
AGENT
    chmod +x "${AGENT}"
    export LOOP_AGENT_COMMAND="${AGENT}"

    reconcile

    [ "$status" -eq 2 ]
    echo "${output}" | grep -q "is not an ancestor of the result"
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}

@test "a resolved merge is verified and pushed" {
    conflicting_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"
    AGENT="${BATS_TEST_TMPDIR}/resolving-agent.sh"
    cat >"${AGENT}" <<'AGENT'
#!/usr/bin/env bash
set -euo pipefail
printf 'resolved line\n' >file.txt
git add file.txt
git -c user.email=agent@example.invalid -c user.name="The Agent" \
    commit --quiet --no-edit
AGENT
    chmod +x "${AGENT}"
    export LOOP_AGENT_COMMAND="${AGENT}"

    reconcile

    [ "$status" -eq 0 ]
    echo "${output}" | grep -q "^LOOP_RECONCILE_BRANCH=loop/630-the-nightly-sync$"
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" != "${before}" ]
    git -C "${WORK}" merge-base --is-ancestor origin/main HEAD
    [ -z "$(git -C "${WORK}" diff --name-only --diff-filter=U)" ]
}

@test "the agent is asked inside the checkout, with the conflicts named" {
    conflicting_branch
    AGENT="${BATS_TEST_TMPDIR}/recording-agent.sh"
    cat >"${AGENT}" <<'AGENT'
#!/usr/bin/env bash
set -euo pipefail
printf 'cwd=%s\n' "${PWD}" >"${AGENT_STATE}.asked"
printf 'args=%s\n' "$*" >>"${AGENT_STATE}.asked"
cat "$1" >>"${AGENT_STATE}.asked"
printf 'resolved line\n' >file.txt
git add file.txt
git -c user.email=agent@example.invalid -c user.name="The Agent" \
    commit --quiet --no-edit
AGENT
    chmod +x "${AGENT}"
    export LOOP_AGENT_COMMAND="${AGENT}"
    export AGENT_STATE="${BATS_TEST_TMPDIR}/agent"

    reconcile

    [ "$status" -eq 0 ]
    grep -q "cwd=${WORK}" "${BATS_TEST_TMPDIR}/agent.asked"
    grep -q "file.txt" "${BATS_TEST_TMPDIR}/agent.asked"
}

# --- Proposal-Only Output ------------------------------------------------------

@test "the push is never forced" {
    behind_branch
    # A shim that logs every git invocation and delegates: the only way to
    # tell a plain push from a forced one is to read the command itself,
    # because against a static remote both land identically.
    REAL_GIT="$(command -v git)"
    GIT_BIN="${BATS_TEST_TMPDIR}/gitbin"
    mkdir -p "${GIT_BIN}"
    cat >"${GIT_BIN}/git" <<SHIM
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"${BATS_TEST_TMPDIR}/git.calls"
exec "${REAL_GIT}" "\$@"
SHIM
    chmod +x "${GIT_BIN}/git"
    export PATH="${GIT_BIN}:${PATH}"

    reconcile

    [ "$status" -eq 0 ]
    grep -q "push origin loop/630-the-nightly-sync" "${BATS_TEST_TMPDIR}/git.calls"
    # A bare `! grep` passes either way under bats' errexit handling, so the
    # absence is asserted through a test on the match output instead.
    [ -z "$(grep -- "--force" "${BATS_TEST_TMPDIR}/git.calls" || true)" ]
}

@test "a proposal heading the base is refused" {
    behind_branch
    export FAKE_PR_HEAD=main
    before="$(remote_branch_sha main)"

    reconcile

    [ "$status" -eq 1 ]
    [ "$(remote_branch_sha main)" = "${before}" ]
}

@test "a foreign proposal URL is refused" {
    behind_branch

    run "${RECONCILE}" --repo "${WORK}" \
        --proposal https://github.com/someone/else/pull/13

    [ "$status" -eq 1 ]
}

@test "a missing proposal is refused before anything moves" {
    behind_branch
    before="$(remote_branch_sha loop/630-the-nightly-sync)"

    run "${RECONCILE}" --repo "${WORK}" --proposal 13 --task-repo "not a repo"

    [ "$status" -eq 1 ]
    [ "$(remote_branch_sha loop/630-the-nightly-sync)" = "${before}" ]
}
