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
# in this tree reaches production tourbot and dispatches a real Run.
#
# So the safe path is one command that cannot be half-remembered. Everything
# outward is pointed at preview-sources/, and the Journal at selector_staging.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export SELECTOR_JOURNAL_DSN="${SELECTOR_JOURNAL_DSN:-dbname=selector_staging}"
export SELECTOR_TRACKER_COMMAND="$HERE/preview-sources/tracker.sh"
export SELECTOR_BOX_COMMAND="$HERE/preview-sources/box.sh"
export SELECTOR_ISSUE_COMMAND="$HERE/preview-sources/issue.sh"
export SELECTOR_TASK_REPO="${SELECTOR_TASK_REPO:-example/preview}"

case "$SELECTOR_JOURNAL_DSN" in
    *dbname=selector\ *|*dbname=selector) 
        echo "refusing: SELECTOR_JOURNAL_DSN names the live Journal" >&2
        echo "A preview cycle appends events, and the live Journal is append-only" >&2
        echo "by trigger - anything written there is permanent and undeletable." >&2
        exit 2
        ;;
esac

exec "${SELECTOR_PYTHON:-$HERE/.venv/bin/python}" "$HERE/cycle.py" "$@"
