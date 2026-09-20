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
`drain.eligibility` - imported, not reimplemented. A page that decided for
itself which issues were Eligible would be a second Selector, and the first
disagreement between them would be a bug in whichever one you did not read
(ADR 0015: the page is a window and a scribe).

Everything it reaches is the same substitutable tracker command the cycle
reads (`SELECTOR_TRACKER_COMMAND`), asked once per label per Target. Each
card names the repository it was read from, so two Targets sharing an
issue number stay distinguishable.
"""
from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import drain  # noqa: E402
import doing  # noqa: E402
from targets import Config  # noqa: E402

# The one skip reason that is not a blockage but a state: an issue with an
# open Proposal is being worked, which is a column of its own on the board
# even though the cycle records it as one more reason to pass over.
PROPOSAL_OPEN_REASON = "proposal-open"

# The reason a card carries when the Journal, not the tracker, is what puts it
# in flight: a dispatch with no outcome. Not a `drain.eligibility` reason -
# per-issue Eligibility does not know about the in-flight lock, the cycle
# halts on it - so it is named here, and named for the halt the cycle
# journals so that the board and a `cycle.finished` row use one word.
DISPATCHED_REASON = "run-in-flight"


def _read(config: Config, label: str, timeout: float):
    """One label's queue, or the reason it could not be read.

    A failure is caught per label rather than for the board as a whole: three
    labels are three reads, and one unreachable column is not a reason to
    render none of them.
    """
    try:
        return drain.fetch_queue(config, label=label, timeout=timeout), None
    except drain.CycleFailed as exc:
        return [], str(exc)


def _has_conflicting_proposal(record: dict) -> bool:
    return any(doing.is_conflicting(p) for p in record.get("proposals") or [])


def _conflicting_proposal_reason(record: dict) -> tuple[str, str] | None:
    for p in record.get("proposals") or []:
        if doing.is_conflicting(p):
            target = f"#{p['number']}" if p.get("number") else (p.get("url") or "proposal")
            return ("conflicting", f"proposal {target} has merge conflicts with base")
    return None


def _card(
    record: dict,
    reason: tuple[str, str] | None = None,
    *,
    conflicting: bool | None = None,
    repo: str | None = None,
) -> dict:
    """One issue, as a card: what it is, and - when it is not Eligible - the
    reason the Selector would journal for it, in that reason's own words."""
    is_conflict = (
        _has_conflicting_proposal(record) if conflicting is None else conflicting
    )
    if reason is None and is_conflict:
        reason = _conflicting_proposal_reason(record)
    return {
        "number": record.get("number"),
        "title": record.get("title"),
        "url": record.get("url"),
        "repo": repo,
        "reason": reason[0] if reason else None,
        "detail": reason[1] if reason else None,
        "conflicting": is_conflict,
        # Named, not counted. `blockedBy` is the count Eligibility decides on;
        # this is the tracker's list of which issues they are, which is the
        # only form of the answer that lets the reader go and look at one.
        "blockers": record.get("blockers") or [],
        # How many the tracker counted, so the card can own up to a list that
        # is shorter than it. The count is unbounded and the naming query is
        # capped, so the two CAN disagree - and a card that showed the short
        # list alone would be a board quietly answering "blocked by what?"
        # with most of the answer.
        "unnamed_blockers": max(
            0,
            int(record.get("blockedBy") or 0) - len(record.get("blockers") or []),
        ),
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


# The five columns, named once. `unconfigured` renders the same board with an
# error in every column, so an instance with no targets file gets the page it
# always gets and one sentence saying what is missing - rather than a 500,
# which is what an unconfigured window used to be.
_COLUMN_KEYS = (
    "eligible", "blocked", "in-flight", "awaiting-review", "ready-for-human",
)


def unconfigured(error: str) -> dict:
    """The board an instance that is not configured yet can still render."""
    return {
        "blind": True,
        "columns": [
            _column(key, key.replace("-", " "), None, "", [], error)
            for key in _COLUMN_KEYS
        ],
    }


def combined(configs: tuple[Config, ...], spend=None,
             *, timeout: float | None = None) -> dict:
    """Every Target's queue, in one board. Each card names its repo.

    File order: a Cycle works the stanzas in this order, and the board
    lists them the same way so the top eligible card of the first Target
    is still what the next cycle picks first.

    `first_review` is that Target's awaiting-review column, snapshotted
    before later Targets are merged in, so the review-capacity strip can
    still report one cap without a second Target's queue spending it.
    """
    pieces = [board(config, spend, timeout=timeout) for config in configs]
    if not pieces:
        return {
            "blind": spend is None, "columns": [],
            "first_review": None,
        }
    columns = pieces[0]["columns"]
    first_review = next(
        c for c in columns if c["key"] == "awaiting-review"
    )
    # Copy before merging later Targets into the same column lists:
    # the strip reports the first Target's capacity, not the merged
    # review queue.
    first_review = {
        "cards": list(first_review["cards"]),
        "error": first_review["error"],
    }
    if len(pieces) > 1:
        for col in columns:
            if col["error"]:
                col["error"] = f"{configs[0].task_repo}: {col['error']}"
        for config, one in zip(configs[1:], pieces[1:]):
            for dest, src in zip(columns, one["columns"]):
                dest["cards"].extend(src["cards"])
                if src["error"]:
                    note = f"{config.task_repo}: {src['error']}"
                    dest["error"] = (
                        f"{dest['error']}; {note}" if dest["error"] else note
                    )
    return {
        "blind": spend is None,
        "columns": columns,
        "first_review": first_review,
    }


def board(config: Config, spend=None, *, timeout: float | None = None):
    """The five columns, read from the tracker now.

    `spend` is the Journal's half of Eligibility - the retry budget and the
    in-flight lock. It is optional because the board must still render when
    the Journal is unreachable, and it is passed in rather than read here so
    that the page opens the Journal once.
    """
    if timeout is None:
        # Read off Config like every other SELECTOR_* value, rather than out
        # of the environment here: one place to look when configuration moves.
        timeout = config.board_timeout_seconds
    labels = (config.label, config.review_label, config.human_label)
    # Concurrently: three reads of a remote tracker, one page request. They
    # are independent, and serialised they are three round trips deep.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(labels)) as pool:
        reads = dict(
            zip(labels, pool.map(lambda label: _read(config, label, timeout), labels))
        )

    handover, handover_error = reads[config.label]
    eligible, blocked, in_flight = [], [], []

    def card(record, reason=None):
        return _card(record, reason, repo=config.task_repo)

    for record in handover:
        number = record.get("number")
        labeled_at = record.get("labeledAt")
        attempts = (
            spend.attempts(number, labeled_at, repo=config.task_repo)
            if spend is not None
            else 0
        )
        last_failed = (
            spend.last_attempt_failed(
                number, labeled_at, repo=config.task_repo
            )
            if spend is not None
            else False
        )
        reason = drain.eligibility(record, config, attempts, last_failed)
        if (
            spend is not None
            and number is not None
            and spend.holds(number, config.task_repo)
        ):
            # The lock the Selector itself is holding, which exists before any
            # Proposal does. Read first because it outranks whatever the
            # tracker still says: an issue being worked right now is not
            # eligible for picking, however eligible it looks.
            in_flight.append(card(record, (
                DISPATCHED_REASON,
                "dispatched by the Selector; no outcome journaled yet",
            )))
        elif reason is None:
            eligible.append(card(record, None))
        elif reason[0] == PROPOSAL_OPEN_REASON:
            in_flight.append(card(record, reason))
        else:
            blocked.append(card(record, reason))

    # An issue carrying two of the three labels at once is a tracker state
    # the Selector's own swaps never produce - it removes the Handover label
    # as it adds the next one - but a hand-labeled issue can be in it, and a
    # card drawn in two columns would make the board report a queue deeper
    # than the queue. Shown once, in the column the Handover lens put it in,
    # because that is the lens this board is for.
    placed = {card["number"] for card in eligible + blocked + in_flight}
    review, review_error = reads[config.review_label]
    review = [r for r in review if r.get("number") not in placed]
    human, human_error = reads[config.human_label]
    human = [
        r for r in human
        if r.get("number") not in placed
        and r.get("number") not in {c.get("number") for c in review}
    ]
    review_cards = [card(r) for r in review]
    human_cards = [card(r) for r in human]
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
            # "blocked" is the spec's word for this column and it is kept,
            # but it is only true of some of its cards: a blocking edge is
            # waiting on other work, while a missing section, an
            # unallowlisted labeler and a spent retry budget are all waiting
            # on the operator. The note says so, because a heading alone
            # would tell him three of these are somebody else's turn.
            _column("blocked", "blocked", config.label,
                    "labeled, and not Eligible - waiting on other work, or"
                    " waiting on you. Each card carries the reason the"
                    " Selector journals for it",
                    blocked, handover_error),
            _column("in-flight", "in flight", None,
                    "being worked: an open Proposal that is not a leftover"
                    " failed-attempt draft, or a dispatch the Selector has"
                    " not recorded an outcome for",
                    in_flight, handover_error),
            _column("awaiting-review", config.review_label,
                    config.review_label,
                    "a green Proposal is open and waiting on you",
                    review_cards, review_error),
            _column("ready-for-human", config.human_label,
                    config.human_label,
                    "the Selector gave up or the checks went red; it will not"
                    " be picked again under this Handover",
                    human_cards, human_error),
        ],
    }
