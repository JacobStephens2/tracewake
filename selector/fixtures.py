"""Canned tracker records, shared by every suite that drives Eligibility.

Three suites now build "an issue as the tracker reports it": the cycle suite,
the dispatch suite, and the dashboard suite behind the queue board. One
definition of it lives here so that a change to what Eligibility reads breaks
all three rather than the one that happened to own the fixture.

Not a test module - it is imported by tests, and by nothing that ships.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BODY = """## Problem

Something is wrong.

## Acceptance criteria

- [ ] It is right

## Owning area

The nightly sync script
"""


def hours_ago_iso(hours):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(number, **over):
    """One tracker record, eligible unless a field is overridden."""
    record = {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.invalid/{number}",
        "state": "OPEN",
        "body": BODY,
        "labeledBy": "JacobStephens2",
        "labeledAt": None,
        "blockedBy": 0,
        "blockers": [],
        "openSubIssues": 0,
        "proposals": [],
    }
    record.update(over)
    return record
