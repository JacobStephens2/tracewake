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

@test "the model credential is reported but does not gate the result" {
    run_assert
    [ "$status" -eq 0 ]
    mkdir -p "${BOX_HOME}/.claude"
    printf '{}\n' >"${BOX_HOME}/.claude/.credentials.json"
    run_assert
    [ "$status" -eq 0 ]
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

@test "a private key in root's .ssh is a violation" {
    printf -- '-----BEGIN RSA PRIVATE KEY-----\nfleet\n' >"${BOX_ROOT}/root/.ssh/id_rsa"
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
