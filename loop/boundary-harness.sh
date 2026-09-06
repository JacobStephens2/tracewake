#!/usr/bin/env bash
#
# The Execution Boundary harness, and the adapter contract it enforces.
#
# Sourced by each agent adapter leaf in agents/<vendor>.sh. Holds the Execution
# Boundary lifecycle and executes an Iteration inside a fresh microVM:
#
#   <adapter> <prompt-file> <max-turns>     one Iteration inside the boundary
#   <adapter> --metered-env-names           one name per line
#   <adapter> --guest-template              the image the boundary is built from
#   <adapter> --credential-expiry           when the model credential stops working
#
# ## One microVM per Iteration
#
# The boundary is created here and destroyed here, so an Iteration cannot leave
# anything behind for the next one to find. That is the same forgetting the
# fresh process gives the agent's context, applied to its filesystem: state that
# survives an Iteration has to be on disk in the repository, where a reviewer
# sees it, rather than in a sandbox nobody reads.
#
# The repository is bind-mounted at its own path, so a commit made inside the
# guest is a commit in the checkout on the host. The Loop's own scripts are
# mounted read-only alongside it, so an Iteration can run the completeness check
# without being able to edit it.
#
# ## What goes in, and what deliberately does not
#
# In: the model credential, the signing key, and the git identity that uses it.
# An Iteration that could not sign would produce commits that are not Verified,
# and the first place anyone would find out is the pull request.
#
# NOT in: the GitHub token. The push and the draft pull request happen on the
# host after every agent process is gone (propose.sh). An agent inside the
# boundary therefore cannot push, cannot open a pull request and cannot reach
# anything with the token, and Proposal-Only Output is a property of what is
# inside the boundary rather than of what the prompt asked for.
#
# Each vendor leaf specifies only what that vendor does differently: its metered
# environment variable names, its default guest template, how it checks and
# provisions its credential, how it runs its agent command inside the boundary,
# and the words it prints on reaching the turn bound (ADR 0004, ADR 0012, ADR 0024).
#
# shellcheck shell=bash
# Variables declared by leaf adapters (metered_env_names, guest_template,
# adapter_create_args) or consumed by leaf hooks (max_turns, signing_key,
# gitconfig, allowed_signers), and trap cleanup function.
# shellcheck disable=SC2154,SC2034,SC2329

set -euo pipefail

harness_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=contract.sh
source "${harness_dir}/contract.sh"

guest_home=/home/agent
guest_user=agent

die() {
    printf '%s: %s\n' "${0##*/}" "$*" >&2
    exit 1
}

as_instant() {
    date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ
}

refuse_metered_keys() {
    local name
    local -a metered_found=()
    for name in "${metered_env_names[@]}"; do
        [[ -n ${!name:-} ]] && metered_found+=("${name}")
    done
    ((${#metered_found[@]} > 0)) || return 0
    printf '%s: %s is set; it would supersede the subscription and move billing to a metered key. Unset it.\n' \
        "${0##*/}" \
        "$(
            IFS=', '
            printf '%s' "${metered_found[*]}"
        )" >&2
    exit 1
}

put() {
    local src="$1" dest="$2" mode="$3" dir
    [[ -f ${src} ]] ||
        die "${src} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
    dir="$(dirname -- "${dest}")"
    if [[ ${adapter_privileged_put:-false} == true ]]; then
        "${sbx}" exec "${sandbox}" sudo mkdir -p "${dir}" ||
            die "could not prepare ${dest} in the boundary"
        "${sbx}" exec "${sandbox}" sudo chown "${guest_user}:${guest_user}" "${dir}" ||
            die "could not prepare ${dest} in the boundary"
    else
        "${sbx}" exec "${sandbox}" mkdir -p "${dir}" ||
            die "could not prepare ${dest} in the boundary: the guest account cannot create $(dirname -- "${dest}"). Everything an Iteration is given goes under ${guest_home}."
    fi
    "${sbx}" cp "${src}" "${sandbox}:${dest}" >/dev/null ||
        die "could not place ${dest} in the boundary"
    "${sbx}" exec "${sandbox}" chmod "${mode}" "${dest}" ||
        die "could not set the mode of ${dest} in the boundary"
}

boundary_harness_main() {
    case "${1:-}" in
        --metered-env-names)
            printf '%s\n' "${metered_env_names[@]}"
            exit 0
            ;;
        --guest-template)
            printf '%s\n' "${guest_template}"
            exit 0
            ;;
        --credential-expiry)
            if declare -F adapter_credential_expiry >/dev/null; then
                local expiry
                expiry="$(adapter_credential_expiry)" || exit 1
                as_instant "${expiry}"
                exit 0
            fi
            exit 1
            ;;
        --*)
            if declare -F adapter_query >/dev/null && adapter_query "$1"; then
                exit 0
            fi
            die "usage: ${0##*/} <prompt-file> <max-turns>"
            ;;
    esac

    prompt_file="${1:?usage: ${0##*/} <prompt-file> <max-turns>}"
    max_turns="${2:?usage: ${0##*/} <prompt-file> <max-turns>}"

    refuse_metered_keys

    [[ -f ${prompt_file} ]] || die "no prompt file at ${prompt_file}"

    sbx="${LOOP_SBX_COMMAND:-sbx}"
    command -v "${sbx}" >/dev/null 2>&1 ||
        die "${sbx} is not on PATH - an Iteration runs inside the Execution Boundary, not beside it"

    if declare -F adapter_check_credentials >/dev/null; then
        adapter_check_credentials
    fi

    workspace="$(pwd)"
    signing_key="${LOOP_SIGNING_KEY:-${HOME}/.ssh/loop_signing_ed25519}"
    gitconfig="${LOOP_GITCONFIG:-${HOME}/.gitconfig}"
    allowed_signers="${LOOP_ALLOWED_SIGNERS:-${HOME}/.config/loop/allowed_signers}"

    sandbox="loop-$$-$(date +%s)"
    staging="$(mktemp -d)"
    transcript="$(mktemp)"

    cleanup() {
        "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true
        rm -rf -- "${staging}"
        rm -f -- "${transcript}"
    }
    trap cleanup EXIT INT TERM

    loop_dir="${harness_dir}"

    "${sbx}" create --quiet --name "${sandbox}" "${adapter_create_args[@]}" "${workspace}" "${loop_dir}:ro" >&2 ||
        die "could not create the Execution Boundary for this Iteration from ${guest_template}. If the box is not holding that image, apply ansible/loop.yml - role loop_guest_template builds it."

    if declare -F adapter_prepare_guest >/dev/null; then
        adapter_prepare_guest
    fi

    set +o pipefail
    adapter_run_agent 2>&1 | tee -- "${transcript}"
    agent_rc="${PIPESTATUS[0]}"
    set -o pipefail

    if ((agent_rc != 0)) && adapter_is_turn_bound "${transcript}"; then
        exit "${LOOP_AGENT_TURN_BOUND_EXIT}"
    fi

    exit "${agent_rc}"
}
