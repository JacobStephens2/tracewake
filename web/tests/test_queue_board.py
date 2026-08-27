"""The queue board on /loop (#158), at HTTP level over a scripted tracker.

The board is the one part of this page that is not read from the Journal: it
is the tracker's own state, fetched when the page is requested and columned by
the same Eligibility predicate the Selector picks with. So these tests script
the tracker rather than seeding rows, and assert on what the columns say.
"""
from fastapi.testclient import TestClient

from app import app
from conftest import column

client = TestClient(app)


def test_the_board_columns_the_queue_by_the_selectors_own_eligibility(db, tracker):
    tracker.queue(
        "ready-for-agent",
        [
            tracker.issue(101),
            tracker.issue(102, blockedBy=1, blockers=[
                {"number": 99, "title": "The migration lands first",
                 "url": "https://example.invalid/99"},
            ]),
            tracker.issue(103, openSubIssues=2),
            tracker.issue(104, proposals=[
                {"number": 7, "url": "https://example.invalid/pull/7",
                 "state": "OPEN"},
            ]),
        ],
    )
    tracker.queue("awaiting-review", [tracker.issue(201)])
    tracker.queue("ready-for-human", [tracker.issue(301)])

    body = client.get("/loop").text

    assert "#101" in column(body, "eligible")
    assert "#102" in column(body, "blocked")
    assert "#103" in column(body, "blocked")
    assert "#104" in column(body, "in-flight")
    assert "#201" in column(body, "awaiting-review")
    assert "#301" in column(body, "ready-for-human")
    # The whole queue, not the slice one cycle happened to consider.
    assert tracker.labels_asked() == [
        "awaiting-review", "ready-for-agent", "ready-for-human"
    ]


def test_a_blocked_card_names_its_open_blockers(db, tracker):
    """The viewing criterion: a count is not an answer to "blocked by what?"."""
    tracker.queue(
        "ready-for-agent",
        [
            tracker.issue(646, blockedBy=1, blockers=[
                {"number": 645, "title": "Widen the sync window",
                 "url": "https://example.invalid/645"},
            ]),
        ],
    )
    blocked = column(client.get("/loop").text, "blocked")
    assert "#645" in blocked
    assert "Widen the sync window" in blocked
    assert "https://example.invalid/645" in blocked


def test_the_board_reads_the_same_reasons_the_journal_records(db, tracker):
    """Each blocked card carries the reason string the Selector journals, so
    a card on the board and a skip row in a cycle read the same word."""
    tracker.queue(
        "ready-for-agent",
        [
            tracker.issue(105, labeledBy="somebody-else"),
            tracker.issue(106, body="## Problem\n\nNo criteria here.\n"),
        ],
    )
    blocked = column(client.get("/loop").text, "blocked")
    assert "labeler-not-allowlisted" in blocked
    assert "missing-section" in blocked


def test_the_retry_budget_the_journal_holds_reaches_the_board(db, tracker, dispatch):
    """Eligibility is a function of the Journal as well as the tracker: an
    issue that has spent its retry budget is not Eligible, and a board that
    read only the tracker would show it as the next pick."""
    labeled = "2026-08-01T00:00:00Z"
    for _ in range(2):
        dispatch(db, 107, outcome="agent-failed")
    tracker.queue("ready-for-agent", [tracker.issue(107, labeledAt=labeled)])

    body = client.get("/loop").text
    assert "#107" in column(body, "blocked")
    assert "attempts-exhausted" in column(body, "blocked")


def test_a_dispatch_with_no_outcome_shows_as_in_flight(db, tracker, dispatch):
    """The Selector's in-flight lock is a dispatch with no outcome, and it
    holds before any Proposal exists. An issue under that lock is in flight
    whatever the tracker still says about it."""
    dispatch(db, 108)
    tracker.queue("ready-for-agent", [tracker.issue(108)])

    body = client.get("/loop").text
    assert "#108" in column(body, "in-flight")
    assert "#108" not in column(body, "eligible")


def test_an_empty_column_says_so_rather_than_vanishing(db, tracker):
    body = client.get("/loop").text
    assert "nothing" in column(body, "eligible").lower()


def test_a_tracker_that_cannot_be_read_does_not_take_the_page_down(db, tracker):
    tracker.fail("gh: could not resolve host github.com")
    resp = client.get("/loop")
    assert resp.status_code == 200
    board = column(resp.text, "eligible")
    assert "could not resolve host" in board


def test_the_label_columns_follow_the_labels_the_selector_is_configured_with(
    db, tracker, monkeypatch
):
    """The columns are the tracker's labels, not words this page invented. So
    a Selector configured for a differently-named review queue gets a board
    that names it - one that still said `awaiting-review` would be describing
    a label the tracker does not have."""
    monkeypatch.setenv("SELECTOR_REVIEW_LABEL", "needs-jacob")
    tracker.queue("needs-jacob", [tracker.issue(401)])

    body = client.get("/loop").text
    assert "needs-jacob" in column(body, "awaiting-review")
    assert "#401" in column(body, "awaiting-review")


def test_a_card_owns_up_to_blockers_the_tracker_did_not_name(db, tracker):
    """The naming query is capped and the count is not, so the two can
    disagree. A card that showed the short list alone would be answering
    "blocked by what?" with most of the answer and no sign of the rest."""
    tracker.queue(
        "ready-for-agent",
        [
            tracker.issue(109, blockedBy=3, blockers=[
                {"number": 90, "title": "The one it named",
                 "url": "https://example.invalid/90"},
            ]),
        ],
    )
    blocked = column(client.get("/loop").text, "blocked")
    assert "#90" in blocked
    assert "2" in blocked and "did not name" in blocked


def test_an_issue_carrying_two_labels_is_drawn_once(db, tracker):
    """A hand-labeled issue can hold both; the Selector's own swaps cannot
    produce it. Drawn twice, the board would report a queue deeper than the
    queue."""
    tracker.queue("ready-for-agent", [tracker.issue(110)])
    tracker.queue("awaiting-review", [tracker.issue(110)])

    body = client.get("/loop").text
    assert "#110" in column(body, "eligible")
    assert "#110" not in column(body, "awaiting-review")
