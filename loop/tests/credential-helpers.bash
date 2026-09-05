# Shared setup for the credential inventory's offline suite.
#
# Every test builds a throwaway "box" - a home directory, a system root, and a
# scripted fake `sbx` - and drives the real assert-credentials.sh against it.
# Nothing here touches loop.etadventures.com, and nothing reaches a network:
# what the script asserts about a box is a function of paths, file modes, git
# config and one command's output, and all four are constructible in a tmpdir.
#
# shellcheck shell=bash

# The environment variable names the script treats as forbidden. Read FROM the
# script rather than restated here: the suite has to unset every one of them
# before each run, because it is meant to run on the Loop's box and also has to
# be runnable in a vaulted-agent session on the orchestration VM, where
# PROD_DB_SERVER_MYSQL_PASS and friends genuinely are in the environment.
# Without that, "a clean box" would fail on the machine most likely to run the
# suite, for a reason with nothing to do with the box under test.
#
# A second copy of the list here would drift the first time a family gained a
# name, and it would drift silently - into tests that pass because the thing
# they meant to unset was never named.
credential_env_names() {
    "${LOOP_SRC}/assert-credentials.sh" --list-env-names
}

# The model credential, with an access token that stops working SECONDS from
# now. Negative for a login that has already lapsed - the state the whole of
# #260 is about, and the one a box can be in for sixteen hours a day.
#
# `refreshTokenExpiresAt` is three weeks out and is in every fixture on
# purpose: it is what a reader matching the wrong field would find, and it
# would report a lapsed box as good until September.
write_credential() {
    printf '{"claudeAiOauth":{"accessToken":"not-a-real-token","refreshTokenExpiresAt":%s000,"expiresAt":%s000,"subscriptionType":"max"}}\n' \
        "$(($(date +%s) + 1814400))" "$(($(date +%s) + $1))" \
        >"${BOX_HOME}/.claude/.credentials.json"
}

setup_credential_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC
    ASSERT="${LOOP_SRC}/assert-credentials.sh"
    export ASSERT

    BOX_HOME="${BATS_TEST_TMPDIR}/home/loop"
    BOX_ROOT="${BATS_TEST_TMPDIR}/root"
    mkdir -p "${BOX_HOME}/.ssh" "${BOX_HOME}/.config/loop" \
        "${BOX_ROOT}/etc" "${BOX_ROOT}/etc/ssh" "${BOX_ROOT}/root/.ssh"
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

    # The model credential: the operator's Claude Code subscription login, which
    # #83 put on the box.
    #
    # This was `{}` until #260, on the grounds that the script asserted the box
    # held one rather than what was in it. That is exactly what made `[held]`
    # meaningless - a box whose login lapsed sixteen hours ago satisfied it -
    # so the contents are now read, and the fixture has to carry the real
    # shape: the field nested, in milliseconds, beside a longer name that ends
    # in the same word.
    mkdir -p "${BOX_HOME}/.claude"
    write_credential 28800
    chmod 0600 "${BOX_HOME}/.claude/.credentials.json"

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
            # The real table's four columns, in the real order. This used to
            # print two of its own invention, and the shape is now load-bearing
            # rather than decorative: the SECRET column is what tells an
            # injectable API key apart from a captured OAuth session (#259), so
            # a fake that omitted it could not express the difference the
            # script now reads.
            #
            # A bare name is the key form, because that is what every test
            # written before #259 meant by one. `<name>:oauth` is the captured
            # session.
            printf 'SCOPE      TYPE      NAME        SECRET\n'
            for service in ${FAKE_SBX_SECRETS}; do
                if [[ ${service} == *:oauth ]]; then
                    printf '(global)   service   %s   (oauth configured)\n' "${service%:oauth}"
                else
                    printf '(global)   service   %s   ****\n' "${service}"
                fi
            done
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
    while IFS= read -r name; do unsets+=(-u "${name}"); done < <(credential_env_names)
    local env_vars=()
    local extra_args=()
    while (($# > 0)); do
        if [[ $1 == *=* && $1 != --* ]]; then
            env_vars+=("$1")
        else
            extra_args+=("$1")
        fi
        shift
    done
    run env "${unsets[@]}" "${env_vars[@]}" \
        "${ASSERT}" --home "${BOX_HOME}" --system-root "${BOX_ROOT}" --sbx "${FAKE_SBX}" "${extra_args[@]}"
}

# The machine-readable first lines, as one field. `output` is bats's, set by
# `run`; shellcheck cannot see that from here.
# shellcheck disable=SC2154
field() {
    grep -E "^${1}=" <<<"${output}" | head -n1 | cut -d= -f2-
}
