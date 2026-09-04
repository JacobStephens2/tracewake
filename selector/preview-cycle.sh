#!/usr/bin/env bash
#
# Run a Selector cycle inside an Attended Preview (ADR 0016).
#
#   ./preview-cycle.sh [--dry-run]
#
# A wrapper rather than a file you source, and that is the whole point. The
# containment ADR 0016 builds - no CONNECT on the live Journal, no SSH key, no
# vault environment - is on the `labstage` account that runs the web process.
# It does NOT extend to your shell: you are conductor, and conductor holds the
# token, the SSH key and write on the live Journal. A bare `python cycle.py`
# in this tree reads the instance's real queue and dispatches a real Run.
#
# So every outward reach is redirected here, and "every" is the load-bearing
# word. Two of them are not commands and are easy to miss:
#
#   TRACEWAKE_TARGETS_FILE  names the instance's real targets, and a target's
#                        `work_repo` is a checkout dispatch.py's push() runs
#                        `git push --set-upstream origin <branch>` against
#                        BEFORE the box is ever reached. Faking the tracker
#                        and the box while pointing at the instance's own
#                        targets file would still put a branch on the real
#                        repository, so a preview writes its own.
#   SELECTOR_SEED_COMMAND  defaults to the Loop's real seed-run.sh.
#
# tests/test_preview_cycle.py asserts that no outward reach is left on its
# default, so adding one to the Selector means adding it there and here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${LAB_PREVIEW_WORK:-/var/lib/lab-preview/work}"

export SELECTOR_JOURNAL_DSN="${SELECTOR_JOURNAL_DSN:-dbname=selector_staging}"

# --- the Journal it may append to -------------------------------------------
# A cycle appends, and the live Journal is append-only by trigger: anything
# written there is permanent and undeletable, in the record whose job is
# answering "why did the Selector do that?". Resolve the DSN to the database
# it actually names rather than matching the string, so `postgresql:///selector`
# and `dbname='selector'` are refused as readily as the obvious spelling.
# Resolved with the Selector's own interpreter, never SELECTOR_PYTHON: the
# suite substitutes a stub there, and a guard that runs the thing it is
# guarding against is not a guard.
guard_python="$HERE/.venv/bin/python"
[[ -x "$guard_python" ]] || guard_python="$(command -v python3 || true)"

resolved_db=""
if [[ -n "$guard_python" ]]; then
    resolved_db="$("$guard_python" -c '
import sys
try:
    from psycopg.conninfo import conninfo_to_dict
except ImportError:
    sys.exit(0)
try:
    print(conninfo_to_dict(sys.argv[1]).get("dbname", ""))
except Exception:
    sys.exit(0)
' "$SELECTOR_JOURNAL_DSN" 2>/dev/null || true)"
fi

# psycopg is not always importable by whichever interpreter is to hand, so the
# textual check is not a nicety - it is the guard in that case. It covers the
# spellings libpq accepts for the same database.
names_live_journal=0
[[ "$resolved_db" == "selector" ]] && names_live_journal=1
[[ "$SELECTOR_JOURNAL_DSN" =~ (^|[[:space:]])dbname=\'?selector\'?([[:space:]]|$) ]] && names_live_journal=1
[[ "$SELECTOR_JOURNAL_DSN" =~ /selector($|\?) ]] && names_live_journal=1

if (( names_live_journal )); then
    {
        echo "refusing: SELECTOR_JOURNAL_DSN resolves to the live Journal"
        echo "A preview cycle appends events, and the live Journal is append-only"
        echo "by trigger - anything written there is permanent and undeletable."
    } >&2
    exit 2
fi

# --- the commands it may run ------------------------------------------------
export SELECTOR_TRACKER_COMMAND="$HERE/preview-sources/tracker.sh"
export SELECTOR_BOX_COMMAND="$HERE/preview-sources/box.sh"
export SELECTOR_ISSUE_COMMAND="$HERE/preview-sources/issue.sh"
export SELECTOR_SEED_COMMAND="$HERE/preview-sources/seed.sh"
# The box's status read (#156). Faked for the same reason the box itself is:
# the real one opens an SSH session from a preview instance to the box that
# runs Runs, once per cycle, on the say-so of unreviewed code. A read is still
# something leaving the VM.
export SELECTOR_BOX_FACTS_COMMAND="$HERE/preview-sources/facts.sh"
# The guardrail read (#165), faked for the same two reasons: it runs `gh api`
# against the instance's repository and walks the deployed tree, and both are
# reaches a preview must not make.
export SELECTOR_GUARDRAIL_COMMAND="$HERE/preview-sources/guardrail.sh"

# --- the repository it may push to ------------------------------------------
# A local bare repo standing in for origin. dispatch.py pushes unconditionally,
# so the only safe answer is a remote that is not GitHub.
mkdir -p "$WORK"
if [[ ! -d "$WORK/origin.git" ]]; then
    git init -q --bare "$WORK/origin.git"
fi
if [[ ! -d "$WORK/work-repo/.git" ]]; then
    git init -q -b master "$WORK/work-repo"
    git -C "$WORK/work-repo" remote add origin "$WORK/origin.git"
    git -C "$WORK/work-repo" -c user.email=preview@example.invalid \
        -c user.name=Preview commit -q --allow-empty -m "preview work repo"
    git -C "$WORK/work-repo" push -q --set-upstream origin master
fi
export SELECTOR_WORK_REMOTE=origin

# --- the target it may work -------------------------------------------------
# Written here rather than pointed at the instance's own file, for the reason
# the work checkout is local: a target stanza carries the repository, the
# checkout that gets pushed and the box's token file, and a preview that read
# the instance's stanzas would reach every one of them.
cat > "$WORK/targets.toml" <<TOML
[[target]]
repo = "example/preview"
work_repo = "$WORK/work-repo"
box_repo = "$WORK/box-repo"
token_file = "$WORK/token"
guest_template = "preview-guest:1"
labeler_allowlist = ["an-operator"]
TOML
export TRACEWAKE_TARGETS_FILE="$WORK/targets.toml"

exec "${SELECTOR_PYTHON:-$HERE/.venv/bin/python}" "$HERE/cycle.py" "$@"
