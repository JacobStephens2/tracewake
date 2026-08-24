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
    cat -- "${REPO}/PROGRESS.md"
}

git_log() {
    git -C "${REPO}" log --format='%s'
}
