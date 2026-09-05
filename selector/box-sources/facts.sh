#!/usr/bin/env bash
#
# The box surface, read-only: what the box is currently holding.
#
#   facts.sh
#
# The default SELECTOR_BOX_FACTS_COMMAND. Prints `LOOP_BOX_*=value` lines - the
# same key=value shape run.sh ends a Run with, so nothing here needs a second
# parser - and exits 0 when the box answered.
#
# Four facts, and each is on the card for a reason (#156, #260):
#
#   LOOP_BOX_SCRIPTS_HASH    the Loop scripts the box is holding, hashed. The
#                            box's copy is placed by an ansible apply, not by a
#                            merge, so it drifts from the reviewed copy in this
#                            repository silently and nothing else would say so.
#                            Story 34 asks for the executed paths to be
#                            protected from unreviewed change; this is the half
#                            of that an operator can see.
#   LOOP_BOX_GUEST_TEMPLATE  the `sbx` template every Iteration's microVM is
#                            built from. It is the Execution Boundary's
#                            identity (ADR 0003), and story 33 makes it a thing
#                            that may CHANGE - a PHP-capable guest, or the
#                            stock image if that work is dropped - so a page
#                            that claims a Run was bounded should say which
#                            boundary it means. Asked of the agent adapter
#                            rather than read out of it, so the answer comes
#                            from the file that makes the choice.
#   LOOP_BOX_AGENT_VERSION   the pinned agent, as installed. The Termination
#                            Contract's five numbers are calibrated against a
#                            Run, and a Run by a different agent version is a
#                            Run against a different calibration.
#   LOOP_BOX_CREDENTIAL_EXPIRES_AT
#                            when the nearest of the box's credentials stops
#                            working, as an absolute instant (#7, #260). The other
#                            three say what the box IS; this one says whether it
#                            can currently do anything.
#
#                            An instant rather than a remaining time, because
#                            this is read once a cycle and the page is viewed
#                            whenever: "7h left" read at 02:00 and rendered at
#                            09:00 would be the card asserting something
#                            nobody observed. What is left is arithmetic the
#                            viewer does, on a number the box actually said.
#                            Asked of the adapter and checked against the box's
#                            credential expiry files, taking the nearest instant.
#
# A fact the box cannot answer is simply not printed. The box's copy of the
# Loop can be older than this repository's - `--guest-template` is new here -
# and an omission is honest where a guess would put a claim on the page that
# nothing observed.
#
# It holds no credential of its own, starts nothing, and writes nothing: one
# SSH hop, three reads. Substitutable (ADR 0004) like every other command the
# Selector reaches through.
#
# Configuration, all environment, shared with box-sources/ssh.sh:
#
#   SELECTOR_BOX_HOST   required        where the box is
#   SELECTOR_BOX_USER   loop            the account a Run executes as
#   SELECTOR_BOX_LOOP   /home/loop/loop
#   SELECTOR_BOX_AGENT  claude          which adapter is asked
#   LOOP_GUEST_TEMPLATE optional        the target's guest image, carried across
#                                       the hop so the adapter reports the image
#                                       THIS target's Iterations would be built
#                                       from rather than the box's default
#
# Exit codes: 0 when the box answered, non-zero when it could not be read.

set -euo pipefail

# The shared refusal. Sourced rather than restated: an instance value has no
# default anywhere in the product, and the sentence that says so belongs in
# one place (issue #3).
# shellcheck source-path=SCRIPTDIR source=../require-value.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/require-value.sh"

require SELECTOR_BOX_HOST
box_host="${SELECTOR_BOX_HOST}"
box_user="${SELECTOR_BOX_USER:-loop}"
box_loop="${SELECTOR_BOX_LOOP:-/home/loop/loop}"
box_agent="${SELECTOR_BOX_AGENT:-claude}"

command -v ssh >/dev/null 2>&1 || {
    printf 'box-sources/facts.sh: ssh is required to reach the box\n' >&2
    exit 1
}

# The hash covers file CONTENT and file NAMES, both: a script deleted from the
# box - which is what the box's copy of the Loop is currently missing relative
# to this repository - has to move the hash as much as one edited. `sort` on
# the path keeps it independent of the order `find` walks the tree in, which is
# a filesystem property and not a property of what the box is holding.
#
# Deliberately not `git rev-parse`: the box's Loop is a copied directory and
# not a checkout, so there is no commit to name. The hash is what exists.
# shellcheck disable=SC2016  # the ${...} below are the REMOTE shell's, and
# must survive this printf unexpanded; the two values that come from HERE
# are passed as %q arguments and bound to `loop` and `agent` on the far side.
remote_command="$(printf 'set -uo pipefail
loop=%q
agent=%q
%s
if [ -d "${loop}" ]; then
    hash="$(find "${loop}" -type f -exec sha256sum {} + \
        | sed "s| ${loop}/| |" | LC_ALL=C sort -k2 | sha256sum | cut -c1-12)"
    printf "LOOP_BOX_SCRIPTS_HASH=%%s\\n" "${hash}"
fi
printf "LOOP_BOX_AGENT=%%s\\n" "${agent}"
adapter="${loop}/agents/${agent}.sh"
candidate_expiries=()
if [ -x "${adapter}" ]; then
    template="$("${adapter}" --guest-template 2>/dev/null || true)"
    [ -n "${template}" ] && printf "LOOP_BOX_GUEST_TEMPLATE=%%s\\n" "${template}"
    expiry="$("${adapter}" --credential-expiry 2>/dev/null || true)"
    [ -n "${expiry}" ] && candidate_expiries+=("${expiry}")
fi
for ef in "${HOME}/.config/loop/credential-expiry" "${HOME}/.config/loop/expiry" \
    "${HOME}/.config/loop"/*.expiry "${HOME}/.config/loop"/*-expiry \
    "${HOME}/.claude/expiry" "${HOME}/.claude/credential-expiry"; do
    if [ -f "${ef}" ]; then
        val="$(tr -d "[:space:]" < "${ef}")"
        [ -n "${val}" ] && candidate_expiries+=("${val}")
    fi
done
min_epoch=""
nearest_expiry=""
for exp in "${candidate_expiries[@]}"; do
    epoch="$(date -u -d "${exp}" +%%s 2>/dev/null || true)"
    if [ -n "${epoch}" ]; then
        if [ -z "${min_epoch}" ] || [ "${epoch}" -lt "${min_epoch}" ]; then
            min_epoch="${epoch}"
            nearest_expiry="$(date -u -d "@${epoch}" +%%Y-%%m-%%dT%%H:%%M:%%SZ)"
        fi
    fi
done
[ -n "${nearest_expiry}" ] && printf "LOOP_BOX_CREDENTIAL_EXPIRES_AT=%%s\\n" "${nearest_expiry}"
PATH="${HOME}/.local/bin:${HOME}/.grok/bin:${PATH}"
version="$("${agent}" --version 2>/dev/null | head -n 1 || true)"
[ -n "${version}" ] && printf "LOOP_BOX_AGENT_VERSION=%%s\\n" "${version}"
exit 0' "${box_loop}" "${box_agent}" \
    "${LOOP_GUEST_TEMPLATE:+$(printf 'export LOOP_GUEST_TEMPLATE=%q\n' "${LOOP_GUEST_TEMPLATE}")}")"

# BatchMode and a connect timeout: this is a status read on a cycle that has
# work to do, so a box that is not answering must fail fast rather than hold
# the cycle open. cycle.py bounds the whole call as well.
exec ssh -o BatchMode=yes -o ConnectTimeout=10 \
    "${box_host}" \
    "su - $(printf '%q' "${box_user}") -s /bin/bash -c $(printf '%q' "${remote_command}")"
