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
