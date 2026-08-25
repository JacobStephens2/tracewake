"""The deliberate breaks tests/mutation-check.sh applies to seed-run.sh.

Seeding is the one place a task the operator chose becomes something an
unattended agent reads, so the guards here are about what the Plan ends up
saying and what a second seeding does to a Run that already happened. Each
entry removes one of them, and the suite has to go red for it.

Kept beside the suite so that adding a guard means adding its mutation, and a
guard nobody mutated is visible as an absence.
"""

import pathlib
import sys

MUTATIONS = {
    # A task with no acceptance criteria seeds anyway, leaving an unattended Run
    # with no definition of done and nobody to ask for one.
    "criteria-not-required": (
        "if [[ -z ${criteria} ]] || ((criteria_count == 0)); then",
        "if false; then",
    ),
    # An acceptance criteria section of prose counts as criteria, so "it should
    # be good" satisfies the requirement that the Plan carry them.
    "prose-counts-as-criteria": (
        "if [[ -z ${criteria} ]] || ((criteria_count == 0)); then",
        "if [[ -z ${criteria} ]]; then",
    ),
    # The criteria stop reaching the Plan, which is the one thing the Plan has
    # to carry for an Iteration to tell whether it is done.
    "criteria-dropped-from-plan": (
        "area above.\n\n${criteria}\n",
        "area above.\n\n(they are in the task)\n",
    ),
    # A Run is seeded without an owning area, so a five-Iteration Run is pointed
    # at a task nobody sized.
    "area-not-required": (
        '[[ -n ${area} ]] ||\n    die "--area is required',
        'true ||\n    die "--area is required',
    ),
    # Re-seeding discards a Run's record without saying so. The Progress Log is
    # the only account of what an unattended Run did.
    "run-history-overwritten": (
        "    if ! ${reseed}; then",
        "    if false; then",
    ),
    # The Plan is appended to rather than written whole, so seeding twice
    # accumulates state instead of reproducing it.
    "plan-appended-not-rewritten": (
        'printf \'%s\\n\' "${new_plan}" >"${plan_target}"',
        'printf \'%s\\n\' "${new_plan}" >>"${plan_target}"',
    ),
    # Seeding an already-seeded repository stops being recognised as a no-op, so
    # "re-running the setup step" and "changing the Run" report the same thing.
    "reseeding-never-unchanged": (
        '    result="unchanged"',
        '    result="seeded"',
    ),
    # Headings inside a fenced block count, so a ticket quoting another ticket's
    # acceptance criteria has them lifted out as its own.
    "fenced-headings-counted": (
        "level = (fenced || is_fence) ? 0 : heading_level(line)",
        "level = heading_level(line)",
    ),
    # The task's own headings arrive at the Plan's level and read as the Plan's
    # own sections.
    "headings-not-demoted": (
        'if (level > 0 && level < 6) print "#" line',
        "if (level > 0 && level < 6) print line",
    ),
    # Anything at all reaches the task source, which is the one argument the
    # operator is choosing and the one place this step takes input.
    "task-number-unvalidated": (
        "[[ ${task_number} =~ ^[1-9][0-9]*$ ]] ||",
        "true ||",
    ),
    # A task source answering with a task nobody asked for is accepted, so the
    # Plan can carry a task the operator never chose - which is the failure
    # ADR 0010 says must not be possible.
    "wrong-task-accepted": (
        '[[ ${fetched_number} == "${task_number}" ]] ||',
        "true ||",
    ),
    # The seed sweeps whatever else was dirty in the working tree into its own
    # commit, the way run.sh deliberately does not.
    "unrelated-work-swept-in": (
        'git -C "${repo}" add -- "${LOOP_PLAN_PATH}" "${LOOP_PROGRESS_LOG_PATH}"',
        'git -C "${repo}" add -A',
    ),
    # The Plan's path stops being the Contract's, so the seed and the Run can
    # disagree about where the Plan is.
    "plan-path-hardcoded": (
        'plan_target="${repo}/${LOOP_PLAN_PATH}"',
        'plan_target="${repo}/PLAN.md"',
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
        print(f"seed-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
