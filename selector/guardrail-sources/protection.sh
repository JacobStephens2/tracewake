#!/usr/bin/env bash
#
# The write protection standing over the paths that run unattended (#165).
#
#   protection.sh
#
# The default SELECTOR_GUARDRAIL_COMMAND. Prints `SELECTOR_GUARDRAIL_*=value`
# lines - the same key=value shape the box answers a Run in, so nothing here
# needs a second parser - and exits 0 when both halves could be read.
#
# Two halves, because either one alone can be satisfied while what runs is
# unreviewed:
#
#   the rules       what GitHub holds over the ref the executed paths are
#                   deployed from. Without a `pull_request` rule a push
#                   straight to that branch is accepted, and the unattended
#                   executor is easier to change than the repository a Run
#                   makes Proposals against - which is the whole of story 34.
#
#   the tree        whether the deployed copy of those paths still matches
#                   that ref. This checkout is shared and group-writable, and
#                   systemd execs what is sitting in it: a rule on a branch
#                   says nothing about the bytes about to run. Compared
#                   against `refs/remotes/<remote>/<ref>` as the tree already
#                   holds it - deliberately no fetch, because a status read
#                   should not write to somebody else's working tree, and a
#                   stale remote ref can only make this stricter, never
#                   laxer: the reviewed change it has not yet seen is not in
#                   the tree either.
#
# It grades nothing. Which rules are required, and what an unanswered half
# means, are decided in `cycle.py` (`guardrail_verdict`) where they are
# reviewed Python rather than shell - and an unprotected ref is reported as a
# fact (`RULES=` empty) rather than as a failure, because that is the one
# state the guardrail exists to catch.
#
# It holds no credential of its own, writes nothing, and reaches two things:
# `gh api` read-only, and `git` read-only against the local tree.
#
# Configuration, all environment:
#
#   SELECTOR_PROTECTED_REPO    Educational-Travel-Adventures/orchestration
#   SELECTOR_PROTECTED_REF     master        the ref the paths are deployed from
#   SELECTOR_PROTECTED_REMOTE  origin
#   SELECTOR_PROTECTED_TREE    the repository this script is in
#   SELECTOR_PROTECTED_PATHS   guardrail-sources/paths.txt
#
# Exit codes: 0 when both halves were read, non-zero when either could not be -
# which `cycle.py` journals as `guardrail.unreadable`, never as green.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

repo="${SELECTOR_PROTECTED_REPO:-Educational-Travel-Adventures/orchestration}"
ref="${SELECTOR_PROTECTED_REF:-master}"
remote="${SELECTOR_PROTECTED_REMOTE:-origin}"
# Two levels up: guardrail-sources -> selector -> the repository root.
# The deployed tree is the one this script is deployed in, which is what makes
# the comparison below about the code that is actually going to run.
tree="${SELECTOR_PROTECTED_TREE:-$(cd "${here}/../.." && pwd)}"
paths_file="${SELECTOR_PROTECTED_PATHS:-${here}/paths.txt}"

command -v gh >/dev/null 2>&1 || {
    printf 'protection.sh: gh is required to read the ref rules\n' >&2
    exit 1
}

mapfile -t paths < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "${paths_file}" \
    | grep -v '^$' || true)

# Read before anything is printed, so a forge that will not answer leaves no
# half-written answer on stdout for something downstream to read as fine.
rules="$(gh api "repos/${repo}/rules/branches/${ref}" --jq '.[].type' \
    | LC_ALL=C sort -u | paste -sd, -)"

base="refs/remotes/${remote}/${ref}"
ref_head="$(git -C "${tree}" rev-parse --short=12 "${base}")"

# Three reads, because unreviewed code reaches a working tree three ways.
#
#   diff HEAD              edits sitting in the tree and in no commit at all.
#                          This checkout is group-writable by three accounts
#                          and systemd execs what is on disk.
#   ls-files --others      files in the tree and in no commit either, which
#                          `git diff` cannot see. A script dropped in by hand
#                          would run. `--exclude-standard` keeps the venvs and
#                          caches the repository already ignores out of it.
#   diff base...HEAD       commits the tree is carrying that the protected
#                          ref's history does not have.
#
# The last one is THREE-dot on purpose. Two-dot would also report every path
# where the protected ref has moved on and this checkout has not pulled - and
# a shared working area is behind master most of the time. Stale is not
# unreviewed: what those commits hold was reviewed when it landed. A chip that
# went red for it would be red permanently, and a chip that is always red is
# one nobody reads.
unreviewed=""
if ((${#paths[@]} > 0)); then
    unreviewed="$(
        {
            git -C "${tree}" diff --name-only HEAD -- "${paths[@]}"
            git -C "${tree}" ls-files --others --exclude-standard -- "${paths[@]}"
            git -C "${tree}" diff --name-only "${base}...HEAD" -- "${paths[@]}"
        } | LC_ALL=C sort -u | paste -sd, -
    )"
fi

printf 'SELECTOR_GUARDRAIL_REF=%s\n' "${ref}"
printf 'SELECTOR_GUARDRAIL_REF_HEAD=%s\n' "${ref_head}"
printf 'SELECTOR_GUARDRAIL_RULES=%s\n' "${rules}"
printf 'SELECTOR_GUARDRAIL_PATHS=%s\n' "$(IFS=,; printf '%s' "${paths[*]}")"
printf 'SELECTOR_GUARDRAIL_UNREVIEWED=%s\n' "${unreviewed}"
