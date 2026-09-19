#!/usr/bin/env bash
#
# The reconcile Run's box-side half: bring one conflicting Proposal's branch
# up to date with its base, inside the microVM boundary.
#
#   reconcile.sh --repo <checkout> --proposal <number-or-url>
#                [--task-repo <owner/name>] [--base <branch>] [--check <command>]
#                [--remote <name>]
#
#   1. resolve the Proposal's head branch from the forge,
#   2. fetch the remote and resolve the base (the default branch unless --base),
#   3. check out the Proposal branch from the remote,
#   4. merge the base; when the merge conflicts, the agent adapter resolves
#      the conflicts inside the Execution Boundary, and the merge is verified,
#   5. run --check when given; a red suite stops the push,
#   6. push the Proposal branch, and report LOOP_RECONCILE_BRANCH.
#
# Proposal-Only Output holds by construction, and three guards hold it:
#
#   1. the only write to the forge is a push of the Proposal's own branch -
#      the base is never checked out for writing and is never pushed to;
#   2. the push is never forced - a push that would have to overwrite the
#      remote has found a state this script must not resolve by discarding;
#   3. a Proposal whose head IS the base is refused outright, so a confused
#      caller cannot push the default branch at itself.
#
# The forge is reached the pr-sources way: `curl` plus the token file, because
# `gh` is not on the box and GH_TOKEN in the environment would put the token
# in every process listing. `curl` by name is the seam the offline suite
# drives.
#
# Exit codes, split the propose.sh way so the Selector can tell what failed:
#
#   0  reconciled, verified and pushed.
#   1  could not run - bad arguments, the Proposal or the base could not be
#      resolved, or the Proposal's head is the base. Nothing was pushed.
#   2  the conflicts could not be resolved - the agent left unmerged paths,
#      or there was no agent to ask. Merged locally, nothing was pushed.
#   3  the verification suite went red. Merged locally, nothing was pushed.
#   4  the push failed. Merged and verified locally, nothing external happened.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=contract.sh
source "${loop_dir}/contract.sh"

die() {
    printf 'reconcile.sh: %s\n' "$*" >&2
    exit 1
}

repo=""
proposal=""
task_repo="${SELECTOR_TASK_REPO:-}"
base=""
check=""
remote="origin"

while (($# > 0)); do
    case "$1" in
        --repo) repo="${2:?reconcile.sh --repo needs a path}"; shift 2 ;;
        --proposal) proposal="${2:?reconcile.sh --proposal needs a number or URL}"; shift 2 ;;
        --task-repo) task_repo="${2:?reconcile.sh --task-repo needs owner/name}"; shift 2 ;;
        --base) base="${2:?reconcile.sh --base needs a branch}"; shift 2 ;;
        --check) check="${2:?reconcile.sh --check needs a command}"; shift 2 ;;
        --remote) remote="${2:?reconcile.sh --remote needs a name}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n ${repo} ]] || die "usage: reconcile.sh --repo <checkout> --proposal <number-or-url>"
[[ -n ${proposal} ]] || die "usage: reconcile.sh --repo <checkout> --proposal <number-or-url>"
[[ -d ${repo}/.git ]] || die "no git checkout at ${repo}"
# An instance value has no default anywhere in the product (issue #3): without
# a repository the forge read below would be a guess about whose Proposal this
# is, and a guess about that is a push to the wrong repository.
[[ -n ${task_repo} ]] ||
    die "no task repository - pass --task-repo or set SELECTOR_TASK_REPO"
[[ ${task_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "repository must be owner/name, got ${task_repo}"

command -v git >/dev/null 2>&1 || die "git is required to reconcile a branch"
command -v curl >/dev/null 2>&1 || die "curl is required to read the Proposal"
command -v jq >/dev/null 2>&1 || die "jq is required to read the Proposal"

# --- The Proposal's head branch, from the forge ------------------------------
#
# A number or a pull request URL, like the Selector's checks and update-branch
# take. A URL must name THIS repository: the token on this box is scoped to
# exactly one, and a foreign URL is a different Proposal answered as though it
# were this one.

proposal_number=""
if [[ ${proposal} =~ ^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/([1-9][0-9]*)$ ]]; then
    [[ ${BASH_REMATCH[1]} == "${task_repo}" ]] ||
        die "the proposal ${proposal} is not in ${task_repo}"
    proposal_number="${BASH_REMATCH[2]}"
elif [[ ${proposal} =~ ^[1-9][0-9]*$ ]]; then
    proposal_number="${proposal}"
else
    die "proposal must be a pull request URL or a number, got ${proposal}"
fi

# The same file the git credential helper and pr-sources read, so the box has
# one token in one place rather than one per caller.
token_file="${LOOP_GITHUB_TOKEN_FILE:-${HOME}/.config/loop/github-token}"
[[ -s ${token_file} ]] ||
    die "no GitHub token at ${token_file} - the box can merge but cannot read the Proposal"
token="$(tr -d '\r\n' <"${token_file}")"

pr_response=""
pr_response="$(curl --silent --show-error --fail-with-body \
    --request GET \
    --header "Authorization: Bearer ${token}" \
    --header "Accept: application/vnd.github+json" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    --header "User-Agent: eta-loop" \
    "https://api.github.com/repos/${task_repo}/pulls/${proposal_number}")" ||
    die "could not read proposal ${proposal_number} on ${task_repo}"

head_branch="$(jq -r '.head.ref // ""' <<<"${pr_response}")"
[[ -n ${head_branch} && ${head_branch} != "null" ]] ||
    die "the forge named no head branch for proposal ${proposal_number}"
[[ ${head_branch} != -* ]] ||
    die "the forge named an unusable head branch for proposal ${proposal_number}"

# --- The base ----------------------------------------------------------------

git -C "${repo}" fetch --prune "${remote}"

if [[ -z ${base} ]]; then
    base="$(git -C "${repo}" symbolic-ref --short "refs/remotes/${remote}/HEAD" 2>/dev/null || true)"
    base="${base#"${remote}/"}"
    [[ -n ${base} ]] ||
        die "no default branch found on ${remote} - pass --base"
fi
git -C "${repo}" rev-parse --verify --quiet "${remote}/${base}" >/dev/null ||
    die "no such base branch: ${remote}/${base}"

# The third guard from the header: a Proposal whose head is the base is a
# caller asking to push the default branch at itself.
[[ ${head_branch} != "${base}" ]] ||
    die "proposal ${proposal_number} heads ${base}, which is the base - refusing"

git -C "${repo}" rev-parse --verify --quiet "${remote}/${head_branch}" >/dev/null ||
    die "no such proposal branch on ${remote}: ${head_branch}"

# A checkout that fails because this checkout is dirty is a box that needs a
# human: discarding whatever is in it unattended is not this script's call.
# --quiet for the report stream's sake; a failure still prints to stderr.
git -C "${repo}" checkout --quiet -B "${head_branch}" "${remote}/${head_branch}"

# --- The merge ----------------------------------------------------------------
#
# Always a merge commit, never a fast-forward: this is what the forge's own
# update-branch does, so a reconciled Proposal reads the same way whichever
# path brought it current. The agent resolves conflicts; the script verifies.

if git -C "${repo}" merge-base --is-ancestor "${remote}/${base}" HEAD; then
    printf 'reconcile.sh: %s already contains %s/%s\n' \
        "${head_branch}" "${remote}" "${base}" >&2
else
    merge_rc=0
    # Routine merge chatter to stderr: stdout carries the report lines and
    # nothing else, so whatever parses them never has to skip git's prose.
    git -C "${repo}" merge --no-ff --no-edit \
        -m "Merge ${remote}/${base} into ${head_branch}" \
        "${remote}/${base}" >&2 || merge_rc=$?
    if ((merge_rc != 0)); then
        # --- Conflict resolution inside the boundary -----------------------
        #
        # The agent adapter's contract (contract.sh, ADR 0004): prompt file
        # and turn bound in, exit status out, executed with the repository as
        # its working directory. The adapter builds the microVM, bind-mounts
        # this checkout into it, and destroys the microVM when the Iteration
        # exits - so the resolution happens inside the Execution Boundary and
        # the merge it leaves behind is a commit in this checkout, where a
        # reviewer sees it. The token never enters the boundary: nothing here
        # pushes from inside it.
        conflicting="$(git -C "${repo}" diff --name-only --diff-filter=U || true)"
        [[ -n ${conflicting} ]] ||
            die "the merge failed with no conflicting files - leaving it for a human"
        : "${LOOP_AGENT_COMMAND:=${loop_dir}/agents/claude.sh}"
        [[ -x ${LOOP_AGENT_COMMAND} ]] ||
            die "no agent to resolve the conflicts (not executable: ${LOOP_AGENT_COMMAND})"
        prompt_file="$(mktemp)"
        trap 'rm -f -- "${prompt_file}"' EXIT
        {
            printf 'You are resolving a merge in an unattended reconcile Run.\n\n'
            printf 'Branch %s has been merged with %s/%s and the merge conflicts.\n' \
                "${head_branch}" "${remote}" "${base}"
            printf 'The conflicting files are:\n\n%s\n\n' "${conflicting}"
            printf 'Resolve every conflict, keeping the intent of both sides. '
            printf 'Then stage every resolved file with git add and commit the merge with git commit --no-edit. '
            printf 'Do not push, do not open a pull request, and do not touch any other branch.\n'
        } >"${prompt_file}"
        agent_rc=0
        (
            cd "${repo}" &&
                "${LOOP_AGENT_COMMAND}" "${prompt_file}" "${LOOP_MAX_TURNS}"
        ) || agent_rc=$?
        rm -f -- "${prompt_file}"
        trap - EXIT
        # A nonzero agent is not "could not run": the agent ran and the merge
        # is still open, which is the unresolvable case the Selector
        # escalates rather than retries.
        if ((agent_rc != 0)); then
            printf 'reconcile.sh: the agent exited %s without resolving the merge\n' \
                "${agent_rc}" >&2
            exit 2
        fi
        # The merge the agent leaves behind is verified, not trusted: no
        # unmerged paths may remain, and the base must be an ancestor of the
        # result - a resolution that dropped the base is a Proposal quietly
        # un-branched from it.
        unmerged="$(git -C "${repo}" diff --name-only --diff-filter=U || true)"
        [[ -z ${unmerged} ]] || {
            printf 'reconcile.sh: still unmerged after the agent: %s\n' "${unmerged}" >&2
            exit 2
        }
        git -C "${repo}" merge-base --is-ancestor "${remote}/${base}" HEAD || {
            printf 'reconcile.sh: %s/%s is not an ancestor of the result - the resolution dropped the base\n' \
                "${remote}" "${base}" >&2
            exit 2
        }
    fi
fi

# The branch is reported once the merge stands, before verification and
# the push, so a reconcile that merged but could not verify or push still
# names where the work got to: the Selector's escalation says that instead
# of that none exists.
printf 'LOOP_RECONCILE_BRANCH=%s\n' "${head_branch}"

# --- Verification before the push ---------------------------------------------
#
# The suite is the owning issue's Check when the Selector passed one. It runs
# here, on the merged branch, and a red suite stops the push: an unverified
# merge pushed to a Proposal is the false pass the checks vocabulary exists
# to prevent. With no Check there is nothing to run, and merge-cleanliness is
# the whole of the verification.

if [[ -n ${check} ]]; then
    check_rc=0
    (cd "${repo}" && bash -c "${check}") || check_rc=$?
    if ((check_rc != 0)); then
        printf 'reconcile.sh: the verification suite exited %s - not pushing\n' \
            "${check_rc}" >&2
        exit 3
    fi
fi

# --- The push -----------------------------------------------------------------
#
# The Proposal's branch, named, never forced. A push that would have to
# overwrite the remote has found a state this script must not resolve by
# discarding - and the default branch is unreachable here twice over: it was
# never checked out, and its name never appears in this command.

push_out=""
push_out="$(git -C "${repo}" push "${remote}" "${head_branch}" 2>&1)" || {
    printf 'reconcile.sh: the push failed: %s\n' "${push_out}" >&2
    exit 4
}
