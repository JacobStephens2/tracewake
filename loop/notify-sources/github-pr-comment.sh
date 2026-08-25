#!/usr/bin/env bash
#
# The notification surface: one comment on the proposal the Run opened.
#
#   github-pr-comment.sh <subject> <body-file> <pull-request-url>
#
# The default LOOP_NOTIFY_COMMAND. Prints where the notification landed on
# stdout and exits non-zero if it could not send one. Substitutable (ADR 0004)
# for the same reasons the pull-request surface is: a Run that should reach a
# phone instead is a different script here and no change to run.sh, and the
# offline suite drives the real run.sh through a scripted fake so it needs no
# token and no network.
#
# ## Why a pull request comment, and not something that arrives on a phone
#
# The Loop's box reaches `github.com` and `api.github.com` and nothing else, and
# holds a repository-scoped token for exactly those (#100, ADR 0009). Every
# other surface - mail, SMS, a chat webhook - is a host on the egress allowlist
# and a credential in an inventory whose whole value is being short. Commenting
# on the pull request the Run just opened costs neither, and lands the news
# where the thing to review already is. ADR 0013 has the reasoning, including
# the one account setting it depends on.
#
# ## The token gains nothing for this
#
# The comment goes to `POST /repos/{owner}/{repo}/issues/{number}/comments`,
# which is the endpoint for a pull request's conversation as well as an issue's
# - a pull request IS an issue to that half of the API. A fine-grained token
# reaches it with "Pull requests: write" when the number is a pull request, so
# the box's existing token posts this and still has no Issues permission at all
# (ADR 0010). That is also why the URL is validated as a pull request URL below
# rather than accepted as whatever it was handed: `/issues/` in, and the same
# request would be an issue comment the token is not supposed to be able to make.

set -euo pipefail

subject="${1:?usage: github-pr-comment.sh <subject> <body-file> <pull-request-url>}"
body_file="${2:?usage: github-pr-comment.sh <subject> <body-file> <pull-request-url>}"
pr_url="${3:?usage: github-pr-comment.sh <subject> <body-file> <pull-request-url>}"

die() {
    printf 'notify-sources/github-pr-comment.sh: %s\n' "$*" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || die "curl is required to comment"
command -v jq >/dev/null 2>&1 || die "jq is required to read GitHub's answer"

[[ -f ${body_file} ]] || die "no such body file: ${body_file}"

# Parsed rather than passed in as three arguments, because the URL is the one
# thing run.sh already has - it is what propose.sh printed - and deriving the
# rest here keeps the notification contract the same for a surface that needs no
# repository at all.
[[ ${pr_url} =~ ^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/([0-9]+)$ ]] ||
    die "not a GitHub pull request URL: ${pr_url}"
target_repo="${BASH_REMATCH[1]}"
number="${BASH_REMATCH[2]}"

# The same file the git credential helper and the pull-request surface read,
# named the same way, so the box has one token in one place rather than one per
# caller.
token_file="${LOOP_GITHUB_TOKEN_FILE:-${HOME}/.config/loop/github-token}"
[[ -s ${token_file} ]] ||
    die "no GitHub token at ${token_file} - the box cannot tell anybody the Run finished"
token="$(tr -d '\r\n' <"${token_file}")"

# The subject leads the body rather than being dropped: this surface has no
# subject line of its own, and a comment whose first line is the outcome is
# readable from a notification list without opening it.
comment_file="$(mktemp)"
trap 'rm -f -- "${comment_file}"' EXIT INT TERM
{
    printf '%s\n\n' "${subject}"
    cat -- "${body_file}"
} >"${comment_file}"

# Assembled by jq rather than interpolated into a JSON string, for the reason
# the pull request's body is: this is markdown carrying a Run's summary, and
# quotes, backslashes and newlines are ordinary in it.
payload="$(jq -n --rawfile body "${comment_file}" '{body: $body}')"

# `--fail-with-body` rather than `--fail`: GitHub's refusal carries its reason in
# the body, and a Run nobody watched has to be diagnosable from what it printed.
answer="$(curl --silent --show-error --fail-with-body \
    --request POST \
    --header "Authorization: Bearer ${token}" \
    --header "Accept: application/vnd.github+json" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    --header "User-Agent: eta-loop" \
    --max-time 30 \
    --data "${payload}" \
    "https://api.github.com/repos/${target_repo}/issues/${number}/comments")" ||
    die "GitHub refused the comment: ${answer:-no response}"

url="$(jq -r '.html_url // ""' <<<"${answer}")"
[[ -n ${url} ]] || die "GitHub accepted the comment and returned no URL: ${answer}"

printf '%s\n' "${url}"
