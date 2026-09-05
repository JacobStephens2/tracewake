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
        '"${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n    env CLAUDE_CODE_OAUTH_TOKEN="${token}" \\\n    claude \\',
        "claude \\",
    ),
    # The sandbox is never removed, so every Iteration leaves one behind.
    "sandbox-leaked": (
        '    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true',
        "    :",
    ),
    # Two mutations are deliberately absent, and both absences are findings
    # rather than gaps.
    #
    # Narrowing `trap cleanup EXIT INT TERM` to EXIT alone: bash runs an EXIT
    # trap when a signal arrives while it is waiting on a child, so `INT TERM`
    # changes nothing a test could see. It stays in the script as a statement of
    # intent, not as a mechanism.
    #
    # `exec`-ing the agent, which is what this file did before it had a sandbox
    # to clean up: the agent's invocation is a pipeline now, because the turn
    # bound has to be recognised in what it printed, and `exec` at the head of a
    # pipeline replaces the subshell rather than this script. The trap survives
    # it. The property is structural rather than tested, and what does test it
    # is `sandbox-leaked` plus the killed-at-its-wall-clock case in the suite.
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach.
    "token-inside-the-boundary": (
        'put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
        'put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        'put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # The signing key goes back to its HOST path inside the guest (#268), where
    # it lands only because the workspace happens to be one level under $HOME.
    # Move the workspace deeper and every Iteration dies before the agent
    # starts, saying it could not prepare the signing key.
    "credentials-at-host-paths": (
        'guest_signing_key="${guest_home}/.ssh/$(basename -- "${signing_key}")"\n'
        'guest_allowed_signers="${guest_home}/.config/loop/allowed_signers"',
        'guest_signing_key="${signing_key}"\n'
        'guest_allowed_signers="${allowed_signers}"',
    ),
    # The key moves to a guest path and the git identity that goes in still
    # names the host's - so the guest signs with a key that is not where it is
    # told to look, and the first sighting is a pull request whose commits are
    # not Verified.
    "gitconfig-names-the-old-path": (
        'git config --file "${guest_gitconfig}" user.signingkey "${guest_signing_key}.pub"',
        ":",
    ),
    # A missing credential is skipped instead of fatal - the shape this had
    # before, which would run an Iteration that commits unsigned.
    "missing-credential-skipped": (
        '    [[ -f ${src} ]] ||\n        die "${src} is not on this box'
        ' - a Run needs it inside the boundary. Apply ansible/loop.yml."',
        "    [[ -f ${src} ]] || return 0",
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '[[ -n ${token} ]] ||',
        '[[ true ]] ||',
    ),
    # The guest authenticates from an environment token.
    "token-not-passed-to-guest": (
        'env CLAUDE_CODE_OAUTH_TOKEN="${token}"',
        'env',
    ),
    # A credential file is copied into the microVM, violating credential isolation.
    "credential-file-copied-to-guest": (
        'put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
        'put "${credentials_dir}/.credentials.json" "${guest_home}/.claude/.credentials.json" 0600\n'
        'put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
    ),
    # The turn bound firing stops being told apart from a broken invocation,
    # which is what it looked like before the first Run.
    "turn-bound-unrecognised": (
        "if ((agent_rc != 0)) && grep -qF 'Reached max turns' -- \"${transcript}\"; then",
        "if false; then",
    ),
    # `pipefail` back on for the agent's pipeline. `set -e` then ends this
    # script the moment the agent exits non-zero, so the turn bound is never
    # recognised - which is how this was written the first time.
    "pipefail-kills-detection": (
        "set +o pipefail\n\"${sbx}\" exec --workdir",
        "\"${sbx}\" exec --workdir",
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '--max-turns "${max_turns}" \\',
        "--max-turns 99 \\",
    ),
    # Back to the mode the first Run failed under: file edits auto-approved,
    # Bash still gated, so no Iteration can commit and every Run aborts on
    # consecutive No-ops with its work uncommitted.
    "permission-mode-gates-bash": (
        "--permission-mode bypassPermissions \\",
        "--permission-mode acceptEdits \\",
    ),
    # The Loop's scripts stop being mounted, so the Plan's completeness check is
    # outside the session's allowed directories and an Iteration cannot grade
    # itself.
    "check-not-mounted": (
        '"${sbx}" create --quiet --name "${sandbox}" -t "${guest_template}" claude "${workspace}" "${loop_dir}:ro"',
        '"${sbx}" create --quiet --name "${sandbox}" -t "${guest_template}" claude "${workspace}"',
    ),
    # The boundary is built from the vendor's stock image again, so an Iteration
    # working on tourbot has no PHP, no Composer and no test runner - and `/tdd`
    # is back to asserting that a test would have failed (#164).
    "stock-guest-template": (
        '-t "${guest_template}" claude "${workspace}" "${loop_dir}:ro"',
        'claude "${workspace}" "${loop_dir}:ro"',
    ),
    # The scripts are mounted writable, so a Run could edit the thing that
    # grades it.
    "check-mounted-writable": (
        '"${loop_dir}:ro" >&2',
        '"${loop_dir}" >&2',
    ),
    # The expiry is read from the refresh token beside it, which is three weeks
    # out. Every lapsed box reports as good until the refresh token dies - the
    # failure reported as its own opposite.
    "expiry-reads-refresh-token": (
        "'\"expiresAt\"[[:space:]]*:[[:space:]]*[0-9]\\+'",
        "'\"refreshTokenExpiresAt\"[[:space:]]*:[[:space:]]*[0-9]\\+'",
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
