#!/usr/bin/env bash
#
# The issue-bookkeeping surface: comment on an issue, and swap its labels.
#
#   github.sh <owner/repo> comment <number>            # body on stdin
#   github.sh <owner/repo> relabel <number> <add> <remove>
#   github.sh <owner/repo> checks <proposal-url-or-number>
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
# `checks` prints one JSON object - {"state": "green"|"red"|"pending"|"none",
# "failing": [...]} - and that shape, not gh's, is the Selector's contract.
# The translation lives here so that a different tracker is a different script
# and no change to cycle.py (ADR 0004). It reads GitHub Actions workflow runs
# rather than check runs, which is a permission story rather than a preference
# - see the `checks` arm below, and #273.
#
# Exit codes: 0 done, 1 could not run or GitHub refused.

set -euo pipefail

die() {
    printf 'issue-sources/github.sh: %s\n' "$*" >&2
    exit 1
}

# The `checks` arm reaches GitHub twice and can be refused at either call, so
# one shape says so, with whatever gh last wrote to `${checks_err}` appended.
# `$1` is what could not be read. Every caller fails closed: no state is
# printed, and the Selector pages rather than routing unverified work.
#
# `${number}` goes after a colon rather than after a `#`, because for `checks`
# it may be a whole URL and `${task_repo}#https://...` reads as neither.
gh_refused() {
    local said=""
    if [[ -s ${checks_err:-} ]]; then
        said=" - $(tr '\n' ' ' < "${checks_err}")"
    fi
    die "could not read $1 on ${task_repo}: ${number}${said}"
}

task_repo="${1:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"
action="${2:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"
number="${3:?usage: github.sh <owner/repo> <comment|relabel> <number> ...}"

[[ ${task_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "repository must be owner/name, got ${task_repo}"

# The two writing actions take an issue NUMBER, and nothing else: the number is
# the whole of what says which issue is commented on or relabeled.
#
# `checks` is the exception, and it is deliberate rather than lax. cycle.py
# holds a Proposal's URL - the box reports it, nothing parses a number out of
# it - so demanding a number here would break the one path that runs after
# every successful Run. It did, on the first unattended dispatch: the Run
# worked, and the cycle died reading the checks of the Proposal it had just
# produced (tourbot #712, 2026-08-31).
#
# A URL is still checked twice over. It must be a pull request URL, because
# `gh pr view` would happily take a branch name and answer about a Proposal
# other than the one this Run produced; and it must name THIS repository,
# because `gh pr view <url>` resolves the repository from the URL and ignores
# --repo, so a foreign URL is not a mismatch gh would catch - it is a
# different Proposal, answered as though it were this one.
if [[ ${action} == checks ]]; then
    if [[ ${number} =~ ^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/[1-9][0-9]*$ ]]; then
        [[ ${BASH_REMATCH[1]} == "${task_repo}" ]] ||
            die "the proposal ${number} is not in ${task_repo}"
    elif [[ ! ${number} =~ ^[1-9][0-9]*$ ]]; then
        die "proposal must be a pull request URL or a number, got ${number}"
    fi
else
    [[ ${number} =~ ^[1-9][0-9]*$ ]] ||
        die "issue must be a number, got ${number}"
fi

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
    checks)
        command -v jq >/dev/null 2>&1 ||
            die "jq is required to read a Proposal's checks"
        # Read as WORKFLOW RUNS, not as check runs, because a fine-grained PAT
        # cannot read check runs at all. That is a property of the token type,
        # not a grant anyone forgot: GitHub's fine-grained tokens offer no
        # Checks permission - the picker has no such entry and the
        # fine-grained-permissions reference has no such section - so
        # `GET /repos/{o}/{r}/commits/{ref}/check-runs`, and the GraphQL
        # `statusCheckRollup` that `gh pr checks` walks, answer 403 for every
        # token this script can be given. Verified on the operator's token on
        # 2026-08-31; the Checks API wants a GitHub App, an OAuth token or a
        # classic PAT with `repo` (#273).
        #
        # `actions/runs?head_sha=` is covered by the Actions permission the
        # token already holds and carries the same status/conclusion fields.
        # The cost is real and bounded: it sees only Actions-based checks, so
        # a required check posted by some other app would be invisible here.
        # Every one of tourbot's checks is Actions today. The alternatives -
        # a classic PAT with `repo`, or minting installation tokens from an
        # App - trade a broad credential or new moving parts for that edge.
        #
        # Two calls, and the first one matters: the runs are asked for by head
        # commit rather than by branch, so a Proposal whose branch has moved
        # on since the Run pushed cannot be graded by a later commit's CI.
        checks_err="$(mktemp)"
        # shellcheck disable=SC2064
        trap "rm -f -- '${checks_err}'" EXIT
        head_sha=""
        head_sha="$(gh pr view "${number}" --repo "${task_repo}" \
                       --json headRefOid --jq .headRefOid 2>"${checks_err}")" ||
            gh_refused "the Proposal"
        # The sha goes into an API path, so it is checked rather than trusted.
        # An empty --jq result prints as `null`, and `head_sha=null` is not an
        # error to GitHub - it is a query that matches nothing, which would
        # arrive here as the state "none" and read as a Proposal whose CI
        # never ran.
        [[ ${head_sha} =~ ^[0-9a-f]{40}$ ]] ||
            die "gh gave no head commit for the Proposal ${task_repo}: ${number}"
        # Fail closed. `gh api` exits non-zero on any refusal, and a refusal is
        # neither "no checks" nor green: an `|| true` and a default of `[]`
        # here would route unverified work to `awaiting-review` the moment a
        # token lost a permission, which is exactly the silent false pass this
        # whole path exists to prevent.
        pages=""
        pages="$(gh api --paginate \
                    "repos/${task_repo}/actions/runs?head_sha=${head_sha}&per_page=100" \
                    2>"${checks_err}")" ||
            gh_refused "the checks"
        [[ -n ${pages} ]] ||
            die "gh returned nothing for the checks on ${task_repo}: ${number}"
        printf '%s' "${pages}" | jq -s -c '
            # --paginate concatenates one object per page, so slurp and take
            # every page: a failure on page two must not ship as green. The
            # runs are counted rather than read off `total_count`, because
            # `total_count` is what GitHub says exists and the runs are what
            # was actually read - and it is the read ones that get graded.
            #
            # Undecided is a run that has not completed, OR one that has but
            # carries no conclusion yet - the API reports that transition
            # briefly, and grading it red would be a FALSE red rather than a
            # safe one: red is terminal (`ready-for-human`), and only pending
            # is polled again.
            #
            # Of the runs that did conclude, success/neutral/skipped are
            # passes - the reading the check-run mapping this replaced also
            # had, kept deliberately: a job that did not need to run has not
            # failed. EVERYTHING else is red, including a conclusion GitHub
            # adds tomorrow and including `stale`, which marks a run whose
            # commit is no longer what the branch points at. Red is the
            # fallthrough because an unknown verdict must never route work to
            # review as if it had passed.
            def green: ["success","neutral","skipped"];
            [ .[].workflow_runs[] ] as $runs
            | [ $runs[] | select(.status != "completed" or .conclusion == null)
              ] as $running
            | [ $runs[] | select(.status == "completed" and .conclusion != null)
                        | select(.conclusion as $c | green | index($c) | not)
                        | (.name // "unnamed workflow run") ] as $failing
            | if ($runs | length) == 0 then
                  # No run was created for this commit. Its own state, NOT a
                  # flavour of green: "every check passed" and "no check ran"
                  # are opposite facts about how far a Proposal has been
                  # verified, and on a repository that does have CI - which
                  # tourbot does - this means something went wrong upstream (a
                  # workflow file that will not parse, Actions disabled, a run
                  # that never triggered) rather than that there was nothing
                  # to run.
                  {state: "none", failing: []}
              elif ($failing | length) > 0 then
                  {state: "red", failing: $failing}
              elif ($running | length) > 0 then
                  {state: "pending", failing: []}
              else
                  {state: "green", failing: []}
              end' ||
            die "could not read the checks on ${task_repo} ${number}"
        ;;
    *)
        die "unknown action: ${action}"
        ;;
esac
