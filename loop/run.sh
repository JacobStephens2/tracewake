#!/usr/bin/env bash
#
# The Loop: one Run.
#
#   run.sh --repo <path> [--task-ref <text>]
#
# A Run is an ordered sequence of Iterations executing under one Termination
# Contract. Each Iteration is a *fresh agent process* that reads the Plan and
# the Progress Log, does one task, commits, and exits. The forgetting at that
# process boundary is the technique, not an implementation detail: this is a
# shell loop rather than a stop hook inside one agent session precisely because
# a single session accumulates context and defeats the point (spec issue #73).
#
# Two of the Contract's five bounds end an ITERATION rather than a Run - the
# Iteration wall clock and the turn bound inside it. Both are recorded as faults,
# so a Run that reached its cap having hit one does not exit 0, and neither is a
# reason to stop: the next Iteration reads in the Progress Log that the last one
# was cut off and what it left behind.
#
# What ends a Run is declared in contract.sh before the Run starts, written into
# the Progress Log at Run start, and named on stdout at the end:
#
#   LOOP_RUN_ENDED_BY=<bound>   iteration-cap | run-clock | consecutive-noops | agent-failed
#
# Exit codes - the Run is honest about whether it worked:
#
#   0  iteration-cap, no fault: the Run executed its planned Iterations and
#      every one of them ran to its own end. This is the only clean exit.
#   1  preflight failed - the Run never started.
#   2  run-clock: the Run's wall clock ended it.
#   3  consecutive-noops: the head stopped moving.
#   4  agent-failed: an agent process exited non-zero on its own.
#   5  iteration-cap, but at least one Iteration was cut off by a bound of its
#      own - its wall clock, or the turn bound inside it.
#   6  the Run reached a planned end and the proposal failed. The bound that
#      ended the Run is still reported; what failed is the Run's only external
#      effect, and a Run whose proposal is missing has produced nothing the
#      operator can review.
#
# The one place this departs from a literal reading of spec issue #73 ("exits
# non-zero whenever any bound fired") is code 0. Every Run ends on a bound,
# because a Completion Promise is never terminal - so a rule that made the cap
# non-zero too would make the exit code carry no information at all, which
# defeats the reason story 14 wants it. Reaching the cap with nothing killed and
# nothing failed is the Run's planned end, and it is the one thing exit 0 means.
# Which bound ended it is still reported, always, for every code including 0.
#
# The Loop does not seed the Plan: `seed-run.sh` does that before a Run starts,
# because putting the task into the repository is a setup step and keeping it one
# is what makes ADR 0003's content-trust reasoning valid (ADR 0010). This script
# needs a repository that already has a Plan in it.
#
# With --propose, the Run's last act is to push its branch and open a draft pull
# request (propose.sh). Without it, a Run's only effect is commits on the branch
# that is checked out - which is what the offline suite drives, and why the flag
# is opt-in rather than a default with a way to turn it off.
#
# Before either, the Run removes its own scaffolding - the Plan, the Progress
# Log, and any kept-earlier log - in a Run-authored commit, so the branch tip is
# merge-clean and nothing mergeable carries them (spec issue #78). Reviewers
# read them from the branch's history instead; the proposal is pushed after.
#
# What the first Iteration was told, emitted on stdout at the end (#80): the
# rendered briefing between LOOP_BRIEFING_BEGIN and LOOP_BRIEFING_END. Later
# Iterations differ only in number, so this one rendering is the stored fact
# the Selector journals. It carries no credentials - file paths, the checklist
# naming the discipline skills, and the completion promise - which is what lets
# the window show it to both roles with no redaction.
#
# With --notify, the Run then tells the operator it has finished (#110), because
# the premise of the whole Contract is that he walked away and a Run he has to
# find is a Run he had to poll for. It reports separately from the Run:
#
#   LOOP_RUN_NOTIFIED=<state>   sent | failed | no-surface | skipped
#
# Nothing about it can change what the Run did. A notification that failed does
# not move the exit code and is not written into the Progress Log - the Run is
# the thing that happened, and telling somebody about it is not - which is also
# why it is the last act of all, after the record has been committed, the
# scaffolding removed, and the proposal pushed.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=contract.sh
source "${loop_dir}/contract.sh"

# Resolved here rather than in contract.sh because the default is a path
# relative to this checkout, and contract.sh is a declaration file that should
# not need to know where it was installed.
: "${LOOP_AGENT_COMMAND:=${loop_dir}/agents/claude.sh}"
: "${LOOP_PROPOSE_COMMAND:=${loop_dir}/propose.sh}"
: "${LOOP_NOTIFY_COMMAND:=${loop_dir}/notify-sources/github-pr-comment.sh}"

die() {
    printf 'run.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
run.sh --repo <path> [--task-ref <text>] [--propose] [--notify]

Executes one Run: a sequence of fresh agent processes under the Termination
Contract declared in contract.sh. Reports the bound that ended the Run on
stdout as LOOP_RUN_ENDED_BY, and exits 0 only when the Run reached its
iteration cap with nothing killed and nothing failed. The header comment in
this file documents every exit code.

  --propose  push the Run's branch and open a draft pull request when the Run
             ends. The Run's only external effect.
  --notify   tell the operator the Run has finished, through the proposal it
             opened. Needs --propose. Per Run, so a Run in a terminal somebody
             is watching sends nothing.
USAGE
}

repo=""
task_ref=""
propose=false
notify=false
while (($# > 0)); do
    case "$1" in
        --repo) repo="${2:?--repo needs a path}"; shift 2 ;;
        --task-ref) task_ref="${2:?--task-ref needs a value}"; shift 2 ;;
        --propose) propose=true; shift ;;
        --notify) notify=true; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# --- Preflight -------------------------------------------------------------
#
# Everything checked here is a condition under which the Run would fail late,
# unattended, after spending model time. Failing at second zero costs nothing.

[[ -n ${repo} ]] || die "--repo is required"
[[ -d ${repo} ]] || die "no such directory: ${repo}"
repo="$(cd -- "${repo}" && pwd)"

git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: ${repo}"
git -C "${repo}" rev-parse HEAD >/dev/null 2>&1 ||
    die "repository has no commits: ${repo}"

[[ -f "${repo}/${LOOP_PLAN_PATH}" ]] ||
    die "no Plan at ${LOOP_PLAN_PATH} - a Run is seeded before it is started: see seed-run.sh"

[[ -x ${LOOP_AGENT_COMMAND} ]] ||
    die "agent command is not executable: ${LOOP_AGENT_COMMAND}"

git -C "${repo}" config user.email >/dev/null ||
    die "no git identity in ${repo} - the Loop commits the Progress Log itself"

# A Run works on its own branch. Checked at second zero rather than at the
# proposal, because by the proposal the Iterations have already committed: a Run
# started on the base branch would have put an unattended agent's commits on the
# branch the proposal exists to keep them off, and there would be nothing left
# to propose. Skipped where there is no remote, which is every fixture the
# offline suite builds.
run_base=""
if run_base="$(loop_base_branch "${repo}" origin)"; then
    run_branch="$(git -C "${repo}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    [[ ${run_branch} != "${run_base}" ]] ||
        die "the Run is on ${run_base}, origin's default branch - a Run works on its own branch so that its Iterations are a proposal rather than a change"
fi

if ${propose}; then
    [[ -x ${LOOP_PROPOSE_COMMAND} ]] ||
        die "propose command is not executable: ${LOOP_PROPOSE_COMMAND}"
fi

# The notification is a comment on the proposal, so a Run that opens none has
# nowhere to send one. Refused here rather than discovered at the end, where the
# operator would have spent a Run to learn that nothing was going to tell him
# about it - which is the exact failure --notify exists to remove.
if ${notify}; then
    ${propose} ||
        die "--notify needs --propose - the notification is a comment on the proposal, and a Run that opens none has nowhere to send one"
    [[ -x ${LOOP_NOTIFY_COMMAND} ]] ||
        die "notify command is not executable: ${LOOP_NOTIFY_COMMAND}"
    [[ ${LOOP_NOTIFY_TIMEOUT_SECONDS} =~ ^[1-9][0-9]*$ ]] ||
        die "LOOP_NOTIFY_TIMEOUT_SECONDS must be a positive integer, got '${LOOP_NOTIFY_TIMEOUT_SECONDS}'"
fi

# The metered-key collision, at second zero and for whichever agent this Run is
# using. Spec issue #73 story 32 asks for it to fail the Run loudly rather than
# switch billing quietly, and the Termination Contract is the entire cost
# control - there is no per-Run spend ceiling to catch it afterwards.
#
# The Loop does not know which names those are and must not: which environment
# variables supersede a subscription is vendor knowledge, which ADR 0004 puts in
# the adapter. So it asks. Every adapter answers `--metered-env-names`, and an
# adapter that answers nothing is a Run that refuses to start rather than one
# that quietly stopped checking - the same rule assert-credentials.sh applies to
# the same list, for the same reason.
#
# The adapter checks again at its own first act, and that is not redundant: this
# preflight covers the Run's environment, and the adapter covers a box whose
# environment changed between the two. Neither costs a token of model time.
metered_names="$("${LOOP_AGENT_COMMAND}" --metered-env-names 2>/dev/null || true)"
[[ -n ${metered_names} ]] ||
    die "${LOOP_AGENT_COMMAND} named no metered key environment variables - a Run must not start without knowing what would supersede its subscription"

metered_set=()
while IFS= read -r metered_name; do
    [[ -n ${metered_name} ]] || continue
    [[ -n ${!metered_name:-} ]] && metered_set+=("${metered_name}")
done <<<"${metered_names}"
((${#metered_set[@]} == 0)) ||
    die "$(printf '%s ' "${metered_set[@]}")is set in this Run's environment; it would supersede the subscription and move billing to a metered key. Unset it."

for bound in LOOP_MAX_ITERATIONS LOOP_ITERATION_TIMEOUT_SECONDS LOOP_MAX_TURNS \
    LOOP_RUN_TIMEOUT_SECONDS LOOP_MAX_CONSECUTIVE_NOOPS; do
    [[ ${!bound} =~ ^[1-9][0-9]*$ ]] ||
        die "${bound} must be a positive integer, got '${!bound}'"
done

progress_log="${repo}/${LOOP_PROGRESS_LOG_PATH}"

# The prompt and the agent's output are scratch files per Iteration. A Run is
# started by a wrapper or a cron entry, so it can be signalled; without this the
# two files from the Iteration in flight are left behind every time.
prompt_file=""
output_file=""
notify_body=""
cleanup_scratch() { rm -f -- "${prompt_file}" "${output_file}" "${notify_body}"; }
trap cleanup_scratch EXIT INT TERM

now() { date +%s; }
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# What the Iterations did, in one place. The Progress Log's Run-end block and
# the notification are two readers of the same fact, and a second copy of this
# line is two records of one Run that can come to disagree about it.
iteration_counts_line() {
    printf -- '- Iterations: %d (committed %d, no-op %d, killed %d, turn bound %d)\n' \
        "${iterations_run}" "${committed_count}" "${noop_count}" "${killed_count}" \
        "${turn_bound_count}"
}

# Commits the Run's own bookkeeping and NOTHING else. The Loop stages the Plan
# and the Progress Log by path: an agent that left unrelated changes dirty in
# the working tree has done something worth seeing in review, and sweeping them
# into a commit the Loop authored would hide it.
commit_bookkeeping() {
    local message="$1"
    git -C "${repo}" add -A -- "${LOOP_PROGRESS_LOG_PATH}" "${LOOP_PLAN_PATH}"
    if git -C "${repo}" diff --cached --quiet; then
        return 0
    fi
    git -C "${repo}" commit --quiet --message "${message}"
}

# --- Run start -------------------------------------------------------------

run_started="$(now)"

{
    printf '\n%s %s\n\n' "${LOOP_RUN_HEADING}" "$(stamp)"
    if [[ -n ${task_ref} ]]; then
        printf 'Task: %s\n\n' "${task_ref}"
    fi
    printf 'Termination Contract:\n\n'
    loop_contract_summary
    printf '\n'
} >>"${progress_log}"

commit_bookkeeping "Loop: Run started"

# --- The Loop --------------------------------------------------------------

ended_by=""
faults=()
noop_streak=0
iterations_run=0
committed_count=0
noop_count=0
killed_count=0
turn_bound_count=0
promise_count=0

# The whole of what an Iteration is told. A fresh process each time, so this
# file is the entire briefing: the Plan for the task, the Progress Log for what
# is already known, and - since a Run occupies the slot a person would have
# invoked `/implement` from - that checklist too, naming the discipline skills
# the agent may invoke for itself (#162). The names come from the Contract
# rather than being spelled out here, because the Progress Log records them as
# well and a prompt that named a different discipline from the record of the
# Run would be wrong in the one place nobody was watching.
prompt_for_iteration() {
    local n="$1"
    cat <<PROMPT
You are Iteration ${n} of at most ${LOOP_MAX_ITERATIONS} in an unattended Run.
You have no memory of earlier Iterations. Everything you know is on disk.

1. Read ${LOOP_PLAN_PATH} for the task and what remains of it.
2. Read ${LOOP_PROGRESS_LOG_PATH} for what earlier Iterations already did,
   decided, and found blocked. Do not relitigate a decision recorded there and
   do not repeat exploration it already reports.
3. Do exactly ONE task from the Plan. One, so that a bad Iteration is small,
   reviewable, and revertible on its own.
4. Work it in the discipline the repository's own skills define, invoking them
   by name for yourself: ${LOOP_DISCIPLINE_SKILLS}. Run the type check and the
   tests for what you touched as you go, and the whole suite once before you
   commit.
5. Update ${LOOP_PLAN_PATH} to reflect what is now done and what remains.
6. Append to ${LOOP_PROGRESS_LOG_PATH} under a heading for this Iteration: what
   you did, what you DECIDED and why, and anything BLOCKED. A later Iteration
   reads this instead of rediscovering it. A log of completed tasks alone is
   not enough.
7. Commit everything you changed, with a message naming the task.
8. Exit.

Do not push, do not open a pull request, and do not merge - the Run's only
external effect is a proposal a human reviews.

If and only if the Plan has no remaining work, append this exact line to
${LOOP_PROGRESS_LOG_PATH} before committing:

${LOOP_COMPLETION_PROMISE}

That line is recorded as evidence. It does not end the Run, so do not emit it to
finish early.
PROMPT
}

# The briefing Iteration 1 will receive, rendered once at Run start. Rendered
# here rather than at the end so that what is emitted is what the Run started
# with, even if the scripts changed under it; emitted at the end so that the
# LOOP_RUN_* block stays the first thing on stdout. Later Iterations differ
# only in number, so one rendering is the whole fact.
run_briefing="$(prompt_for_iteration 1)"

for ((iteration = 1; iteration <= LOOP_MAX_ITERATIONS; iteration++)); do
    elapsed=$(($(now) - run_started))
    remaining=$((LOOP_RUN_TIMEOUT_SECONDS - elapsed))
    if ((remaining <= 0)); then
        ended_by="run-clock"
        break
    fi

    # The Iteration's wall clock is clamped to what is left of the Run's, so an
    # Iteration cannot carry the Run past its own cap. Without the clamp a Run
    # can overshoot by a whole Iteration timeout, which for the shipped values
    # is fifteen unattended minutes.
    iteration_timeout=${LOOP_ITERATION_TIMEOUT_SECONDS}
    if ((remaining < iteration_timeout)); then
        iteration_timeout=${remaining}
    fi

    prompt_file="$(mktemp)"
    output_file="$(mktemp)"
    prompt_for_iteration "${iteration}" >"${prompt_file}"

    head_before="$(git -C "${repo}" rev-parse HEAD)"
    log_lines_before="$(wc -l <"${progress_log}")"
    iteration_started="$(stamp)"
    iteration_started_at="$(now)"

    # The fresh process is the mechanism. `timeout` owns the Iteration's wall
    # clock; the turn bound is the agent's own, passed as its second argument.
    agent_rc=0
    (
        cd "${repo}" &&
            timeout --kill-after=10s "${iteration_timeout}s" \
                "${LOOP_AGENT_COMMAND}" "${prompt_file}" "${LOOP_MAX_TURNS}"
    ) >"${output_file}" 2>&1 || agent_rc=$?

    head_after="$(git -C "${repo}" rev-parse HEAD)"
    iterations_run=$((iterations_run + 1))

    # 124 and 137 are what `timeout` exits with, but they are also exit codes an
    # agent may choose for itself. Reaching the wall clock is what makes it a
    # kill, so the elapsed time has to agree before the Run reports one.
    killed=false
    if ((agent_rc == 124 || agent_rc == 137)) &&
        (($(now) - iteration_started_at >= iteration_timeout)); then
        killed=true
    fi

    # The turn bound firing is not the agent failing, and the first Run proved
    # the difference is the whole Run: read as a failure it ended everything at
    # Iteration 1, with the Iteration's work sitting uncommitted in the working
    # tree. It is one of the Contract's five bounds and it ends an ITERATION,
    # exactly as the Iteration wall clock does.
    #
    # Which vendor message means it is the agent adapter's to know (ADR 0004);
    # what arrives here is one exit status the Contract declares.
    turn_bound=false
    if ((agent_rc == LOOP_AGENT_TURN_BOUND_EXIT)); then
        turn_bound=true
    fi

    noop=false
    if [[ ${head_before} == "${head_after}" ]]; then
        noop=true
    fi

    # A Promise is looked for in what THIS Iteration produced: the agent's own
    # output, and the lines it appended to the Progress Log. Grepping the whole
    # log would report a Promise from Iteration 1 as recorded again in every
    # Iteration after it, which would make the count a lie.
    promise=false
    if grep -qF -- "${LOOP_COMPLETION_PROMISE}" "${output_file}" ||
        tail -n "+$((log_lines_before + 1))" "${progress_log}" 2>/dev/null |
            grep -qF -- "${LOOP_COMPLETION_PROMISE}"; then
        promise=true
    fi

    dirty=false
    if [[ -n "$(git -C "${repo}" status --porcelain)" ]]; then
        dirty=true
    fi

    # Which bound cut the Iteration off, if either did, in the words the Progress
    # Log uses. Built here rather than inline in the printf below: two `&&`
    # chains inside one command substitution read as a trick, and this file is
    # the one an operator reads to work out what a Run did.
    exit_note=""
    if ${killed}; then
        exit_note="$(printf ' (killed at its %ss wall clock)' "${iteration_timeout}")"
    elif ${turn_bound}; then
        exit_note="$(printf ' (turn bound of %s reached)' "${LOOP_MAX_TURNS}")"
    fi

    # The Loop's own record of the Iteration, appended AFTER the head comparison
    # so that the bookkeeping commit below cannot make a No-op Iteration look
    # like progress. Whatever the agent wrote about its decisions and blockers is
    # already above this, written by the agent itself.
    {
        printf '\n%s %d - %s\n\n' "${LOOP_ITERATION_HEADING}" "${iteration}" "${iteration_started}"
        printf -- '- Agent exit: %d%s\n' "${agent_rc}" "${exit_note}"
        printf -- '- Turn bound: %s\n' "${LOOP_MAX_TURNS}"
        if ${noop}; then
            printf -- '- No-op Iteration: head unchanged at %s\n' "${head_before:0:12}"
        else
            printf -- '- Head: %s -> %s\n' "${head_before:0:12}" "${head_after:0:12}"
        fi
        printf -- '- Completion Promise: %s\n' \
            "$(${promise} && printf 'recorded (advisory - the Run continues)' || printf 'not recorded')"
        if ${dirty}; then
            printf -- '- Uncommitted changes left in the working tree\n'
        fi
        # Only on a fault, and only the tail. A Run nobody watched is diagnosed
        # from this file, and "the agent exited 3" with nothing behind it is not
        # a diagnosis - but pasting a healthy Iteration's whole transcript in
        # here would bury the narrative the Progress Log exists to be.
        if ${killed} || ${turn_bound} || ((agent_rc != 0)); then
            printf '\nAgent output, last %d lines:\n\n' "${LOOP_FAULT_OUTPUT_LINES}"
            sed 's/^/    /' <(tail -n "${LOOP_FAULT_OUTPUT_LINES}" "${output_file}")
        fi
        printf '\n'
    } >>"${progress_log}"

    commit_bookkeeping "Loop: Iteration ${iteration} record"
    cleanup_scratch
    prompt_file=""
    output_file=""

    if ${promise}; then
        promise_count=$((promise_count + 1))
    fi

    if ${killed}; then
        killed_count=$((killed_count + 1))
        faults+=("iteration-timeout")
    elif ${turn_bound}; then
        # A fault, so a Run that reached its cap having hit it does not exit 0 -
        # the same treatment the Iteration wall clock gets, and for the same
        # reason: an Iteration that was cut off did not finish what it started.
        # NOT an ending bound: the Run continues, and the next Iteration reads
        # in the Progress Log that this one was cut off and what it left behind.
        turn_bound_count=$((turn_bound_count + 1))
        faults+=("turn-bound")
    elif ((agent_rc != 0)); then
        faults+=("agent-failed")
        ended_by="agent-failed"
    fi

    if ${noop}; then
        noop_count=$((noop_count + 1))
        noop_streak=$((noop_streak + 1))
    else
        committed_count=$((committed_count + 1))
        noop_streak=0
    fi

    if [[ -z ${ended_by} ]] && ((noop_streak >= LOOP_MAX_CONSECUTIVE_NOOPS)); then
        ended_by="consecutive-noops"
    fi

    if [[ -n ${ended_by} ]]; then
        break
    fi
done

[[ -n ${ended_by} ]] || ended_by="iteration-cap"

# --- Run end ---------------------------------------------------------------

case "${ended_by}" in
    iteration-cap) exit_code=$((${#faults[@]} > 0 ? 5 : 0)) ;;
    run-clock) exit_code=2 ;;
    consecutive-noops) exit_code=3 ;;
    agent-failed) exit_code=4 ;;
    *) exit_code=1 ;;
esac

fault_summary="none"
if ((${#faults[@]} > 0)); then
    fault_summary="$(printf '%s ' "${faults[@]}")"
    fault_summary="${fault_summary% }"
fi

{
    printf '\n### Run ended %s\n\n' "$(stamp)"
    printf -- '- Ended by: %s\n' "${ended_by}"
    iteration_counts_line
    printf -- '- Completion Promises recorded: %d\n' "${promise_count}"
    printf -- '- Faults: %s\n' "${fault_summary}"
    printf -- '- Exit code: %d\n' "${exit_code}"
    if ${propose}; then
        printf -- '- Proposal: pushing this branch and opening a draft pull request\n'
    else
        printf -- '- Proposal: none - this Run was not started with --propose\n'
    fi
    printf '\n'
} >>"${progress_log}"

commit_bookkeeping "Loop: Run ended (${ended_by})"

# --- Merge-clean -----------------------------------------------------------
#
# The enforcement point the whole of spec issue #78 keys off: after the last
# Iteration, the Run removes the Plan, the Progress Log, and any kept-earlier
# log in a Run-authored commit, BEFORE the proposal is pushed. The branch tip is
# always merge-clean - nothing mergeable carries the scaffolding - while the
# branch's history still carries it readably, which is where reviewers read it.
#
# Unconditional rather than only with --propose: the tip is merge-clean whatever
# ends the Run, and a Run that never proposes still leaves no scaffolding behind
# for a later Seeding to trip over.
#
# Removed by path, never swept: an agent that left unrelated changes dirty in
# the working tree keeps them dirty, exactly as commit_bookkeeping above keeps
# them out of the Loop's own commits. And committed only when something was
# staged: a Run whose scaffolding is already gone has nothing to say here.

earlier_log="$(loop_earlier_log_path "${LOOP_PROGRESS_LOG_PATH}")"
git -C "${repo}" rm --quiet --ignore-unmatch -- \
    "${LOOP_PLAN_PATH}" "${LOOP_PROGRESS_LOG_PATH}" "${earlier_log}"
if ! git -C "${repo}" diff --cached --quiet; then
    git -C "${repo}" commit --quiet --message \
        "Loop: Remove the Run scaffolding so the branch tip is merge-clean"
fi

# --- The proposal ----------------------------------------------------------
#
# The Run's only external effect, and the last thing it does. It runs on EVERY
# ending bound rather than only on a clean one: a Run that was killed, that
# stalled on No-op Iterations, or whose agent exited non-zero has still produced
# a Progress Log saying so, and that record is the thing worth reviewing when a
# Run went wrong. A failed first Run is a result, not a blocker.
#
# Its output is echoed rather than parsed. propose.sh already prints
# LOOP_PROPOSE_* lines in the same machine-readable shape as this block, so
# forwarding them puts the branch, the base and the pull request's URL on the
# same stdout the ending bound is on.

proposal="skipped"
propose_output=""
if ${propose}; then
    propose_args=(--repo "${repo}" --ended-by "${ended_by}" --exit "${exit_code}")
    [[ -n ${task_ref} ]] && propose_args+=(--task-ref "${task_ref}")

    # Captured rather than left to stream, so that the LOOP_RUN_* block stays
    # the first thing on stdout. Stderr is not captured: a proposal that failed
    # should say why where a human running the Run sees it.
    propose_rc=0
    propose_output="$("${LOOP_PROPOSE_COMMAND}" "${propose_args[@]}")" || propose_rc=$?

    if ((propose_rc == 0)); then
        proposal="proposed"
    else
        proposal="failed"
        # A Run that reached its planned end and produced no proposal produced
        # nothing to review, and exit 0 would say the opposite. A Run that
        # already ended on a bound keeps that bound's code: which bound ended it
        # is the more useful fact, and LOOP_RUN_PROPOSAL below says the rest.
        ((exit_code != 0)) || exit_code=6

        # The Run-ended record was written and committed BEFORE the proposal ran,
        # because the proposal pushes it and a pull request has to contain the
        # record of the Run that produced it. So on this path history holds an
        # exit code that is out of date. Correct it here rather than leave the
        # two disagreeing in exactly the case the record exists for.
        #
        # As an empty commit rather than appended to the Progress Log: the
        # scaffolding is gone from the tip by now, and reviving the file for one
        # paragraph would put a Progress Log back on the branch the cleanup just
        # took it off. The tip stays merge-clean; the correction travels in
        # history like everything else the Run did.
        #
        # Only on failure, and not pushed again: when a proposal fails there is
        # no pull request to keep in sync - either nothing was pushed at all, or
        # the branch is up with nothing open on it. Re-running propose.sh by
        # hand, which is what that second case is for, carries this commit up
        # with it.
        git -C "${repo}" commit --quiet --allow-empty \
            --message "Loop: the proposal failed (${ended_by})" \
            --message "The exit code in the Run-ended record was written before the proposal ran. Ending bound: ${ended_by}. Exit code: ${exit_code}."
    fi
fi

# --- Telling the operator ---------------------------------------------------
#
# The Run's second external effect and the last thing it does, after the
# proposal, because it carries the proposal's URL and because nothing it does
# may change what the Run did (#110).
#
# It cannot: the exit code is already decided above and is not touched here, and
# nothing below writes to the Progress Log. That is deliberate rather than
# incidental. The record has been committed, the scaffolding removed, and the
# tip pushed with the proposal, so a commit made now would leave the branch on
# GitHub disagreeing with the checkout on the box - and a Run's record is the
# Run, while whether somebody was told about it is not part of what happened.
#
# The surface is one substitutable command (ADR 0004). The one shipped comments
# on the proposal, which is why --notify needs --propose: the boundary's egress
# is github.com and api.github.com and the box holds a token for exactly those,
# so a comment on the pull request the Run just opened costs no new host on the
# allowlist and no new credential in the inventory. Like the push, it happens
# here on the host, after every agent process is gone. ADR 0013.

notified="skipped"
if ${notify}; then
    proposal_url=""
    if [[ -n ${propose_output} ]]; then
        proposal_url="$(sed -n 's/^LOOP_PROPOSE_URL=//p' <<<"${propose_output}" | head -n 1)"
    fi

    if [[ -z ${proposal_url} ]]; then
        # A proposal that failed left nothing to comment on, and there is no
        # second surface: the box's whole reach is the repository. Said in one
        # word rather than reported as a failed notification, because the two
        # are different - one is a surface that refused, the other is a Run that
        # had none - and the operator's next command differs.
        notified="no-surface"
        printf 'run.sh: the proposal produced no URL, so there is nothing to comment on.\n' >&2
    else
        notify_body="$(mktemp)"
        {
            [[ -n ${task_ref} ]] && printf -- '- Task: %s\n' "${task_ref}"
            printf -- '- Ended by: %s\n' "${ended_by}"
            printf -- '- Exit code: %d\n' "${exit_code}"
            iteration_counts_line
            printf -- '- Faults: %s\n' "${fault_summary}"
            printf -- '- Proposal: %s\n' "${proposal_url}"
            printf '\nNothing is merged, deployed or applied. Read %s in this branch'"'"'s history first - the tip carries no Run scaffolding.\n' \
                "${LOOP_PROGRESS_LOG_PATH}"
        } >"${notify_body}"

        # Bounded, like every other thing this script waits on. The Run has
        # already happened and its report is written; a surface that hangs must
        # not be able to hold that report open, and abandoning the notification
        # costs the operator one comment while not abandoning it costs him the
        # answer he was waiting for.
        notify_rc=0
        timeout --kill-after=5s "${LOOP_NOTIFY_TIMEOUT_SECONDS}s" \
            "${LOOP_NOTIFY_COMMAND}" \
            "Run ended: ${ended_by} (exit ${exit_code})" \
            "${notify_body}" \
            "${proposal_url}" >/dev/null || notify_rc=$?
        rm -f -- "${notify_body}"
        notify_body=""

        if ((notify_rc == 0)); then
            notified="sent"
        else
            notified="failed"
        fi
    fi
fi

# Machine-readable and first, so triage after a Run is one line rather than a
# whole log.
printf 'LOOP_RUN_ENDED_BY=%s\n' "${ended_by}"
printf 'LOOP_RUN_EXIT=%d\n' "${exit_code}"
printf 'LOOP_RUN_ITERATIONS=%d\n' "${iterations_run}"
printf 'LOOP_RUN_FAULTS=%s\n' "${fault_summary}"
printf 'LOOP_RUN_PROPOSAL=%s\n' "${proposal}"
printf 'LOOP_RUN_NOTIFIED=%s\n' "${notified}"
[[ -n ${propose_output} ]] && printf '%s\n' "${propose_output}"

# Last, so the LOOP_RUN_* block above stays the first thing on stdout: the
# briefing rendered at Run start, which the Selector journals (#80).
printf 'LOOP_BRIEFING_BEGIN\n'
printf '%s\n' "${run_briefing}"
printf 'LOOP_BRIEFING_END\n'

exit "${exit_code}"
