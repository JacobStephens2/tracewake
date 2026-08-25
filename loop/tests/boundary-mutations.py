"""The deliberate breaks tests/mutation-check.sh applies to agents/claude.sh.

ADR 0003 makes the Execution Boundary the one subsystem Attendedness re-earns.
Each entry removes one property of it: that the Iteration is inside one, that a
fresh one is built and destroyed per Iteration, and that the GitHub token is
not in it.
"""

import pathlib
import sys

MUTATIONS = {
    # The agent runs on the host, beside the boundary rather than inside it.
    # The Run still works, which is the point of mutating it: nothing about the
    # output of a Run says where it happened.
    "no-boundary": (
        '"${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n    claude \\',
        "claude \\",
    ),
    # The sandbox is never removed, so every Iteration leaves one behind.
    "sandbox-leaked": (
        '    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true',
        "    :",
    ),
    # The agent is exec'd, which is what this file did before it had a boundary
    # to clean up. `exec` replaces the shell and discards its traps, so every
    # Iteration leaks its sandbox - including one killed at its wall clock,
    # which is a bound of the Termination Contract rather than an anomaly.
    #
    # Narrowing the trap to EXIT alone is NOT here, and the absence is a
    # finding: bash runs an EXIT trap when a signal arrives while it is waiting
    # on a child, so `INT TERM` changes nothing that a test could see. It is
    # kept in the script as a statement of intent, not as a mechanism.
    "agent-exec-discards-cleanup": (
        '"${sbx}" exec --workdir "${workspace}" "${sandbox}" \\',
        'exec "${sbx}" exec --workdir "${workspace}" "${sandbox}" \\',
    ),
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach.
    "token-inside-the-boundary": (
        'put "${gitconfig}" "${guest_home}/.gitconfig" 0644',
        'put "${gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        'put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '[[ -f "${credentials_dir}/.credentials.json" ]] ||',
        "[[ true ]] ||",
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '--max-turns "${max_turns}" \\',
        "--max-turns 99 \\",
    ),
    # The agent stops accepting edits, so every Iteration stalls on a prompt
    # nobody is there to answer.
    "permission-mode-dropped": (
        "--permission-mode acceptEdits \\",
        "--permission-mode plan \\",
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
        print(f"boundary-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
