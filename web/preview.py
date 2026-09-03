"""Is this instance an Attended Preview, and of what? (ADR 0016)

An Attended Preview serves an unmerged branch so it can be looked at without
being merged first. It is permissible on the VM that holds production
credentials only because somebody is looking at it, which makes "say what you
are" a safety property rather than a nicety: an operator who cannot tell a
preview from the live app has lost the attendedness the ruling rests on.

Two facts, deliberately kept apart:

- **Being a preview** is `LAB_PREVIEW_LEASE` being set at all. It comes from
  the unit file, so the live app cannot acquire it by accident and a preview
  cannot lose it by deleting a file.
- **Which branch** comes from the lease `web/lab-preview.sh` writes. That
  file can go missing or be half-written, and when it does the banner says so
  and stays up.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ENV_VAR = "LAB_PREVIEW_LEASE"
# The unit's RuntimeMaxSec, so the banner quotes the bound that is actually
# configured rather than a number written twice and drifted once.
MAX_AGE_VAR = "LAB_PREVIEW_MAX_AGE_SECONDS"
DEFAULT_MAX_AGE_SECONDS = 4 * 60 * 60

# Long enough to compare against `git log --oneline`, short enough not to be
# noise in a banner that has to be read every time.
SHA_CHARS = 7


def _uptime(started_at: str | None) -> str | None:
    """How long this preview has been up, as the banner says it.

    Uptime is the attendedness signal - a preview that has been up for three
    hours is one nobody is watching - so an unparseable timestamp reads as
    unknown rather than as zero.
    """
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    minutes = max(0, int((datetime.now(timezone.utc) - started).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def _max_age_hours() -> int:
    try:
        seconds = int(os.environ.get(MAX_AGE_VAR, DEFAULT_MAX_AGE_SECONDS))
    except ValueError:
        seconds = DEFAULT_MAX_AGE_SECONDS
    return max(1, seconds // 3600)


def banner() -> dict | None:
    """What the banner should say, or None on the live app.

    Never raises: this is rendered on every page, and a preview that 500s
    because its lease is half-written is a preview that stopped telling anyone
    it was a preview.
    """
    lease_path = os.environ.get(ENV_VAR)
    if not lease_path:
        return None
    try:
        lease = json.loads(Path(lease_path).read_text())
        if not isinstance(lease, dict):
            raise ValueError("lease is not an object")
    except (OSError, ValueError):
        return {"unreadable": True, "max_age_hours": _max_age_hours()}
    sha = str(lease.get("sha") or "")
    return {
        "unreadable": False,
        "max_age_hours": _max_age_hours(),
        "branch": lease.get("branch"),
        "sha": sha[:SHA_CHARS] or None,
        "started_by": lease.get("started_by"),
        "uptime": _uptime(lease.get("started_at")),
    }
