#!/usr/bin/env bash
#
# Mutation-check the Selector's offline suite.
#
#   tests/mutation-check.sh [python]
#
# Breaks one guard in cycle.py at a time, runs tests/test_cycle.py against the
# broken copy, and reports how many tests went red. Anything whose removal
# leaves the suite green is something the suite does not actually verify.
# Same contract as the Loop's tests/mutation-check.sh; pytest rather than bats
# because the Selector is Python.
#
# cycle.py is restored on the way out, including on interrupt: a half-mutated
# Selector left on disk is worse than no check at all.

set -euo pipefail

selector_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_cmd="${1:-${selector_dir}/.venv/bin/python}"
target="${selector_dir}/cycle.py"
mutations="${selector_dir}/tests/cycle-mutations.py"
suite="${selector_dir}/tests/test_cycle.py"

backup="$(mktemp)"
cp -- "${target}" "${backup}"
restore() {
    [[ -f ${backup} ]] || return 0
    cp -- "${backup}" "${target}"
    rm -f -- "${backup}"
}
trap restore EXIT INT TERM

survivors=0
applied=0
declared="$(python3 "${mutations}" --list | grep -c '')"

while read -r mutation; do
    cp -- "${backup}" "${target}"
    python3 "${mutations}" "${mutation}" "${target}"
    applied=$((applied + 1))

    # </dev/null for the reason the Loop's runner documents: without it the
    # suite inherits this loop's stdin - the mutation list - and the loop ends
    # early while reporting every mutation caught.
    output="$("${python_cmd}" -m pytest "${suite}" -q --no-header -p no:cacheprovider \
        </dev/null 2>&1 || true)"
    # pytest -q names each failure on its own FAILED line in the short
    # summary. Counting those rather than parsing the "N failed" tally, which
    # begins the line and so has nothing in front of the number to anchor on.
    red="$(grep -c '^FAILED ' <<<"${output}" || true)"

    if ((red > 0)); then
        printf '  %-32s caught, %2d red\n' "${mutation}" "${red}"
    else
        printf '  %-32s SURVIVED - the suite does not verify this\n' "${mutation}"
        survivors=$((survivors + 1))
    fi
done < <(python3 "${mutations}" --list)

if ((applied != declared)); then
    printf 'APPLIED %d of %d declared mutations - the list was truncated\n' \
        "${applied}" "${declared}" >&2
    survivors=$((survivors + declared - applied))
fi

if ((survivors > 0)); then
    printf '\n%d mutation(s) survived.\n' "${survivors}" >&2
    exit 1
fi
printf '\nAll %d mutations caught.\n' "${applied}"
