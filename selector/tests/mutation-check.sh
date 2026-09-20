#!/usr/bin/env bash
#
# Mutation-check the Selector's offline suites.
#
#   tests/mutation-check.sh [--only <name>]... [python]
#
# Breaks one guard at a time - in cycle.py, dispatch.py, watcher.py, board.py,
# guardrail-sources/protection.sh or the window's app.py - runs the suite
# that is supposed to notice against the broken copy, and reports how many
# tests went red. Anything whose removal leaves the suite green is something
# the suite does not actually verify. Same contract as the Loop's
# tests/mutation-check.sh; pytest rather than bats because the Selector is
# Python.
#
# `--only <name>` runs that table entry and no others, against the suite the
# entry names. Repeat `--only` for each name so the optional python stays a
# positional. A name that is not in the table is a refusal, before anything
# is mutated. With no `--only` this is a full run, as it was.
#
# Each mutation names its own file AND its own suite (tests/selector-
# mutations.py), because the Selector is several modules with several suites
# and a mutation checked against the wrong one would be reported as caught by
# tests that never exercised it.
#
# Both files are restored on the way out, including on interrupt: a
# half-mutated Selector left on disk is worse than no check at all.

set -euo pipefail

selector_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# One run at a time, and it is not a nicety. This script edits the target
# files in place and restores them from a backup it took at the start,
# so two overlapping runs restore each other's mutations - and a run that
# overlaps an EDITING SESSION silently reverts uncommitted work to whatever
# the older run had backed up. Both happened on 2026-08-27; the second cost an
# hour and looked like a flaky suite rather than like a clobber.
#
# `flock -n` on this script's own file: no lock file to leave behind, and
# re-running the script under flock is what makes the lock cover the whole run
# rather than one line. `-E 99` because a refused lock has to be
# distinguishable from this script's own exit 1, which means "mutations
# survived" - two different answers that must not share a code.
#
# NOT `exec`: with exec the shell is gone before the branch below could
# report anything, so a refused run would exit silently, which is the failure
# mode this guard exists to make visible.
if [[ -z ${SELECTOR_MUTATION_LOCK_HELD:-} ]]; then
    export SELECTOR_MUTATION_LOCK_HELD=1
    # `|| rc=$?` rather than a bare call: `set -e` is on, so a non-zero flock
    # would end the script on that line and the branch below would never run.
    rc=0
    flock -n -E 99 "${BASH_SOURCE[0]}" "${BASH_SOURCE[0]}" "$@" || rc=$?
    if ((rc == 99)); then
        printf 'mutation-check.sh: another run is already mutating %s - refusing,\n' \
            "${selector_dir}" >&2
        printf '  because two runs restore each other'"'"'s backups and lose whatever\n' >&2
        printf '  uncommitted work the older one had not seen.\n' >&2
        exit 1
    fi
    exit "${rc}"
fi

# Repeatable `--only NAME` so the optional python interpreter stays a
# positional: `tests/mutation-check.sh --only a --only b /path/to/python`.
only_names=()
python_cmd="${selector_dir}/.venv/bin/python"
while (($# > 0)); do
    case "$1" in
        --only)
            if (($# < 2)); then
                printf 'mutation-check.sh: --only needs a mutation name\n' >&2
                exit 2
            fi
            only_names+=("$2")
            shift 2
            ;;
        *)
            python_cmd="$1"
            shift
            ;;
    esac
done
mutations="${selector_dir}/tests/selector-mutations.py"

# Paths relative to the Selector, so a target may live outside it: the status
# strip is the Journal's window and its guards are the Selector's guards
# rendered, so they belong to this check rather than to a second one nobody
# would remember to run.
#
# DERIVED from the table rather than written out beside it. The list used to
# be a hand-kept array, and it had drifted: `events.py`, `seed.sql`,
# `../web/templates/_runs.html` and `../web/static/loop.css` are all mutated
# by the table and were in no backup. Two things follow from a file being
# mutated with no backup, and both are silent - it is left broken on disk when
# the run ends, and it stays broken INTO THE NEXT MUTATION, whose suite then
# goes red for a reason that has nothing to do with the mutation under test
# and is reported as caught. A runner that reports false passes is worse than
# no runner, and the only way to keep the two lists in step is to have one.
mapfile -t listed < <(python3 "${mutations}" --list)
((${#listed[@]} > 0)) || {
    printf 'mutation-check.sh: %s named no targets\n' "${mutations}" >&2
    exit 2
}

# Every `--only` name is checked against the table before a backup is taken
# or a file is edited. Reporting them all at once is what makes two typos
# one refusal rather than a second run to find the second.
if ((${#only_names[@]} > 0)); then
    missing=()
    for name in "${only_names[@]}"; do
        found=0
        for row in "${listed[@]}"; do
            if [[ ${row%%$'\t'*} == "$name" ]]; then
                found=1
                break
            fi
        done
        if ((found == 0)); then
            missing+=("$name")
        fi
    done
    if ((${#missing[@]} > 0)); then
        for name in "${missing[@]}"; do
            printf 'mutation-check.sh: not a mutation: %s\n' "$name" >&2
        done
        exit 2
    fi
    rows=()
    for row in "${listed[@]}"; do
        name="${row%%$'\t'*}"
        for want in "${only_names[@]}"; do
            if [[ $name == "$want" ]]; then
                rows+=("$row")
                break
            fi
        done
    done
else
    rows=("${listed[@]}")
fi

mapfile -t targets < <(printf '%s\n' "${rows[@]}" | cut -f2 | LC_ALL=C sort -u)
((${#targets[@]} > 0)) || {
    printf 'mutation-check.sh: %s named no targets\n' "${mutations}" >&2
    exit 2
}
for target in "${targets[@]}"; do
    [[ -f ${selector_dir}/${target} ]] || {
        printf 'mutation-check.sh: %s names %s, which does not exist\n' \
            "${mutations}" "${target}" >&2
        exit 2
    }
done
backup_dir="$(mktemp -d)"
# Backed up under a flattened name - `../web/app.py` would otherwise
# write outside the backup directory, which is a mutation runner quietly
# scribbling on the repository.
backup_of() { printf '%s/%s' "${backup_dir}" "${1//\//_}"; }
for target in "${targets[@]}"; do
    cp -- "${selector_dir}/${target}" "$(backup_of "${target}")"
done
restore() {
    [[ -d ${backup_dir} ]] || return 0
    for target in "${targets[@]}"; do
        # `if` rather than `[[ ... ]] &&`: as the last statement of the
        # function that would make a missing backup the trap's exit status,
        # and an EXIT trap that returns non-zero is a runner that reports a
        # failure it did not have.
        if [[ -f $(backup_of "${target}") ]]; then
            cp -- "$(backup_of "${target}")" "${selector_dir}/${target}"
        fi
    done
    rm -rf -- "${backup_dir}"
}
trap restore EXIT INT TERM

survivors=0
applied=0
stale=0
# Against the selected set, not the full table: otherwise `--only` of two
# entries reports a truncated list of 223 and the guard fires on a clean run.
declared=${#rows[@]}

while IFS=$'\t' read -r mutation target suite; do
    for name in "${targets[@]}"; do
        cp -- "$(backup_of "${name}")" "${selector_dir}/${name}"
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
    # The window's suite is the window's own, and the window has its own
    # venv - FastAPI and its test client are not in the Selector's. Running it
    # with the wrong interpreter would fail at `import fastapi` and report
    # every window mutation "caught" for a reason that has nothing to do with
    # the mutation, which is worse than not checking it at all.
    suite_python="${python_cmd}"
    case "${suite}" in
        ../web/*) suite_python="${selector_dir}/../web/.venv/bin/python" ;;
    esac
    # An interpreter that is not there fails the OTHER way round: pytest never
    # runs, no `FAILED` line is printed, and the mutation is reported
    # SURVIVED - so a missing venv reads as "the suite verifies none of this"
    # and would send someone writing tests that already exist. Reachable
    # normally, not exotically: `.venv/` is gitignored, so every worktree
    # starts without one, and CLAUDE.md sends factory work into a worktree.
    if [[ ! -x ${suite_python} ]]; then
        printf 'mutation-check.sh: %s is missing - %s cannot run.\n' \
            "${suite_python}" "${suite}" >&2
        printf '  Build it: python3 -m venv %s && %s/bin/pip install -r %s\n' \
            "$(dirname -- "$(dirname -- "${suite_python}")")" \
            "$(dirname -- "$(dirname -- "${suite_python}")")" \
            "${selector_dir}/../web/requirements-dev.txt" >&2
        exit 2
    fi

    output="$("${suite_python}" -m pytest "${selector_dir}/${suite}" -q --no-header \
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
done < <(printf '%s\n' "${rows[@]}")

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
