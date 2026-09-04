"""Canned tracker records, shared by every suite that drives Eligibility.

Three suites now build "an issue as the tracker reports it": the cycle suite,
the dispatch suite, and the dashboard suite behind the queue board. One
definition of it lives here so that a change to what Eligibility reads breaks
all three rather than the one that happened to own the fixture.

Not a test module - it is imported by tests, and by nothing that ships.

The names in here are deliberately invented ones. A fixture that carried a
real operator's account or a real repository would be a company's fact in the
shipping tree by another road, and `tests/test_configuration.py` would be
grading the code against a file that quietly disagreed with it.
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


# The account the canned records are labeled by, and the one every suite puts
# in its target's allowlist. Named once because "labeled by an allowlisted
# operator" is one fact and two spellings of it is a fixture that can pass
# while the allowlist it is supposed to match has drifted.
ALLOWLISTED_OPERATOR = "an-operator"


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
        "labeledBy": ALLOWLISTED_OPERATOR,
        "labeledAt": None,
        "blockedBy": 0,
        "blockers": [],
        "openSubIssues": 0,
        "proposals": [],
    }
    record.update(over)
    return record


# --- A configured instance, for the suites ----------------------------------
#
# Every suite that drives a cycle needs a targets file, because there is no
# longer any other way to say which repository is being worked (issue #3).
# One writer of it, here, so that a change to what a stanza must carry breaks
# every suite at once rather than the one that happened to own the fixture.

TARGET_REPO = "acme/widgets"


def targets_toml(**over) -> str:
    """One `[[target]]` stanza as TOML, with anything overridden.

    Values are written as TOML literals by type: a list becomes an array, a
    number stays bare, everything else is quoted. Small on purpose - the
    suites need one stanza and occasionally two, not a TOML writer.
    """
    stanza = {
        "repo": TARGET_REPO,
        "work_repo": "/nonexistent/work",
        "box_repo": "/nonexistent/box",
        "token_file": "/nonexistent/token",
        "guest_template": "widgets-guest:1",
        "labeler_allowlist": [ALLOWLISTED_OPERATOR],
    }
    stanza.update(over)
    labels = stanza.pop("labels", None)

    def literal(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(literal(item) for item in value) + "]"
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = ["[[target]]"]
    lines += [f"{key} = {literal(value)}" for key, value in stanza.items()]
    if labels:
        lines.append("")
        lines.append("[target.labels]")
        lines += [f"{key} = {literal(value)}" for key, value in labels.items()]
    return "\n".join(lines) + "\n"


def write_targets(path, *stanzas) -> str:
    """Write a targets file at `path`; returns the path as a string.

    Each argument is a dict of overrides for one stanza; with none, one
    default target is written.
    """
    text = "\n".join(targets_toml(**over) for over in (stanzas or ({},)))
    path.write_text(text)
    return str(path)
