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
    # The real shape, since #260: an Iteration renews this before copying it
    # in, so a placeholder with no expiry in it would now fail every test here
    # for a reason none of them is about. Eight hours is a freshly-minted
    # session, which is the state the renewal leaves alone. Written by the same
    # helper the credential tests use, so the shape this suite argues a reader
    # can get wrong is spelled out once.
    credential_expiring_in 28800
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519"
    printf 'ssh-ed25519 AAAA loop@loop\n' >"${BOX_HOME}/.ssh/loop_signing_ed25519.pub"
    printf 'jstephens@etadventures.com ssh-ed25519 AAAA\n' >"${BOX_HOME}/.config/loop/allowed_signers"
    printf 'github_pat_11EXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    # The identity as `ansible/roles/loop_credentials` writes it, rather than an
    # email on its own: the two settings that name a FILE are the ones #268 is
    # about, and a fixture without them cannot tell whether the copy that goes
    # into the guest still points at paths that only exist on the host.
    printf '[user]\n\temail = jstephens@etadventures.com\n\tsigningkey = %s\n' \
        "${BOX_HOME}/.ssh/loop_signing_ed25519.pub" >"${BOX_HOME}/.gitconfig"
    printf '[gpg]\n\tformat = ssh\n[gpg "ssh"]\n\tallowedSignersFile = %s\n' \
        "${BOX_HOME}/.config/loop/allowed_signers" >>"${BOX_HOME}/.gitconfig"
    printf '[commit]\n\tgpgsign = true\n' >>"${BOX_HOME}/.gitconfig"
    export BOX_HOME

    export LOOP_CLAUDE_CONFIG_DIR="${BOX_HOME}/.claude"
    export LOOP_SIGNING_KEY="${BOX_HOME}/.ssh/loop_signing_ed25519"
    export LOOP_GITCONFIG="${BOX_HOME}/.gitconfig"
    export LOOP_ALLOWED_SIGNERS="${BOX_HOME}/.config/loop/allowed_signers"

    # An inert renewal, so that nothing in this suite can reach a network even
    # if a test constructs a credential the adapter decides to renew. Every
    # test that is ABOUT the renewal overrides this with one that mints.
    printf '#!/usr/bin/env bash\nexit 66\n' >"${BATS_TEST_TMPDIR}/inert-renewal"
    chmod +x "${BATS_TEST_TMPDIR}/inert-renewal"
    export LOOP_CLAUDE_REFRESH_COMMAND="${BATS_TEST_TMPDIR}/inert-renewal"

    WORKSPACE="${BATS_TEST_TMPDIR}/tourbot"
    mkdir -p "${WORKSPACE}"

    PROMPT="${BATS_TEST_TMPDIR}/prompt"
    printf 'You are Iteration 1 of at most 5 in an unattended Run.\n' >"${PROMPT}"
}

# Every name the adapter guards is cleared here rather than assumed absent: the
# suite has to be runnable in a vaulted-agent session on the orchestration VM,
# where a metered model key plausibly is in the environment. Read off the
# adapter rather than restated, so a name added to its list is cleared here too
# (#84).
run_an_iteration() {
    local -a clear=()
    local name
    while IFS= read -r name; do clear+=(-u "${name}"); done < <("${AGENT}" --metered-env-names)
    run env "${clear[@]}" bash -c \
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

@test "the boundary is built from the guest template the adapter declares" {
    # Read off the adapter rather than restated, for the reason every other
    # cross-file value in this suite is: the box card on /loop reports what
    # `--guest-template` answers (#156), and a test that spelled the tag out
    # would keep passing on the day the create started using a different one.
    #
    # `-t <image>` and the `claude` after it are two different arguments and
    # both are asserted. The image is the PHP-capable guest (#164); `claude` is
    # still the agent whose kit `sbx` attaches, and dropping it would take the
    # boundary's six Anthropic egress rules with it.
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"-t $("${AGENT}" --guest-template) claude ${WORKSPACE}"* ]]
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
    FAKE_SBX_BEHAVIOUR=agent-hangs run env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
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

# `acceptEdits` gates Bash, so an Iteration under it cannot `git add` - and an
# Iteration that cannot commit is a No-op by the Loop's own definition. The
# first Run found that the hard way; the boundary, not a prompt, is the control
# (ADR 0003).
@test "the agent acts without prompting, because nobody is there to answer one" {
    run_an_iteration
    [[ "$(calls)" == *"--permission-mode bypassPermissions"* ]]
}

# Backpressure an Iteration cannot reach is not backpressure: a sandbox mounts
# the workspace and nothing else, so the Plan's completeness check was outside
# the session's allowed directories for the whole of the first Run.
@test "the Loop's scripts are mounted so an Iteration can run the check" {
    run_an_iteration
    [[ "$(calls)" == *"${LOOP_SRC}:ro"* ]]
}

# Read-only, because the check is what says the work did not land, and an agent
# that could edit it could make it say otherwise.
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

@test "the model credential goes in as an environment token, and no credential file is copied" {
    run_an_iteration
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"CLAUDE_CODE_OAUTH_TOKEN="* ]]
    [[ "$(calls)" != *"cp "*".credentials.json"* ]]
    [[ ! -f "${FAKE_SBX_STATE}.copied..credentials.json" ]]
}

@test "an Iteration refuses to start when no model credential is held" {
    rm -f "${BOX_HOME}/.claude/.credentials.json"
    run env -u CLAUDE_CODE_OAUTH_TOKEN -u LOOP_CLAUDE_TOKEN_FILE \
        bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"no model credential"* ]]
}

@test "the signing key goes in, so an Iteration's commits are Verified" {
    run_an_iteration
    [[ "$(calls)" == *"cp ${BOX_HOME}/.ssh/loop_signing_ed25519 "* ]]
    [[ -f "${FAKE_SBX_STATE}.copied..gitconfig" ]]
}

# --- Where in the guest the credentials land (#268) --------------------------
#
# The signing key used to be placed at its HOST path, and that only worked
# because of where the workspace happened to be: `sbx` synthesises a bind
# mount's parent directories and owns them by depth, so `/home/loop` is the
# guest account's when the workspace is one level under it and root's when it
# is two. `loop_scripts_workspace: /home/loop/tourbot` is one level, so every
# real Run worked and the coupling was invisible.
#
# What it cost when it did fire was the worst shape available: an Iteration
# that died before the agent started, with a message naming the signing key,
# for a fault whose cause is the workspace argument.
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

# The key is placed somewhere the host's git config does not name, so the copy
# of that config which goes in has to name the new place instead. Without this
# the guest signs with a key it cannot find, and the first sighting of that is
# a pull request whose commits are not Verified.
@test "the git identity that goes in names the key at its guest path" {
    run_an_iteration
    [ "$status" -eq 0 ]
    run cat "${FAKE_SBX_STATE}.copied..gitconfig"
    [[ "$output" == *"/home/agent/.ssh/loop_signing_ed25519.pub"* ]]
    [[ "$output" == *"/home/agent/.config/loop/allowed_signers"* ]]
    [[ "$output" == *"jstephens@etadventures.com"* ]]
    [[ "$output" != *"${BOX_HOME}/.ssh"* ]]
}

# The Proposal-Only Output property, as a property of what is inside the
# boundary rather than of what the prompt asked for. The push and the draft pull
# request happen on the host after every agent process is gone, so an agent
# inside the boundary has nothing to push with.
@test "the GitHub token does not go in" {
    run_an_iteration
    [[ "$(calls)" != *"github-token"* ]]
}

# Skipping a missing credential would produce an Iteration that runs, commits,
# and lands commits that are unsigned or attributed to nobody - which is not
# recoverable afterwards, and whose first sighting would be the pull request.
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
    [[ "$(calls)" == *"create "*" claude ${target_repo}"* ]]
    [[ "$(calls)" == *"exec --workdir ${target_repo}"* ]]
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

# --- The turn bound is the adapter's to recognise ----------------------------
#
# ADR 0004 puts vendor concerns here: which message means the turn bound fired
# is a property of Claude Code, and the Contract declares only the exit status
# the two sides share.

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
    [[ "$output" == *"Reached max turns"* ]]
}

@test "the boundary is destroyed when the turn bound fires" {
    FAKE_SBX_BEHAVIOUR=turn-bound run_an_iteration
    [[ "$(calls)" == *"rm --force loop-"* ]]
}

# --- The model credential's clock (#260) -------------------------------------
#
# The subscription login expires eight hours after it is minted and nothing
# inside the boundary can renew the host's copy - the copy dies with the
# microVM (ADR 0011). So the adapter answers two questions about it, and both
# live here for the reason `--guest-template` does: the file, its shape and
# what renews it are vendor facts, and ADR 0004 puts vendor facts in the
# adapter.

# Write a credential whose access token expires SECONDS from now. The shape is
# the real one - the field is nested, sits beside a longer name ending in the
# same word, and is in milliseconds - because every one of those three is
# something a reader of this file could get wrong.
credential_expiring_in() {
    printf '{"claudeAiOauth":{"accessToken":"not-a-real-token","refreshTokenExpiresAt":%s000,"expiresAt":%s000,"subscriptionType":"max"}}\n' \
        "$(($(date +%s) + 1814400))" "$(($(date +%s) + $1))" \
        >"${BOX_HOME}/.claude/.credentials.json"
}

# The instant SECONDS from now, in the shape the adapter prints.
instant_in() { date -u -d "@$(($(date +%s) + $1))" +%Y-%m-%dT%H:%M:%SZ; }

@test "the adapter says when the box's model credential stops working" {
    credential_expiring_in 28800
    run "${AGENT}" --credential-expiry
    [ "$status" -eq 0 ]
    # An absolute instant, not a duration. The Selector reads this once a
    # cycle and the page is viewed whenever: a "7h left" read at 02:00 and
    # shown at 09:00 would be a card asserting something nobody observed.
    [[ "$output" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]
    [ "$output" = "$(instant_in 28800)" ]
}

@test "the expiry is read from expiresAt and not from the refresh token beside it" {
    # `refreshTokenExpiresAt` ends in the same word and is three weeks out. A
    # reader that matched it would report a box as good for twenty-one days
    # when its access token dies tonight - the exact failure this fact exists
    # to catch, reported as its own opposite.
    credential_expiring_in 60
    run "${AGENT}" --credential-expiry
    [ "$status" -eq 0 ]
    [ "$output" = "$(instant_in 60)" ]
}

@test "a box with no model credential answers nothing rather than a bad expiry" {
    # "Could not be answered" and "expired" are different, and the card says
    # so differently. A missing credential that reported an expiry in the past
    # would page for a clock rather than for a login.
    rm -f "${BOX_HOME}/.claude/.credentials.json"
    run "${AGENT}" --credential-expiry
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

@test "a credential with no expiry in it answers nothing rather than guessing" {
    printf '{"claudeAiOauth":{"accessToken":"not-a-real-token"}}\n' \
        >"${BOX_HOME}/.claude/.credentials.json"
    run "${AGENT}" --credential-expiry
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

# --- The yearly credential and metered key guards ---------------------------

@test "asking which names are metered still answers when one of them is set" {
    # The guard must not cover this option. `assert-credentials.sh` reads the
    # names off every adapter to grade the box, and a box that HAS a metered
    # key set is exactly the box it most needs to grade - an adapter that
    # refused to answer would shorten the family it was checking.
    run env ANTHROPIC_API_KEY=sk-not-a-real-key "${AGENT}" --metered-env-names
    [ "$status" -eq 0 ]
    [[ "$output" == *"ANTHROPIC_API_KEY"* ]]
}

@test "an Iteration refuses to start when a metered API key is set" {
    run env ANTHROPIC_API_KEY=sk-not-a-real-key \
        bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 1 ]
    [[ "$output" == *"metered"* ]]
    [ -z "$(calls)" ]
}

@test "an environment CLAUDE_CODE_OAUTH_TOKEN is passed to the guest" {
    run env CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-specific-env-token \
        bash -c "cd '${WORKSPACE}' && '${AGENT}' '${PROMPT}' 40"
    [ "$status" -eq 0 ]
    [[ "$(calls)" == *"CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-specific-env-token"* ]]
}

@test "the adapter does not offer per-iteration renewal" {
    run "${AGENT}" --refresh-credential
    [ "$status" -ne 0 ]
}
