#!/usr/bin/env bash
#
# Owner-wide search for the Handover label, across every repository the
# account can see.
#
#   github.sh <owner> <label>
#
# The default SELECTOR_SEARCH_COMMAND. Prints the labeled issues as one JSON
# object on stdout and exits non-zero if they could not be fetched.
# Substitutable (ADR 0004): a tracker somewhere other than GitHub Issues is a
# different script here and no change to cycle.py. The offline suite
# substitutes canned hits through this same seam.
#
# Read-only on purpose. This is the unenrolled-Target warning's tracker
# reach (issue #39): it may list, and it may not comment, relabel, enroll, or
# otherwise write. cycle.py diffs the result against the declared Targets;
# this script does not know what is enrolled.
#
# Output shape, one flat record per issue:
#
#   {"issues": [{"repo": "acme/other", "number": 7, "title": ..., "url": ...}]}
#
# `repo` is `owner/name`. cycle.py groups by it and drops any that already
# have a Target stanza.
#
# Exit codes: 0 listed (including zero hits), 1 could not run or GitHub
# refused.

set -euo pipefail

die() {
    printf 'search-sources/github.sh: %s\n' "$*" >&2
    exit 1
}

owner="${1:?usage: github.sh <owner> <label>}"
label="${2:?usage: github.sh <owner> <label>}"

# An owner, not a repository. A slug here would search the wrong thing and
# look like a successful empty gap.
[[ ${owner} =~ ^[A-Za-z0-9._-]+$ ]] ||
    die "owner must be a GitHub user or organization, got ${owner}"
[[ -n ${label} ]] || die "label must not be empty"

command -v gh >/dev/null 2>&1 ||
    die "gh is not installed - the Selector searches the tracker as the operator, off the Loop's box"
command -v jq >/dev/null 2>&1 ||
    die "jq is required to normalize the search response"

# --limit 1000 is gh's ceiling. A Cycle that saw only the first page of an
# unenrolled pile would warn about a subset and then treat the rest as new
# the next time they scrolled into view.
hits=""
# --archived=false: GitHub's default is every repository the account can
# see, including archived ones. An archived repository is read-only, so a
# Handover label on one is not work this instance can operate on and not
# an unenrolled-Target gap the operator can enroll.
hits="$(gh search issues \
    --owner "${owner}" \
    --label "${label}" \
    --state open \
    --archived=false \
    --limit 1000 \
    --json repository,number,title,url)" ||
    die "GitHub refused the owner-wide search for ${label} on ${owner}"

[[ -n ${hits} ]] || hits='[]'

printf '%s' "${hits}" | jq -c '
    {
        issues: [
            .[]
            | select((.repository.isArchived // false) | not)
            | {
                repo: (.repository.nameWithOwner // ""),
                number, title, url
            }
            | select(.repo != "")
        ]
    }' ||
    die "could not read the owner-wide search for ${label} on ${owner}"
