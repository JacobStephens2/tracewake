#!/usr/bin/env bash
#
# The agent adapter for Grok Build, as a leaf of boundary-harness.sh (ADR 0004,
# ADR 0012, ADR 0024).
#
# Holds only what Grok Build does differently from the harness:
# - xAI metered environment names
# - The default guest template (shell)
# - The pinned version (LOOP_GROK_VERSION) and --pinned-version query
# - Grok auth.json and config.toml credential checks
# - Agent-less shell boundary creation args
# - In-guest curl installation and version check
# - In-guest privileged directory creation for host paths
# - Copying auth.json and prompt file into guest
# - Agent invocation with telemetry and auto-updater disabled
# - Grok Build turn bound detection ('max turns reached')

set -euo pipefail

# The pin, declared once. Ansible asks this file for it rather than carrying a
# second copy, so moving the pin is one line here.
LOOP_GROK_VERSION="${LOOP_GROK_VERSION:-1.0.5}"

# Every environment variable name that would supersede the subscription. Read by
# assert-credentials.sh, so this is the one place any of them is written down.
metered_env_names=(
    XAI_API_KEY
    GROK_CODE_XAI_API_KEY
    GROK_DEPLOYMENT_KEY
    GROK_AUTH_PROVIDER_ACCESS_TOKEN
    GROK_AUTH_PROVIDER_COMMAND
)

# `shell`, not an agent template - there is no Grok template to ask for. The
# default names the stock `shell` agent outright; a configured template names
# an image and rides `-t`, the way the Claude adapter's always does. Passing
# an image where `sbx` expects an agent is "unknown agent" and no boundary -
# found by the live proving Run for #85, which configures
# `docker.io/docker/sandbox-templates:shell-docker`.
guest_template="${LOOP_GUEST_TEMPLATE:-shell}"
if [[ ${guest_template} == shell ]]; then
    adapter_create_args=(shell)
else
    adapter_create_args=(-t "${guest_template}" shell)
fi
adapter_privileged_put=true

grok_home="${LOOP_GROK_HOME:-${HOME}/.grok}"

adapter_query() {
    case "$1" in
        --pinned-version)
            printf '%s\n' "${LOOP_GROK_VERSION}"
            return 0
            ;;
    esac
    return 1
}

adapter_check_credentials() {
    [[ -f "${grok_home}/auth.json" ]] ||
        die "no model credential at ${grok_home}/auth.json - run wizards/loop-grok-login.sh"

    local grok_config="${grok_home}/config.toml"
    if [[ -f ${grok_config} ]] &&
        grep -qE '^[[:space:]]*(api_key|env_key)[[:space:]]*=' "${grok_config}"; then
        die "${grok_config} sets a model api_key or env_key; it resolves ahead of the subscription session and would move billing. Remove it."
    fi
}

guest_agent=""
adapter_prepare_guest() {
    "${sbx}" exec "${sandbox}" bash -lc \
        "set -o pipefail; curl -fsSL https://x.ai/cli/install.sh | bash -s ${LOOP_GROK_VERSION}" >&2 ||
        die "could not install the agent inside the boundary - is x.ai:443 on the egress allowlist? See ansible/roles/loop_execution_boundary/defaults/main.yml"

    guest_agent="${guest_home}/.grok/bin/grok"
    local installed
    installed="$("${sbx}" exec "${sandbox}" "${guest_agent}" --version 2>&1 || true)"
    [[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||
        die "the boundary is holding '${installed}', not the pinned ${LOOP_GROK_VERSION}"

    put "${grok_home}/auth.json" "${guest_home}/.grok/auth.json" 0600
    put "${gitconfig}" "${guest_home}/.gitconfig" 0644
    put "${signing_key}" "${signing_key}" 0600
    put "${signing_key}.pub" "${signing_key}.pub" 0644
    put "${allowed_signers}" "${allowed_signers}" 0644

    put "${prompt_file}" "${guest_home}/prompt" 0644
}

adapter_run_agent() {
    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \
        env GROK_TELEMETRY_ENABLED=0 GROK_CHANGELOG_OFFLINE=1 GROK_DISABLE_AUTOUPDATER=1 \
        "${guest_agent}" \
        --prompt-file "${guest_home}/prompt" \
        --permission-mode bypassPermissions \
        --max-turns "${max_turns}" \
        --output-format plain
}

adapter_is_turn_bound() {
    grep -qiF 'max turns reached' -- "$1"
}

agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=../boundary-harness.sh
source "${agent_dir}/../boundary-harness.sh"

boundary_harness_main "$@"
