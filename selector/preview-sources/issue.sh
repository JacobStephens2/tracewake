#!/usr/bin/env bash
#
# Issue bookkeeping, faked for an Attended Preview (ADR 0016).
#
#   issue.sh <owner/repo> comment <number>            # body on stdin
#   issue.sh <owner/repo> relabel <number> <add> <remove>
#   issue.sh <owner/repo> checks <proposal>
#
# Same contract as issue-sources/github.sh. Label swaps and comments are
# writes to a real tracker under the operator's token, so a preview journals
# what it would have done and writes nothing.
set -euo pipefail

repo="${1:?usage: issue.sh <owner/repo> <verb> ...}"
verb="${2:?usage: issue.sh <owner/repo> <verb> ...}"
shift 2

case "$verb" in
    comment)
        cat >/dev/null  # the body arrives on stdin; drop it
        printf 'preview: would have commented on %s#%s\n' "$repo" "${1:-?}" >&2
        ;;
    relabel)
        printf 'preview: would have swapped %s#%s: +%s -%s\n' \
            "$repo" "${1:-?}" "${2:-?}" "${3:-?}" >&2
        ;;
    checks)
        # Green, so the awaiting-review route is the one a preview cycle takes.
        printf '{"state": "green", "failing": []}\n'
        ;;
    *)
        printf 'issue.sh: unknown verb %s\n' "$verb" >&2
        exit 1
        ;;
esac
