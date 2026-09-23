#!/usr/bin/env bash
#
# The labeled queue, faked for an Attended Preview (ADR 0016).
#
#   tracker.sh <owner/repo> <label>
#
# Same contract as tracker-sources/github.sh - {"issues": [...]}, one flat
# record per issue. `archived` is omitted, which the Cycle treats as not
# archived. The issues are invented. A preview may run a whole
# cycle so that what a branch does to /loop can be seen end to end; what it
# may not do is read the real tracker, because the next thing a cycle does
# with what it reads is dispatch a Run against it.
#
# It answers per label, because the queue board (#158) asks for each label in
# the lifecycle in turn and a fixture that ignored the argument would render
# the same three cards in all five columns - which is exactly the bug a
# preview of a board change exists to catch.
#
# The Handover label's four issues are chosen to exercise the four paths a
# labeled issue can take: one eligible, one blocked by a native edge, one with
# an open Proposal, one missing its `Acceptance criteria` and so handed back
# loudly. The other two labels carry one issue each so their columns are not
# empty.
set -euo pipefail

label="${2:?usage: tracker.sh <owner/repo> <label>}"
labeled_at="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)"

# The two label columns carry one issue each so that a preview of the board
# shows five filled columns. One arm rather than two: they differ only in the
# number and the sentence, and two copies of a twelve-line record would drift.
case "${label}" in
  awaiting-review) number=9101; what="a green Proposal waiting on you" ;;
  ready-for-human) number=9201; what="the Selector gave up on this one" ;;
  *) number="" ;;
esac

if [[ -n ${number} ]]; then
    jq -n --arg at "$labeled_at" --argjson number "$number" --arg what "$what" '{
      issues: [
        {
          number: $number,
          title: ("Preview: " + $what),
          url: ("https://example.invalid/issues/" + ($number | tostring)),
          state: "OPEN",
          body: "## Acceptance criteria\n\nSeen on the board.\n",
          labeledBy: "an-operator", labeledAt: $at,
          blockedBy: 0, blockers: [], openSubIssues: 0, proposals: []
        }
      ]
    }'
    exit 0
fi

jq -n --arg at "$labeled_at" '{
  issues: [
    {
      number: 9001,
      title: "Preview: an eligible issue",
      url: "https://example.invalid/issues/9001",
      state: "OPEN",
      body: "## Acceptance criteria\n\nThe preview shows a Run card.\n\n## Owning area\n\nThe preview fixture\n",
      labeledBy: "an-operator",
      labeledAt: $at,
      blockedBy: 0,
      blockers: [],
      openSubIssues: 0,
      proposals: []
    },
    {
      number: 9002,
      title: "Preview: an issue with an open blocker",
      url: "https://example.invalid/issues/9002",
      state: "OPEN",
      body: "## Acceptance criteria\n\nSkipped.\n\n## Owning area\n\nThe preview fixture\n",
      labeledBy: "an-operator",
      labeledAt: $at,
      blockedBy: 1,
      blockers: [
        {
          number: 9000,
          title: "Preview: the issue that has to land first",
          url: "https://example.invalid/issues/9000"
        }
      ],
      openSubIssues: 0,
      proposals: []
    },
    {
      number: 9003,
      title: "Preview: an issue missing its Acceptance criteria",
      url: "https://example.invalid/issues/9003",
      state: "OPEN",
      body: "## Problem\n\nUnderspecified, and handed back loudly.\n",
      labeledBy: "an-operator",
      labeledAt: $at,
      blockedBy: 0,
      blockers: [],
      openSubIssues: 0,
      proposals: []
    },
    {
      number: 9004,
      title: "Preview: an issue with an open Proposal",
      url: "https://example.invalid/issues/9004",
      state: "OPEN",
      body: "## Acceptance criteria\n\nIn flight.\n",
      labeledBy: "an-operator",
      labeledAt: $at,
      blockedBy: 0,
      blockers: [],
      openSubIssues: 0,
      proposals: [
        {
          number: 77,
          url: "https://example.invalid/pull/77",
          state: "OPEN",
          isDraft: true
        }
      ]
    }
  ]
}'
