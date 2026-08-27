#!/usr/bin/env bash
#
# The labeled queue, faked for an Attended Preview (ADR 0016).
#
#   tracker.sh <owner/repo> <label>
#
# Same contract as tracker-sources/github.sh - {"issues": [...]}, one flat
# record per issue - and the issues are invented. A preview may run a whole
# cycle so that what a branch does to /loop can be seen end to end; what it
# may not do is read the real tracker, because the next thing a cycle does
# with what it reads is dispatch a Run against it.
#
# The three issues are chosen to exercise the three interesting paths: one
# eligible, one blocked by a native edge, one missing its `Owning area` and so
# handed back loudly.
set -euo pipefail

labeled_at="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)"

jq -n --arg at "$labeled_at" '{
  issues: [
    {
      number: 9001,
      title: "Preview: an eligible issue",
      url: "https://example.invalid/issues/9001",
      state: "OPEN",
      body: "## Acceptance criteria\n\nThe preview shows a Run card.\n\n## Owning area\n\nThe preview fixture\n",
      labeledBy: "JacobStephens2",
      labeledAt: $at,
      blockedBy: 0,
      openSubIssues: 0,
      proposals: []
    },
    {
      number: 9002,
      title: "Preview: an issue with an open blocker",
      url: "https://example.invalid/issues/9002",
      state: "OPEN",
      body: "## Acceptance criteria\n\nSkipped.\n\n## Owning area\n\nThe preview fixture\n",
      labeledBy: "JacobStephens2",
      labeledAt: $at,
      blockedBy: 1,
      openSubIssues: 0,
      proposals: []
    },
    {
      number: 9003,
      title: "Preview: an issue missing its Owning area",
      url: "https://example.invalid/issues/9003",
      state: "OPEN",
      body: "## Acceptance criteria\n\nHanded back loudly.\n",
      labeledBy: "JacobStephens2",
      labeledAt: $at,
      blockedBy: 0,
      openSubIssues: 0,
      proposals: []
    }
  ]
}'
