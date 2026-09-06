"""The deliberate breaks tests/mutation-check.sh applies to agents/codex.sh.

Vendor-specific properties of OpenAI Codex: credential placement and verification,
metered-key doors, vendor invocation arguments, missing egress host reporting,
and turn bound wording. Structural boundary properties live in boundary-harness-mutations.py.
"""

import pathlib
import sys

MUTATIONS = {
    # The agent runs on the host, beside the boundary rather than inside it.
    "no-boundary": (
        '    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n'
        '        codex \\',
        '        codex \\',
    ),
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach.
    "token-inside-the-boundary": (
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644',
        '    put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        '    put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # The signing key goes back to its HOST path inside the guest.
    "credentials-at-host-paths": (
        '    guest_signing_key="${guest_home}/.ssh/$(basename -- "${signing_key}")"\n'
        '    guest_allowed_signers="${guest_home}/.config/loop/allowed_signers"',
        '    guest_signing_key="${signing_key}"\n'
        '    guest_allowed_signers="${allowed_signers}"',
    ),
    # The key moves to a guest path and the git identity that goes in still
    # names the host's - so the guest signs with a key that is not where it is
    # told to look.
    "gitconfig-names-the-old-path": (
        '    git config --file "${guest_gitconfig}" user.signingkey "${guest_signing_key}.pub"',
        '    :',
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '    [[ -f "${codex_home}/auth.json" ]] ||',
        '    [[ true ]] ||',
    ),
    # The auth.json door reopens: a per-model api_key in auth.json resolves ahead
    # of the subscription session.
    "auth-api-key-door-open": (
        'if grep -o \'"OPENAI_API_KEY"[[:space:]]*:[[:space:]]*"[^"]\\+"\' -- "${codex_home}/auth.json"',
        'if false && grep -o \'"OPENAI_API_KEY"[[:space:]]*:[[:space:]]*"[^"]\\+"\' -- "${codex_home}/auth.json"',
    ),
    # The file door reopens: a per-model api_key in config.toml resolves ahead
    # of the session token.
    "config-key-door-open": (
        '    if [[ -f ${codex_config} ]] &&',
        '    if false &&',
    ),
    # The credential is not placed in the guest.
    "credential-not-placed": (
        '    put "${codex_home}/auth.json" "${guest_home}/.codex/auth.json" 0600',
        '    :',
    ),
    # The turn bound firing stops being told apart from a broken invocation.
    "turn-bound-unrecognised": (
        "    grep -qiE '(turn limit reached|max turns reached)' -- \"$1\"",
        "    false",
    ),
    # The other vendor's wording.
    "turn-bound-wrong-vendor": (
        "grep -qiE '(turn limit reached|max turns reached)'",
        "grep -qF 'Reached max turns'",
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '        --max-turns "${max_turns}" \\',
        '        --max-turns 99 \\',
    ),
    # The mode the first Run failed under: file edits auto-approved, Bash still gated.
    "permission-mode-gates-bash": (
        '        --permission-mode bypassPermissions \\',
        '        --permission-mode acceptEdits \\',
    ),
    # The guard covers one name only and lets another door through.
    "metered-key-partial": (
        "metered_env_names=(\n    OPENAI_API_KEY\n    CODEX_API_KEY\n)",
        "metered_env_names=(\n    OPENAI_API_KEY\n)",
    ),
    # Missing egress host is not detected or named on failure.
    "missing-host-unreported": (
        '        if [[ -n ${blocked_host} ]] && grep -qiE \'(blocked by network policy|connection (failed|refused)|failed to connect)\' "${raw_err}"; then',
        '        if false; then',
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
        print(
            f"boundary-codex-mutations.py: {name} no longer applies to {target}",
            file=sys.stderr,
        )
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
