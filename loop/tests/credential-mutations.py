"""The deliberate breaks tests/mutation-check.sh applies to assert-credentials.sh.

The script is an assertion about what the box holds, and an assertion that
passes when its subject is broken reports a property the box does not have. That
is the highest-value verification available on a project with no adversarial
reviewer (spec issue #73), and it is worth more here than anywhere else in the
Loop: nothing else notices a fleet key or a metered model key arriving.

Each entry disables one credential family or one property of a held credential.
Adding a family to the script means adding its mutation here, and a family
nobody mutated is visible as an absence.
"""

import pathlib
import sys

MUTATIONS = {
    # Nothing in any environment is forbidden any more: a metered key, a vault
    # token and a database password all pass.
    "env-families": (
        "            if env_is_set \"${name}\"; then",
        "            if false; then",
    ),
    # A name exported from a shell profile is invisible - the door that is not
    # this process's own environment.
    "env-files": (
        '        [[ -f ${file} ]] || continue\n        if grep -qE',
        '        [[ -f ${file} ]] || continue\n        if false && grep -qE',
    ),
    # A forbidden file on the box is no longer a finding.
    "forbidden-files": (
        "        if [[ -e ${path} ]]; then",
        "        if false; then",
    ),
    # Any private key that is not at the signing key's exact path is ignored -
    # which is every fleet key, since a fleet key would not be at that path.
    "fleet-key-sweep": (
        "    if head -n1 -- \"${candidate}\" 2>/dev/null | grep -q -- '-----BEGIN .*PRIVATE KEY-----'; then",
        "    if false; then",
    ),
    # The third door: a metered credential the Execution Boundary stores, in no
    # environment and no file.
    "boundary-secrets": (
        '            if grep -qiE "(^|[[:space:]])${service}([[:space:]]|$)" <<<"${sbx_secrets}"; then',
        "            if false; then",
    ),
    # The GitHub token's mode stops mattering, so a world-readable token passes.
    "token-mode": (
        'if [[ ${mode} != "0600" && ${mode} != "0400" ]]; then\n        violation "github-token:',
        'if false; then\n        violation "github-token:',
    ),
    # A classic account-wide token passes as a repository-scoped one.
    "token-fine-grained": (
        "    if ! grep -q '^github_pat_' -- \"${token_file}\"; then",
        "    if false; then",
    ),
    # The signing key is present but git never signs with it, which produces
    # unsigned commits and no Verified badge - the failure this whole ticket is
    # about, and the one that is silent until someone looks at GitHub.
    "signing-configured": (
        '    [[ "$(git_setting commit.gpgsign)" == "true" ]] ||',
        '    [[ "$(git_setting commit.gpgsign)" == "true" ]] || true ||',
    ),
    # git signs with whatever key is configured, the Loop's or not.
    "signing-key-identity": (
        '        *)\n            violation "signing-key: git user.signingkey is',
        '        *)\n            true "signing-key: git user.signingkey is',
    ),
    # A gating credential that is absent no longer fails the assertion, so an
    # empty box reports clean.
    "gating-absent": (
        'if [[ ${gating} == "yes" && ${state["${name}"]} != "held" ]]; then',
        "if false; then",
    ),
    # An Execution Boundary that cannot be asked is treated as one that answered
    # yes - the fail-open direction.
    "boundary-unknown": (
        '    state[docker-identity]="unknown"',
        '    state[docker-identity]="held"',
    ),
    # An unreadable probe is scored as a pass rather than as an absence of
    # evidence. The documented invocation is `su - loop` and /root is 0700, so
    # this is the mutation that turns the whole fifth acceptance criterion into
    # a check that reports [clear] for four families it never looked at.
    "indeterminate-silent": (
        "undetermined() { indeterminate+=(\"$1\"); }",
        "undetermined() { :; }",
    ),
    # The box's own SSH host keys are reported as fleet keys, which makes the
    # family red on every correctly-built box - the direction that stops a check
    # being read at all.
    "host-key-exception": (
        '    [[ $(basename -- "${candidate}") == ssh_host_* ]] && continue',
        "    :",
    ),
    # The sweep narrows back to ~/.ssh, so a key anywhere else in the home is
    # invisible.
    "home-sweep-narrowed": (
        '    find "${home}" -type f -size -32k \\',
        '    find "${home}/.ssh" -maxdepth 1 -type f -size -32k \\',
    ),
    # The exemption widens from "on the remote's default branch" to "anywhere
    # inside a checkout", so a key somebody dropped into the work checkout is
    # skipped.
    "repository-exception-too-wide": (
        '    if key_came_with_the_clone "${candidate}"; then',
        '    if git -C "$(dirname -- "${candidate}")" rev-parse --show-toplevel'
        ' >/dev/null 2>&1; then',
    ),
    # The exemption widens from "on the remote's default branch" to "tracked",
    # which is the version an unattended agent can defeat: a Run commits into
    # this checkout, so an Iteration could commit a key and exempt it in one move.
    "repository-exception-trusts-tracked": (
        '    base="$(git -C "${root}" symbolic-ref --quiet --short'
        ' refs/remotes/origin/HEAD 2>/dev/null)" ||\n        return 1',
        '    base=HEAD',
    ),
    # The exception stops being reported, so an exclusion nobody can see is one
    # nobody can audit.
    "repository-exception-silent": (
        'if ((${#repository_keys[@]} > 0)); then',
        "if false; then",
    ),
    # Violations are counted but the exit code stops carrying them, so every
    # caller - a Run preflight, a wizard, an operator - reads success.
    "exit-code": (
        "    printf '\\nViolations:\\n'",
        "    exit 0\n    printf '\\nViolations:\\n'",
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
        print(f"credential-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
