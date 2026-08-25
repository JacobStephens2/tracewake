# Shared setup for the credential inventory's offline suite.
#
# Every test builds a throwaway "box" - a home directory, a system root, and a
# scripted fake `sbx` - and drives the real assert-credentials.sh against it.
# Nothing here touches loop.etadventures.com, and nothing reaches a network:
# what the script asserts about a box is a function of paths, file modes, git
# config and one command's output, and all four are constructible in a tmpdir.
#
# shellcheck shell=bash

# The environment variable names the script treats as forbidden. Listed here so
# the suite can UNSET every one of them before each run: the suite is meant to
# run on the Loop's box, but it also has to be runnable in a vaulted-agent
# session on the orchestration VM, where PROD_DB_SERVER_MYSQL_PASS and friends
# genuinely are in the environment. Without this, "a clean box" would fail on
# the one machine most likely to run the suite, for a reason that has nothing to
# do with the box under test.
CREDENTIAL_ENV_NAMES=(
    OP_SERVICE_ACCOUNT_TOKEN OP_CONNECT_TOKEN OP_API_TOKEN VAULT_TOKEN
    MYSQL_PWD DATABASE_URL DB_PASSWORD
    PROD_DB_SERVER_HOST PROD_DB_SERVER_MYSQL_USER PROD_DB_SERVER_MYSQL_PASS
    DIGITALOCEAN_TOKEN DIGITALOCEAN_ACCESS_TOKEN DO_TOKEN DO_API_TOKEN
    TF_VAR_do_token SPACES_ACCESS_KEY_ID SPACES_SECRET_ACCESS_KEY
    ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN OPENAI_API_KEY XAI_API_KEY
    SSH_AUTH_SOCK
)

setup_credential_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC
    ASSERT="${LOOP_SRC}/assert-credentials.sh"
    export ASSERT

    BOX_HOME="${BATS_TEST_TMPDIR}/home/loop"
    BOX_ROOT="${BATS_TEST_TMPDIR}/root"
    mkdir -p "${BOX_HOME}/.ssh" "${BOX_HOME}/.config/loop" "${BOX_ROOT}/etc" "${BOX_ROOT}/root/.ssh"
    export BOX_HOME BOX_ROOT

    # The GitHub token: fine-grained, non-empty, owner-only.
    printf 'github_pat_11EXAMPLEEXAMPLEEXAMPLE\n' >"${BOX_HOME}/.config/loop/github-token"
    chmod 0600 "${BOX_HOME}/.config/loop/github-token"

    # The signing key. Content shape matters to the script (it looks for a
    # private key header when it sweeps for fleet keys), so write the real
    # header rather than a placeholder.
    signing_key="${BOX_HOME}/.ssh/loop_signing_ed25519"
    printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n-----END OPENSSH PRIVATE KEY-----\n' \
        >"${signing_key}"
    chmod 0600 "${signing_key}"
    printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLE loop@loop.etadventures.com\n' \
        >"${signing_key}.pub"
    chmod 0644 "${signing_key}.pub"

    git config --file "${BOX_HOME}/.gitconfig" user.name "Jacob Stephens"
    git config --file "${BOX_HOME}/.gitconfig" user.email "jstephens@etadventures.com"
    git config --file "${BOX_HOME}/.gitconfig" gpg.format ssh
    git config --file "${BOX_HOME}/.gitconfig" user.signingkey "${signing_key}.pub"
    git config --file "${BOX_HOME}/.gitconfig" commit.gpgsign true

    # The fake `sbx`. Its two answers are the only things the script asks a
    # command for, and both are driven by environment variables so a test can
    # say "signed out" or "an anthropic secret is stored" in one line.
    FAKE_SBX="${BATS_TEST_TMPDIR}/sbx"
    cat >"${FAKE_SBX}" <<'SBX'
#!/usr/bin/env bash
[[ -n ${FAKE_SBX_BROKEN:-} ]] && exit 1
case "$1 ${2:-}" in
    "diagnose ")
        printf 'Virtualization    supported\n'
        if [[ ${FAKE_SBX_AUTH:-yes} == yes ]]; then
            printf 'Authentication    authenticated as someone\n'
        else
            printf 'Authentication    not authenticated\n'
        fi
        ;;
    "secret ls")
        if [[ -n ${FAKE_SBX_SECRETS:-} ]]; then
            printf 'SERVICE     SCOPE\n'
            for service in ${FAKE_SBX_SECRETS}; do printf '%s     global\n' "${service}"; done
        else
            printf "No secrets found. Run 'sbx secret set --help' to see available services.\n"
        fi
        ;;
    *) exit 64 ;;
esac
SBX
    chmod +x "${FAKE_SBX}"
    export FAKE_SBX
    # Exported here, empty, rather than only where a test sets them: a
    # `FOO=x run_assert` prefix updates an already-exported name, and silently
    # fails to reach the fake if the name was never exported at all.
    export FAKE_SBX_AUTH=yes
    export FAKE_SBX_SECRETS=""
    export FAKE_SBX_BROKEN=""
}

# Drive the real script against the fixture box. Any NAME=value arguments are
# put into its environment; every forbidden name not named that way is unset,
# so a test says what it is testing and nothing else leaks in.
run_assert() {
    local unsets=() name
    for name in "${CREDENTIAL_ENV_NAMES[@]}"; do unsets+=(-u "${name}"); done
    run env "${unsets[@]}" "$@" \
        "${ASSERT}" --home "${BOX_HOME}" --system-root "${BOX_ROOT}" --sbx "${FAKE_SBX}"
}

# The machine-readable first lines, as one field. `output` is bats's, set by
# `run`; shellcheck cannot see that from here.
# shellcheck disable=SC2154
field() {
    grep -E "^${1}=" <<<"${output}" | head -n1 | cut -d= -f2-
}
