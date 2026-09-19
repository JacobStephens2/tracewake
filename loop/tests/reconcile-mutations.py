"""The deliberate breaks tests/mutation-check.sh applies to reconcile.sh.

The reconcile Run pushes to a Proposal branch unattended, so what holds
Proposal-Only Output up here is three small guards - the push is never
forced, a Proposal heading the base is refused, and nothing is pushed until
the merge is verified - plus the two verifications of what the agent left
behind. Each entry breaks one of them.
"""

import pathlib
import sys

MUTATIONS = {
    # The push is forced, so a reconcile resolves a moved remote by
    # discarding whatever was on it.
    "push-forced": (
        'push_out="$(git -C "${repo}" push "${remote}" "${head_branch}" 2>&1)"',
        'push_out="$(git -C "${repo}" push --force "${remote}" "${head_branch}" 2>&1)"',
    ),
    # A Proposal heading the base is reconciled, so the default branch is
    # pushed at itself.
    "head-is-base-allowed": (
        '[[ ${head_branch} != "${base}" ]] ||',
        "[[ true ]] ||",
    ),
    # A red verification suite still pushes: the merge lands unverified.
    "red-suite-still-pushes": (
        "        exit 3\n",
        "        :\n",
    ),
    # A resolution that left unmerged paths still pushes: the conflict
    # markers land on the Proposal.
    "unmerged-resolution-pushes": (
        '            printf \'reconcile.sh: still unmerged after the agent: %s\\n\' "${unmerged}" >&2\n'
        "            exit 2\n",
        '            printf \'reconcile.sh: still unmerged after the agent: %s\\n\' "${unmerged}" >&2\n'
        "            :\n",
    ),
    # A resolution that dropped the base still pushes: the Proposal is
    # quietly un-branched from what it claims to be current with.
    "base-ancestry-unchecked": (
        '        git -C "${repo}" merge-base --is-ancestor "${remote}/${base}" HEAD || {',
        '        git -C "${repo}" merge-base --is-ancestor "${remote}/${base}" HEAD && {',
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
        print(f"reconcile-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
