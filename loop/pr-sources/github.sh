#!/usr/bin/env bash
#
# The pull-request surface: one draft pull request, opened on GitHub.
#
#   github.sh <owner/repo> <head-branch> <base-branch> <title> <body-file>
#
# The default LOOP_PR_COMMAND. Prints the pull request's URL on stdout and exits
# non-zero if it could not open one. Substitutable (ADR 0004) for the same two
# reasons the task source is: a repository living somewhere other than GitHub is
# a different script here and no change to propose.sh, and the offline suite
# drives the real propose.sh through a scripted fake so it needs no token and no
# network.
#
# ## Draft is not a courtesy here
#
# Proposal-Only Output is the invariant of the whole spec (issue #73): the worst
# outcome of a fully rogue Run is a proposal nobody merges. `draft: true` is one
# of the three things holding that up, and it is the weakest of them - a draft
# can be marked ready by anyone who can see it. The other two are what actually
# enforce it: the box's fine-grained token holds Contents and Pull requests and
# nothing that can merge, and the Execution Boundary's egress reaches two hosts.
# So this flag is the part a reviewer sees, not the part that holds.
#
# ## Why curl rather than `gh`
#
# `gh` is not on the Loop's box and this is the only call that would justify it.
# A CLI that can do everything a GitHub token can do, installed on a box whose
# design property is holding as little as possible, buys one flag - and `gh`
# reads GH_TOKEN from the environment, which would put the token in every
# process listing on the box. curl plus a token read from a file mode 0600 at
# the moment it is used is the same shape as the git credential helper next to
# it, and for the same reason.
#
# ## Re-running it does not open a second pull request
#
# A Run that is re-run against the same branch must not leave two proposals
# behind. The existing open pull request for this head is looked up first and
# its URL returned; opening one is what happens when there is not one already.

set -euo pipefail

task_repo="${1:?usage: github.sh <owner/repo> <head> <base> <title> <body-file>}"
head_branch="${2:?usage: github.sh <owner/repo> <head> <base> <title> <body-file>}"
base_branch="${3:?usage: github.sh <owner/repo> <head> <base> <title> <body-file>}"
title="${4:?usage: github.sh <owner/repo> <head> <base> <title> <body-file>}"
body_file="${5:?usage: github.sh <owner/repo> <head> <base> <title> <body-file>}"

die() {
    printf 'pr-sources/github.sh: %s\n' "$*" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || die "curl is required to open a pull request"
command -v jq >/dev/null 2>&1 || die "jq is required to read GitHub's answer"

[[ -f ${body_file} ]] || die "no such body file: ${body_file}"

# The same file the git credential helper reads, named the same way, so the box
# has one token in one place rather than one per caller.
token_file="${LOOP_GITHUB_TOKEN_FILE:-${HOME}/.config/loop/github-token}"
[[ -s ${token_file} ]] ||
    die "no GitHub token at ${token_file} - the box can commit but cannot propose"
token="$(tr -d '\r\n' <"${token_file}")"

api="${LOOP_GITHUB_API:-https://api.github.com}"
owner="${task_repo%%/*}"

# `--fail-with-body` rather than `--fail`: a 422 from GitHub carries the reason
# in its body ("No commits between master and ...", "A pull request already
# exists"), and a Run nobody watched has to be diagnosable from what it printed.
github_api() {
    local method="$1" path="$2"
    shift 2
    curl --silent --show-error --fail-with-body \
        --request "${method}" \
        --header "Authorization: Bearer ${token}" \
        --header "Accept: application/vnd.github+json" \
        --header "X-GitHub-Api-Version: 2022-11-28" \
        --header "User-Agent: eta-loop" \
        "$@" \
        "${api}/${path#/}"
}

# --- An open pull request for this head already? ----------------------------

existing="$(github_api GET "repos/${task_repo}/pulls?state=open&head=${owner}:${head_branch}")" ||
    die "could not ask GitHub about existing pull requests for ${head_branch}: ${existing:-no response}"

existing_url="$(jq -r 'if type == "array" and length > 0 then .[0].html_url else "" end' <<<"${existing}")"
if [[ -n ${existing_url} ]]; then
    printf '%s\n' "${existing_url}"
    exit 0
fi

# --- Open one ---------------------------------------------------------------
#
# The body is passed as a file and assembled by jq rather than interpolated into
# a JSON string. The body is markdown holding a Progress Log excerpt and a task
# title, and both of those routinely contain quotes, backslashes and newlines.

payload="$(jq -n \
    --arg title "${title}" \
    --arg head "${head_branch}" \
    --arg base "${base_branch}" \
    --rawfile body "${body_file}" \
    '{title: $title, head: $head, base: $base, body: $body, draft: true}')"

created="$(github_api POST "repos/${task_repo}/pulls" --data "${payload}")" ||
    die "GitHub refused the pull request: ${created:-no response}"

url="$(jq -r '.html_url // ""' <<<"${created}")"
[[ -n ${url} ]] || die "GitHub accepted the pull request and returned no URL: ${created}"

printf '%s\n' "${url}"
