#!/usr/bin/env bash
#
# Proposal-Only Output: the Run's one external effect.
#
#   propose.sh --repo <path> [--task-ref <text>] [--ended-by <bound>]
#              [--exit <code>] [--remote <name>] [--base <branch>]
#
# Pushes the Run's branch and opens a draft pull request against the base
# branch, referencing the task the Run was seeded from. Called by run.sh at the
# end of a Run when it is started with --propose; runnable on its own afterwards,
# which is what a Run whose proposal failed needs.
#
# ## What makes it proposal-ONLY
#
# Not this script. Three things outside it, in descending order of how much they
# are worth:
#
#   1. The box's fine-grained GitHub token holds Contents and Pull requests and
#      nothing else. It cannot merge, cannot administer, cannot deploy, and has
#      no Issues permission at all (ADR 0010).
#   2. The Execution Boundary's egress is deny-all plus two hosts, and the
#      Iteration that does the work does not have the token inside it - the push
#      happens HERE, on the host, after the agent processes are gone.
#   3. Branch protection on the target repository's default branch.
#
# `draft: true` is fourth and weakest: anyone who can see a draft can mark it
# ready. It is what a reviewer sees, not what holds.
#
# ## Two seams, for the same reason the agent has one
#
# The push is a real `git push` to a real remote - a local bare repository in
# the suite, GitHub on the box - so what is tested is the command a Run runs.
# The pull request is one substitutable command (LOOP_PR_COMMAND), which is what
# lets the suite drive this script end to end with no token and no network.
#
# Exit codes, split so that triage after a failed proposal does not need the log:
#
#   0  proposed, or an open pull request for this branch already existed.
#   1  could not run - bad arguments, no base branch, or the Run is on the base
#      branch itself. Nothing was pushed.
#   2  the push failed. Nothing external happened.
#   3  the push landed and the pull request did not. The branch is on GitHub;
#      opening the proposal by hand is one command.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=contract.sh
source "${loop_dir}/contract.sh"

: "${LOOP_PR_COMMAND:=${loop_dir}/pr-sources/github.sh}"

die() {
    printf 'propose.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
propose.sh --repo <path> [--task-ref <text>] [--ended-by <bound>] [--exit <code>]
           [--remote <name>] [--base <branch>]

Pushes the Run's branch and opens a draft pull request referencing the task.

  --repo      the repository the Run worked in.
  --task-ref  the task the Run was seeded from, as owner/name#number. Read from
              the Plan when not given.
  --ended-by  the bound that ended the Run, for the pull request's body.
  --exit      the Run's exit code, for the same.
  --remote    the git remote to push to (default origin).
  --base      the branch to propose against (default the remote's HEAD).
USAGE
}

repo=""
task_ref=""
ended_by=""
run_exit=""
remote="origin"
base=""

while (($# > 0)); do
    case "$1" in
        --repo) repo="${2:?--repo needs a path}"; shift 2 ;;
        --task-ref) task_ref="${2:?--task-ref needs a value}"; shift 2 ;;
        --ended-by) ended_by="${2:?--ended-by needs a value}"; shift 2 ;;
        --exit) run_exit="${2:?--exit needs a code}"; shift 2 ;;
        --remote) remote="${2:?--remote needs a name}"; shift 2 ;;
        --base) base="${2:?--base needs a branch}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# --- Preflight --------------------------------------------------------------

[[ -n ${repo} ]] || die "--repo is required"
[[ -d ${repo} ]] || die "no such directory: ${repo}"
repo="$(cd -- "${repo}" && pwd)"

git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: ${repo}"

git -C "${repo}" remote get-url "${remote}" >/dev/null 2>&1 ||
    die "no remote '${remote}' in ${repo} - a proposal needs somewhere to go"

branch="$(git -C "${repo}" symbolic-ref --quiet --short HEAD)" ||
    die "${repo} is not on a branch - a Run works on a branch so that its proposal is one"

# The base is taken from the remote's own HEAD rather than assumed to be `main`
# or `master`: the target repository's default branch is `master` and this
# repository's is not, and a wrong base opens a pull request nobody asked for
# against a branch nobody is watching.
default_base="$(loop_base_branch "${repo}" "${remote}" || true)"
if [[ -z ${base} ]]; then
    base="${default_base}"
    [[ -n ${base} ]] ||
        die "cannot tell what ${remote}'s default branch is - pass --base. \`git remote set-head ${remote} --auto\` records it."
fi

# Whether this proposal lands on the default branch decides one thing only: the
# `Closes #n` line below. It is derived here rather than there because an
# explicit --base has to be compared against the same remote HEAD the default
# path reads, and a remote with no recorded HEAD leaves it empty - which reads
# as "not the default", the safe direction.
base_is_default=no
[[ -n ${default_base} && ${base} == "${default_base}" ]] && base_is_default=yes

# The one refusal that is a safety property rather than an argument check. A Run
# on the base branch would push its Iterations straight at the branch the
# proposal was supposed to protect, and there would be no proposal to open.
[[ ${branch} != "${base}" ]] ||
    die "the Run is on ${base}, which is the base branch - a Run works on its own branch"

[[ -x ${LOOP_PR_COMMAND} ]] ||
    die "pull-request command is not executable: ${LOOP_PR_COMMAND}"

# owner/name, from the remote. The pull-request command needs it and nothing
# else in a Run does, so it is derived here rather than passed in.
# `git config --get remote.<name>.url` rather than `git remote get-url`: the
# latter applies `url.<base>.insteadOf` rewrites, which are a transport concern
# - where the bytes go - and not an identity. The pull request has to be opened
# against the repository the remote DECLARES, and a box with a rewrite in place
# would otherwise have its proposal aimed at whatever the rewrite pointed to.
origin_url="$(git -C "${repo}" config --get "remote.${remote}.url")"
origin_url="${origin_url%.git}"
target_repo=""
case "${origin_url}" in
    *github.com[:/]*) target_repo="${origin_url#*github.com}"; target_repo="${target_repo#[:/]}" ;;
esac
[[ ${target_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "cannot read owner/name out of ${remote}: ${origin_url}"

plan="${repo}/${LOOP_PLAN_PATH}"

# --- What the proposal says -------------------------------------------------
#
# Read out of the Plan rather than passed in. The Plan is what the operator
# handed over and what the Run worked against, so it is the honest source for
# what this proposal is about - and it means run.sh does not have to carry the
# task's title through a Run in order to put it in a pull request at the end.

plan_field() {
    [[ -f ${plan} ]] || return 0
    awk -v heading="$1" '
        $0 == heading { capture = 1; next }
        capture && /^## / { exit }
        capture && $0 ~ /[^ \t]/ { print; exit }
    ' "${plan}"
}

# "**owner/name#648 - Audit every tblEmailMessage read and classify it**"
task_line="$(plan_field '## Task')"
task_line="${task_line#\*\*}"
task_line="${task_line%\*\*}"
[[ -n ${task_ref} ]] || task_ref="${task_line%% - *}"
task_title="${task_line#* - }"

area="$(plan_field '## The owning area this Run is scoped to')"
area="${area#\*\*}"
area="${area%\*\*}"

title="Loop: ${task_title:-a proposal from an unattended Run}"
[[ -n ${area} && ${area} != "${task_title}" ]] && title="${title} (${area})"

body_file="$(mktemp)"
trap 'rm -f -- "${body_file}"' EXIT INT TERM

# Assembled in an unquoted heredoc so the values above land in it, with the
# markdown's backticks escaped. printf with backticks in the format string is
# the same output and reads to shellcheck as an unexpanded command substitution.
{
    cat <<HEAD
A draft proposal from one unattended Run of the Loop. Nothing here is merged,
deployed, or applied; the Run pushed this branch and opened this pull request,
and that is the whole of what it did outside its own repository.

HEAD
    [[ -n ${task_ref} ]] && printf -- '- Task: %s\n' "${task_ref}"
    [[ -n ${area} ]] && printf -- '- Owning area this Run was scoped to: %s\n' "${area}"
    [[ -n ${ended_by} ]] && printf -- '- Ended by: %s\n' "${ended_by}"
    [[ -n ${run_exit} ]] && printf -- '- Run exit code: %s\n' "${run_exit}"
    cat <<BODY
- Branch: \`${branch}\`, proposed against \`${base}\`

## How to review this

Read \`${LOOP_PROGRESS_LOG_PATH}\` first. It is the narrative of the Run - what each
Iteration did, what it decided and why, and what it found blocked - and the diff
is easier to judge knowing how it was arrived at. \`${LOOP_PLAN_PATH}\` carries the
task, its acceptance criteria, and the mechanical check that grades the work.

The commits are attributed to the operator and signed with a key dedicated to
the Loop, which is what tells a Loop commit from a hand-authored one. A Verified
signature here asserts that the operator caused the commit, not that he wrote
it.
BODY
    # `Closes #n` so that merging the proposal retires the task from the queue
    # (spec #151, story 18). Last, and on a line of its own with a blank line
    # in front of it: put between the bullets above it would end the metadata
    # list and start a second one under it, which is what a reader sees even
    # though the keyword still works.
    #
    # Two conditions, both there for the same reason: a line that silently does
    # nothing is worse than none.
    #
    # The task must live in the repository the proposal is opened against.
    # GitHub's keyword closes a cross-repository reference too, but only for an
    # actor with write access on the OTHER repository.
    #
    # And the proposal must land on the default branch, which is the only merge
    # GitHub fires the keyword on. A Run proposed against a release branch with
    # --base would otherwise carry a line claiming to retire a task that stays
    # in the queue after the merge.
    if [[ -n ${task_ref} && ${task_ref} == "${target_repo}#"* && ${base_is_default} == yes ]]; then
        printf -- '\nCloses #%s\n' "${task_ref##*#}"
    fi
} >"${body_file}"

# --- Push -------------------------------------------------------------------
#
# No --force and no --force-with-lease. A Run that would have to overwrite what
# is already on the remote has found a conflict, and resolving one unattended is
# not something this script gets to do.

if ! push_output="$(git -C "${repo}" push --set-upstream "${remote}" "${branch}" 2>&1)"; then
    printf 'propose.sh: the push failed; nothing external happened.\n' >&2
    printf '%s\n' "${push_output}" >&2
    printf 'LOOP_PROPOSE_RESULT=push-failed\n'
    exit 2
fi

# --- Propose ----------------------------------------------------------------

if ! url="$("${LOOP_PR_COMMAND}" "${target_repo}" "${branch}" "${base}" "${title}" "${body_file}")"; then
    printf 'propose.sh: %s is pushed and the pull request was refused.\n' "${branch}" >&2
    printf 'LOOP_PROPOSE_RESULT=pull-request-failed\n'
    printf 'LOOP_PROPOSE_BRANCH=%s\n' "${branch}"
    exit 3
fi

printf 'LOOP_PROPOSE_RESULT=proposed\n'
printf 'LOOP_PROPOSE_URL=%s\n' "${url}"
printf 'LOOP_PROPOSE_BRANCH=%s\n' "${branch}"
printf 'LOOP_PROPOSE_BASE=%s\n' "${base}"
