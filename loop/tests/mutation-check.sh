#!/usr/bin/env bash
#
# Mutation-check the offline suites.
#
#   tests/mutation-check.sh [--only <script>] [bats-command]
#
# Deliberately breaks one thing at a time - a bound of the Termination Contract
# in run.sh, a guard in check-inventory.sh - runs that script's suite against
# the broken copy, and reports how many tests went red. Anything whose removal
# leaves the suite green is something the suite does not actually verify, and on
# a single-operator project with no adversarial reviewer that is the
# highest-value verification available (spec issue #73).
#
# The check script is here for a second reason on top of that one. It is the
# only thing that can say the first task's work did not land, and spec issue #73
# asks specifically that the thing it guards be broken deliberately and the
# check confirmed to fail. Its suite does that from the subject side - occurrences
# added after the inventory was written, entries pointing at lines that no longer
# hold the symbol - and these mutations do it from the check's own side.
#
# Every mutation must be caught. Each script is restored on the way out,
# including on interrupt, because a half-mutated script left on disk is worse
# than no check at all.
#
# Takes a few minutes: one full suite run per mutation.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Each subject is a script, the suite that is supposed to notice it breaking,
# and the mutations to apply. Adding a script to the Loop means adding a row.
subjects=(
    "run.sh|tests/loop.bats|tests/mutations.py"
    "check-inventory.sh|tests/check-inventory.bats|tests/check-mutations.py"
)

only=""
bats_cmd="bats"
while (($# > 0)); do
    case "$1" in
        --only) only="${2:?--only needs a script name}"; shift 2 ;;
        *) bats_cmd="$1"; shift ;;
    esac
done

target=""
backup=""
restore() {
    [[ -n ${backup} ]] || return 0
    cp -- "${backup}" "${target}"
    rm -f -- "${backup}"
    backup=""
}
trap restore EXIT INT TERM

survivors=0
applied=0
for subject in "${subjects[@]}"; do
    IFS='|' read -r script suite mutations <<<"${subject}"
    [[ -z ${only} || ${only} == "${script}" ]] || continue

    target="${loop_dir}/${script}"
    backup="$(mktemp)"
    cp -- "${target}" "${backup}"

    printf '%s\n' "${script}"
    while read -r mutation; do
        cp -- "${backup}" "${target}"
        python3 "${loop_dir}/${mutations}" "${mutation}" "${target}"
        applied=$((applied + 1))

        output="$("${bats_cmd}" "${loop_dir}/${suite}" 2>&1 || true)"
        red="$(grep -c '^not ok' <<<"${output}" || true)"

        if ((red > 0)); then
            printf '  %-28s caught, %2d red\n' "${mutation}" "${red}"
        else
            printf '  %-28s SURVIVED - the suite does not verify this\n' "${mutation}"
            survivors=$((survivors + 1))
        fi
    done < <(python3 "${loop_dir}/${mutations}" --list)

    restore
done

if ((survivors > 0)); then
    printf '\n%d mutation(s) survived.\n' "${survivors}" >&2
    exit 1
fi
printf '\nAll %d mutations caught.\n' "${applied}"
