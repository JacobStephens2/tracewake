"""The deliberate breaks tests/mutation-check.sh applies to check-inventory.sh.

The check is the Run's honest failure signal, so the question "would the suite
notice if the check stopped checking?" is the one that matters most here (spec
issue #73). Each entry removes one guard - a fault that stops counting, a
denominator that stops being derived - and the suite has to go red for it.

Kept beside the suite so that adding a guard means adding its mutation, and a
guard nobody mutated is visible as an absence.
"""

import pathlib
import sys

MUTATIONS = {
    # A denominator of zero reports success instead of refusing to run - the
    # shape where the check passes hardest when it is most broken.
    "zero-denominator-passes": (
        "if ((total_scope == 0)); then",
        "if false; then",
    ),
    # The denominator stops coming from the checkout's own text: the symbol is
    # matched case-sensitively, so a lowercased occurrence is not counted.
    "case-sensitive-symbol": (
        'grep -I -z -n -i -F -e "${symbol}"',
        'grep -I -z -n -F -e "${symbol}"',
    ),
    # Untracked scratch files enter the denominator, making it depend on
    # whatever is lying around in the working tree.
    "untracked-files-counted": (
        'git -C "${checkout}" grep -I -z -n',
        'git -C "${checkout}" grep --no-index -I -z -n',
    ),
    # An occurrence nobody classified is no longer a fault.
    "missing-not-a-fault": (
        "faults=$((n_missing + n_stale",
        "faults=$((n_stale",
    ),
    # An entry written against an older tree is no longer a fault, so an
    # inventory can account for occurrences that no longer exist.
    "stale-not-a-fault": (
        "if [[ -z ${occurrence_line[\"${key}\"]+set} ]]; then\n        stale+=(\"${key}\")\n        continue\n    fi",
        "if false; then\n        stale+=(\"${key}\")\n        continue\n    fi",
    ),
    # The same occurrence can be classified twice, so two contradictory
    # classifications both count as accounting for it.
    "duplicates-ignored": (
        'if [[ -n ${seen["${key}"]:-} ]]; then\n        duplicate+=("${key}")',
        'if false; then\n        duplicate+=("${key}")',
    ),
    # An entry naming an occurrence but classifying it as nothing is accepted.
    "unclassified-accepted": (
        'unclassified) unclassified+=("${key}"); continue ;;',
        'unclassified) ;;',
    ),
    # An entry carrying two classifications at once is accepted.
    "ambiguous-accepted": (
        'ambiguous) ambiguous+=("${key}"); continue ;;',
        'ambiguous) ;;',
    ),
    # The rationale floor goes away, so "- x" satisfies issue 648's requirement
    # that a should-include-notes entry name the user-visible symptom.
    "rationale-floor-removed": (
        "min_rationale_chars=12",
        "min_rationale_chars=0",
    ),
    # The scope stops narrowing the denominator, so a Run asked about one owning
    # area is graded against the whole checkout.
    "scope-ignored": (
        'if ((${#scopes[@]} > 0)) && ! first_matching_glob "${path}" "${scopes[@]}" >/dev/null; then',
        "if false; then",
    ),
    # The exclusions stop being declared in the script, leaving the caller free
    # to shrink the denominator until the inventory looks complete.
    "declared-excludes-dropped": (
        "    'mysql_files/*'            # migrations: the table's definition, not a read",
        "",
    ),
    # An inventory in a shape the parser does not read is reported as an
    # inventory that classified nothing, which is the same output as work that
    # was never done.
    "wrong-shape-inventory-silent": (
        "if ((total_entries == 0)); then",
        "if false; then",
    ),
    # A subdirectory of a checkout is accepted, which leaves every declared
    # exclusion matching nothing and the denominator a different shape.
    "subdirectory-silently-rescoped": (
        '[[ ${toplevel} == "${checkout}" ]] ||',
        "true ||",
    ),
    # awk failing is read as a grade rather than as the check not running: its
    # exit 2 is the code that means the inventory is incomplete.
    "unreadable-inventory-graded": (
        ')" || die "could not read the inventory: ${inventory}"',
        ')" || true',
    ),
    # The report says how many occurrences are unaccounted for and not which,
    # which is a number rather than an actionable report.
    "report-names-nothing": (
        "    printf '\\n%s (%d) - %s\\n' \"${heading}\" \"$#\" \"${note}\"",
        "    printf '\\n%s (%d) - %s\\n' \"${heading}\" \"$#\" \"${note}\"\n    return 0",
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
        print(f"check-mutations.py: {name} no longer applies to {target}", file=sys.stderr)
        return 1
    target.write_text(source.replace(old, new, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
