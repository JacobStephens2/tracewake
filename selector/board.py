#!/usr/bin/env python3
"""The queue board: the whole tracker queue, through the Selector's own lens.

Every other panel on /loop is read from the Journal - what the Selector
decided, remembered. This one is not: it is the tracker's state as it is right
now, fetched when the page is requested, so that "what is waiting" is answered
by the place the work actually lives rather than by the last cycle's summary
(story 24). A cycle that has not run for an hour leaves the Journal an hour
stale; the board is never stale, and the gap between the two is itself worth
seeing.

What makes it a board rather than a second opinion is that the columning is
`cycle.eligibility` - imported, not reimplemented. A page that decided for
itself which issues were Eligible would be a second Selector, and the first
disagreement between them would be a bug in whichever one you did not read
(ADR 0015: the page is a window and a scribe).

Everything it reaches is the same substitutable tracker command the cycle
reads (`SELECTOR_TRACKER_COMMAND`), asked once per label.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cycle  # noqa: E402

# How long one tracker read may take. Short, and much shorter than the
# cycle's own reads: this one happens inside a request, and a tracker that is
# not answering must make the column say so rather than hold the page open -
# the same rule the status strip's timer read follows.
DEFAULT_TIMEOUT_SECONDS = 20

# The one skip reason that is not a blockage but a state: an issue with an
# open Proposal is being worked, which is a column of its own on the board
# even though the cycle records it as one more reason to pass over.
IN_FLIGHT_REASON = "proposal-open"

# The reason a card carries when the Journal, not the tracker, is what puts it
# in flight: a dispatch with no outcome. Not a `cycle.eligibility` reason -
# per-issue Eligibility does not know about the in-flight lock, the cycle
# halts on it - so it is named here, and named for the halt the cycle
# journals so that the board and a `cycle.finished` row use one word.
DISPATCHED_REASON = "run-in-flight"


def _read(config: cycle.Config, label: str, timeout: float):
    """One label's queue, or the reason it could not be read.

    A failure is caught per label rather than for the board as a whole: three
    labels are three reads, and one unreachable column is not a reason to
    render none of them.
    """
    try:
        return cycle.fetch_queue(config, label=label, timeout=timeout), None
    except cycle.CycleFailed as exc:
        return [], str(exc)


def _card(record: dict, reason: tuple[str, str] | None) -> dict:
    """One issue, as a card: what it is, and - when it is not Eligible - the
    reason the Selector would journal for it, in that reason's own words."""
    return {
        "number": record.get("number"),
        "title": record.get("title"),
        "url": record.get("url"),
        "reason": reason[0] if reason else None,
        "detail": reason[1] if reason else None,
        # Named, not counted. `blockedBy` is the count Eligibility decides on;
        # this is the tracker's list of which issues they are, which is the
        # only form of the answer that lets the reader go and look at one.
        "blockers": record.get("blockers") or [],
    }


def _column(key: str, name: str, label: str | None, note: str, cards: list,
            error: str | None) -> dict:
    """One column.

    `key` and `name` are deliberately two things. The name is what the column
    is called on the page, and for the two label columns that is the label
    itself as configured - rename `SELECTOR_REVIEW_LABEL` and the board says
    the new name, because a board that kept calling it `awaiting-review` would
    be describing a label the tracker no longer has. The key is fixed, and is
    what the stylesheet and the tests address the column by: those are about
    the column's ROLE, which a rename does not change.
    """
    return {"key": key, "name": name, "label": label, "note": note,
            "cards": cards, "error": error}


def board(config: cycle.Config, spend=None, *, timeout: float | None = None):
    """The five columns, read from the tracker now.

    `spend` is the Journal's half of Eligibility - the retry budget and the
    in-flight lock. It is optional because the board must still render when
    the Journal is unreachable, and it is passed in rather than read here so
    that the page opens the Journal once.
    """
    if timeout is None:
        timeout = float(
            os.environ.get("SELECTOR_BOARD_TIMEOUT_SECONDS",
                           DEFAULT_TIMEOUT_SECONDS)
        )
    labels = (config.label, config.review_label, config.human_label)
    # Concurrently: three reads of a remote tracker, one page request. They
    # are independent, and serialised they are three round trips deep.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(labels)) as pool:
        reads = dict(
            zip(labels, pool.map(lambda label: _read(config, label, timeout), labels))
        )

    handover, handover_error = reads[config.label]
    in_flight_numbers = set(spend.in_flight) if spend is not None else set()
    eligible, blocked, in_flight = [], [], []
    for record in handover:
        number = record.get("number")
        attempts = (
            spend.attempts(number, record.get("labeledAt"))
            if spend is not None
            else 0
        )
        reason = cycle.eligibility(record, config, attempts)
        if number in in_flight_numbers:
            # The lock the Selector itself is holding, which exists before any
            # Proposal does. Read first because it outranks whatever the
            # tracker still says: an issue being worked right now is not
            # eligible for picking, however eligible it looks.
            in_flight.append(_card(record, (
                DISPATCHED_REASON,
                "dispatched by the Selector; no outcome journaled yet",
            )))
        elif reason is None:
            eligible.append(_card(record, None))
        elif reason[0] == IN_FLIGHT_REASON:
            in_flight.append(_card(record, reason))
        else:
            blocked.append(_card(record, reason))

    review, review_error = reads[config.review_label]
    human, human_error = reads[config.human_label]
    return {
        # The Journal's half was missing, so the two columns it decides -
        # eligible and blocked - are a reading of the tracker alone. Said on
        # the board rather than inferred from the error banner above it,
        # because an issue whose retry budget is spent looks Eligible without
        # it and that is the one card an operator would act on.
        "blind": spend is None,
        "columns": [
            _column("eligible", "eligible", config.label,
                    "lowest first; the top card is what the next cycle picks",
                    eligible, handover_error),
            _column("blocked", "blocked", config.label,
                    "labeled, and passed over - each card with the reason the"
                    " Selector journals for it",
                    blocked, handover_error),
            _column("in-flight", "in flight", None,
                    "being worked: an open Proposal, or a dispatch the"
                    " Selector has not recorded an outcome for",
                    in_flight, handover_error),
            _column("awaiting-review", config.review_label,
                    config.review_label,
                    "a green Proposal is open and waiting on you",
                    review, review_error),
            _column("ready-for-human", config.human_label,
                    config.human_label,
                    "the Selector gave up or the checks went red; it will not"
                    " be picked again under this Handover",
                    human, human_error),
        ],
    }
