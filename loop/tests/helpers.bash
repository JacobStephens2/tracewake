# Shared setup for the Loop's offline suite.
#
# Every test builds a throwaway repository, points the Loop at the scripted fake
# agent, and collapses the Termination Contract's minutes to seconds. Nothing
# here reaches a model or a network, which is what lets the whole suite run in
# seconds and lets the Contract's five numbers be changed with confidence.
#
# shellcheck shell=bash

setup_loop_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC

    REPO="${BATS_TEST_TMPDIR}/repo"
    mkdir -p "${REPO}"
    git -C "${REPO}" init --quiet --initial-branch=main
    git -C "${REPO}" config user.email "loop@example.invalid"
    git -C "${REPO}" config user.name "The Loop"
    git -C "${REPO}" config commit.gpgsign false

    cat >"${REPO}/PLAN.md" <<'PLAN'
# Plan

Task: three small things.

- [ ] task 1
- [ ] task 2
- [ ] task 3
PLAN
    printf '# Progress Log\n' >"${REPO}/PROGRESS.md"
    git -C "${REPO}" add -A
    git -C "${REPO}" commit --quiet --message "Seed the Run"
    export REPO

    export FAKE_AGENT_STATE="${BATS_TEST_TMPDIR}/fake-agent-state"
    export LOOP_AGENT_COMMAND="${LOOP_SRC}/tests/fake-agent.sh"

    # Seconds, not minutes. The real values are in contract.sh; these exist so
    # the suite exercises the same code paths without waiting for them.
    export LOOP_MAX_ITERATIONS=3
    export LOOP_ITERATION_TIMEOUT_SECONDS=5
    export LOOP_MAX_TURNS=40
    export LOOP_RUN_TIMEOUT_SECONDS=60
    export LOOP_MAX_CONSECUTIVE_NOOPS=2

    # The proposal is the Run's only external effect, so the suite gets a
    # scripted fake for it exactly as it gets one for the agent. A Run without
    # --propose never reaches it; a Run with --propose reaches this and no
    # network.
    export FAKE_PROPOSE_STATE="${BATS_TEST_TMPDIR}/fake-propose"
    export FAKE_PROPOSE_BEHAVIOUR=ok
    export LOOP_PROPOSE_COMMAND="${LOOP_SRC}/tests/fake-propose.sh"

    # And a scripted fake for the notification, for the same reason: telling the
    # operator a Run has finished is the Run's second external effect, and the
    # suite reaches it without a token or a network.
    export FAKE_NOTIFY_STATE="${BATS_TEST_TMPDIR}/fake-notify"
    export FAKE_NOTIFY_BEHAVIOUR=ok
    export LOOP_NOTIFY_COMMAND="${LOOP_SRC}/tests/fake-notify.sh"
}

# What the Run asked the proposal for. One argument per line, as the fake wrote
# them, so a test can assert that the ending bound and the exit code reached it.
proposed_with() {
    if [[ -f ${FAKE_PROPOSE_STATE} ]]; then
        cat -- "${FAKE_PROPOSE_STATE}"
    fi
}

# What the Run told the operator. The subject, the proposal URL and the body as
# the fake received them, so a test asserts what a finished Run said about
# itself rather than what the notification surface did with it.
notified_with() {
    if [[ -f ${FAKE_NOTIFY_STATE} ]]; then
        cat -- "${FAKE_NOTIFY_STATE}"
    fi
}

# Give the fixture repository a remote with a recorded default branch, which is
# what run.sh's base-branch refusal turns on. Left out of the default fixture
# deliberately: the Loop itself does not need a remote, and a fixture that had
# one would make that refusal untestable by making it unconditional.
give_the_repo_a_remote() {
    local remote="${BATS_TEST_TMPDIR}/remote.git"
    git init --quiet --bare --initial-branch=main "${remote}"
    git -C "${REPO}" remote add origin "${remote}"
    git -C "${REPO}" push --quiet origin main
    git -C "${REPO}" remote set-head origin main
}

# A ceiling well above every Contract value the suite sets, and unrelated to
# them. The Iteration wall clock is one of the five bounds, and the way a broken
# one fails is by never returning - so without a ceiling here that mutation is
# caught by a wedged terminal rather than by a red test. Exceeding it surfaces
# as status 124, which fails every assertion below.
run_the_loop() {
    run timeout 60 "${LOOP_SRC}/run.sh" --repo "${REPO}" "$@"
}

# How many times the agent was launched. A fresh process per Iteration is the
# mechanism the technique rests on, and the counter the fake increments on each
# invocation is the externally observable form of it.
agent_invocations() {
    if [[ -f ${FAKE_AGENT_STATE} ]]; then
        cat -- "${FAKE_AGENT_STATE}"
    else
        printf '0\n'
    fi
}

progress_log() {
    if [[ -f "${REPO}/PROGRESS.md" ]]; then
        cat -- "${REPO}/PROGRESS.md"
    else
        # The Run ends merge-clean: the scaffolding is removed from the branch
        # tip in a cleanup commit, so the record lives in the branch's history.
        # The Run-ended commit's tree is the last one that carries it.
        git -C "${REPO}" show \
            "$(git -C "${REPO}" log --format='%H' --grep='Loop: Run ended' | head -n 1):PROGRESS.md"
    fi
}

git_log() {
    git -C "${REPO}" log --format='%s'
}
