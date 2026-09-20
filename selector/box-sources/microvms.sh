#!/usr/bin/env bash
#
# The box surface, read-only: the microVMs the box is currently holding.
#
#   microvms.sh
#
# The remote-Box SELECTOR_BOX_MICROVMS_COMMAND. An instance substitutes this
# for microvms-local.sh. Prints `sbx ls --json` on stdout and exits 0 when
# the box answered. An empty list is none running, not a missing read.
#
# It holds no credential of its own, starts nothing, and writes nothing: one
# SSH hop, one list. Substitutable (ADR 0004) like every other command the
# Selector reaches through.
#
# Configuration, all environment, shared with box-sources/ssh.sh:
#
#   SELECTOR_BOX_HOST   required        where the box is
#   SELECTOR_BOX_USER   loop            the account a Run executes as
#   LOOP_SBX_COMMAND    sbx             the Execution Boundary CLI, on the box
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
sbx="${LOOP_SBX_COMMAND:-sbx}"

command -v ssh >/dev/null 2>&1 || {
    printf 'box-sources/microvms.sh: ssh is required to reach the box\n' >&2
    exit 1
}

# shellcheck disable=SC2016  # the ${...} below are the REMOTE shell's.
remote_command="$(printf 'set -euo pipefail
PATH="${HOME}/.local/bin:${HOME}/.grok/bin:${PATH}"
sbx=%q
command -v "${sbx}" >/dev/null 2>&1 || {
    printf "microvms.sh: %%s is not on PATH\\n" "${sbx}" >&2
    exit 1
}
exec "${sbx}" ls --json' "${sbx}")"

# BatchMode and a connect timeout: this is a status read on a page view, so
# a box that is not answering must fail fast rather than hold the dashboard
# open. microvms.py bounds the whole call as well.
exec ssh -o BatchMode=yes -o ConnectTimeout=10 \
    "${box_host}" \
    "su - $(printf '%q' "${box_user}") -s /bin/bash -c $(printf '%q' "${remote_command}")"
