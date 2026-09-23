#!/usr/bin/env bash
#
# The box surface, read-only, local: what this machine is currently holding.
#
#   facts-local.sh
#
# The product-default SELECTOR_BOX_FACTS_COMMAND (ADR 0029). Sibling to
# box-sources/facts.sh for the instance whose box is the controller itself
# (ADR 0004, ADR 0019): the same `LOOP_BOX_*=value` lines and the same rule -
# a fact the box cannot answer is simply not printed, never guessed at. And no SSH
# hop, so no SELECTOR_BOX_HOST: keeping the ssh-based read while setting
# that host to `local` is the `ssh: Could not resolve hostname local` the
# box card carries.
#
# Reads as the Run account (SELECTOR_BOX_USER, default loop) - see
# become-run-user.sh, sourced below. The hash, the guest template and the
# agent version are that account's answers, not the invoker's, and the
# credential's expiry files live under its home.
#
# Configuration, all environment:
#
#   SELECTOR_BOX_LOOP    /home/loop/loop   the Loop tree, on this machine
#   SELECTOR_BOX_USER    loop               the account a Run executes as
#   SELECTOR_BOX_AGENT   claude             which adapter is asked
#   LOOP_GUEST_TEMPLATE  optional           the target's guest image, read
#                                           directly here rather than carried
#                                           across a hop that does not exist
#
# Exit codes: 0 when the read ran - even when it answered nothing, because an
# omission is honest where a guess would put an unobserved claim on the page.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=./become-run-user.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/become-run-user.sh"

become_run_user ${1+"$@"}

box_loop="${SELECTOR_BOX_LOOP:-/home/loop/loop}"
box_agent="${SELECTOR_BOX_AGENT:-claude}"

# The hash covers file CONTENT and file NAMES, both - the same pipeline
# facts.sh runs on the far side of its hop, so the two answers stay
# comparable: a script deleted here moves the hash as much as one edited.
if [ -d "${box_loop}" ]; then
    hash="$(find "${box_loop}" -type f -exec sha256sum {} + \
        | sed "s| ${box_loop}/| |" | LC_ALL=C sort -k2 | sha256sum | cut -c1-12)"
    printf 'LOOP_BOX_SCRIPTS_HASH=%s\n' "${hash}"
fi
printf 'LOOP_BOX_AGENT=%s\n' "${box_agent}"
adapter="${box_loop}/agents/${box_agent}.sh"
candidate_expiries=()
if [ -x "${adapter}" ]; then
    template="$("${adapter}" --guest-template 2>/dev/null || true)"
    [ -n "${template}" ] && printf 'LOOP_BOX_GUEST_TEMPLATE=%s\n' "${template}"
    expiry="$("${adapter}" --credential-expiry 2>/dev/null || true)"
    [ -n "${expiry}" ] && candidate_expiries+=("${expiry}")
fi
for ef in "${HOME}/.config/loop/credential-expiry" "${HOME}/.config/loop/expiry" \
    "${HOME}/.config/loop"/*.expiry "${HOME}/.config/loop"/*-expiry; do
    if [ -f "${ef}" ]; then
        val="$(tr -d "[:space:]" < "${ef}")"
        [ -n "${val}" ] && candidate_expiries+=("${val}")
    fi
done
min_epoch=""
nearest_expiry=""
for exp in ${candidate_expiries[@]+"${candidate_expiries[@]}"}; do
    epoch="$(date -u -d "${exp}" +%s 2>/dev/null || true)"
    if [ -n "${epoch}" ]; then
        if [ -z "${min_epoch}" ] || [ "${epoch}" -lt "${min_epoch}" ]; then
            min_epoch="${epoch}"
            nearest_expiry="$(date -u -d "@${epoch}" +%Y-%m-%dT%H:%M:%SZ)"
        fi
    fi
done
[ -n "${nearest_expiry}" ] && printf 'LOOP_BOX_CREDENTIAL_EXPIRES_AT=%s\n' "${nearest_expiry}"
PATH="${HOME}/.local/bin:${HOME}/.grok/bin:${PATH}"
version="$("${box_agent}" --version 2>/dev/null | head -n 1 || true)"
[ -n "${version}" ] && printf 'LOOP_BOX_AGENT_VERSION=%s\n' "${version}"
exit 0
