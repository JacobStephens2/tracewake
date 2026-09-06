"""The deliberate breaks tests/mutation-check.sh applies to agents/claude.sh.

ADR 0003 makes the Execution Boundary the one subsystem Attendedness re-earns.
Each entry removes one vendor-specific property of Claude Code: credential token
resolution and expiry reading, guest identity rewriting, and vendor invocation
arguments. Structural boundary properties live in boundary-harness-mutations.py.
"""

import pathlib
import sys

MUTATIONS = {
    # The agent runs on the host, beside the boundary rather than inside it.
    "no-boundary": (
        '    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n'
        '        env CLAUDE_CODE_OAUTH_TOKEN="${token}" \\\n'
        '        claude \\',
        "        claude \\",
    ),
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach.
    "token-inside-the-boundary": (
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        '    put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # The signing key goes back to its HOST path inside the guest (#268), where
    # it lands only because the workspace happens to be one level under $HOME.
    "credentials-at-host-paths": (
        '    guest_signing_key="${guest_home}/.ssh/$(basename -- "${signing_key}")"\n'
        '    guest_allowed_signers="${guest_home}/.config/loop/allowed_signers"',
        '    guest_signing_key="${signing_key}"\n'
        '    guest_allowed_signers="${allowed_signers}"',
    ),
    # The key moves to a guest path and the git identity that goes in still
    # names the host's - so the guest signs with a key that is not where it is
    # told to look, and the first sighting is a pull request whose commits are
    # not Verified.
    "gitconfig-names-the-old-path": (
        '    git config --file "${guest_gitconfig}" user.signingkey "${guest_signing_key}.pub"',
        "    :",
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '    [[ -n ${token} ]] ||',
        '    [[ true ]] ||',
    ),
    # The guest authenticates from an environment token.
    "token-not-passed-to-guest": (
        'env CLAUDE_CODE_OAUTH_TOKEN="${token}"',
        'env',
    ),
    # A credential file is copied into the microVM, violating credential isolation.
    "credential-file-copied-to-guest": (
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
        '    put "${credentials_dir}/.credentials.json" "${guest_home}/.claude/.credentials.json" 0600\n'
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
    ),
    # The turn bound firing stops being told apart from a broken invocation.
    "turn-bound-unrecognised": (
        "    grep -qF 'Reached max turns' -- \"$1\"",
        "    false",
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '        --max-turns "${max_turns}" \\',
        "        --max-turns 99 \\",
    ),
    # Back to the mode the first Run failed under: file edits auto-approved,
    # Bash still gated, so no Iteration can commit and every Run aborts on
    # consecutive No-ops with its work uncommitted.
    "permission-mode-gates-bash": (
        "        --permission-mode bypassPermissions \\",
        "        --permission-mode acceptEdits \\",
    ),
    # The boundary is built from the vendor's stock image again, so an Iteration
    # working on tourbot has no PHP, no Composer and no test runner - and `/tdd`
    # is back to asserting that a test would have failed (#164).
    "stock-guest-template": (
        'adapter_create_args=(-t "${guest_template}" claude)',
        "adapter_create_args=(claude)",
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
