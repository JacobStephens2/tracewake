"""The deliberate breaks tests/mutation-check.sh applies to run.sh.

Each entry is one bound of the Termination Contract disabled, or one piece of
the machinery that makes a bound observable. Kept beside the suite rather than
typed from memory so that adding a bound means adding its mutation here, and a
bound nobody mutated is visible as an absence.
"""

import pathlib
import sys

MUTATIONS = {
    # The Run's wall clock is never consulted, so only the cap can end a Run.
    "run-clock": (
        "    if ((remaining <= 0)); then",
        "    if false; then",
    ),
    # Consecutive No-op Iterations no longer abort.
    "noop-abort": (
        "if [[ -z ${ended_by} ]] && ((noop_streak >= LOOP_MAX_CONSECUTIVE_NOOPS)); then",
        "if false; then",
    ),
    # An Iteration is never killed, so one hung agent stalls the whole Run.
    "iteration-timeout": (
        '            timeout --kill-after=10s "${iteration_timeout}s" \\\n',
        "",
    ),
    # A Completion Promise ends the Run - the failure Pocock's loop has.
    "promise-terminal": (
        "    if ${promise}; then\n        promise_count=$((promise_count + 1))\n    fi",
        '    if ${promise}; then\n        promise_count=$((promise_count + 1))\n        ended_by="iteration-cap"\n    fi',
    ),
    # An agent exiting non-zero is not treated as a failed step.
    "agent-failure-ignored": (
        '        faults+=("agent-failed")\n        ended_by="agent-failed"',
        "        :",
    ),
    # The head is compared after the Loop's own bookkeeping commit, which makes
    # every Iteration look like progress and No-op detection meaningless.
    "head-after-bookkeeping": (
        '    head_after="$(git -C "${repo}" rev-parse HEAD)"',
        '    head_after="$(git -C "${repo}" rev-parse HEAD~1 2>/dev/null'
        ' || git -C "${repo}" rev-parse HEAD)"',
    ),
    # The iteration cap is off by two.
    "iteration-cap": (
        "iteration <= LOOP_MAX_ITERATIONS",
        "iteration <= LOOP_MAX_ITERATIONS + 2",
    ),
    # The Iteration is no longer asked for decisions and blockers, only for a
    # list of what it did - which is what makes a Progress Log unreadable as a
    # narrative and lets a later Iteration relitigate a settled choice.
    "prompt-decisions": (
        "what\n   you did, what you DECIDED and why, and anything BLOCKED.",
        "what\n   you did.",
    ),
    # The Plan and Progress Log paths stop coming from the Contract.
    "hardcoded-state-paths": (
        'progress_log="${repo}/${LOOP_PROGRESS_LOG_PATH}"',
        'progress_log="${repo}/PROGRESS.md"',
    ),
    # The agent is given a turn bound the Contract did not declare.
    "turn-bound": (
        '"${LOOP_AGENT_COMMAND}" "${prompt_file}" "${LOOP_MAX_TURNS}"',
        '"${LOOP_AGENT_COMMAND}" "${prompt_file}" 99',
    ),
    # The Run never proposes, so it has no external effect and the operator has
    # nothing to review - silently, because the Run still exits 0.
    "proposal-never-made": (
        "proposal=\"skipped\"\npropose_output=\"\"\nif ${propose}; then",
        'proposal="skipped"\npropose_output=""\nif false; then',
    ),
    # A proposal that failed no longer moves a clean Run off exit 0, so a Run
    # that produced nothing reviewable reports that it worked.
    "proposal-failure-ignored": (
        "        ((exit_code != 0)) || exit_code=6",
        "        :",
    ),
    # The turn bound is read as a broken invocation again, so one of the
    # Contract's five bounds ends the whole Run at whichever Iteration hits it.
    # This is the defect the first Run found, kept as a mutation.
    "turn-bound-is-a-failure": (
        "    if ((agent_rc == LOOP_AGENT_TURN_BOUND_EXIT)); then\n        turn_bound=true\n    fi",
        "    if false; then\n        turn_bound=true\n    fi",
    ),
    # The turn bound stops being a fault, so a Run that reached its cap with an
    # Iteration cut off short reports that it worked.
    "turn-bound-not-a-fault": (
        '        turn_bound_count=$((turn_bound_count + 1))\n        faults+=("turn-bound")',
        "        turn_bound_count=$((turn_bound_count + 1))",
    ),
    # A Run may execute on the branch its proposal was supposed to protect.
    "base-branch-allowed": (
        '    [[ ${run_branch} != "${run_base}" ]] ||',
        "    [[ true ]] ||",
    ),
    # The Run stops asking the adapter what would supersede its subscription, so
    # a stray metered key starts a Run instead of stopping one - with no error,
    # no output difference, and no per-Run spend ceiling behind it (#84).
    "metered-key-preflight-dropped": (
        "((${#metered_set[@]} == 0)) ||",
        "((0)) ||",
    ),
    # The adapter's answer stops being required, so an adapter that could not
    # answer produces a preflight that passes while checking nothing.
    "metered-names-optional": (
        '[[ -n ${metered_names} ]] ||\n    die "${LOOP_AGENT_COMMAND} named no metered key environment variables'
        ' - a Run must not start without knowing what would supersede its subscription"',
        "[[ -n ${metered_names} ]] || true",
    ),
}


def main() -> int:
    if sys.argv[1] == "--list":
        print("\n".join(MUTATIONS))
        return 0
    name, target = sys.argv[1], pathlib.Path(sys.argv[2])
    old, new = MUTATIONS[name]
    source = target.read_text()
    if old not in source:
        print(f"mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
