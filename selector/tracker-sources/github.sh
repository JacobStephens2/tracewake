#!/usr/bin/env bash
#
# The tracker source: the whole labeled queue, fetched from GitHub.
#
#   github.sh <owner/repo> <label>
#
# The default SELECTOR_TRACKER_COMMAND. Prints the queue as one JSON object on
# stdout and exits non-zero if it could not be fetched. Substitutable (ADR
# 0004, and the Loop's *_COMMAND convention): a tracker somewhere other than
# GitHub Issues is a different script here and no change to cycle.py. The
# offline suite substitutes canned queue states through this same seam.
#
# Note what this is NOT: the Loop's task source (task-sources/github.sh)
# fetches exactly one task by number, and that narrowness is ADR 0010's
# enforcement of Seeding-is-not-intake. This command deliberately lists. It is
# allowed to, because it runs off the box as the operator, and because since
# ADR 0014 the label IS the handover - listing the operator's own labeled
# queue is reading his own attestations, not reading arbitrary issues. The
# box's token still holds no Issues permission and still cannot run this.
#
# Output shape, one flat record per issue - everything Eligibility needs and
# nothing else:
#
#   {"issues": [{"number": 646, "title": ..., "url": ..., "state": "OPEN",
#                "body": ..., "labeledBy": "an-operator",
#                "labeledAt": "2026-08-26T12:00:00Z", "blockedBy": 1,
#                "blockers": [{"number": 645, "title": ..., "url": ...}],
#                "openSubIssues": 0,
#                "proposals": [{"number": 12, "url": ..., "isDraft": true}]}]}
#
# labeledBy is the actor of the MOST RECENT application of the label, which is
# the timeline check ADR 0014 asks for: the allowlist is checked against who
# handed the task over, not against who opened the issue. It is null when no
# LABELED_EVENT for that label is on the timeline, and a null labeler is not
# on any allowlist - the safe direction.
#
# labeledAt is when that same labeling happened, and it is what scopes the
# retry budget: re-applying the label is a fresh Handover, so dispatches from
# before it are history rather than attempts at the task as it stands now.
#
# blockedBy is the tracker's native edge count and the single blocking signal;
# prose "Blocked by" text is deliberately not read (ADR 0014).
#
# blockers are those same edges, named: the open issues the edges point at.
# Eligibility decides on the COUNT and nothing else - which is what keeps the
# predicate one query with no text parsing - but "blocked by 1" is not an
# answer to "blocked by what?", and the queue board's blocked cards have to
# name them (#158). Only the open ones: `blockedBy` is the whole dependency
# list including edges already satisfied, while `issueDependenciesSummary`
# counts only the open ones, so filtering here is what makes the list a
# naming of the same edges the count counts. It is capped where the count is
# not (see the query below), so the list may be shorter than the count and
# the board says so rather than pretending it is whole.
#
# proposals are the open pull requests that would close the issue - the
# `Closes #n` link the Loop's propose.sh writes into a Proposal body on the
# box. An open one means the issue is in flight.

set -euo pipefail

task_repo="${1:?usage: github.sh <owner/repo> <label>}"
label="${2:?usage: github.sh <owner/repo> <label>}"

[[ ${task_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || {
    printf 'github.sh: repository must be owner/name, got %s\n' "${task_repo}" >&2
    exit 1
}

command -v gh >/dev/null 2>&1 || {
    printf 'github.sh: gh is not installed - the Selector reads the tracker as the operator, off the Loop'"'"'s box\n' >&2
    exit 1
}
command -v jq >/dev/null 2>&1 || {
    printf 'github.sh: jq is required to normalize the tracker response\n' >&2
    exit 1
}

owner="${task_repo%%/*}"
name="${task_repo##*/}"

# timelineItems(last: 100) rather than first: the most recent labeling is the
# handover, and a long-lived issue's early timeline is not interesting here.
#
# The cursor variable is named $endCursor because `gh api graphql
# --paginate` injects exactly that name between pages. A differently-named
# variable ($cursor) is silently never set, so every page request fetches
# page one again: an unbounded identical-page loop that only ends when
# GitHub 504s it or jq runs out of memory. That is how a 72-issue queue
# once streamed hundreds of megabytes and killed a cycle.
read -r -d '' query <<'GRAPHQL' || true
query($owner: String!, $name: String!, $label: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $endCursor, states: OPEN, labels: [$label],
           orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url state body
        issueDependenciesSummary { blockedBy }
        # 50 rather than the summary's unbounded count: this is the naming
        # half, and a page cannot list an unbounded number of blockers
        # anyway. The deepest chain on either tracker is one edge, so the cap
        # has never bitten - and when it does the board says "and N more"
        # rather than quietly showing a short list, because the count above
        # is never truncated and the two would otherwise disagree.
        blockedBy(first: 50) { nodes { number title url state } }
        subIssues(first: 100) { nodes { number state } }
        timelineItems(last: 100, itemTypes: [LABELED_EVENT]) {
          nodes { ... on LabeledEvent { createdAt label { name } actor { login } } }
        }
        closedByPullRequestsReferences(first: 20, includeClosedPrs: false) {
          nodes { number url state isDraft mergeable mergeStateStatus }
        }
      }
    }
  }
}
GRAPHQL

gh api graphql --paginate \
    -F owner="${owner}" -F name="${name}" -F label="${label}" \
    -f query="${query}" |
    # `label` is a jq keyword, so the argument cannot be named for what it is.
    jq -s --arg wanted "${label}" '{
        issues: [
            .[].data.repository.issues.nodes[] | {
                number, title, url, state,
                body: (.body // ""),
                labeledBy: (
                    [.timelineItems.nodes[]
                     | select((.["label"].name? // "") == $wanted)]
                    | last | .actor.login? // null
                ),
                labeledAt: (
                    [.timelineItems.nodes[]
                     | select((.["label"].name? // "") == $wanted)]
                    | last | .createdAt? // null
                ),
                blockedBy: (.issueDependenciesSummary.blockedBy // 0),
                blockers: [
                    .blockedBy.nodes[]
                    | select(.state == "OPEN")
                    | {number, title, url}
                ],
                openSubIssues: (
                    [.subIssues.nodes[] | select(.state == "OPEN")] | length
                ),
                proposals: [
                    .closedByPullRequestsReferences.nodes[]
                    | {number, url, state, isDraft, mergeable, mergeStateStatus}
                ]
            }
        ] | sort_by(.number)
    }'

