"""The deliberate breaks tests/mutation-check.sh applies to pr-sources/github.sh.

One request is the Run's entire external effect. These break what that request
says: that it is a draft, that it is opened once, and that a refusal is a
failure rather than a silence.
"""

import pathlib
import sys

MUTATIONS = {
    # The pull request is opened ready for review rather than as a draft.
    "not-a-draft": (
        "draft: true}",
        "draft: false}",
    ),
    # A second pull request is opened every time a Run is re-run against the
    # same branch.
    "existing-ignored": (
        'if [[ -n ${existing_url} ]]; then',
        "if false; then",
    ),
    # GitHub's refusal is swallowed and a URL is reported for a pull request
    # that was never opened. Swallowing it alone is not enough to break this:
    # the empty-URL guard below catches that on its own, which is worth knowing.
    "refusal-swallowed": (
        'created="$(github_api POST "repos/${task_repo}/pulls" --data "${payload}")" ||\n'
        '    die "GitHub refused the pull request: ${created:-no response}"',
        'created="$(github_api POST "repos/${task_repo}/pulls" --data "${payload}")" ||\n'
        '    created=\'{"html_url": "https://github.com/owner/name/pull/1"}\'',
    ),
    # A box with no token sends the request anyway, with an empty credential.
    "missing-token-ignored": (
        '[[ -s ${token_file} ]] ||\n    die "no GitHub token at ${token_file}'
        ' - the box can commit but cannot propose"',
        "true",
    ),
    # The existing-pull-request lookup stops being scoped to this branch, so any
    # open pull request on the repository is reported as this Run's proposal.
    "lookup-not-scoped-to-head": (
        'pulls?state=open&head=${owner}:${head_branch}',
        "pulls?state=open",
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
        print(f"pr-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
