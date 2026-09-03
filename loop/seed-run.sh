#!/usr/bin/env bash
#
# Seed a Run from one chosen task.
#
#   seed-run.sh --repo <path> --task <number> --area <text>
#               [--task-repo <owner/name>] [--check <command>] [--reseed]
#
# A Run is seeded before it is started. This step fetches the ONE task the
# operator picked, writes it into the Plan with its acceptance criteria, and
# initializes the Progress Log ready for the first Iteration - so that starting
# a Run is one command, and re-running one is the same command again.
#
# THIS IS NOT ISSUE INTAKE, and the distinction is load-bearing rather than
# pedantic (ADR 0010). It is a human handing over a task he authored, and that
# authorship is what makes ADR 0003's content-trust collapse valid: the Execution
# Boundary is there to contain an unsupervised agent, not to defend against
# hostile input, and lessons.md's content-trust floor stays deleted only while
# nothing the Loop reads was written by someone else. A Loop that read arbitrary
# issues would be feeding an unattended agent text the operator never saw,
# straight into the prompt of a process that can commit and open a pull request.
# That is a different trust model and it needs its own decision.
#
# Two things keep it a setup step rather than a convention:
#
#   - This command runs as the OPERATOR, with the operator's own GitHub
#     identity, off the Loop's box. The box's fine-grained token holds Contents
#     and Pull requests and NOT Issues, so a Run cannot fetch a task even if
#     something inside it tried. The property is enforced by what the token can
#     reach, in the same way Proposal-Only Output is.
#   - What the agent will read is committed to the repository first, so the
#     operator can see the handover in a diff before the Run starts and a
#     reviewer can see it in the pull request afterwards.
#
# Exit codes:
#
#   0  seeded, re-seeded, or already seeded and unchanged.
#   1  could not run: bad arguments, a fetch that failed, or a task with no
#      acceptance criteria - a Plan without them cannot tell an Iteration
#      whether it is done, which is the whole reason the Plan carries them.
#   2  refused: the Progress Log already records a Run. Seeding resets it, and
#      discarding a Run's record is not something to do silently. --reseed says
#      to do it anyway.
#
# Reproducible: the Plan and the Progress Log are a pure function of the task,
# the owning area and the check command. Nothing here writes a timestamp, so
# seeding the same task twice produces the same two files byte for byte and the
# second seeding commits nothing. State does not accumulate: both files are
# written whole rather than appended to.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=contract.sh
source "${loop_dir}/contract.sh"

# Resolved here rather than in contract.sh because the default is a path
# relative to this checkout, and contract.sh is a declaration file that should
# not need to know where it was installed.
: "${LOOP_TASK_SOURCE_COMMAND:=${loop_dir}/task-sources/github.sh}"

die() {
    printf 'seed-run.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
seed-run.sh --repo <path> --task <number> --area <text>
            [--task-repo <owner/name>] [--check <command>] [--reseed]

Fetches one chosen task, writes it into the Plan with its acceptance criteria,
and initializes the Progress Log ready for the first Iteration.

  --repo       the repository a Run will work in. The Plan and the Progress Log
               are written into it and committed.
  --task       the task's number. One task, by number - see ADR 0010.
  --area       the one owning area of the task this Run is scoped to. Required:
               a Run is five Iterations, most tasks are larger than that, and
               deciding how much of one a Run is for is the operator's call and
               not an Iteration's.
  --task-repo  where the task lives, as owner/name. Defaults to the work
               repository's origin.
  --check      the mechanical completeness check for this task, recorded in the
               Plan as the Run's backpressure and the operator's acceptance.
  --reseed     discard a Progress Log that already records a Run.
USAGE
}

repo=""
task_number=""
task_repo=""
area=""
check_command=""
reseed=false

while (($# > 0)); do
    case "$1" in
        --repo) repo="${2:?--repo needs a path}"; shift 2 ;;
        --task) task_number="${2:?--task needs a number}"; shift 2 ;;
        --task-repo) task_repo="${2:?--task-repo needs owner/name}"; shift 2 ;;
        --area) area="${2:?--area needs a value}"; shift 2 ;;
        --check) check_command="${2:?--check needs a command}"; shift 2 ;;
        --reseed) reseed=true; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# --- Preflight -------------------------------------------------------------

[[ -n ${repo} ]] || die "--repo is required"
[[ -d ${repo} ]] || die "no such directory: ${repo}"
repo="$(cd -- "${repo}" && pwd)"

git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 ||
    die "not a git repository: ${repo}"
git -C "${repo}" rev-parse HEAD >/dev/null 2>&1 ||
    die "repository has no commits: ${repo}"
git -C "${repo}" config user.email >/dev/null ||
    die "no git identity in ${repo} - the seed commits the Plan itself"

[[ -n ${task_number} ]] || die "--task is required"
# Validated before it is passed anywhere, not because the task source is
# careless with it but because a number is the only thing the operator is
# choosing here and anything else is a mistake worth naming at second zero.
[[ ${task_number} =~ ^[1-9][0-9]*$ ]] ||
    die "--task must be a task number, got '${task_number}'"

# Free text, matched against nothing - not the task's body, not the check
# script's grouping. The areas live in a ticket's prose in whatever words its
# author used ("dashboards/reports" in issue 648, which is not how anyone types
# it), so a match would reject correct input more often than it caught a typo.
# What guards against the wrong area is that it is printed back, written into the
# Plan, and committed before a Run starts.
[[ -n ${area} ]] ||
    die "--area is required - a Run is scoped to one owning area of the task, not the whole of it"

if [[ -z ${task_repo} ]]; then
    origin="$(git -C "${repo}" remote get-url origin 2>/dev/null || true)"
    origin="${origin%.git}"
    case "${origin}" in
        *github.com[:/]*) task_repo="${origin#*github.com}"; task_repo="${task_repo#[:/]}" ;;
    esac
    [[ -n ${task_repo} ]] ||
        die "--task-repo is required - ${repo} has no GitHub origin to take it from"
fi
[[ ${task_repo} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "--task-repo must be owner/name, got '${task_repo}'"

[[ -x ${LOOP_TASK_SOURCE_COMMAND} ]] ||
    die "task source is not executable: ${LOOP_TASK_SOURCE_COMMAND}"

command -v jq >/dev/null 2>&1 || die "jq is required to read the task"

plan_target="${repo}/${LOOP_PLAN_PATH}"
progress_target="${repo}/${LOOP_PROGRESS_LOG_PATH}"
task_ref="${task_repo}#${task_number}"

# --- Refuse to discard a Run silently ---------------------------------------
#
# Seeding writes both files whole. That is what keeps it reproducible and stops
# state accumulating, and it is also why a Progress Log that already records a
# Run has to stop this: the Run's record is the only account of what happened,
# and losing it costs more than re-running the seed.

if [[ -f ${progress_target} ]] &&
    grep -q -- "^${LOOP_RUN_HEADING}" "${progress_target}"; then
    recorded_runs="$(grep -c -- "^${LOOP_RUN_HEADING}" "${progress_target}" || true)"
    recorded_iterations="$(grep -c -- "^${LOOP_ITERATION_HEADING} " "${progress_target}" || true)"
    if ! ${reseed}; then
        printf 'seed-run.sh: %s already records %s Run(s) and %s Iteration(s).\n' \
            "${LOOP_PROGRESS_LOG_PATH}" "${recorded_runs}" "${recorded_iterations}" >&2
        printf 'Seeding rewrites it. Pass --reseed to discard that record, or seed a fresh checkout.\n' >&2
        exit 2
    fi
    discarded_runs="${recorded_runs}"
    discarded_iterations="${recorded_iterations}"
fi

# --- Fetch the one task -----------------------------------------------------

task_json="$("${LOOP_TASK_SOURCE_COMMAND}" "${task_repo}" "${task_number}")" ||
    die "could not fetch ${task_ref}"

jq -e . >/dev/null 2>&1 <<<"${task_json}" ||
    die "the task source did not return a task: ${task_ref}"

# A JSON null becomes an empty string rather than the four characters "null".
# GitHub returns `"body": null` for an issue nobody wrote a body for, and `jq -r`
# prints that as the word - which would put "null" in the Plan and satisfy every
# emptiness check below. Absent, empty and null are the same thing here and are
# read as the same thing.
task_field() {
    jq -r --arg field "$1" '(.[$field] // "") | tostring' <<<"${task_json}"
}

fetched_number="$(task_field number)"
task_title="$(task_field title)"
task_url="$(task_field url)"
task_state="$(task_field state)"
task_body="$(task_field body)"

[[ ${fetched_number} == "${task_number}" ]] ||
    die "asked for ${task_ref} and got #${fetched_number}"
[[ -n ${task_title} ]] || die "the task has no title: ${task_ref}"
[[ -n ${task_body} ]] || die "the task has no body: ${task_ref}"

# --- Read the task ----------------------------------------------------------
#
# Two passes over the same body: one lifts the acceptance criteria out, the
# other keeps everything else. Fenced blocks are tracked in both, because a
# ticket quoting another ticket's criteria in a code block is quoting them, and
# a shell comment in an example is not a heading.

task_sections() {
    awk -v mode="$1" '
    function heading_level(s,   n) {
        if (s !~ /^#{1,6}([ \t]|$)/) return 0
        n = 0
        while (substr(s, n + 1, 1) == "#") n++
        return n
    }
    BEGIN { fenced = 0; fence_char = ""; fence_len = 0; in_criteria = 0; criteria_level = 0 }
    {
        line = $0
        is_fence = 0
        if (match(line, /^ {0,3}(```+|~~~+)/)) {
            token = substr(line, RSTART, RLENGTH)
            sub(/^ +/, "", token)
            if (!fenced) {
                fenced = 1; fence_char = substr(token, 1, 1); fence_len = length(token); is_fence = 1
            } else if (substr(token, 1, 1) == fence_char && length(token) >= fence_len) {
                fenced = 0; is_fence = 1
            }
        }
        level = (fenced || is_fence) ? 0 : heading_level(line)
        if (level > 0) {
            text = line
            sub(/^#+[ \t]*/, "", text)
            if (tolower(text) ~ /^acceptance criteria/) {
                in_criteria = 1; criteria_level = level
            } else if (in_criteria && level <= criteria_level) {
                in_criteria = 0
            }
        }
        if (mode == "criteria") {
            if (in_criteria && level == 0) print line
            next
        }
        if (in_criteria) next
        # Demoted so the task nests under the Plan'"'"'s own headings instead of
        # reading as one of them. Six is as deep as markdown goes, so a heading
        # already there is left alone rather than turned into text.
        if (level > 0 && level < 6) print "#" line
        else print line
    }'
}

trim_blank_lines() {
    awk '
    { lines[NR] = $0; if ($0 ~ /[^ \t]/) { if (!first) first = NR; last = NR } }
    END { if (first > 0) for (i = first; i <= last; i++) print lines[i] }'
}

criteria="$(printf '%s\n' "${task_body}" | task_sections criteria | trim_blank_lines)"
task_rest="$(printf '%s\n' "${task_body}" | task_sections rest | trim_blank_lines)"

# A criterion is a list item. The count is reported so that "the Plan carries the
# acceptance criteria" is something the operator can see rather than assume, and
# a heading with nothing under it is refused for the same reason a completeness
# check refuses a denominator of zero: an empty result and a broken read must
# not produce the same output.
# Only the outermost level of the list. A criterion with sub-bullets under it is
# one criterion, and counting its children as criteria of their own would make
# the reported number larger than the number of things that have to be true - the
# opposite of the reason it is reported at all.
criteria_count="$(printf '%s\n' "${criteria}" | awk '
    /^[ \t]*([-*+]|[0-9]+[.)])[ \t]/ {
        match($0, /^[ \t]*/)
        items[NR] = RLENGTH
        if (shallowest == "" || RLENGTH < shallowest) shallowest = RLENGTH
    }
    END {
        for (n in items) if (items[n] == shallowest) count++
        print count + 0
    }')"

if [[ -z ${criteria} ]] || ((criteria_count == 0)); then
    printf 'seed-run.sh: %s has no acceptance criteria this step could read.\n' "${task_ref}" >&2
    printf 'A Plan without them cannot tell an Iteration whether it is done, and an\n' >&2
    printf 'unattended Run has nobody to ask. Give the task an "## Acceptance criteria"\n' >&2
    printf 'section with one list item per criterion, then seed it.\n' >&2
    exit 1
fi

# --- Render -----------------------------------------------------------------
#
# Both files are written whole and hold no timestamp, which is what makes
# re-running this reproducible. Anything that varies between two seedings of the
# same task would turn "re-run the setup step" into "accumulate another commit".

render_plan() {
    cat <<PLAN
# Plan

<!--
Generated by seed-run.sh from ${task_ref}, scoped to one owning area.
Seeding the same task and the same area again reproduces this file byte for
byte. Everything above "## Remaining work" is the task as its author wrote it
and the scope the operator chose; Iterations edit "## Remaining work" and
nothing above it.
-->

## Task

**${task_ref} - ${task_title}**

${task_url}

State when this Run was seeded: ${task_state}

## The owning area this Run is scoped to

**${area}**

One Run resolves one task under a Termination Contract of a handful of
Iterations, and this task is larger than that. So this Run is scoped to the
owning area named here. Everything else in the task is out of scope for this
Run: it is not remaining work, and an Iteration that starts on it has left the
Plan.

## Acceptance criteria

Verbatim from the task, and the only definition of done. They are the task
author's words - do not edit them, and read each one as applying to the owning
area above.

${criteria}

## How to tell whether the work has landed

$(render_check)

## The task, as written

Headings demoted one level so they nest here; nothing else altered. The
acceptance criteria are not repeated - they are above.

Read this as the request, not as fact about the codebase today. A count written
into a ticket was true when it was written; the check above derives its own
denominator from the checkout on every run rather than trusting one (ADR 0008).

${task_rest}

## Remaining work

Iterations edit this section and nothing above it. Keep it true: a fresh
Iteration has no memory of the last one, and this is where it learns where the
work stopped.

- [ ] Nothing recorded yet. The first Iteration breaks the owning area above
      into steps here, then does one of them.
PLAN
}

render_check() {
    if [[ -n ${check_command} ]]; then
        cat <<CHECK
Run this from the repository root. Exit 0 means the work is accounted for;
non-zero names what is not. Run it before recording a criterion above as met -
it is the Run's backpressure while the Run is happening and the operator's
acceptance afterwards, and it derives its own denominator rather than trusting a
count written in the task.

\`\`\`
${check_command}
\`\`\`
CHECK
    else
        cat <<'CHECK'
**This Run has no mechanical check.** Nothing here can say the work did not
land, so the acceptance criteria above are the only grade and a human applies
them by reading the proposal. That is a weaker position than a Run with a check,
and it is said here rather than left as a blank section, because a check that is
missing and a check that passed must not look the same.
CHECK
    fi
}

render_progress_log() {
    cat <<LOG
# Progress Log

Task: ${task_ref} - ${task_title}
Owning area: ${area}

See ${LOOP_PLAN_PATH} for the task, its acceptance criteria, and what remains.

This log is the Run's memory. Every Iteration is a fresh process with no
recollection of the one before it, so what is not written here did not happen.
Record decisions and blockers and not only completed tasks: a later Iteration
reads this instead of relitigating a settled choice or repeating exploration
that has already been done.

Seeded by seed-run.sh. No Iteration has run yet.
LOG
}

new_plan="$(render_plan)"
new_progress_log="$(render_progress_log)"

result="seeded"
if [[ -n ${discarded_runs:-} ]]; then
    result="reseeded"
elif [[ -f ${plan_target} && -f ${progress_target} ]] &&
    [[ "$(cat -- "${plan_target}")" == "${new_plan}" ]] &&
    [[ "$(cat -- "${progress_target}")" == "${new_progress_log}" ]]; then
    result="unchanged"
fi

if [[ ${result} != "unchanged" ]]; then
    mkdir -p -- "$(dirname -- "${plan_target}")" "$(dirname -- "${progress_target}")"
    printf '%s\n' "${new_plan}" >"${plan_target}"
    printf '%s\n' "${new_progress_log}" >"${progress_target}"
fi

# Staged by path, like the Loop's own bookkeeping commits: an operator who was
# in the middle of something else has not asked for it to be committed here.
git -C "${repo}" add -- "${LOOP_PLAN_PATH}" "${LOOP_PROGRESS_LOG_PATH}"
if ! git -C "${repo}" diff --cached --quiet; then
    verb="Seed"
    [[ ${result} == "reseeded" ]] && verb="Re-seed"
    git -C "${repo}" commit --quiet \
        --message "Loop: ${verb} the Run from ${task_ref} (${area})"
fi

# --- Report -----------------------------------------------------------------

printf 'LOOP_SEED_RESULT=%s\n' "${result}"
printf 'LOOP_SEED_TASK=%s\n' "${task_ref}"
printf 'LOOP_SEED_TASK_STATE=%s\n' "${task_state}"
printf 'LOOP_SEED_AREA=%s\n' "${area}"
printf 'LOOP_SEED_CRITERIA=%d\n' "${criteria_count}"
printf 'LOOP_SEED_PLAN=%s\n' "${LOOP_PLAN_PATH}"
printf 'LOOP_SEED_PROGRESS=%s\n' "${LOOP_PROGRESS_LOG_PATH}"

printf '\n%s#%s - %s\n' "${task_repo}" "${task_number}" "${task_title}"
printf '  owning area   %s\n' "${area}"
printf '  criteria      %d carried into %s\n' "${criteria_count}" "${LOOP_PLAN_PATH}"
if [[ -n ${check_command} ]]; then
    printf '  check         %s\n' "${check_command}"
else
    printf '  check         none - the acceptance criteria are the only grade\n'
fi
case "${result}" in
    seeded) printf '  result        seeded, and committed\n' ;;
    reseeded)
        printf '  result        re-seeded, discarding %s Run(s) and %s Iteration(s)\n' \
            "${discarded_runs:-0}" "${discarded_iterations:-0}"
        ;;
    unchanged) printf '  result        already seeded from this task and area; nothing changed\n' ;;
esac

if [[ ${task_state} != "OPEN" ]]; then
    printf '\nThe task is %s. Seeded anyway - it is the operator'"'"'s choice - but a\n' "${task_state}"
    printf 'closed task is usually the wrong number.\n'
fi
