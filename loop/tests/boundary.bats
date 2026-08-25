#!/usr/bin/env bats
#
# The agent adapter's offline suite: an Iteration inside the Execution Boundary
# (#83).
#
# ADR 0003 makes the boundary the one subsystem Attendedness re-earns - nobody
# is watching a Run, so it is the only control still standing - and the Loop
# deliberately knows nothing about it. All of it lives in agents/claude.sh, so
# that is what this drives, through a scripted fake `sbx`.
#
# What is asserted is what an Iteration does to the boundary: that one is
# created for it, that what goes inside is what should and nothing that should
# not, and that it is destroyed afterwards even when the agent fails or the
# Iteration is killed. Nothing here needs a hypervisor, an image, or a model.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    AGENT="${LOOP_SRC}/agents/claude.sh"

    FAKE_SBX_STATE="${BATS_TEST_TMPDIR}/sbx"
    export FAKE_SBX_STATE
    export FAKE_SBX_BEHAVIOUR=ok
    export LOOP_SBX_COMMAND="${LOOP_SRC}/tests/fake-sbx.sh"

    # The box, as the account a Run executes as holds it: the four credentials
    # in the places ansible puts them.
    BOX_HOME="${BATS_TEST_TMPDIR}/home/loop"
    mkdir -p "${BOX_HOME}/.claude" "${BOX_HOME}/.ssh" "${BOX_HOME}/.config/loop"
    printf '{"access_token": "not-a-real-token"}\n' >"${BOX_HOME}/.claude/.credentials.json"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519"
    printf 'ssh-ed25519 AAAA loop@loop\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519.pub"
    printf 'jstephens@etadventures.com ssh-ed25519 AAAA\n' >"${BOX_HOME}/.config/loop/allowed_signers"
    printf 'github_pat_11EXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    printf '[user]\n\temail = jstephens@etadventures.com\n' >"${BOX_HOME}/.gitconfig"
    export BOX_HOME

    export LOOP_CLAUDE_CONFIG_DIR="${BOX_HOME}/.claude"
    export LOOP_SIGNING_KEY="${BOX_HOME}/.ssh/loop_signing_ed25519"
    export LOOP_GITCONFIG="${BOX_HOME}/.gitconfig"
    export LOOP_ALLOWED_SIGNERS="${BOX_HOME}/.config/loop/allowed_signers"

    WORKSPACE="${BATS_TEST_TMPDIR}/tourbot"
    mkdir -p "${WORKSPACE}"

    PROMPT="${BATS_TEST_TMPDIR}/prompt"
    printf 'You are Iteration 1 of at most 5 in an unattended Run.\n' >"${PROMPT}"
}

run_an_iteration() {
    run env -u ANTHROPIC_API_KEY bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
}

calls() {
    if [[ -f "${FAKE_SBX_STATE}.calls" ]]; then
        cat -- "${FAKE_SBX_STATE}.calls"
    fi
}

# --- One microVM per Iteration ----------------------------------------------

@test "an Iteration gets a boundary of its own, for the repository it works in" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"create "*" claude ${WORKSPACE}"* ]]
}

@test "the boundary is destroyed when the Iteration ends" {
    run_an_iteration
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

@test "the boundary is destroyed even when the agent inside it fails" {
    FAKE_SBX_BEHAVIOUR=agent-fails run_an_iteration
    [ "$status" -eq 3 ]
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

@test "the boundary is destroyed when the Iteration is killed at its wall clock" {
    # This is the Termination Contract's Iteration timeout, arriving as a signal
    # the way run.sh's `timeout` sends it. A sandbox left behind by every killed
    # Iteration would accumulate on the box until nothing could start.
    FAKE_SBX_BEHAVIOUR=agent-hangs run env -u ANTHROPIC_API_KEY \
        timeout --kill-after=5s 2s bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

@test "two Iterations do not share a boundary" {
    run_an_iteration
    first="$(grep '^create ' "${FAKE_SBX_STATE}.calls" | head -n1)"
    sleep 1
    run_an_iteration
    second="$(grep '^create ' "${FAKE_SBX_STATE}.calls" | tail -n1)"
    [ "${first}" != "${second}" ]
}

# --- What the agent is asked for ---------------------------------------------

@test "the agent runs with the turn bound the Contract gave it" {
    run_an_iteration
    [[ "$(calls)" == *"--max-turns 40"* ]]
}

@test "the agent edits without prompting, because nobody is there to answer one" {
    run_an_iteration
    [[ "$(calls)" == *"--permission-mode acceptEdits"* ]]
}

@test "the agent is given the Iteration's prompt" {
    run_an_iteration
    [[ "$(calls)" == *"You are Iteration 1 of at most 5"* ]]
}

@test "the agent runs with the repository as its working directory" {
    run_an_iteration
    [[ "$(calls)" == *"--workdir ${WORKSPACE}"* ]]
}

@test "the agent's exit status is the Iteration's" {
    FAKE_SBX_BEHAVIOUR=agent-fails run_an_iteration
    [ "$status" -eq 3 ]
}

# --- What goes inside the boundary, and what does not ------------------------

@test "the model credential goes in, so the agent can run at all" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.claude/.credentials.json"* ]]
}

@test "the signing key goes in, so an Iteration's commits are Verified" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.ssh/loop_signing_ed25519 "* ]]
    [[ "$(calls)" == *"cp ${BOX_HOME}/.gitconfig"* ]]
}

# The Proposal-Only Output property, as a property of what is inside the
# boundary rather than of what the prompt asked for. The push and the draft pull
# request happen on the host after every agent process is gone, so an agent
# inside the boundary has nothing to push with.
@test "the GitHub token does not go in" {
    run_an_iteration
    [[ "$(calls)" != *"github-token"* ]]
}

@test "a box with no model credential does not build a boundary at all" {
    rm -f "${BOX_HOME}/.claude/.credentials.json"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"loop-claude-login.sh"* ]]
    [ -z "$(calls)" ]
}

@test "a boundary that cannot be created fails the Iteration" {
    FAKE_SBX_BEHAVIOUR=create-fails run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$(calls)" != *" exec "* ]]
}

@test "an Iteration will not run beside the boundary when there is no sbx" {
    LOOP_SBX_COMMAND="${BATS_TEST_TMPDIR}/absent" run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"Execution Boundary"* ]]
}

@test "a metered API key fails the Iteration before a boundary is built" {
    run env ANTHROPIC_API_KEY=sk-not-a-real-key bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"metered"* ]]
    [ -z "$(calls)" ]
}
