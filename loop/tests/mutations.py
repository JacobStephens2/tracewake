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
    # The agent is given a turn bound the Contract did not declare.
    "turn-bound": (
        '"${LOOP_AGENT_COMMAND}" "${prompt_file}" "${LOOP_MAX_TURNS}"',
        '"${LOOP_AGENT_COMMAND}" "${prompt_file}" 99',
    ),
}


def main() -> int:
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
