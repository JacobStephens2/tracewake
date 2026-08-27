"""The deliberate breaks tests/mutation-check.sh applies to agents/grok.sh.

The same shape as boundary-mutations.py, and deliberately not the same list.
The properties Grok Build shares with Claude Code are mutated here too - a
suite that only covered the differences would let the shared ones rot the day
somebody edited this adapter alone. The ones only this adapter can lose are the
pin, the install, the metered-key doors, and the vendor's own wording for the
turn bound.
"""

import pathlib
import sys

MUTATIONS = {
    # The agent runs on the host, beside the boundary rather than inside it.
    "no-boundary": (
        '"${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n'
        "    env GROK_TELEMETRY_ENABLED=0",
        "env GROK_TELEMETRY_ENABLED=0",
    ),
    # The sandbox is never removed, so every Iteration leaves one behind.
    "sandbox-leaked": (
        '    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true',
        "    :",
    ),
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach. It is the one first-Run
    # property #84 exists to confirm transfers, so it is mutated here rather
    # than assumed to hold because the other adapter holds it.
    "token-inside-the-boundary": (
        'put "${gitconfig}" "${guest_home}/.gitconfig" 0644',
        'put "${gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        'put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # A missing credential is skipped instead of fatal, so an Iteration runs and
    # commits unsigned.
    "missing-credential-skipped": (
        '    [[ -f ${src} ]] ||\n        die "${src} is not on this box'
        ' - a Run needs it inside the boundary. Apply ansible/loop.yml."',
        "    [[ -f ${src} ]] || return 0",
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '[[ -f "${grok_home}/auth.json" ]] ||',
        "[[ true ]] ||",
    ),
    # The turn bound firing stops being told apart from a broken invocation.
    # Under the other vendor this ended a whole Run at Iteration 1.
    "turn-bound-unrecognised": (
        "if ((agent_rc != 0)) && grep -qiF 'max turns reached' -- \"${transcript}\"; then",
        "if false; then",
    ),
    # The other vendor's wording, which is the mistake this adapter exists to
    # make impossible: the two agents report the same event in different words,
    # and a copied adapter that kept the first one's string would report every
    # turn bound as an agent failure.
    "turn-bound-wrong-vendor": (
        "grep -qiF 'max turns reached'",
        "grep -qF 'Reached max turns'",
    ),
    # `pipefail` back on for the agent's pipeline. `set -e` then ends this
    # script the moment the agent exits non-zero, so the turn bound is never
    # recognised.
    "pipefail-kills-detection": (
        'set +o pipefail\n"${sbx}" exec --workdir',
        '"${sbx}" exec --workdir',
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '--max-turns "${max_turns}" \\',
        "--max-turns 99 \\",
    ),
    # The mode the first Run failed under: file edits auto-approved, Bash still
    # gated, so no Iteration can commit.
    "permission-mode-gates-bash": (
        "--permission-mode bypassPermissions \\",
        "--permission-mode acceptEdits \\",
    ),
    # The Loop's scripts stop being mounted, so the Plan's completeness check is
    # outside the session's allowed directories.
    "check-not-mounted": (
        '"${sbx}" create --quiet --name "${sandbox}" "${guest_template}" "${workspace}" "${loop_dir}:ro"',
        '"${sbx}" create --quiet --name "${sandbox}" "${guest_template}" "${workspace}"',
    ),
    # The scripts are mounted writable, so a Run could edit the thing that
    # grades it.
    "check-mounted-writable": (
        '"${loop_dir}:ro" >&2',
        '"${loop_dir}" >&2',
    ),
    # --- What only this adapter can lose ------------------------------------
    #
    # The install stops being pinned: whatever `stable` points at that minute
    # goes inside an unattended Run. This is the mutation the other adapter has
    # no equivalent of, because the other agent's guest copy is the boundary
    # vendor's rather than ours.
    "install-unpinned": (
        "curl -fsSL https://x.ai/cli/install.sh | bash -s ${LOOP_GROK_VERSION}",
        "curl -fsSL https://x.ai/cli/install.sh | bash",
    ),
    # The install is asked for at the pin and never read back, so an installer
    # that fell back to a channel pointer puts an unobserved version inside the
    # Run and nothing notices.
    "pin-not-verified": (
        '[[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||',
        "[[ true ]] ||",
    ),
    # The credential is placed before the installer runs, into the directory the
    # installer writes into.
    "credential-placed-before-install": (
        'put "${grok_home}/auth.json" "${guest_home}/.grok/auth.json" 0600\n',
        "",
    ),
    # The metered key guard goes away entirely: a stray XAI_API_KEY moves every
    # Iteration onto per-token billing with no error and no output difference.
    # Spec #73 story 32, and the failure shape that broke Remote Control.
    "metered-key-unguarded": (
        "if ((${#metered_found[@]} > 0)); then",
        "if false; then",
    ),
    # The guard covers the documented name only. It reads as complete and lets
    # every other door the vendor honours through.
    "metered-key-partial": (
        "metered_env_names=(\n    XAI_API_KEY\n    GROK_CODE_XAI_API_KEY\n"
        "    GROK_DEPLOYMENT_KEY\n    GROK_AUTH_PROVIDER_ACCESS_TOKEN\n"
        "    GROK_AUTH_PROVIDER_COMMAND\n)",
        "metered_env_names=(\n    XAI_API_KEY\n)",
    ),
    # The file door reopens: a per-model api_key in the config resolves ahead of
    # the session token, and that file goes into the guest.
    "config-key-door-open": (
        "if [[ -f ${grok_config} ]] &&",
        "if false &&",
    ),
    # The agent updates itself mid-Run, so the version the check above passed is
    # not the version that did the work.
    "autoupdater-left-on": (
        "GROK_DISABLE_AUTOUPDATER=1 \\",
        "\\",
    ),
    # The directory preparation drops back to the other adapter's plain mkdir,
    # which the `shell` template refuses. An Iteration then dies placing the
    # signing key - after building a boundary and installing an agent.
    "guest-dir-unprivileged": (
        '"${sbx}" exec "${sandbox}" sudo mkdir -p "${dir}"',
        '"${sbx}" exec "${sandbox}" mkdir -p "${dir}"',
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
            f"boundary-grok-mutations.py: {name} no longer applies to {target}",
            file=sys.stderr,
        )
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
