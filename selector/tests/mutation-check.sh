#!/usr/bin/env bash
#
# Mutation-check the Selector's offline suites.
#
#   tests/mutation-check.sh [python]
#
# Breaks one guard at a time - in cycle.py or dispatch.py - runs the suite
# that is supposed to notice against the broken copy, and reports how many
# tests went red. Anything whose removal leaves the suite green is something
# the suite does not actually verify. Same contract as the Loop's
# tests/mutation-check.sh; pytest rather than bats because the Selector is
# Python.
#
# Each mutation names its own file AND its own suite (tests/selector-
# mutations.py), because the Selector is two modules with two suites and a
# mutation checked against the wrong one would be reported as caught by tests
# that never exercised it.
#
# Both files are restored on the way out, including on interrupt: a
# half-mutated Selector left on disk is worse than no check at all.

set -euo pipefail

selector_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_cmd="${1:-${selector_dir}/.venv/bin/python}"
mutations="${selector_dir}/tests/selector-mutations.py"

targets=(cycle.py dispatch.py)
backup_dir="$(mktemp -d)"
for target in "${targets[@]}"; do
    cp -- "${selector_dir}/${target}" "${backup_dir}/${target}"
done
restore() {
    [[ -d ${backup_dir} ]] || return 0
    for target in "${targets[@]}"; do
        # `if` rather than `[[ ... ]] &&`: as the last statement of the
        # function that would make a missing backup the trap's exit status,
        # and an EXIT trap that returns non-zero is a runner that reports a
        # failure it did not have.
        if [[ -f ${backup_dir}/${target} ]]; then
            cp -- "${backup_dir}/${target}" "${selector_dir}/${target}"
        fi
    done
    rm -rf -- "${backup_dir}"
}
trap restore EXIT INT TERM

survivors=0
applied=0
stale=0
declared="$(python3 "${mutations}" --list | grep -c '')"

while IFS=$'\t' read -r mutation target suite; do
    for name in "${targets[@]}"; do
        cp -- "${backup_dir}/${name}" "${selector_dir}/${name}"
    done
    # A mutation whose anchor has drifted no longer breaks anything, so it
    # verifies nothing - reported as a survivor rather than abandoning the
    # run, because one stale anchor should not hide the state of the rest.
    if ! python3 "${mutations}" "${mutation}" "${selector_dir}/${target}"; then
        printf '  %-36s STALE - its anchor no longer matches %s\n' \
            "${mutation}" "${target}"
        survivors=$((survivors + 1))
        stale=$((stale + 1))
        continue
    fi
    applied=$((applied + 1))

    # </dev/null for the reason the Loop's runner documents: without it the
    # suite inherits this loop's stdin - the mutation list - and the loop ends
    # early while reporting every mutation caught.
    output="$("${python_cmd}" -m pytest "${selector_dir}/${suite}" -q --no-header \
        -p no:cacheprovider </dev/null 2>&1 || true)"
    # pytest -q names each failure on its own FAILED line in the short
    # summary. Counting those rather than parsing the "N failed" tally, which
    # begins the line and so has nothing in front of the number to anchor on.
    red="$(grep -c '^FAILED ' <<<"${output}" || true)"

    if ((red > 0)); then
        printf '  %-36s caught, %2d red\n' "${mutation}" "${red}"
    else
        printf '  %-36s SURVIVED - the suite does not verify this\n' "${mutation}"
        survivors=$((survivors + 1))
    fi
done < <(python3 "${mutations}" --list)

# The list is read through a pipe, so a truncated read would otherwise look
# like a clean run of however many lines arrived. Stale mutations are already
# counted, so only what neither ran nor was reported is added here.
reached=$((applied + stale))
if ((reached != declared)); then
    printf 'REACHED %d of %d declared mutations - the list was truncated\n' \
        "${reached}" "${declared}" >&2
    survivors=$((survivors + declared - reached))
fi

if ((survivors > 0)); then
    printf '\n%d mutation(s) survived.\n' "${survivors}" >&2
    exit 1
fi
printf '\nAll %d mutations caught.\n' "${applied}"
