#!/usr/bin/env bats
#
# The credential inventory's offline suite (#81).
#
# Every test drives the real assert-credentials.sh against a constructed box and
# asserts only what the script externally produces: its exit code, the
# machine-readable result line, and which credential line it names. None of them
# names an internal function or depends on the order of the checks - the checks
# are a list, and reorganising that list should not be a red suite.
#
# The script is the thing that says the box holds what spec issue #73 claims it
# holds, so it is seamed and tested on its own for the same reason the
# completeness check is: a component that is itself an assertion should not have
# its correctness established only through whatever runs it.

load credential-helpers

setup() { setup_credential_fixture; }

# --- A clean box ------------------------------------------------------------

@test "a box holding exactly the declared credentials is clean" {
    run_assert
    [ "$status" -eq 0 ]
    [ "$(field CREDENTIALS_RESULT)" = "clean" ]
    [ "$(field CREDENTIALS_VIOLATIONS)" = "0" ]
}

@test "the report names every allowed credential, present or not" {
    run_assert
    [[ "$output" == *"github-token"* ]]
    [[ "$output" == *"signing-key"* ]]
    [[ "$output" == *"docker-identity"* ]]
    [[ "$output" == *"model-credential"* ]]
}

@test "a box with no model credential is a violation naming it" {
    rm -rf "${BOX_HOME}/.claude"
    run_assert
    [ "$status" -eq 2 ]
    [ "$(field CREDENTIALS_RESULT)" = "violations" ]
    [[ "$output" == *"model-credential"* ]]
}

# --- The model credential is graded on validity, not presence (#260) ---------
#
# `[held]` came from `[[ -f .credentials.json ]]` and read no field inside it,
# so a login that lapsed sixteen hours ago was a held credential by that test.
# It is the one row where presence and usefulness come apart on a clock rather
# than on an operator doing something: the subscription login expires eight
# hours after it is minted, and nothing on the box renews it on its own.

@test "a credential that has expired is not held" {
    write_credential -3600
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"model-credential"* ]]
    # The word matters as much as the exit code: an operator scanning this
    # report has to be able to tell "no login" from "a login that lapsed",
    # because they are different things to do about it.
    [[ "$output" == *"expired"* ]]
    [[ "$output" != *"[held]     model-credential"* ]]
}

@test "a credential expiring within the hour is still held" {
    # Graded on whether it works NOW, not on whether it will last. What a
    # dispatch does about a short one is the adapter's renewal, and a report
    # that called this a violation would be red on a box that is fine.
    write_credential 1800
    run_assert
    [ "$status" -eq 0 ]
    [ "$(field CREDENTIALS_RESULT)" = "clean" ]
}

@test "the expiry is read from expiresAt and not the refresh token beside it" {
    # The two fields are inverted here rather than both pointing the same way,
    # which is what makes this test say something the two either side of it do
    # not: a live access token beside a refresh token that died last week. A
    # reader matching the wrong name calls this box EXPIRED, and one matching
    # the right name calls it held - so the assertion can only pass one way
    # round.
    printf '{"claudeAiOauth":{"accessToken":"not-a-real-token","refreshTokenExpiresAt":%s000,"expiresAt":%s000}}\n' \
        "$(($(date +%s) - 604800))" "$(($(date +%s) + 28800))" \
        >"${BOX_HOME}/.claude/.credentials.json"
    run_assert
    [ "$status" -eq 0 ]
    [ "$(field CREDENTIALS_RESULT)" = "clean" ]
}

@test "a credential whose expiry cannot be read is not held either" {
    # Unknown is not green - the standard the guardrail chip already holds to.
    # A file whose shape this cannot read is one nobody can say anything about,
    # and saying "held" about it is the reassurance rather than the check.
    printf '{"claudeAiOauth":{"accessToken":"not-a-real-token"}}\n' \
        >"${BOX_HOME}/.claude/.credentials.json"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"model-credential"* ]]
    # And it is not reported as EXPIRED, which is the state it collapses into
    # the moment the unreadable branch stops existing: an unparsable expiry
    # compares as epoch zero. The two want different things done about them -
    # a lapsed login wants a renewal, a file nobody can parse wants a human -
    # so the report has to keep them apart.
    [[ "$output" == *"unreadable"* ]]
    [[ "$output" != *"the login lapsed"* ]]
}

@test "an expired credential is reported as expired rather than as absent" {
    # Two different things to do about them: absent names the login wizard,
    # expired names the renewal. A report that conflated them would send an
    # operator to re-do a login the box already has.
    write_credential -3600
    run_assert
    [[ "$output" != *"no agent login on the box"* ]]
}

# Claude Code writes ~/.claude.json the moment it is installed. Accepting it as
# a credential would report a login on a box where nobody logged in, and the Run
# would find out at its first Iteration - after the boundary was built.
# The Loop's box holds a checkout of the repository a Run works in, and
# `tourbot` carries three vendor sample keys in phpdocx's examples. Flagging
# them would make this family red on a correctly-built box, which is how a check
# stops being read.
# A checkout of the repository a Run works in, with an upstream it was cloned
# from - which is what the exemption below turns on.
make_checkout() {
    CHECKOUT="${BOX_HOME}/tourbot"
    UPSTREAM="${BATS_TEST_TMPDIR}/upstream.git"
    git init --quiet --bare --initial-branch=main "${UPSTREAM}"
    git init --quiet --initial-branch=main "${CHECKOUT}"
    git -C "${CHECKOUT}" config user.email "someone@example.invalid"
    git -C "${CHECKOUT}" config user.name "Someone"
    git -C "${CHECKOUT}" config commit.gpgsign false
    git -C "${CHECKOUT}" remote add origin "${UPSTREAM}"
}

publish_upstream() {
    git -C "${CHECKOUT}" add -A
    git -C "${CHECKOUT}" commit --quiet --message "$1"
    git -C "${CHECKOUT}" push --quiet origin main
    git -C "${CHECKOUT}" remote set-head origin main
}

@test "a private key that came with the clone is not a fleet key" {
    make_checkout
    mkdir -p "${CHECKOUT}/examples"
    printf -- '-----BEGIN RSA PRIVATE KEY-----\nsample\n' >"${CHECKOUT}/examples/Test.pem"
    publish_upstream "a vendor's sample key"

    run_assert
    [ "$status" -eq 0 ]
    [[ "$output" == *"skipped as upstream repository content"* ]]
    [[ "$output" == *"Test.pem"* ]]
}

# A key somebody put there is a key on this box whatever directory it landed in.
@test "an untracked private key inside a checkout is still a violation" {
    make_checkout
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nfleet\n' >"${CHECKOUT}/id_ed25519"

    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
    [[ "$output" == *"id_ed25519"* ]]
}

# The one an unattended agent could reach for. A Run commits into this same
# checkout, so an exemption keyed on "tracked" would let an Iteration put a key
# on the box and take it out of the sweep in the same move.
@test "a private key an Iteration committed is still a violation" {
    make_checkout
    printf 'nothing\n' >"${CHECKOUT}/README.md"
    publish_upstream "the repository as it was cloned"

    git -C "${CHECKOUT}" checkout --quiet -b loop/run-1
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nfleet\n' >"${CHECKOUT}/id_ed25519"
    git -C "${CHECKOUT}" add -A
    git -C "${CHECKOUT}" commit --quiet --message "Iteration 1: helpfully commit a key"

    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
    [[ "$output" == *"id_ed25519"* ]]
}

# No recorded default branch means nothing to compare against, and the safe
# direction is to report rather than to exempt.
@test "a checkout with no upstream default branch exempts nothing" {
    make_checkout
    printf -- '-----BEGIN RSA PRIVATE KEY-----\nsample\n' >"${CHECKOUT}/Test.pem"
    git -C "${CHECKOUT}" add -A
    git -C "${CHECKOUT}" commit --quiet --message "tracked, but nowhere upstream"

    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"Test.pem"* ]]
}

@test "an installed agent that was never logged in is not a model credential" {
    rm -rf "${BOX_HOME}/.claude"
    printf '{"hasCompletedOnboarding": false}\n' >"${BOX_HOME}/.claude.json"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"model-credential"* ]]
}

@test "--help explains the script and exits 0" {
    run "${ASSERT}" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"assert-credentials.sh"* ]]
}

# --- Could not run ----------------------------------------------------------

@test "an unknown argument is a could-not-run, not a violation" {
    run "${ASSERT}" --nonsense
    [ "$status" -eq 1 ]
}

@test "a home that does not exist is a could-not-run" {
    run "${ASSERT}" --home "${BATS_TEST_TMPDIR}/absent" --sbx "${FAKE_SBX}"
    [ "$status" -eq 1 ]
}

# --- The GitHub token -------------------------------------------------------

@test "a missing GitHub token is a violation naming it" {
    rm -f "${BOX_HOME}/.config/loop/github-token"
    run_assert
    [ "$status" -eq 2 ]
    [ "$(field CREDENTIALS_RESULT)" = "violations" ]
    [[ "$output" == *"github-token"* ]]
}

@test "an empty GitHub token file is a violation, not a present credential" {
    : >"${BOX_HOME}/.config/loop/github-token"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"github-token"* ]]
}

@test "a group- or world-readable GitHub token is a violation" {
    chmod 0644 "${BOX_HOME}/.config/loop/github-token"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"0644"* ]]
}

@test "a classic GitHub token is a violation - it cannot be repository-scoped" {
    printf 'ghp_11EXAMPLEEXAMPLEEXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fine-grained"* ]]
}

# --- The signing key --------------------------------------------------------

@test "a missing signing key is a violation" {
    rm -f "${BOX_HOME}/.ssh/loop_signing_ed25519"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"signing-key"* ]]
}

@test "a signing key with no public half is a violation" {
    rm -f "${BOX_HOME}/.ssh/loop_signing_ed25519.pub"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"signing-key"* ]]
}

@test "a readable-by-others signing key is a violation" {
    chmod 0644 "${BOX_HOME}/.ssh/loop_signing_ed25519"
    run_assert
    [ "$status" -eq 2 ]
}

@test "commits not configured to sign is a violation - the key would go unused" {
    git config --file "${BOX_HOME}/.gitconfig" commit.gpgsign false
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"commit.gpgsign"* ]]
}

@test "a gpg format other than ssh is a violation" {
    git config --file "${BOX_HOME}/.gitconfig" gpg.format openpgp
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"gpg.format"* ]]
}

@test "signing with a key other than the Loop's is a violation" {
    git config --file "${BOX_HOME}/.gitconfig" user.signingkey "${BOX_HOME}/.ssh/somebody-elses.pub"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"user.signingkey"* ]]
}

@test "no author email is a violation - the commit would not attribute" {
    git config --file "${BOX_HOME}/.gitconfig" --unset user.email
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"user.email"* ]]
}

# --- No fleet SSH key -------------------------------------------------------

@test "a second private key in the Loop's .ssh is a violation" {
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nfleet\n' >"${BOX_HOME}/.ssh/id_ed25519"
    chmod 0600 "${BOX_HOME}/.ssh/id_ed25519"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
    [[ "$output" == *"id_ed25519"* ]]
}

@test "a private key anywhere in the home is a violation, not only in .ssh" {
    mkdir -p "${BOX_HOME}/notes/keys"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nfleet\n' >"${BOX_HOME}/notes/keys/deploy"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
}

@test "a probe that could not be evaluated is reported, and is not a pass" {
    mkdir -p "${BOX_ROOT}/root/.ssh"
    chmod 0000 "${BOX_ROOT}/root"
    run_assert
    chmod 0755 "${BOX_ROOT}/root"
    [ "$(field CREDENTIALS_INDETERMINATE)" -ge 1 ]
    [[ "$output" == *"[partial]"* ]]
    [[ "$output" == *"could not be checked"* ]]
}

@test "an unevaluable probe does not on its own fail the assertion" {
    mkdir -p "${BOX_ROOT}/root/.ssh"
    chmod 0000 "${BOX_ROOT}/root"
    run_assert
    chmod 0755 "${BOX_ROOT}/root"
    [ "$status" -eq 0 ]
}

@test "a private key in root's .ssh is a violation" {
    printf -- '-----BEGIN RSA PRIVATE KEY-----\nfleet\n' >"${BOX_ROOT}/root/.ssh/id_rsa"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
}

@test "the box's own SSH host keys are not fleet keys" {
    mkdir -p "${BOX_ROOT}/etc/ssh"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nhost\n' >"${BOX_ROOT}/etc/ssh/ssh_host_ed25519_key"
    run_assert
    [ "$status" -eq 0 ]
}

@test "a private key parked in /etc/ssh under another name is a violation" {
    mkdir -p "${BOX_ROOT}/etc/ssh"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nfleet\n' >"${BOX_ROOT}/etc/ssh/deploy_key"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
}

@test "an authorized_keys file is not a private key and not a violation" {
    printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLE conductor@orchestrate\n' \
        >"${BOX_ROOT}/root/.ssh/authorized_keys"
    run_assert
    [ "$status" -eq 0 ]
}

@test "a forwarded ssh agent is a violation - it is fleet reach without a file" {
    run_assert SSH_AUTH_SOCK=/tmp/ssh-XXXX/agent.1
    [ "$status" -eq 2 ]
    [[ "$output" == *"fleet-ssh-key"* ]]
}

# --- No vault token ---------------------------------------------------------

@test "a vault service-account token in the environment is a violation" {
    run_assert OP_SERVICE_ACCOUNT_TOKEN=ops_example
    [ "$status" -eq 2 ]
    [[ "$output" == *"vault-token"* ]]
    [[ "$output" == *"OP_SERVICE_ACCOUNT_TOKEN"* ]]
}

@test "the orchestration VM's op.env arriving on the box is a violation" {
    mkdir -p "${BOX_ROOT}/etc/orchestration"
    printf 'OP_SERVICE_ACCOUNT_TOKEN=ops_example\n' >"${BOX_ROOT}/etc/orchestration/op.env"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"vault-token"* ]]
}

# --- No database credential -------------------------------------------------

@test "a database password in the environment is a violation" {
    run_assert MYSQL_PWD=hunter2
    [ "$status" -eq 2 ]
    [[ "$output" == *"database-credential"* ]]
}

@test "a .my.cnf is a violation" {
    printf '[client]\npassword=hunter2\n' >"${BOX_HOME}/.my.cnf"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"database-credential"* ]]
}

# --- No DigitalOcean token --------------------------------------------------

@test "a DigitalOcean token in the environment is a violation" {
    run_assert DIGITALOCEAN_TOKEN=dop_v1_example
    [ "$status" -eq 2 ]
    [[ "$output" == *"digitalocean-token"* ]]
}

@test "a doctl config is a violation" {
    mkdir -p "${BOX_HOME}/.config/doctl"
    printf 'access-token: dop_v1_example\n' >"${BOX_HOME}/.config/doctl/config.yaml"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"digitalocean-token"* ]]
}

# --- The credential collision ------------------------------------------------

@test "a metered model key in the environment is a violation" {
    run_assert ANTHROPIC_API_KEY=sk-ant-example
    [ "$status" -eq 2 ]
    [[ "$output" == *"metered-model-key"* ]]
}

@test "a metered model key exported from a shell profile is a violation" {
    printf 'export ANTHROPIC_API_KEY=sk-ant-example\n' >>"${BOX_HOME}/.bashrc"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *".bashrc"* ]]
}

@test "a metered model key in /etc/environment is a violation" {
    printf 'ANTHROPIC_API_KEY=sk-ant-example\n' >"${BOX_ROOT}/etc/environment"
    run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"metered-model-key"* ]]
}

# The second vendor's names arrive here from `agents/grok.sh` rather than from
# this script, so that the Run-start guard and the box-level inventory cannot
# disagree about what a metered key is called (#84). Both directions are worth a
# test: that every name an adapter declares is graded, and that the derivation
# failing is loud rather than a family that quietly stops looking.
@test "every metered key name an adapter declares is graded on the box" {
    local adapter name
    for adapter in "${LOOP_SRC}"/agents/*.sh; do
        while IFS= read -r name; do
            [[ -n "${name}" ]] || continue
            run_assert "${name}=not-a-real-key"
            [ "$status" -eq 2 ]
            [[ "$output" == *"metered-model-key: ${name}"* ]]
        done < <("${adapter}" --metered-env-names)
    done
}

@test "an adapter that names nothing fails the check rather than shortening it" {
    # A copy of the script beside a copy of the adapters, because the derivation
    # is relative to where the script sits. Called directly rather than through
    # run_assert: the point is which script runs, and run_assert names the real
    # one.
    copy="${BATS_TEST_TMPDIR}/copy"
    mkdir -p "${copy}/agents"
    cp "${LOOP_SRC}"/agents/*.sh "${copy}/agents/"
    # The Contract too: an adapter sources it, and an adapter that cannot start
    # answers nothing - which would make this test pass on the wrong adapter.
    cp "${LOOP_SRC}/contract.sh" "${copy}/contract.sh"
    cp "${ASSERT}" "${copy}/assert-credentials.sh"
    printf '#!/usr/bin/env bash\nexit 0\n' >"${copy}/agents/silent.sh"
    chmod +x "${copy}/agents/silent.sh"

    local unsets=() name
    while IFS= read -r name; do unsets+=(-u "${name}"); done < <(credential_env_names)
    run env "${unsets[@]}" "${copy}/assert-credentials.sh" \
        --home "${BOX_HOME}" --system-root "${BOX_ROOT}" --sbx "${FAKE_SBX}"
    [ "$status" -eq 1 ]
    [[ "$output" == *"silent.sh"* ]]
}

@test "a model vendor secret stored in the boundary is a violation" {
    FAKE_SBX_SECRETS="anthropic" run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"metered-model-key"* ]]
}

@test "a stored secret that is not a model vendor is reported, not a violation" {
    FAKE_SBX_SECRETS="github" run_assert
    [ "$status" -eq 0 ]
    [[ "$output" == *"github"* ]]
}

# The captured OAuth session (#259). Every Run leaves one, so these two tests
# are the difference between an inventory an operator reads and one that is
# always red - and the family is `metered-model-key`, so what has to survive is
# that a real metered key in the same slot is still caught.
@test "a captured OAuth session in the boundary is reported, not a violation" {
    FAKE_SBX_SECRETS="anthropic:oauth" run_assert
    [ "$status" -eq 0 ]
    [[ "$output" == *"anthropic"* ]]
    [[ "$output" != *"metered-model-key: the Execution Boundary"* ]]
}

@test "a captured OAuth session does not excuse a metered key beside it" {
    FAKE_SBX_SECRETS="anthropic:oauth openai" run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"metered-model-key: the Execution Boundary stores a 'openai'"* ]]
    # The captured session is named too - as an observation. What must not
    # happen is it being counted, so the count is what this asserts.
    [ "$(field CREDENTIALS_VIOLATIONS)" -eq 1 ]
}

@test "a metered key for a vendor that also has a captured session is a violation" {
    # The same service name, the other form. What separates them is the SECRET
    # column, not the name - so a check keyed on the name alone would call this
    # one clean the moment the OAuth case was excused.
    FAKE_SBX_SECRETS="anthropic" run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"metered-model-key"* ]]
}

# --- The Docker identity, the fourth credential ------------------------------

@test "a boundary that is not signed in is a violation" {
    FAKE_SBX_AUTH=no run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"docker-identity"* ]]
}

@test "a boundary that cannot be asked is a violation, not a pass" {
    FAKE_SBX_BROKEN=1 run_assert
    [ "$status" -eq 2 ]
    [[ "$output" == *"docker-identity"* ]]
}

# --- Reporting ---------------------------------------------------------------

@test "every violation is reported, not just the first" {
    run_assert MYSQL_PWD=hunter2 DIGITALOCEAN_TOKEN=dop_v1_example
    [ "$status" -eq 2 ]
    [ "$(field CREDENTIALS_VIOLATIONS)" -ge 2 ]
    [[ "$output" == *"database-credential"* ]]
    [[ "$output" == *"digitalocean-token"* ]]
}

@test "one violation counts as one" {
    run_assert ANTHROPIC_API_KEY=sk-ant-example
    [ "$(field CREDENTIALS_VIOLATIONS)" = "1" ]
}
