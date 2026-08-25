#!/usr/bin/env bash
#
# The completeness check: does an inventory account for every occurrence?
#
#   check-inventory.sh --checkout <path> --inventory <path>
#                      [--symbol <name>] [--scope <glob>]... [--exclude <glob>]...
#
# The Loop's first task (Tourbot issue 648) is a classification exercise, and a
# classification exercise has no test suite to grade it. This script is the
# grade. It is the Run's backpressure while the Run is happening and its
# acceptance afterwards, and it is the only thing on a single-operator project
# with no adversarial reviewer that can say the work did not land (spec #73).
#
# The one property that matters more than any other: IT DERIVES ITS OWN
# DENOMINATOR. The task says "78 files and 276 occurrences". That number was
# true when it was written and is not true now, and a check that trusted it
# would pass on an inventory that had missed everything added since. So the
# denominator comes from the checkout, every time, and the report prints how it
# was arrived at - the symbol, the scope, the exclusions and what they removed -
# so that a reader can audit the number rather than take it.
#
# Exit codes:
#
#   0  the inventory accounts for every occurrence in scope.
#   1  the check could not run, or derived a denominator of zero. Refusing to
#      run is the honest answer; a zero denominator that reported success would
#      be a check that passes hardest when it is most broken.
#   2  the inventory does not account for every occurrence. Every unaccounted
#      occurrence is named, with the line that produced it, because "something
#      is missing" is not an actionable report.
#
# No network, no model, no writes to the checkout. It runs in the Execution
# Boundary on `deny-all` egress and on a laptop, identically.

set -euo pipefail

die() {
    printf 'check-inventory.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
check-inventory.sh --checkout <path> --inventory <path>
                   [--symbol <name>] [--scope <glob>]... [--exclude <glob>]...

Derives every occurrence of <symbol> in the checkout's tracked application code
and reports whether the inventory accounts for each one. Exits 0 when it does,
2 when it does not, 1 when the check could not run.

The inventory is markdown. One occurrence per list item:

  - `path/to/file.php:123` - should-include-notes - the contact history tab
    shows an empty timeline when the only contact was a note.

The path:line names the occurrence. What follows it, up to the next " - ", is
the classification: one of should-include-notes, emails-only-by-design or write.
The rest is the rationale, required for the first two - a should-include-notes
entry names the user-visible symptom, an emails-only-by-design entry says why
email-only is correct. Indented continuation lines are part of the rationale, so
a symptom can run across two lines. Headings, prose and list items with no
path:line are ignored, so the inventory can be a document rather than a data
file.
USAGE
}

# --- What counts, declared here rather than passed at the call site ----------
#
# These live in the script because they are part of what the check asserts. A
# denominator whose exclusions are supplied by the caller is a denominator the
# caller can shrink until the inventory looks complete, which is the failure
# this whole component exists to prevent. `--exclude` can add to the list and
# never remove from it, and every exclusion that removed anything is printed in
# the report with its count.

# The first task's subject. Overridable because the mechanism is not specific to
# one table, and the check is worth having the next time an audit like this runs.
default_symbol="tblEmailMessage"

# Not application code. Each of these holds occurrences of the symbol that
# nobody is going to migrate: schema definitions, documentation about the
# schema, and third-party code.
default_excludes=(
    'mysql_files/*'            # migrations: the table's definition, not a read
    'documentation/*'          # prose about the schema
    'specifications/*'         # prose about the schema
    'notes/*'                  # prose about the schema
    '*.md'                     # prose anywhere else
    '*.sql'                    # schema anywhere else
    'vendor/*'                 # third-party
    '*/vendor/*'               # third-party
    'node_modules/*'           # third-party
    '*/node_modules/*'         # third-party
    '*.min.js'                 # build output
    '*.min.css'                # build output
)

# The three classifications Tourbot issue 648 asks for.
classifications=(should-include-notes emails-only-by-design write)

# Which of them have to carry a rationale, and how much of one. Issue 648 makes
# the symptom an acceptance criterion ("so the fix is gradeable") and the reason
# a criterion too; without a floor, "- x" satisfies both.
rationale_required=(should-include-notes emails-only-by-design)
min_rationale_chars=12

# --- Arguments ---------------------------------------------------------------

checkout=""
inventory=""
symbol="${default_symbol}"
scopes=()
extra_excludes=()

while (($# > 0)); do
    case "$1" in
        --checkout) checkout="${2:?--checkout needs a path}"; shift 2 ;;
        --inventory) inventory="${2:?--inventory needs a path}"; shift 2 ;;
        --symbol) symbol="${2:?--symbol needs a value}"; shift 2 ;;
        --scope) scopes+=("${2:?--scope needs a glob}"); shift 2 ;;
        --exclude) extra_excludes+=("${2:?--exclude needs a glob}"); shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n ${checkout} ]] || die "--checkout is required"
[[ -n ${inventory} ]] || die "--inventory is required"
[[ -d ${checkout} ]] || die "no such directory: ${checkout}"
[[ -f ${inventory} ]] || die "no such file: ${inventory}"
checkout="$(cd -- "${checkout}" && pwd)"

git -C "${checkout}" rev-parse --git-dir >/dev/null 2>&1 ||
    die "not a git repository: ${checkout}"
git -C "${checkout}" rev-parse HEAD >/dev/null 2>&1 ||
    die "checkout has no commits: ${checkout}"

excludes=("${default_excludes[@]}" ${extra_excludes+"${extra_excludes[@]}"})

# --- The denominator ---------------------------------------------------------
#
# Tracked files only. An untracked file is scratch: it is not in the checkout a
# reviewer will read, and requiring it to be classified would make the check
# depend on whatever happens to be lying around in the working tree.
#
# One occurrence is one line. A line naming the symbol twice is still one thing
# to decide about, and line granularity is what makes an inventory entry
# reviewable against `git blame`.

declare -A occurrence_line=()   # path:line -> the matching source line
declare -A in_scope=()          # path:line -> 1 when it needs to be classified
declare -A excluded_by=()       # glob -> how many occurrences it removed
total_all=0
total_scope=0
total_excluded=0
total_out_of_scope_by_glob=0

path_matches_any() {
    local path="$1" glob
    shift
    for glob in "$@"; do
        # shellcheck disable=SC2053  # a glob compared against a path is the point
        [[ ${path} == ${glob} ]] && return 0
    done
    return 1
}

# `git grep -z` separates path, line number and text with NUL, so a path
# holding a colon cannot be mis-split. Bash cannot use NUL as a field
# separator, so the NULs become tabs on the way in; the source line is the
# last field and absorbs any tabs of its own.
while IFS=$'\t' read -r path lineno text; do
    [[ -n ${path} ]] || continue
    key="${path}:${lineno}"
    occurrence_line["${key}"]="${text}"
    total_all=$((total_all + 1))

    hit=""
    for glob in "${excludes[@]}"; do
        # shellcheck disable=SC2053
        if [[ ${path} == ${glob} ]]; then hit="${glob}"; break; fi
    done
    if [[ -n ${hit} ]]; then
        excluded_by["${hit}"]=$(( ${excluded_by["${hit}"]:-0} + 1 ))
        total_excluded=$((total_excluded + 1))
        continue
    fi

    if ((${#scopes[@]} > 0)) && ! path_matches_any "${path}" "${scopes[@]}"; then
        total_out_of_scope_by_glob=$((total_out_of_scope_by_glob + 1))
        continue
    fi

    in_scope["${key}"]=1
    total_scope=$((total_scope + 1))
done < <(git -C "${checkout}" grep -I -z -n -i -F -e "${symbol}" -- . | tr '\0' '\t' || true)

if ((total_scope == 0)); then
    die "derived a denominator of zero for '${symbol}' - the symbol, the scope or the exclusions are wrong, and a check with nothing to check cannot say the work is done"
fi

# --- The inventory -----------------------------------------------------------
#
# Parsed leniently on purpose. The inventory is a document a human reads and
# reviews, grouped by owning area with headings and prose; the check picks the
# entries out of it rather than demanding a data file that nobody would read.

parsed="$(
    awk \
        -v classes="${classifications[*]}" \
        '
        # POSIX awk only: the Loop box is Ubuntu, where /usr/bin/awk is mawk.
        function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
        function emit() {
            if (have) printf "%s\t%s\t%s\t%s\n", e_path, e_line, e_class, trim(e_rationale)
            have = 0
        }
        BEGIN { n = split(classes, C, " ") }
        {
            raw = $0
            line = raw
            gsub(/`/, "", line)

            is_item = (line ~ /^[ \t]*[-*+][ \t]+/)
            body = line
            sub(/^[ \t]*[-*+][ \t]+/, "", body)

            has_ref = match(body, /[A-Za-z0-9_.\/-]+:[0-9]+/)
            if (has_ref) {
                ref = substr(body, RSTART, RLENGTH)
                after = substr(body, RSTART + RLENGTH)
                refpath = ref
                sub(/:[0-9]+$/, "", refpath)
                # A bare "note: 5" is prose, not a file reference.
                if (refpath !~ /[\/.]/) has_ref = 0
            }

            if (is_item && has_ref) {
                emit()
                e_line = ref
                sub(/^.*:/, "", e_line)
                e_path = refpath

                # The classification sits in its own slot: everything between
                # the reference and the first " - ". Reading it there rather
                # than anywhere in the line is what lets a rationale use the
                # word "write" in a sentence without the entry reading as two
                # classifications at once.
                seg = after
                sub(/^[ \t]*[-:,;]+[ \t]*/, "", seg)
                if (match(seg, /[ \t]+-[ \t]+/)) {
                    slot = substr(seg, 1, RSTART - 1)
                    e_rationale = substr(seg, RSTART + RLENGTH)
                } else {
                    slot = seg
                    e_rationale = ""
                }

                found = 0
                for (i = 1; i <= n; i++) {
                    if (index(slot, C[i]) > 0) { found++; e_class = C[i] }
                }
                if (found == 0) { e_class = "unclassified"; e_rationale = "" }
                if (found > 1)  { e_class = "ambiguous";    e_rationale = "" }
                have = 1
                next
            }

            # An indented continuation line belongs to the entry above it, so a
            # symptom can be written across two lines like normal prose.
            if (have && !is_item && raw ~ /^[ \t]+[^ \t]/) {
                e_rationale = e_rationale " " trim(line)
                next
            }
            if (trim(line) == "") emit()
        }
        END { emit() }
        ' "${inventory}"
)"

# --- Reconciliation ----------------------------------------------------------

declare -A seen=()
missing=()
stale=()
duplicate=()
unclassified=()
ambiguous=()
unjustified=()
out_of_scope=()
declare -A by_class=()
accounted=0

needs_rationale() {
    local class="$1" required
    for required in "${rationale_required[@]}"; do
        [[ ${class} == "${required}" ]] && return 0
    done
    return 1
}

while IFS=$'\t' read -r path lineno class rationale; do
    [[ -n ${path} ]] || continue
    key="${path}:${lineno}"

    # Marked seen before anything else can reject it, so that one bad entry is
    # reported once - as the thing that is wrong with it - rather than twice, as
    # a fault and again as an occurrence nobody wrote down.
    if [[ -n ${seen["${key}"]:-} ]]; then
        duplicate+=("${key}")
        continue
    fi
    seen["${key}"]=1

    case "${class}" in
        unclassified) unclassified+=("${key}"); continue ;;
        ambiguous) ambiguous+=("${key}"); continue ;;
    esac

    if [[ -z ${occurrence_line["${key}"]+set} ]]; then
        stale+=("${key}")
        continue
    fi
    if [[ -z ${in_scope["${key}"]:-} ]]; then
        out_of_scope+=("${key}")
        continue
    fi

    by_class["${class}"]=$(( ${by_class["${class}"]:-0} + 1 ))
    if needs_rationale "${class}" && ((${#rationale} < min_rationale_chars)); then
        unjustified+=("${key} (${class})")
        continue
    fi
    accounted=$((accounted + 1))
done <<<"${parsed}"

for key in "${!in_scope[@]}"; do
    [[ -n ${seen["${key}"]:-} ]] || missing+=("${key}")
done

# --- The report --------------------------------------------------------------

count() { printf '%d' "$#"; }

n_missing=$(count ${missing+"${missing[@]}"})
n_stale=$(count ${stale+"${stale[@]}"})
n_duplicate=$(count ${duplicate+"${duplicate[@]}"})
n_unclassified=$(count ${unclassified+"${unclassified[@]}"})
n_ambiguous=$(count ${ambiguous+"${ambiguous[@]}"})
n_unjustified=$(count ${unjustified+"${unjustified[@]}"})
n_out_of_scope=$(count ${out_of_scope+"${out_of_scope[@]}"})

faults=$((n_missing + n_stale + n_duplicate + n_unclassified + n_ambiguous + n_unjustified))
result="complete"
((faults == 0)) || result="incomplete"

printf 'CHECK_RESULT=%s\n' "${result}"
printf 'CHECK_SYMBOL=%s\n' "${symbol}"
printf 'CHECK_OCCURRENCES=%d\n' "${total_scope}"
printf 'CHECK_ACCOUNTED=%d\n' "${accounted}"
printf 'CHECK_MISSING=%d\n' "${n_missing}"
printf 'CHECK_STALE=%d\n' "${n_stale}"
printf 'CHECK_DUPLICATE=%d\n' "${n_duplicate}"
printf 'CHECK_UNCLASSIFIED=%d\n' "${n_unclassified}"
printf 'CHECK_AMBIGUOUS=%d\n' "${n_ambiguous}"
printf 'CHECK_UNJUSTIFIED=%d\n' "${n_unjustified}"
printf 'CHECK_OUT_OF_SCOPE=%d\n' "${n_out_of_scope}"
printf 'CHECK_EXCLUDED=%d\n' "${total_excluded}"
if ((${#scopes[@]} > 0)); then
    printf 'CHECK_SCOPE=%s\n' "${scopes[*]}"
fi

printf '\nDenominator\n'
printf '  %d occurrences of %s in tracked files\n' "${total_all}" "${symbol}"
for glob in "${!excluded_by[@]}"; do
    printf '  -%-4d %s (not application code)\n' "${excluded_by["${glob}"]}" "${glob}"
done
if ((${#scopes[@]} > 0)); then
    printf '  -%-4d outside the scope: %s\n' "${total_out_of_scope_by_glob}" "${scopes[*]}"
fi
printf '  =%-4d to be classified\n' "${total_scope}"

printf '\nClassified\n'
for class in "${classifications[@]}"; do
    printf '  %s: %d\n' "${class}" "${by_class["${class}"]:-0}"
done

report_list() {
    local heading="$1" note="$2"
    shift 2
    (($# > 0)) || return 0
    printf '\n%s (%d) - %s\n' "${heading}" "$#" "${note}"
    local key
    for key in "$@"; do
        if [[ -n ${occurrence_line["${key}"]+set} ]]; then
            printf '  %s\n      %s\n' "${key}" "$(printf '%s' "${occurrence_line["${key}"]}" | sed 's/^[[:space:]]*//')"
        else
            printf '  %s\n' "${key}"
        fi
    done
}

# Sorted so that two runs over the same checkout produce the same report and a
# diff between them is the work that happened in between.
sort_in_place() {
    local -n array="$1"
    ((${#array[@]} > 0)) || return 0
    mapfile -t array < <(printf '%s\n' "${array[@]}" | sort)
}
for name in missing stale duplicate unclassified ambiguous unjustified; do
    sort_in_place "${name}"
done

((n_missing == 0)) || report_list "Missing" \
    "in the checkout, not in the inventory" "${missing[@]}"
((n_stale == 0)) || report_list "Stale" \
    "in the inventory, not in the checkout - written against an older tree" "${stale[@]}"
((n_duplicate == 0)) || report_list "Duplicate" \
    "classified more than once" "${duplicate[@]}"
((n_unclassified == 0)) || report_list "Unclassified" \
    "named but carrying none of: ${classifications[*]}" "${unclassified[@]}"
((n_ambiguous == 0)) || report_list "Ambiguous" \
    "carrying more than one classification" "${ambiguous[@]}"
if ((n_unjustified > 0)); then
    printf '\nUnjustified (%d) - a should-include-notes entry names the user-visible symptom, an emails-only-by-design entry says why\n' "${n_unjustified}"
    printf '  %s\n' "${unjustified[@]}"
fi
if ((n_out_of_scope > 0)); then
    printf '\nOut of scope (%d) - real occurrences, classified, outside what this run asked about\n' "${n_out_of_scope}"
fi

((faults == 0)) || exit 2
exit 0
