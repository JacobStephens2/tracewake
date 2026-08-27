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
# Run after every checkout, not once at provision time. git restores tracked
# files at the parent directory's default label, and /srv is var_t - so each
# preview hands back the faked tracker and box scripts, the edges that keep a
# cycle inside this VM, at the one label systemd refuses to exec (203/EXEC,
# and no traceback). The role's restorecon fixes the tree once; the first
# preview undoes it.
RELABEL="${LAB_PREVIEW_RELABEL_COMMAND:-sudo restorecon -R}"
URL="${LAB_PREVIEW_URL:-https://lab-staging.etadventures.com}"
# Matches RuntimeMaxSec on the unit: past this the preview is not running, so
# its lease is not held by anything.
MAX_AGE="${LAB_PREVIEW_MAX_AGE_SECONDS:-14400}"
# Whether a preview is actually up. The lease says WHICH branch; this says
# WHETHER. Both are needed: a lease alone reads as free when it is corrupt or
# missing, which is precisely when a running preview would be taken out from
# under whoever is looking at it.
STATUS_COMMAND="${LAB_PREVIEW_STATUS_COMMAND:-systemctl is-active --quiet lab-webapp-staging}"
# Whether the preview THIS run started came up. The same systemctl question as
# STATUS_COMMAND, asked at the other end of the script, and deliberately not
# the same variable: before the restart the question is "is somebody else
# holding one", after it the question is "is mine up", and a test that fakes
# one has to be able to leave the other alone.
READY_COMMAND="${LAB_PREVIEW_READY_COMMAND:-systemctl is-active --quiet lab-webapp-staging}"
# How long the unit has to stay up before this says it started. The unit is
# Type=simple, so systemd reports the start job done at exec and a process
# that dies a moment later - 203/EXEC on a mislabelled venv, an import error
# in the branch being previewed - is still "active" for that moment. Watching
# is the only way to tell the two apart, and watching is what happens here:
# the check runs across the whole window rather than once at the end of it, so
# a death anywhere inside it is caught rather than only a death before it.
READY_SETTLE="${LAB_PREVIEW_READY_SETTLE_SECONDS:-5}"
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
running=0
preview_is_running && running=1
if [[ -z "$holder" && "$running" -eq 1 ]]; then
    # Up, but the lease cannot say whose. Still somebody's preview.
    holder=$'unknown\tunknown'
fi
if [[ -n "$holder" && "$force" -eq 0 ]]; then
    held_branch="${holder%%$'\t'*}"
    held_by="${holder##*$'\t'}"
    {
        # Two refusals, because they are two different situations and only one
        # of them has somebody to displace. The lease is theirs until it
        # expires either way - but a script whose job is saying what is
        # running must not claim a preview is up when it can see it is not.
        if [[ "$running" -eq 1 ]]; then
            echo "a preview is already running: $held_branch, started by $held_by"
            echo "It exits on its own within $((MAX_AGE / 3600)) hours, or pass --force to take it now."
            echo "Forcing means $held_by is looking at your branch and does not know it."
        else
            echo "the unit is stopped, but the lease is still $held_by's: $held_branch"
            echo "Nothing is being served, so --force takes it now and displaces nobody."
            echo "Left alone the lease clears $((MAX_AGE / 3600)) hours after it was taken."
        fi
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

# Before the restart: a unit started over a var_t venv dies 203/EXEC, and
# relabelling afterwards would be fixing the tree for a preview that has
# already failed.
read -ra relabel_argv <<< "$RELABEL"
if ! "${relabel_argv[@]}" "$WORKTREE" >/dev/null 2>&1; then
    rm -f "$tmp"
    echo "could not relabel $WORKTREE; the running preview is untouched" >&2
    exit 7
fi

# One rename: atomic, so the banner never reads a half-written lease, and the
# only step left after it is the restart - whose failure leaves the tree and
# the lease agreeing about a preview that is simply not up.
mv -f "$tmp" "$LEASE"

read -ra restart_argv <<< "$RESTART"
"${restart_argv[@]}"

# `systemctl restart` exiting 0 is not the preview being up, so do not report
# one until the unit has survived the settle. The lease and the tree still
# agree at this point - they name a preview that is not running, which is the
# honest description and the one the next run's lease check reads correctly.
read -ra ready_argv <<< "$READY_COMMAND"
ready=1
# Deliberately not an early return on the first success: the question is
# whether it STAYED up, so every sample across the window has to hold.
for (( elapsed = 0; elapsed <= READY_SETTLE; elapsed++ )); do
    if ! "${ready_argv[@]}" >/dev/null 2>&1; then
        ready=0
        break
    fi
    (( elapsed < READY_SETTLE )) && sleep 1
done

if (( ready == 0 )); then
    cat >&2 <<EOF
the unit did not stay up after checking out ${sha:0:7} on $branch.

  journalctl -u lab-webapp-staging -n 30

Nothing is being served, so nobody is looking at the wrong branch. The lease
names what was checked out; the next preview takes it without --force.
EOF
    exit 6
fi

cat <<EOF
Attended Preview started.

  branch   $branch
  sha      ${sha:0:7}  ($sha)
  url      $URL
  expires  in $((MAX_AGE / 3600))h, or when you stop the unit

It runs unreviewed code and is permissible only while you are looking at it.
EOF
