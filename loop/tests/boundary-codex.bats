#!/usr/bin/env bats
#
# The third agent adapter's offline suite: an Iteration inside the Execution
# Boundary, under OpenAI Codex (#12).
#
# A sibling of tests/boundary.bats and tests/boundary-grok.bats rather than a
# parameterisation of them, and that is the point of ADR 0012 rather than
# duplication. A property belongs to an agent until two agents have shown it;
# shared properties are asserted twice on purpose, and each adapter's own
# vendor facts have nowhere else to be said.
#
# Nothing here needs a hypervisor, an image, a network or a model.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    AGENT="${LOOP_SRC}/agents/codex.sh"

    FAKE_SBX_STATE="${BATS_TEST_TMPDIR}/sbx"
    export FAKE_SBX_STATE
    export FAKE_SBX_BEHAVIOUR=ok
    export LOOP_SBX_COMMAND="${LOOP_SRC}/tests/fake-sbx.sh"

    # The box, as the account a Run executes as holds it - with the Codex
    # subscription credential.
    BOX_HOME="${BATS_TEST_TMPDIR}/home/loop"
    mkdir -p "${BOX_HOME}/.codex" "${BOX_HOME}/.ssh" "${BOX_HOME}/.config/loop"
    printf '{"auth_mode": "chatgpt", "OPENAI_API_KEY": null, "tokens": {"access_token": "not-a-real-token"}}\n' \
        >"${BOX_HOME}/.codex/auth.json"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519"
    printf 'ssh-ed25519 AAAA loop@loop\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519.pub"
    printf 'jstephens@etadventures.com ssh-ed25519 AAAA\n' >"${BOX_HOME}/.config/loop/allowed_signers"
    printf 'github_pat_11EXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    printf '[user]\n\temail = jstephens@etadventures.com\n\tsigningkey = %s\n' \
        "${BOX_HOME}/.ssh/loop_signing_ed25519.pub" >"${BOX_HOME}/.gitconfig"
    printf '[gpg]\n\tformat = ssh\n[gpg "ssh"]\n\tallowedSignersFile = %s\n' \
        "${BOX_HOME}/.config/loop/allowed_signers" >>"${BOX_HOME}/.gitconfig"
    printf '[commit]\n\tgpgsign = true\n' >>"${BOX_HOME}/.gitconfig"
    export BOX_HOME

    export LOOP_CODEX_HOME="${BOX_HOME}/.codex"
    export LOOP_SIGNING_KEY="${BOX_HOME}/.ssh/loop_signing_ed25519"
    export LOOP_GITCONFIG="${BOX_HOME}/.gitconfig"
    export LOOP_ALLOWED_SIGNERS="${BOX_HOME}/.config/loop/allowed_signers"

    WORKSPACE="${BATS_TEST_TMPDIR}/tourbot"
    mkdir -p "${WORKSPACE}"

    PROMPT="${BATS_TEST_TMPDIR}/prompt"
    printf 'You are Iteration 1 of at most 5 in an unattended Run.\n' >"${PROMPT}"
}

run_an_iteration() {
    local -a clear=()
    local name
    while IFS= read -r name; do clear+=(-u "${name}"); done < <("${AGENT}" --metered-env-names)
    run env "${clear[@]}" bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
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
    [[ "$(calls)" == *"create "*" codex ${WORKSPACE}"* ]]
}

@test "the boundary is built from the guest template the adapter declares" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"-t $("${AGENT}" --guest-template) codex ${WORKSPACE}"* ]]
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
    local -a clear=()
    local name
    while IFS= read -r name; do clear+=(-u "${name}"); done < <("${AGENT}" --metered-env-names)
    FAKE_SBX_BEHAVIOUR=agent-hangs run env "${clear[@]}" timeout --kill-after=5s 2s \
        bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
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

@test "the agent acts without prompting, because nobody is there to answer one" {
    run_an_iteration
    [[ "$(calls)" == *"--permission-mode bypassPermissions"* ]]
}

@test "the Loop's scripts are mounted so an Iteration can run the check" {
    run_an_iteration
    [[ "$(calls)" == *"${LOOP_SRC}:ro"* ]]
}

@test "the Loop's scripts are mounted read-only" {
    run_an_iteration
    [[ "$(calls)" != *"${LOOP_SRC} "* ]]
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
    [[ "$(calls)" == *"cp ${BOX_HOME}/.codex/auth.json "*":/home/agent/.codex/auth.json"* ]]
}

@test "the signing key goes in, so an Iteration's commits are Verified" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.ssh/loop_signing_ed25519 "*":/home/agent/.ssh/loop_signing_ed25519"* ]]
    [[ -f "${FAKE_SBX_STATE}.copied..gitconfig" ]]
}

@test "the credentials go to the guest's own home, not to a host path" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"cp ${BOX_HOME}/.ssh/loop_signing_ed25519 "*":/home/agent/.ssh/loop_signing_ed25519"* ]]
    [[ "$(calls)" != *":${BOX_HOME}/"* ]]
}

@test "a workspace deeper than one level under \$HOME still gets its credentials" {
    WORKSPACE="${BATS_TEST_TMPDIR}/work/tourbot"
    mkdir -p "${WORKSPACE}"
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$output" != *"Permission denied"* ]]
    [[ "$(calls)" == *"--max-turns"* ]]
}

@test "the git identity that goes in names the key at its guest path" {
    run_an_iteration
    [ "$status" -eq 0 ]
    run cat "${FAKE_SBX_STATE}.copied..gitconfig"
    [[ "$output" == *"/home/agent/.ssh/loop_signing_ed25519.pub"* ]]
    [[ "$output" == *"/home/agent/.config/loop/allowed_signers"* ]]
    [[ "$output" == *"jstephens@etadventures.com"* ]]
    [[ "$output" != *"${BOX_HOME}/.ssh"* ]]
}

@test "the GitHub token does not go in" {
    run_an_iteration
    [[ "$(calls)" != *"github-token"* ]]
}

@test "a box missing the signing key fails the Iteration rather than running it" {
    rm -f "${BOX_HOME}/.ssh/loop_signing_ed25519"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"loop_signing_ed25519"* ]]
    [[ "$(calls)" != *"--max-turns"* ]]
}

@test "a box missing the git identity fails the Iteration rather than running it" {
    rm -f "${BOX_HOME}/.gitconfig"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$(calls)" != *"--max-turns"* ]]
}

@test "a box with no model credential does not build a boundary at all" {
    rm -f "${BOX_HOME}/.codex/auth.json"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"loop-codex-login.sh"* ]]
    [ -z "$(calls)" ]
}

@test "a boundary that cannot be created fails the Iteration" {
    FAKE_SBX_BEHAVIOUR=create-fails run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$(calls)" != *" exec "* ]]
}

@test "a box missing a declared image fails at boundary creation naming the play" {
    LOOP_GUEST_TEMPLATE="custom-target:1" FAKE_SBX_BEHAVIOUR=create-fails run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"could not create the Execution Boundary for this Iteration from custom-target:1"* ]]
    [[ "$output" == *"apply ansible/loop.yml - role loop_guest_template builds it."* ]]
    [[ "$(calls)" != *" exec "* ]]
}

@test "an Iteration for a declared target runs inside the boundary against that target's checkout" {
    local target_repo="${BATS_TEST_TMPDIR}/widgets-checkout"
    mkdir -p "${target_repo}"
    WORKSPACE="${target_repo}" run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"create "*" codex ${target_repo}"* ]]
    [[ "$(calls)" == *"exec --workdir ${target_repo}"* ]]
}

@test "an Iteration will not run beside the boundary when there is no sbx" {
    LOOP_SBX_COMMAND="${BATS_TEST_TMPDIR}/absent" run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"Execution Boundary"* ]]
}

# --- The metered key, which has more than one door ---------------------------

@test "a metered API key fails the Iteration before a boundary is built" {
    run env OPENAI_API_KEY=not-a-real-key bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"metered"* ]]
    [ -z "$(calls)" ]
}

@test "every name that would supersede the subscription fails the Iteration" {
    while IFS= read -r name; do
        run env "${name}=not-a-real-key" bash -c \
            "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
        [ "$status" -eq 1 ]
        [[ "$output" == *"${name}"* ]]
        [ -z "$(calls)" ]
    done < <("${AGENT}" --metered-env-names)
}

@test "the guard names every key that is set, not only the first" {
    run env OPENAI_API_KEY=k CODEX_API_KEY=k bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"OPENAI_API_KEY"* ]]
    [[ "$output" == *"CODEX_API_KEY"* ]]
}

@test "an auth.json that carries an OPENAI_API_KEY fails the Iteration" {
    printf '{"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-not-real"}\n' \
        >"${BOX_HOME}/.codex/auth.json"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"OPENAI_API_KEY"* ]]
    [ -z "$(calls)" ]
}

@test "a config that carries a model api_key fails the Iteration" {
    printf 'api_key = "sk-not-real"\n' >"${BOX_HOME}/.codex/config.toml"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"api_key"* ]]
    [ -z "$(calls)" ]
}

@test "a config that carries no key runs normally" {
    printf 'model = "o3"\n' >"${BOX_HOME}/.codex/config.toml"
    run_an_iteration
    [ "$status" -eq 0 ]
}

# --- The turn bound is the adapter's to recognise ----------------------------

@test "an agent that ran out of turns exits the turn-bound status, not a failure" {
    FAKE_SBX_BEHAVIOUR=turn-bound run_an_iteration
    [ "$status" -eq 33 ]
}

@test "an agent that failed for another reason keeps its own status" {
    FAKE_SBX_BEHAVIOUR=agent-fails run_an_iteration
    [ "$status" -eq 3 ]
}

@test "the agent's output still reaches the caller, so a fault is diagnosable" {
    FAKE_SBX_BEHAVIOUR=turn-bound run_an_iteration
    [[ "$output" == *"turn limit reached"* ]]
}

@test "the boundary is destroyed when the turn bound fires" {
    FAKE_SBX_BEHAVIOUR=turn-bound run_an_iteration
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

# --- Egress and missing host reporting ---------------------------------------

@test "a run that fails for a missing host names it" {
    FAKE_SBX_BEHAVIOUR=missing-host run_an_iteration
    [ "$status" -ne 0 ]
    [[ "$output" == *"api.openai.com:443"* ]]
    [[ "$output" == *"egress allowlist"* ]]
}

# --- The swap is one line ----------------------------------------------------

@test "the adapters answer to the same contract" {
    run "${LOOP_SRC}/agents/codex.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"<prompt-file> <max-turns>"* ]]
    run "${LOOP_SRC}/agents/grok.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"<prompt-file> <max-turns>"* ]]
    run "${LOOP_SRC}/agents/claude.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"<prompt-file> <max-turns>"* ]]
}

@test "the adapter answers the guest template it declares" {
    run "${AGENT}" --guest-template
    [ "$status" -eq 0 ]
    [ "$output" = "loop-php:1" ]
}

@test "an adapter with no credential clock answers nothing to --credential-expiry" {
    run "${AGENT}" --credential-expiry
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

@test "the swap is one line: nothing but the default names an adapter" {
    run bash -c "grep -vE '^[[:space:]]*#' '${LOOP_SRC}/run.sh' | grep -c 'agents/'"
    [ "$output" -eq 1 ]

    run bash -c "grep -vE '^[[:space:]]*#' '${LOOP_SRC}/contract.sh' | grep -c 'agents/'"
    [ "$output" -eq 0 ]
}
