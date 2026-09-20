#!/usr/bin/env bash
#
# The box surface, read-only, local: the microVMs this machine is holding.
#
#   microvms-local.sh
#
# The product-default SELECTOR_BOX_MICROVMS_COMMAND (ADR 0029). Sibling to
# box-sources/microvms.sh for the instance whose box is the controller
# itself (ADR 0004, ADR 0019): the same `sbx ls --json` on stdout, and no
# SSH hop, so no SELECTOR_BOX_HOST. Keeping the ssh-based read while setting
# that host to `local` is the `ssh: Could not resolve hostname local` the
# widget would carry.
#
# Reads as the Run account (SELECTOR_BOX_USER, default loop) - see
# become-run-user.sh. `sbx` is that account's, not the invoker's.
#
# Configuration, all environment:
#
#   SELECTOR_BOX_USER    loop     the account a Run executes as
#   LOOP_SBX_COMMAND     sbx      the Execution Boundary CLI
#
# Exit codes: 0 when the list was read (an empty list is none running),
# non-zero when it could not be.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=./become-run-user.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/become-run-user.sh"

become_run_user ${1+"$@"}

PATH="${HOME}/.local/bin:${HOME}/.grok/bin:${PATH}"
sbx="${LOOP_SBX_COMMAND:-sbx}"
command -v "${sbx}" >/dev/null 2>&1 || {
    printf 'microvms-local.sh: %s is not on PATH - the box lists microVMs through the Execution Boundary CLI\n' "${sbx}" >&2
    exit 1
}
exec "${sbx}" ls --json
