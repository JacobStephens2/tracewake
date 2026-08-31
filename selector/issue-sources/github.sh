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
# and no change to cycle.py (ADR 0004).
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
# `gh pr checks` would happily take a branch name and answer about a Proposal
# other than the one this Run produced; and it must name THIS repository,
# because `gh pr checks <url>` resolves the repository from the URL and
# ignores --repo, so a foreign URL is not a mismatch gh would catch - it is a
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
        # `gh pr checks` exits non-zero to MEAN something - 8 while checks are
        # still running, 1 when one has failed - so its exit code alone cannot
        # say whether the call worked, and the state is read from the rows.
        #
        # The dangerous case is an empty stdout, because THREE different things
        # produce it, all of them with exit 1: a Proposal with no checks
        # configured, a token that may not read check runs, and any other API
        # failure. Only the first is green. Treating them alike - which an
        # `|| true` and a default of `[]` does - routes unverified work to
        # `awaiting-review` the moment a token loses a permission, which is
        # exactly the silent false-pass this whole path exists to prevent.
        # So the three are told apart by what gh said on stderr, and anything
        # that is not "no checks reported" is a failure the Selector pages for.
        checks_err="$(mktemp)"
        # shellcheck disable=SC2064
        trap "rm -f -- '${checks_err}'" EXIT
        rows=""
        rows="$(gh pr checks "${number}" --repo "${task_repo}" \
                   --json name,state 2>"${checks_err}")" || true
        if [[ -z ${rows} ]]; then
            if grep -q 'no checks reported' "${checks_err}"; then
                # No check is configured on this Proposal. Reported as its
                # own state, NOT as green: "every check passed" and "no
                # check ran" are opposite facts about how far a Proposal has
                # been verified, and on a repository that does have CI - which
                # tourbot does - this answer means something went wrong
                # upstream (a broken workflow file, Actions disabled) rather
                # than that there was nothing to run. Sending that to review
                # as though it had passed is the same false pass as the one
                # above, arrived at by a different road.
                printf '{"state": "none", "failing": []}\n'
                exit 0
            else
                die "could not read the checks on ${task_repo}#${number}: $(tr '\n' ' ' < "${checks_err}")"
            fi
        fi
        printf '%s' "${rows}" | jq -c '
            # gh reports one row per check with a state. PENDING/QUEUED/
            # IN_PROGRESS are not decided yet; FAILURE/ERROR/TIMED_OUT/
            # CANCELLED are red; SUCCESS/NEUTRAL/SKIPPED are green. Anything
            # unrecognised counts as red, which is the safe direction: an
            # unknown state must never route work to review as if it passed.
            def red: ["FAILURE","ERROR","TIMED_OUT","CANCELLED","ACTION_REQUIRED","STARTUP_FAILURE"];
            def green: ["SUCCESS","NEUTRAL","SKIPPED"];
            def waiting: ["PENDING","QUEUED","IN_PROGRESS","REQUESTED","WAITING"];
            (map(select(.state as $s | waiting | index($s))) | length) as $pending
            | [ .[] | select(.state as $s | green | index($s) | not)
                    | select(.state as $s | waiting | index($s) | not)
                    | .name ] as $failing
            | if ($failing | length) > 0 then
                  {state: "red", failing: $failing}
              elif $pending > 0 then
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
