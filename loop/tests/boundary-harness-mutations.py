"""The deliberate breaks tests/mutation-check.sh applies to boundary-harness.sh.

ADR 0003 makes the Execution Boundary the one subsystem Attendedness re-earns.
Each entry removes one structural property of it: that the Iteration is inside one,
that a fresh one is built and destroyed per Iteration, that the Loop's scripts are
mounted read-only, that metered keys are refused before building, and that credentials
fail loudly.
"""

import pathlib
import sys

MUTATIONS = {
    # The sandbox is never removed, so every Iteration leaves one behind.
    "sandbox-leaked": (
        '        "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true',
        "        :",
    ),
    # A missing credential is skipped instead of fatal - the shape this had
    # before, which would run an Iteration that commits unsigned.
    "missing-credential-skipped": (
        '    [[ -f ${src} ]] ||\n'
        '        die "${src} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."',
        "    [[ -f ${src} ]] || return 0",
    ),
    # `pipefail` back on for the agent's pipeline. `set -e` then ends this
    # script the moment the agent exits non-zero, so the turn bound is never
    # recognised - which is how this was written the first time.
    "pipefail-kills-detection": (
        "    set +o pipefail\n    adapter_run_agent",
        "    adapter_run_agent",
    ),
    # The Loop's scripts stop being mounted, so the Plan's completeness check is
    # outside the session's allowed directories and an Iteration cannot grade
    # itself.
    "check-not-mounted": (
        '"${adapter_create_args[@]}" "${workspace}" "${loop_dir}:ro"',
        '"${adapter_create_args[@]}" "${workspace}"',
    ),
    # The scripts are mounted writable, so a Run could edit the thing that
    # grades it.
    "check-mounted-writable": (
        '"${loop_dir}:ro" >&2',
        '"${loop_dir}" >&2',
    ),
    # The metered key guard goes away entirely: a stray key moves every
    # Iteration onto per-token billing with no error and no output difference.
    "metered-key-unguarded": (
        "    ((${#metered_found[@]} > 0)) || return 0",
        "    return 0",
    ),
    # A boundary that cannot be created is ignored rather than failing.
    "create-fails-ignored": (
        '    "${sbx}" create --quiet --name "${sandbox}" "${adapter_create_args[@]}" "${workspace}" "${loop_dir}:ro" >&2 ||\n'
        '        die "could not create the Execution Boundary for this Iteration from ${guest_template}. If the box is not holding that image, apply ansible/loop.yml - role loop_guest_template builds it."',
        '    "${sbx}" create --quiet --name "${sandbox}" "${adapter_create_args[@]}" "${workspace}" "${loop_dir}:ro" >&2 || true',
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
        print(f"boundary-harness-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
