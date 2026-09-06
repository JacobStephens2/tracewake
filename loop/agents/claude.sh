#!/usr/bin/env bash
#
# The agent adapter for Claude Code, as a leaf of boundary-harness.sh (ADR 0004,
# ADR 0012, ADR 0024).
#
# Holds only what Claude Code does differently from the harness:
# - ANTHROPIC metered environment names
# - The default guest template (loop-php:1)
# - Long-lived token credential resolution and expiry reading
# - Docker Sandboxes claude kit creation args (-t <template> claude)
# - Guest identity path rewriting (/home/agent)
# - Agent invocation inside the boundary
# - Claude Code turn bound detection ('Reached max turns')

set -euo pipefail

# Every environment variable name that would supersede the subscription login.
# Declared here because which names a vendor honours is vendor knowledge, which
# ADR 0004 puts in the adapter; assert-credentials.sh reads this list rather
# than restating it.
metered_env_names=(
    ANTHROPIC_API_KEY
    ANTHROPIC_AUTH_TOKEN
)

# The `sbx` template every Iteration's microVM is built from. Declared here,
# once, and used by the create below rather than the create naming it a second
# time: the template IS the Execution Boundary the Run was bounded by (ADR
# 0003), and it is a thing that may change.
guest_template="${LOOP_GUEST_TEMPLATE:-loop-php:1}"
adapter_create_args=(-t "${guest_template}" claude)
adapter_privileged_put=false

credentials_dir="${LOOP_CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
credentials_file="${credentials_dir}/.credentials.json"

adapter_credential_expiry() {
    if [[ -n ${LOOP_CLAUDE_EXPIRY:-} ]]; then
        if [[ "${LOOP_CLAUDE_EXPIRY}" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "${LOOP_CLAUDE_EXPIRY}"
            return 0
        fi
        local ts
        ts="$(date -u -d "${LOOP_CLAUDE_EXPIRY}" +%s 2>/dev/null)" && {
            printf '%s\n' "${ts}"
            return 0
        }
    fi
    local milliseconds
    if [[ -f ${credentials_file} ]]; then
        milliseconds="$(grep -o '"expiresAt"[[:space:]]*:[[:space:]]*[0-9]\+' \
            -- "${credentials_file}" 2>/dev/null | head -n1 | grep -o '[0-9]\+$')"
        if [[ -n ${milliseconds} ]]; then
            printf '%s\n' "$((milliseconds / 1000))"
            return 0
        fi
    fi
    local exp_file
    for exp_file in "${credentials_dir}/expiry" "${credentials_dir}/credential-expiry" \
        "${HOME}/.config/loop/credential-expiry"; do
        if [[ -f ${exp_file} ]]; then
            local exp_val
            exp_val="$(<"${exp_file}")"
            if [[ -n ${exp_val} ]]; then
                if [[ "${exp_val}" =~ ^[0-9]+$ ]]; then
                    printf '%s\n' "${exp_val}"
                    return 0
                fi
                local ts_file
                ts_file="$(date -u -d "${exp_val}" +%s 2>/dev/null)" && {
                    printf '%s\n' "${ts_file}"
                    return 0
                }
            fi
        fi
    done
    return 1
}

token=""
adapter_check_credentials() {
    token="${CLAUDE_CODE_OAUTH_TOKEN:-}"
    if [[ -z ${token} ]]; then
        if [[ -f "${credentials_file}" ]]; then
            token="$(grep -o '"accessToken"[[:space:]]*:[[:space:]]*"[^"]\+"' -- "${credentials_file}" 2>/dev/null | head -n1 | sed -E 's/.*"accessToken"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
        fi
    fi
    if [[ -z ${token} ]]; then
        local token_candidate
        for token_candidate in "${LOOP_CLAUDE_TOKEN_FILE:-}" "${credentials_dir}/token" \
            "${HOME}/.config/loop/model-token"; do
            if [[ -n ${token_candidate} && -f ${token_candidate} ]]; then
                token="$(<"${token_candidate}")"
                break
            fi
        done
    fi
    [[ -n ${token} ]] ||
        die "no model credential: CLAUDE_CODE_OAUTH_TOKEN is unset - run wizards/loop-credentials.sh"
}

adapter_prepare_guest() {
    guest_signing_key="${guest_home}/.ssh/$(basename -- "${signing_key}")"
    guest_allowed_signers="${guest_home}/.config/loop/allowed_signers"

    guest_gitconfig="${staging}/gitconfig"
    [[ -f ${gitconfig} ]] ||
        die "${gitconfig} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
    cp -- "${gitconfig}" "${guest_gitconfig}"
    git config --file "${guest_gitconfig}" user.signingkey "${guest_signing_key}.pub"
    git config --file "${guest_gitconfig}" gpg.ssh.allowedSignersFile "${guest_allowed_signers}"

    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644
    put "${signing_key}" "${guest_signing_key}" 0600
    put "${signing_key}.pub" "${guest_signing_key}.pub" 0644
    put "${allowed_signers}" "${guest_allowed_signers}" 0644
}

adapter_run_agent() {
    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \
        env CLAUDE_CODE_OAUTH_TOKEN="${token}" \
        claude \
        --print \
        --permission-mode bypassPermissions \
        --max-turns "${max_turns}" \
        "$(cat -- "${prompt_file}")"
}

adapter_is_turn_bound() {
    grep -qF 'Reached max turns' -- "$1"
}

agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=../boundary-harness.sh
source "${agent_dir}/../boundary-harness.sh"

boundary_harness_main "$@"
