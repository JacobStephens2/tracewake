"""The deliberate breaks tests/mutation-check.sh applies to propose.sh.

Proposal-Only Output is the invariant of the whole spec, and this script is
where it stops being an argument. Each entry breaks one thing that holds it up,
or one thing that makes a failed proposal distinguishable from a successful one
- which matters more here than anywhere else in the Loop, because the proposal
is what an operator who was not watching comes back to.
"""

import pathlib
import sys

MUTATIONS = {
    # A Run on the base branch pushes its Iterations straight at the branch the
    # proposal exists to keep them off.
    "base-branch-allowed": (
        '[[ ${branch} != "${base}" ]] ||',
        "[[ true ]] ||",
    ),
    # The base is guessed rather than read from the remote, so a proposal is
    # opened against a branch that may not be the default one. The derivation
    # itself lives in contract.sh, which both this script and run.sh source;
    # what is mutated here is this script's use of it.
    "base-guessed": (
        '    base="${default_base}"',
        '    base="main"',
    ),
    # The push is forced, so a Run resolves a conflict by discarding whatever
    # was on the remote.
    "push-forced": (
        'git -C "${repo}" push --set-upstream "${remote}" "${branch}"',
        'git -C "${repo}" push --force --set-upstream "${remote}" "${branch}"',
    ),
    # The setup-token leak guard is dropped, allowing a branch that committed
    # a model setup-token to be pushed.
    "token-pattern-guard-dropped": (
        'if git -C "${repo}" diff "${base_ref}...${branch}" | grep -qF \'sk-ant-oat01-\'; then',
        "if false; then",
    ),
    # A push that failed is reported as a proposal, so a Run that had no
    # external effect at all says it had one.
    "push-failure-ignored": (
        '    printf \'LOOP_PROPOSE_RESULT=push-failed\\n\'\n    exit 2',
        "    :",
    ),
    # A pull request that was refused is reported as a proposal. The branch is
    # on GitHub and nothing is open on it, and the Run says it proposed.
    "pull-request-failure-ignored": (
        "    printf 'LOOP_PROPOSE_RESULT=pull-request-failed\\n'\n"
        "    printf 'LOOP_PROPOSE_BRANCH=%s\\n' \"${branch}\"\n"
        "    exit 3",
        "    :",
    ),
    # The proposal stops referencing the task it came from, so a reviewer cannot
    # trace it back to the request.
    "task-reference-dropped": (
        "    [[ -n ${task_ref} ]] && printf -- '- Task: %s\\n' \"${task_ref}\"",
        "    :",
    ),
    # The proposal stops naming the owning area, so a reviewer cannot tell
    # what the Run was scoped to.
    "area-dropped": (
        "    [[ -n ${area} ]] && printf -- '- Owning area this Run was scoped to: %s\\n' \"${area}\"",
        "    :",
    ),
    # The proposal stops naming the removal commit, so what is no longer on
    # the tip cannot be found in history.
    "removal-commit-dropped": (
        "    [[ -n ${removal_commit} ]] && printf -- '- Run scaffolding removed in: %s\\n' \"${removal_commit}\"",
        "    :",
    ),
    # The proposal sends the reviewer back to a file the cleanup removed from
    # the tip, instead of pointing at history and the Run's comment.
    "review-points-at-tip-files": (
        "${history_note}${comment_note} The diff is easier to judge knowing how it was arrived at.",
        "Read `${LOOP_PROGRESS_LOG_PATH}` first.",
    ),
    # The Closes line is dropped, so merging the proposal leaves the task open
    # and the queue keeps offering work that is already done (spec #151,
    # story 18).
    "closes-line-dropped": (
        "        printf -- '\\nCloses #%s\\n' \"${task_ref##*#}\"",
        "        :",
    ),
    # The same-repository guard goes, so a cross-repository task gets a Closes
    # line that GitHub silently ignores for an actor without write access on
    # the other repository - a proposal that claims to retire a task and does
    # not.
    "closes-repository-guard-dropped": (
        '    if [[ -n ${task_ref} && ${task_ref} == "${target_repo}#"* && ${base_is_default} == yes ]]; then',
        '    if [[ -n ${task_ref} && ${base_is_default} == yes ]]; then',
    ),
    # The default-branch guard goes, so a proposal aimed at a release branch
    # with --base carries a Closes line GitHub never fires: the keyword acts on
    # a merge into the default branch and nowhere else.
    "closes-default-branch-guard-dropped": (
        '    if [[ -n ${task_ref} && ${task_ref} == "${target_repo}#"* && ${base_is_default} == yes ]]; then',
        '    if [[ -n ${task_ref} && ${task_ref} == "${target_repo}#"* ]]; then',
    ),
    # The default is compared against a guess rather than the remote's own
    # HEAD, which is the same fault as `base-guessed` one step later: on a
    # repository whose default branch is not `master` every proposal silently
    # loses its Closes line.
    "closes-default-guessed": (
        'default_base="$(loop_base_branch "${repo}" "${remote}" || true)"',
        'default_base="master"',
    ),
    # The proposal's target is taken from where git actually pushes rather than
    # from what the remote declares, so a rewrite aims it somewhere else.
    "target-from-transport": (
        'origin_url="$(git -C "${repo}" config --get "remote.${remote}.url")"',
        'origin_url="$(git -C "${repo}" remote get-url "${remote}")"',
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
        print(f"propose-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
