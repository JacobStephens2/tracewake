"""The deliberate breaks tests/mutation-check.sh applies to agents/grok.sh.

Vendor-specific properties of Grok Build: the pinned version and installer,
credential placement and verification, metered-key doors, vendor invocation arguments,
and turn bound wording. Structural boundary properties live in boundary-harness-mutations.py.
"""

import pathlib
import sys

MUTATIONS = {
    # The agent runs on the host, beside the boundary rather than inside it.
    "no-boundary": (
        '    "${sbx}" exec --workdir "${workspace}" "${sandbox}" \\\n'
        "        env GROK_TELEMETRY_ENABLED=0",
        "        env GROK_TELEMETRY_ENABLED=0",
    ),
    # The GitHub token goes inside the boundary, and Proposal-Only Output stops
    # being a property of what the agent can reach. It is the one first-Run
    # property #84 exists to confirm transfers, so it is mutated here rather
    # than assumed to hold because the other adapter holds it.
    "token-inside-the-boundary": (
        '    put "${gitconfig}" "${guest_home}/.gitconfig" 0644',
        '    put "${gitconfig}" "${guest_home}/.gitconfig" 0644\n'
        '    put "${HOME}/.config/loop/github-token"'
        ' "${guest_home}/.config/loop/github-token" 0600',
    ),
    # A Run starts with no model credential on the box and finds out one
    # Iteration at a time.
    "model-credential-unchecked": (
        '    [[ -f "${grok_home}/auth.json" ]] ||',
        "    [[ true ]] ||",
    ),
    # The turn bound firing stops being told apart from a broken invocation.
    # Under the other vendor this ended a whole Run at Iteration 1.
    "turn-bound-unrecognised": (
        "    grep -qiF 'max turns reached' -- \"$1\"",
        "    false",
    ),
    # The other vendor's wording, which is the mistake this adapter exists to
    # make impossible: the two agents report the same event in different words,
    # and a copied adapter that kept the first one's string would report every
    # turn bound as an agent failure.
    "turn-bound-wrong-vendor": (
        "grep -qiF 'max turns reached'",
        "grep -qF 'Reached max turns'",
    ),
    # The turn bound the Contract declared never reaches the agent.
    "turn-bound-dropped": (
        '        --max-turns "${max_turns}" \\',
        "        --max-turns 99 \\",
    ),
    # The mode the first Run failed under: file edits auto-approved, Bash still
    # gated, so no Iteration can commit.
    "permission-mode-gates-bash": (
        "        --permission-mode bypassPermissions \\",
        "        --permission-mode acceptEdits \\",
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
        '    [[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||',
        "    [[ true ]] ||",
    ),
    # The credential is placed before the installer runs, into the directory the
    # installer writes into.
    "credential-placed-before-install": (
        '    "${sbx}" exec "${sandbox}" bash -lc \\\n'
        '        "set -o pipefail; curl -fsSL https://x.ai/cli/install.sh | bash -s ${LOOP_GROK_VERSION}" >&2 ||\n'
        '        die "could not install the agent inside the boundary - is x.ai:443 on the egress allowlist? See ansible/roles/loop_execution_boundary/defaults/main.yml"\n'
        '\n'
        '    guest_agent="${guest_home}/.grok/bin/grok"\n'
        '    local installed\n'
        '    installed="$("${sbx}" exec "${sandbox}" "${guest_agent}" --version 2>&1 || true)"\n'
        '    [[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||\n'
        '        die "the boundary is holding \'${installed}\', not the pinned ${LOOP_GROK_VERSION}"\n'
        '\n'
        '    put "${grok_home}/auth.json" "${guest_home}/.grok/auth.json" 0600',
        '    put "${grok_home}/auth.json" "${guest_home}/.grok/auth.json" 0600\n'
        '    "${sbx}" exec "${sandbox}" bash -lc \\\n'
        '        "set -o pipefail; curl -fsSL https://x.ai/cli/install.sh | bash -s ${LOOP_GROK_VERSION}" >&2 ||\n'
        '        die "could not install the agent inside the boundary - is x.ai:443 on the egress allowlist? See ansible/roles/loop_execution_boundary/defaults/main.yml"\n'
        '\n'
        '    guest_agent="${guest_home}/.grok/bin/grok"\n'
        '    local installed\n'
        '    installed="$("${sbx}" exec "${sandbox}" "${guest_agent}" --version 2>&1 || true)"\n'
        '    [[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||\n'
        '        die "the boundary is holding \'${installed}\', not the pinned ${LOOP_GROK_VERSION}"',
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
        "adapter_privileged_put=true",
        "adapter_privileged_put=false",
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
