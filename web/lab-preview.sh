#!/bin/bash
# Start an Attended Preview: serve one unmerged branch at lab-staging so it can
# be looked at without being merged first. ADR 0016 is the ruling; this script
# is the only supported way in.
#
# Two properties live here and nowhere else:
#
#   1. The preview says what it landed on. This prints the SHA, and writes it
#      into the lease the banner renders, so "am I looking at what I think I
#      am" never needs a second command.
#   2. One preview at a time, visibly. A live lease is refused by name rather
#      than taken silently - the cost of first-come is not the collision, it is
#      that the displaced operator keeps reporting on somebody else's branch.
#
# Everything it reaches is overridable, so the suite in test_lab_preview.py
# drives the real script against a throwaway origin and restarts nothing.
set -euo pipefail

WORKTREE="${LAB_PREVIEW_WORKTREE:-/srv/lab-webapp-staging}"
LEASE="${LAB_PREVIEW_LEASE:-/var/lib/lab-preview/lease.json}"
RESTART="${LAB_PREVIEW_RESTART_COMMAND:-sudo systemctl restart lab-webapp-staging}"
URL="${LAB_PREVIEW_URL:-https://lab-staging.etadventures.com}"
# Matches RuntimeMaxSec on the unit: past this the preview is not running, so
# its lease is not held by anything.
MAX_AGE="${LAB_PREVIEW_MAX_AGE_SECONDS:-14400}"
# Whether a preview is actually up. The lease says WHICH branch; this says
# WHETHER. Both are needed: a lease alone reads as free when it is corrupt or
# missing, which is precisely when a running preview would be taken out from
# under whoever is looking at it.
STATUS_COMMAND="${LAB_PREVIEW_STATUS_COMMAND:-systemctl is-active --quiet lab-webapp-staging}"
OPERATOR="${LAB_PREVIEW_OPERATOR:-${SUDO_USER:-$(id -un)}}"

usage() {
    cat <<EOF
lab-preview.sh [--force] <branch>

Serve <branch> as an Attended Preview at ${URL} (ADR 0016).

Fetches origin, checks the branch out in ${WORKTREE}, writes the lease the
banner reads, restarts the staging unit, and prints the SHA it landed on.

  --force   take a preview somebody else is holding. The lease names who has
            it and which branch; forcing means their report is now about your
            branch, so tell them.
  --help    this.

The preview exits on its own after $((MAX_AGE / 3600))h. That is the ruling,
not a bug to work around: it runs unreviewed code and is permissible only
while somebody is looking at it.

It reads selector_staging and cannot reach the live Journal. Tracker and box
edges are faked, so no Run is ever dispatched from a preview.
EOF
}

force=0
branch=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) force=1 ;;
        --help|-h) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [[ -n "$branch" ]]; then
                echo "one branch at a time (got '$branch' and '$1')" >&2
                exit 2
            fi
            branch="$1"
            ;;
    esac
    shift
done

if [[ -z "$branch" ]]; then
    echo "no branch given" >&2
    usage >&2
    exit 2
fi

# --- the branch must exist on origin ----------------------------------------
# Refused rather than created: a preview of a branch only this box has is a
# preview of something nobody can review.
git -C "$WORKTREE" fetch -q --prune origin
if ! git -C "$WORKTREE" rev-parse --verify -q "refs/remotes/origin/$branch" >/dev/null; then
    echo "no such branch on origin: $branch" >&2
    echo "push it first - a preview serves what a reviewer can also fetch." >&2
    exit 3
fi
sha="$(git -C "$WORKTREE" rev-parse "refs/remotes/origin/$branch")"

# --- the lease --------------------------------------------------------------
# Read with python rather than grep: a half-written lease must not wedge the
# script, because it would need a human with a text editor to clear at exactly
# the moment they are trying to look at something. Unreadable reads as free.
lease_holder() {
    [[ -f "$LEASE" ]] || return 0
    python3 - "$LEASE" "$MAX_AGE" <<'PY' 2>/dev/null || true
import json, sys, time
from datetime import datetime

try:
    lease = json.loads(open(sys.argv[1]).read())
    started = datetime.fromisoformat(lease["started_at"]).timestamp()
except Exception:
    sys.exit(0)  # unreadable: nothing is holding this
if time.time() - started >= float(sys.argv[2]):
    sys.exit(0)  # expired: the unit that held it has exited
print(f'{lease.get("branch", "unknown")}\t{lease.get("started_by", "unknown")}')
PY
}

preview_is_running() {
    read -ra status_argv <<< "$STATUS_COMMAND"
    "${status_argv[@]}" >/dev/null 2>&1
}

holder="$(lease_holder)"
if [[ -z "$holder" ]] && preview_is_running; then
    # Up, but the lease cannot say whose. Still somebody's preview.
    holder=$'unknown\tunknown'
fi
if [[ -n "$holder" && "$force" -eq 0 ]]; then
    held_branch="${holder%%$'\t'*}"
    held_by="${holder##*$'\t'}"
    {
        echo "a preview is already running: $held_branch, started by $held_by"
        echo "It exits on its own within $((MAX_AGE / 3600)) hours, or pass --force to take it now."
        echo "Forcing means $held_by is looking at your branch and does not know it."
    } >&2
    exit 4
fi

# --- take it ----------------------------------------------------------------
# The lease is STAGED before the checkout and moved into place after it, so
# there is no window in which the tree and the banner disagree. A banner
# naming one branch over a checkout of another is the worst outcome here: it
# is the failure the banner exists to prevent, wearing the banner's authority.
mkdir -p "$(dirname "$LEASE")"
started_at="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
tmp="$(mktemp "${LEASE}.XXXXXX")"
python3 - "$tmp" "$branch" "$sha" "$started_at" "$OPERATOR" <<'PY'
import json, sys
path, branch, sha, started_at, started_by = sys.argv[1:6]
with open(path, "w") as fh:
    json.dump({"branch": branch, "sha": sha,
               "started_at": started_at, "started_by": started_by}, fh, indent=2)
    fh.write("\n")
PY
chmod 0644 "$tmp"

# Detached and forced. This tree is machine-owned - nobody edits it by hand and
# `labstage` cannot - so discarding whatever was there is the correct read of
# "check out this branch", and detaching keeps the preview from accumulating
# local branches that drift from origin.
if ! git -C "$WORKTREE" checkout -q -f --detach "$sha"; then
    rm -f "$tmp"
    echo "could not check out $sha in $WORKTREE; the running preview is untouched" >&2
    exit 5
fi

# One rename: atomic, so the banner never reads a half-written lease, and the
# only step left after it is the restart - whose failure leaves the tree and
# the lease agreeing about a preview that is simply not up.
mv -f "$tmp" "$LEASE"

read -ra restart_argv <<< "$RESTART"
"${restart_argv[@]}"

cat <<EOF
Attended Preview started.

  branch   $branch
  sha      ${sha:0:7}  ($sha)
  url      $URL
  expires  in $((MAX_AGE / 3600))h, or when you stop the unit

It runs unreviewed code and is permissible only while you are looking at it.
EOF
