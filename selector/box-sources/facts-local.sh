#!/usr/bin/env bash
#
# The box surface, read-only, local: what the box is currently holding.
#
#   facts-local.sh
#
# The Single-Host SELECTOR_BOX_FACTS_COMMAND. Sibling to box-sources/facts.sh
# for Single-Host instances (ADR 0019): dispatch runs through
# box-sources/local.sh with no SSH hop, so the status-card read must not open
# one either. Prints `LOOP_BOX_*=value` lines - the same key=value shape
# run.sh ends a Run with, so nothing here needs a second parser - and exits 0
# when the box answered.
#
# Four facts, and each is on the card for a reason (#156, #260):
#
#   LOOP_BOX_SCRIPTS_HASH    the Loop scripts the box is holding, hashed.
#   LOOP_BOX_GUEST_TEMPLATE  the `sbx` template every Iteration's microVM is
#                            built from. Asked of the agent adapter rather than
#                            read out of it, so the answer comes from the file
#                            that makes the choice.
#   LOOP_BOX_AGENT_VERSION   the pinned agent, as installed.
#   LOOP_BOX_CREDENTIAL_EXPIRES_AT
#                            when the nearest of the box's credentials stops
#                            working, as an absolute instant (#7, #260).
#
# A fact the box cannot answer is simply not printed. An omission is honest
# where a guess would put a claim on the page that nothing observed.
#
# It holds no credential of its own, starts nothing, and writes nothing.
# Substitutable (ADR 0004) like every other command the Selector reaches
# through.
#
# Configuration, all environment, shared with box-sources/local.sh:
#
#   SELECTOR_BOX_LOOP   the Loop directory (default ../../loop, the checkout
#                       beside this script - the Single-Host Loop lives in the
#                       deployed tree, not at the box's /home/loop/loop)
#   SELECTOR_BOX_AGENT  claude          which adapter is asked
#   LOOP_GUEST_TEMPLATE optional        the target's guest image, read from the
#                       environment directly - there is no hop to carry it
#                       across, so what consumes it (the adapter) already sees it
#
# Requires no SELECTOR_BOX_HOST: there is no hop to address. A Single-Host
# instance that left the default facts.sh in place would SSH to whatever that
# names - INSTALL.md's `local` does not resolve - and the status card would
# degrade on every cycle while the Runs beside it worked perfectly.
#
# Exit codes: 0 when the box answered, non-zero when it could not be read.

set -euo pipefail

box_loop="${SELECTOR_BOX_LOOP:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../loop" && pwd)}"
box_agent="${SELECTOR_BOX_AGENT:-claude}"

# The hash covers file CONTENT and file NAMES, both: a script deleted from the
# Loop has to move the hash as much as one edited. `sort` on the path keeps it
# independent of the order `find` walks the tree in, which is a filesystem
# property and not a property of what is held.
#
# Deliberately not `git rev-parse`: the deployed Loop may be a copied
# directory and not a checkout, so there is no commit to name. The hash is
# what exists.
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
        val="$(tr -d '[:space:]' < "${ef}")"
        [ -n "${val}" ] && candidate_expiries+=("${val}")
    fi
done
min_epoch=""
nearest_expiry=""
# Same normalization facts.sh applies on the box: every candidate through
# `date -u -d`, taking the nearest instant. The Single-Host machine is Linux
# like the box, so GNU `date` is there; the offline suite answers it with a
# shim, like ssh and git.
for exp in "${candidate_expiries[@]:-}"; do
    epoch="$(date -u -d "${exp}" +%s 2>/dev/null || true)"
    if [ -n "${epoch}" ]; then
        if [ -z "${min_epoch}" ] || [ "${epoch}" -lt "${min_epoch}" ]; then
            min_epoch="${epoch}"
            nearest_expiry="$(date -u -d "@${epoch}" +%Y-%m-%dT%H:%M:%SZ)"
        fi
    fi
done
[ -n "${nearest_expiry}" ] && printf 'LOOP_BOX_CREDENTIAL_EXPIRES_AT=%s\n' "${nearest_expiry}"
if command -v "${box_agent}" >/dev/null 2>&1; then
    version="$("${box_agent}" --version 2>/dev/null | head -n 1 || true)"
    [ -n "${version}" ] && printf 'LOOP_BOX_AGENT_VERSION=%s\n' "${version}"
fi
exit 0
