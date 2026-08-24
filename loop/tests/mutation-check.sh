#!/usr/bin/env bash
#
# Mutation-check the offline suite.
#
#   tests/mutation-check.sh [bats-command]
#
# Deliberately breaks each bound of the Termination Contract in run.sh, runs the
# suite against the broken copy, and reports how many tests went red. A bound
# whose removal leaves the suite green is a bound the suite does not actually
# verify - and on a single-operator project with no adversarial reviewer, that
# is the highest-value verification available (spec issue #73).
#
# Every mutation must be caught. Restores run.sh on the way out, including on
# interrupt, because a half-mutated entry point left on disk is worse than no
# check at all.
#
# Takes a few minutes: one full suite run per mutation.

set -euo pipefail

loop_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bats_cmd="${1:-bats}"
target="${loop_dir}/run.sh"
backup="$(mktemp)"

cp -- "${target}" "${backup}"
restore() { cp -- "${backup}" "${target}"; rm -f -- "${backup}"; }
trap restore EXIT INT TERM

mutations=(
    run-clock
    noop-abort
    iteration-timeout
    promise-terminal
    agent-failure-ignored
    head-after-bookkeeping
    iteration-cap
    turn-bound
    prompt-decisions
    hardcoded-state-paths
)

survivors=0
for mutation in "${mutations[@]}"; do
    cp -- "${backup}" "${target}"
    python3 "${loop_dir}/tests/mutations.py" "${mutation}" "${target}"

    red=0
    output="$("${bats_cmd}" "${loop_dir}/tests/loop.bats" 2>&1 || true)"
    red="$(grep -c '^not ok' <<<"${output}" || true)"

    if ((red > 0)); then
        printf '%-24s caught, %2d red\n' "${mutation}" "${red}"
    else
        printf '%-24s SURVIVED - the suite does not verify this bound\n' "${mutation}"
        survivors=$((survivors + 1))
    fi
done

if ((survivors > 0)); then
    printf '\n%d mutation(s) survived.\n' "${survivors}" >&2
    exit 1
fi
printf '\nAll %d mutations caught.\n' "${#mutations[@]}"
