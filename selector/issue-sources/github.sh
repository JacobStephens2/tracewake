#!/usr/bin/env bash
#
# The issue-bookkeeping surface: comment on an issue, and swap its labels.
#
#   github.sh <owner/repo> comment <number>            # body on stdin
#   github.sh <owner/repo> relabel <number> <add> <remove>
#
# The default SELECTOR_ISSUE_COMMAND. This is the half of the Selector the
# Loop's box deliberately cannot do: the box's fine-grained token holds no
# Issues permission at all (ADR 0010), so every write to the tracker happens
# here, off the box, as the operator - which since ADR 0014 is exactly what
# the Handover authorized.
#
# Substitutable (ADR 0004) like every other outward reach, and it is the seam
# the offline suite drives: a scripted fake in its place is what lets the
# loud-skip scenarios assert which comment was written and which labels were
# swapped, with no token and no network.
#
# The comment body arrives on stdin rather than as an argument. It is markdown
# with newlines and backticks in it, and an argument would put the whole of it
# in this VM's process listing.
#
# Exit codes: 0 done, 1 could not run or GitHub refused.

set -euo pipefail

die() {
    printf 'issue-sources/github.sh: %s\n' "$*" >&2
    exit 1
}

task_repo="${1:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"
action="${2:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"
number="${3:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"

[[ ${task_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "repository must be owner/name, got ${task_repo}"
[[ ${number} =~ ^[1-9][0-9]*$ ]] || die "issue must be a number, got ${number}"

command -v gh >/dev/null 2>&1 ||
    die "gh is not installed - the Selector writes to the tracker as the operator, off the Loop's box"

case "${action}" in
    comment)
        body="$(cat)"
        [[ -n ${body} ]] || die "refusing to post an empty comment on #${number}"
        # --body-file - reads stdin. A comment nobody can attribute is worse
        # than none, so the failure is loud rather than swallowed.
        printf '%s' "${body}" |
            gh issue comment "${number}" --repo "${task_repo}" --body-file - ||
            die "GitHub refused a comment on ${task_repo}#${number}"
        ;;
    relabel)
        add="${4:?usage: github.sh <owner/repo> relabel <number> <add> <remove>}"
        remove="${5:?usage: github.sh <owner/repo> relabel <number> <add> <remove>}"
        # One call, both directions. Two calls would leave an issue carrying
        # both labels - or neither - if the second failed, and the whole point
        # of the swap is that an issue is in exactly one queue.
        gh issue edit "${number}" --repo "${task_repo}" \
            --add-label "${add}" --remove-label "${remove}" >/dev/null ||
            die "GitHub refused the label swap on ${task_repo}#${number}"
        ;;
    *)
        die "unknown action: ${action}"
        ;;
esac
