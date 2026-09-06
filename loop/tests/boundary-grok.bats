#!/usr/bin/env bats
#
# The second agent adapter's offline suite: an Iteration inside the Execution
# Boundary, under Grok Build (#84).
#
# A sibling of tests/boundary.bats rather than a parameterisation of it, and
# that is the point of the ticket rather than duplication. #84 exists to find
# out which of the first Run's properties belonged to the technique and which
# belonged to Claude Code, and a shared suite would have to be written in the
# vocabulary the two agents have in common - which is exactly the vocabulary
# that hides the difference. What the two files share is asserted twice on
# purpose; what only one of them can say has nowhere else to be said.
#
# Nothing here needs a hypervisor, an image, a network or a model.

setup() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    AGENT="${LOOP_SRC}/agents/grok.sh"

    FAKE_SBX_STATE="${BATS_TEST_TMPDIR}/sbx"
    export FAKE_SBX_STATE
    export FAKE_SBX_BEHAVIOUR=ok
    export LOOP_SBX_COMMAND="${LOOP_SRC}/tests/fake-sbx.sh"

    # The box, as the account a Run executes as holds it - with the second
    # agent's credential in place of the first's. `auth.json` rather than
    # `.credentials.json` is not a detail: it is an OAuth session with a refresh
    # token in it, which is why `auth.x.ai` is on this agent's egress set and
    # why the credential-injection property does not hold here.
    BOX_HOME="${BATS_TEST_TMPDIR}/home/loop"
    mkdir -p "${BOX_HOME}/.grok" "${BOX_HOME}/.ssh" "${BOX_HOME}/.config/loop"
    printf '{"https://auth.x.ai::client": {"key": "not-a-real-token"}}\n' >"${BOX_HOME}/.grok/auth.json"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519"
    printf 'ssh-ed25519 AAAA loop@loop\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519.pub"
    printf 'jstephens@etadventures.com ssh-ed25519 AAAA\n' >"${BOX_HOME}/.config/loop/allowed_signers"
    printf 'github_pat_11EXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    printf '[user]\n\temail = jstephens@etadventures.com\n' >"${BOX_HOME}/.gitconfig"
    export BOX_HOME

    export LOOP_GROK_HOME="${BOX_HOME}/.grok"
    export LOOP_SIGNING_KEY="${BOX_HOME}/.ssh/loop_signing_ed25519"
    export LOOP_GITCONFIG="${BOX_HOME}/.gitconfig"
    export LOOP_ALLOWED_SIGNERS="${BOX_HOME}/.config/loop/allowed_signers"

    WORKSPACE="${BATS_TEST_TMPDIR}/tourbot"
    mkdir -p "${WORKSPACE}"

    PROMPT="${BATS_TEST_TMPDIR}/prompt"
    printf 'You are Iteration 1 of at most 5 in an unattended Run.\n' >"${PROMPT}"

    PINNED="$("${AGENT}" --pinned-version)"
    export PINNED
}

# The suite has to be runnable in a vaulted-agent session on the orchestration
# VM, where TOURBOT_PREVIEW_XAI_API_KEY lives and a plain XAI_API_KEY plausibly
# might - so every guarded name is cleared here rather than assumed absent.
# Read off the adapter, not restated: a name added to its list must be cleared
# here too, and finding that out from a suite that mysteriously fails is worse
# than never having written this comment.
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

# --- One microVM per Iteration, and it is agent-less --------------------------

# The difference that starts everything else. `sbx create` has no Grok template,
# so the boundary is a `shell` one and the agent is installed within it - which
# is what puts an install host on the egress allowlist for this agent and not
# for the other.
@test "an Iteration gets an agent-less boundary of its own, for the repository it works in" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"create "*" shell ${WORKSPACE}"* ]]
    [[ "$(calls)" != *" claude ${WORKSPACE}"* ]]
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
    FAKE_SBX_BEHAVIOUR=agent-hangs run timeout --kill-after=5s 3s \
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

# --- The agent is installed inside, at the pin -------------------------------

@test "the agent is installed inside the boundary at the pinned version" {
    run_an_iteration
    [[ "$(calls)" == *"install.sh | bash -s ${PINNED}"* ]]
}

# An installer that quietly fell back to a channel pointer would put an
# unobserved version inside an unattended Run, and the Termination Contract's
# numbers are calibrated against a version. Asking for the pin is not the same
# claim as holding it.
@test "a boundary holding a version other than the pin does not run an Iteration" {
    FAKE_GROK_VERSION=9.9.9 run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"9.9.9"* ]]
    [[ "$output" == *"${PINNED}"* ]]
    [[ "$(calls)" != *"--max-turns"* ]]
}

# The failure an operator will actually meet, because `x.ai` was on the list of
# hosts #100 deliberately left OFF - as a cosmetic changelog fetch. Installing
# inside the boundary is what makes it required, so the message names the file
# the fix goes in rather than leaving a curl error to be interpreted.
@test "an install the boundary's egress refused names the allowlist" {
    FAKE_SBX_BEHAVIOUR=install-fails run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"egress allowlist"* ]]
    [[ "$(calls)" != *"--max-turns"* ]]
}

# The credential must not be placed before the installer runs: it writes into
# the same directory the credential lives in, and a step that could clobber the
# credential must not run once it is there.
@test "the agent is installed before the credential is placed" {
    run_an_iteration
    install_line="$(grep -n 'install.sh' "${FAKE_SBX_STATE}.calls" | head -n1 | cut -d: -f1)"
    cred_line="$(grep -n 'cp .*auth.json' "${FAKE_SBX_STATE}.calls" | head -n1 | cut -d: -f1)"
    [ -n "${install_line}" ]
    [ -n "${cred_line}" ]
    [ "${install_line}" -lt "${cred_line}" ]
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
    [[ "$(calls)" == *"cp ${PROMPT} "* ]]
    [[ "$(calls)" == *"--prompt-file /home/agent/prompt"* ]]
}

@test "the agent runs with the repository as its working directory" {
    run_an_iteration
    [[ "$(calls)" == *"--workdir ${WORKSPACE}"* ]]
}

@test "the agent's exit status is the Iteration's" {
    FAKE_SBX_BEHAVIOUR=agent-fails run_an_iteration
    [ "$status" -eq 3 ]
}

# Three settings, each turning off a thing that would reach a host `deny-all`
# blocks - and the autoupdater, which would make the pinned version a lie one
# Iteration into a Run.
@test "the agent does not phone home, and does not update itself mid-Run" {
    run_an_iteration
    [[ "$(calls)" == *"GROK_TELEMETRY_ENABLED=0"* ]]
    [[ "$(calls)" == *"GROK_CHANGELOG_OFFLINE=1"* ]]
    [[ "$(calls)" == *"GROK_DISABLE_AUTOUPDATER=1"* ]]
}

# --- What goes inside the boundary, and what does not ------------------------

@test "the model credential goes in, so the agent can run at all" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.grok/auth.json"* ]]
}

@test "the signing key goes in, so an Iteration's commits are Verified" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.ssh/loop_signing_ed25519 "* ]]
    [[ "$(calls)" == *"cp ${BOX_HOME}/.gitconfig"* ]]
}

# Proposal-Only Output as a property of what is inside the boundary rather than
# of what the prompt asked for. It holds for this agent exactly as it holds for
# the other one, and that is worth an assertion rather than an assumption: it is
# the one first-Run property #84 must confirm transfers.
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
    rm -f "${BOX_HOME}/.grok/auth.json"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"loop-grok-login.sh"* ]]
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
    [[ "$(calls)" == *"create "*" shell ${target_repo}"* ]]
    [[ "$(calls)" == *"exec --workdir ${target_repo}"* ]]
}

@test "an Iteration will not run beside the boundary when there is no sbx" {
    LOOP_SBX_COMMAND="${BATS_TEST_TMPDIR}/absent" run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"Execution Boundary"* ]]
}

# --- The metered key, which has more than one door ---------------------------
#
# Spec issue #73 story 32, and the failure shape that broke Remote Control. The
# vendor's own README says the API key takes precedence over browser
# credentials, so none of these is theoretical.

@test "a metered API key fails the Iteration before a boundary is built" {
    run env XAI_API_KEY=not-a-real-key bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"metered"* ]]
    [ -z "$(calls)" ]
}

# Every name the vendor honours, not just the documented one. A guard that
# caught XAI_API_KEY and let GROK_DEPLOYMENT_KEY through would be a guard that
# reads as complete and is not.
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
    run env XAI_API_KEY=k GROK_DEPLOYMENT_KEY=k bash -c \
        "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"XAI_API_KEY"* ]]
    [[ "$output" == *"GROK_DEPLOYMENT_KEY"* ]]
}

# The file door. A per-model api_key in the config resolves ahead of the session
# token, and that file goes into the guest - so this is the same collision with
# nothing in the environment to show for it.
@test "a config that carries a model api_key fails the Iteration" {
    printf '[model.grok-build]\napi_key = "not-a-real-key"\n' >"${BOX_HOME}/.grok/config.toml"
    run_an_iteration
    [ "$status" -eq 1 ]
    [[ "$output" == *"api_key"* ]]
    [ -z "$(calls)" ]
}

@test "a config that carries no key runs normally" {
    printf '[ui]\nscreen_mode = "minimal"\n' >"${BOX_HOME}/.grok/config.toml"
    run_an_iteration
    [ "$status" -eq 0 ]
}

# --- The turn bound is the adapter's to recognise ----------------------------
#
# ADR 0004 puts vendor concerns here, and this is the clearest evidence for why:
# Grok Build prints `Error: max turns reached` where Claude Code prints
# `Error: Reached max turns (N)`. Same event, same exit status, different words.
# The Contract declares only the number the two sides share.

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
    [[ "$output" == *"max turns reached"* ]]
}

@test "the boundary is destroyed when the turn bound fires" {
    FAKE_SBX_BEHAVIOUR=turn-bound run_an_iteration
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

# --- The swap is one line ----------------------------------------------------
#
# Spec #73 user story 31, asserted rather than asserted-in-prose. The Loop reads
# LOOP_AGENT_COMMAND and knows nothing else about any agent; ADR 0004 is what
# made that true, and this is the test that would go red the day something in
# run.sh started knowing which vendor it was running.

@test "the two adapters answer to the same contract" {
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
    [ "$output" = "shell" ]
}

@test "an adapter with no credential clock answers nothing to --credential-expiry" {
    run "${AGENT}" --credential-expiry
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

@test "the swap is one line: nothing but the default names an adapter" {
    # Comments are excluded - run.sh and contract.sh both record what the first
    # Run learned about Claude Code, and that history is worth keeping. What
    # must stay at one line is the CODE that names an agent.
    run bash -c "grep -vE '^[[:space:]]*#' '${LOOP_SRC}/run.sh' | grep -c 'agents/'"
    [ "$output" -eq 1 ]

    # And the Contract names none at all: it declares the exit status the two
    # sides share and leaves which vendor says what to the adapter (ADR 0004).
    run bash -c "grep -vE '^[[:space:]]*#' '${LOOP_SRC}/contract.sh' | grep -c 'agents/'"
    [ "$output" -eq 0 ]
}

# --- What the guest's filesystem does not give you ---------------------------
#
# The signing key and the allowed-signers file go in at their HOST paths,
# because the git config naming them is the host's. Under the `shell` template
# `/home/loop` inside the guest is root-owned, so preparing that directory takes
# the guest's passwordless sudo - which the `claude` template does not need. A
# template difference, found by running it, and the failure without it is late:
# a boundary built, an agent installed, and an Iteration that dies placing a
# credential.
@test "the guest's directories are prepared with the privilege the template needs" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"sudo mkdir -p ${BOX_HOME}/.ssh"* ]]
    [[ "$(calls)" == *"sudo chown agent:agent ${BOX_HOME}/.ssh"* ]]
}
