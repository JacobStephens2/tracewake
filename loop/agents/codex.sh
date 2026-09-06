#!/usr/bin/env bash
#
# The agent adapter for OpenAI Codex, as a leaf of boundary-harness.sh (ADR 0004,
# ADR 0012, ADR 0024, ADR 0025).
#
# Holds only what Codex does differently from the harness:
# - OpenAI metered environment names
# - The default guest template (loop-php:1)
# - Docker Sandboxes codex kit creation args (-t <template> codex)
# - Codex auth.json and config.toml credential checks
# - Guest identity path rewriting and credential placement (/home/agent)
# - Agent invocation inside the boundary with bypass permissions
# - Codex turn bound detection ('turn limit reached' / 'max turns reached')
# - Missing egress host detection and allowlist naming

set -euo pipefail

# Every environment variable name that would supersede the subscription login.
# Declared here because which names a vendor honours is vendor knowledge, which
# ADR 0004 puts in the adapter; assert-credentials.sh reads this list rather
# than restating it.
metered_env_names=(
    OPENAI_API_KEY
    CODEX_API_KEY
)

# The `sbx` template every Iteration's microVM is built from. Declared here,
# once, and used by the create below rather than the create naming it a second
# time: the template IS the Execution Boundary the Run was bounded by (ADR
# 0003), and it is a thing that may change.
guest_template="${LOOP_GUEST_TEMPLATE:-loop-php:1}"
adapter_create_args=(-t "${guest_template}" codex)
adapter_privileged_put=false

codex_home="${LOOP_CODEX_HOME:-${HOME}/.codex}"

adapter_check_credentials() {
    [[ -f "${codex_home}/auth.json" ]] ||
        die "no model credential at ${codex_home}/auth.json - run wizards/loop-codex-login.sh"

    if grep -o '"OPENAI_API_KEY"[[:space:]]*:[[:space:]]*"[^"]\+"' -- "${codex_home}/auth.json" 2>/dev/null | grep -q '[^[:space:]]'; then
        die "${codex_home}/auth.json sets an OPENAI_API_KEY; it resolves ahead of the subscription session and would move billing to a metered key. Remove it."
    fi

    local codex_config="${codex_home}/config.toml"
    if [[ -f ${codex_config} ]] &&
        grep -qE '^[[:space:]]*(api_key|openai_api_key)[[:space:]]*=' "${codex_config}"; then
        die "${codex_config} sets a model api_key or openai_api_key; it resolves ahead of the subscription session and would move billing. Remove it."
    fi
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

    put "${codex_home}/auth.json" "${guest_home}/.codex/auth.json" 0600
}

adapter_run_agent() {
    local raw_err
    raw_err="$(mktemp)"
    local rc=0
    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \
        codex \
        --permission-mode bypassPermissions \
        --max-turns "${max_turns}" \
        "$(cat -- "${prompt_file}")" 2>"${raw_err}" || rc=$?

    cat -- "${raw_err}" >&2

    if ((rc != 0)); then
        local blocked_host
        blocked_host="$(grep -oiE '([a-zA-Z0-9.-]+:(443|80)|[a-zA-Z0-9.-]+\.openai\.com|chatgpt\.com)' "${raw_err}" | head -n1 || true)"
        if [[ -n ${blocked_host} ]] && grep -qiE '(blocked by network policy|connection (failed|refused)|failed to connect)' "${raw_err}"; then
            printf '%s: egress connection to %s failed - is it on the egress allowlist? See ansible/roles/loop_execution_boundary/defaults/main.yml\n' \
                "${0##*/}" "${blocked_host}" >&2
        fi
    fi
    rm -f -- "${raw_err}"
    return "${rc}"
}

adapter_is_turn_bound() {
    grep -qiE '(turn limit reached|max turns reached)' -- "$1"
}

agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=../boundary-harness.sh
source "${agent_dir}/../boundary-harness.sh"

boundary_harness_main "$@"
