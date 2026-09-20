"""The queue board on `/` (#158), at HTTP level over a scripted tracker.

The board is the one part of this page that is not read from the Journal: it
is the tracker's own state, fetched when the page is requested and columned by
the same Eligibility predicate the Selector picks with. So these tests script
the tracker rather than seeding rows, and assert on what the columns say.
"""
import pytest
from fastapi.testclient import TestClient

from app import app
import fixtures
from conftest import column

client = TestClient(app)


def test_a_queue_card_names_the_target_it_was_read_from(db, tracker):
    """A card that only named the issue would not tell two Targets apart.
    The board reads every Target; the card says which."""
    tracker.queue("ready-for-agent", [tracker.issue(80)])
    eligible = column(client.get("/").text, "eligible")
    assert "#80" in eligible
    assert "acme/widgets" in eligible


def test_the_board_shows_every_targets_cards_each_named_by_repo(
    db, tracker, monkeypatch, tmp_path
):
    """The queue is every Target's, not the first stanza's. Two Targets can
    share an issue number; the repo note is what tells the cards apart."""
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/widgets"},
            {"repo": "acme/voice-agent"},
        ),
    )
    tracker.queue_for("acme/widgets", "ready-for-agent", [tracker.issue(80)])
    tracker.queue_for(
        "acme/voice-agent",
        "ready-for-agent",
        [tracker.issue(80, title="Enroll and contract-test Grok Build")],
    )
    eligible = column(client.get("/").text, "eligible")
    assert eligible.count("#80") == 2
    assert "acme/widgets" in eligible
    assert "acme/voice-agent" in eligible
    assert "Enroll and contract-test Grok Build" in eligible


def test_an_in_flight_lock_on_one_target_does_not_move_the_others_card(
    db, tracker, monkeypatch, tmp_path
):
    """Eligibility's lock is per (repo, issue). The same number Eligible on
    one Target and in flight on another is two cards, not one in-flight
    card drawn twice."""
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/widgets"},
            {"repo": "acme/voice-agent"},
        ),
    )
    import events
    import journal
    with journal.connect(db) as conn:
        journal.append(conn, *events.run_dispatched(
            cycle=None, issue=80, title=None, url=None,
            task_ref="acme/voice-agent#80", attempt=None, branch=None,
            area=None, check=None, kept_progress=None,
        ))
    tracker.queue_for("acme/widgets", "ready-for-agent", [tracker.issue(80)])
    tracker.queue_for("acme/voice-agent", "ready-for-agent", [tracker.issue(80)])
    body = client.get("/").text
    eligible = column(body, "eligible")
    in_flight = column(body, "in-flight")
    assert "#80" in eligible
    assert "acme/widgets" in eligible
    assert "#80" in in_flight
    assert "acme/voice-agent" in in_flight
    assert "acme/voice-agent" not in eligible
    assert "acme/widgets" not in in_flight


def test_a_second_targets_review_queue_does_not_spend_the_firsts_cap(
    db, tracker, monkeypatch, tmp_path
):
    """The strip is one Target's remaining capacity. Merging the board must
    not let another Target's awaiting-review cards spend it."""
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "targets.toml",
            {"repo": "acme/widgets"},
            {"repo": "acme/voice-agent"},
        ),
    )
    tracker.queue_for(
        "acme/voice-agent",
        "awaiting-review",
        [tracker.issue(80), tracker.issue(81)],
    )
    body = client.get("/").text
    assert "#80" in column(body, "awaiting-review")
    assert "acme/voice-agent" in column(body, "awaiting-review")
    assert "20 remaining" in body
    assert "0 of 20" in body


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

    body = client.get("/").text

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
    blocked = column(client.get("/").text, "blocked")
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
    blocked = column(client.get("/").text, "blocked")
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

    body = client.get("/").text
    assert "#107" in column(body, "blocked")
    assert "attempts-exhausted" in column(body, "blocked")


def test_a_leftover_failed_attempt_draft_is_eligible_not_in_flight(
    db, tracker, dispatch
):
    """#102. The leftover draft of a failed attempt is the branch the retry
    continues. The board columns by Eligibility, so that issue is Eligible
    rather than in-flight."""
    labeled = "2026-08-01T00:00:00Z"
    dispatch(db, 109, outcome="agent-failed")
    tracker.queue(
        "ready-for-agent",
        [tracker.issue(109, labeledAt=labeled, proposals=[
            {"number": 13, "url": "https://example.invalid/pull/13",
             "state": "OPEN", "isDraft": True},
        ])],
    )

    body = client.get("/").text
    assert "#109" in column(body, "eligible")
    assert "#109" not in column(body, "in-flight")


def test_a_dispatch_with_no_outcome_shows_as_in_flight(db, tracker, dispatch):
    """The Selector's in-flight lock is a dispatch with no outcome, and it
    holds before any Proposal exists. An issue under that lock is in flight
    whatever the tracker still says about it."""
    dispatch(db, 108)
    tracker.queue("ready-for-agent", [tracker.issue(108)])

    body = client.get("/").text
    assert "#108" in column(body, "in-flight")
    assert "#108" not in column(body, "eligible")


def test_an_empty_column_says_so_rather_than_vanishing(db, tracker):
    body = client.get("/").text
    assert "nothing" in column(body, "eligible").lower()


def test_a_tracker_that_cannot_be_read_does_not_take_the_page_down(db, tracker):
    tracker.fail("gh: could not resolve host github.com")
    resp = client.get("/")
    assert resp.status_code == 200
    board = column(resp.text, "eligible")
    assert "could not resolve host" in board


def test_front_page_is_the_queue_board(db, tracker):
    tracker.queue("ready-for-agent", [tracker.issue(101)])
    resp = client.get("/")
    assert resp.status_code == 200
    assert "terminal.css" in resp.text
    assert "#101" in column(resp.text, "eligible")


def test_the_label_columns_follow_the_labels_the_selector_is_configured_with(
    db, tracker, monkeypatch, tmp_path
):
    """The columns are the tracker's labels, not words this page invented. So
    a target whose stanza renames the review queue gets a board that names it
    - one that still said `awaiting-review` would be describing a label the
    tracker does not have."""
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(
            tmp_path / "renamed-targets.toml",
            {"labels": {"review": "second-look"}},
        ),
    )
    tracker.queue("second-look", [tracker.issue(401)])

    body = client.get("/").text
    assert "second-look" in column(body, "awaiting-review")
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
    blocked = column(client.get("/").text, "blocked")
    assert "#90" in blocked
    assert "2" in blocked and "did not name" in blocked


def test_an_issue_carrying_two_labels_is_drawn_once(db, tracker):
    """A hand-labeled issue can hold both; the Selector's own swaps cannot
    produce it. Drawn twice, the board would report a queue deeper than the
    queue."""
    tracker.queue("ready-for-agent", [tracker.issue(110)])
    tracker.queue("awaiting-review", [tracker.issue(110)])

    body = client.get("/").text
    assert "#110" in column(body, "eligible")
    assert "#110" not in column(body, "awaiting-review")


def test_conflicting_proposal_shows_as_conflicting_on_board(db, tracker):
    """AC 2: A conflicting Proposal is not updated and is shown as conflicting on the board."""
    tracker.queue(
        "awaiting-review",
        [
            tracker.issue(
                201,
                proposals=[
                    {
                        "number": 15,
                        "url": "https://example.invalid/pull/15",
                        "state": "OPEN",
                        "mergeable": "CONFLICTING",
                        "mergeStateStatus": "DIRTY",
                    }
                ],
            ),
            tracker.issue(
                202,
                proposals=[
                    {
                        "number": 16,
                        "url": "https://example.invalid/pull/16",
                        "state": "OPEN",
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                    }
                ],
            ),
        ],
    )
    body = client.get("/").text
    awaiting = column(body, "awaiting-review")
    assert "#201" in awaiting
    assert "conflicting" in awaiting
    assert "#202" in awaiting


def test_a_reconciled_proposal_no_longer_shows_as_conflicting(db, tracker):
    """The badge derives live from what the forge reports: once a reconcile
    Run has brought the Proposal current and the tracker reports it
    mergeable, the flag clears with no Journal row involved (#34)."""
    tracker.queue(
        "awaiting-review",
        [
            tracker.issue(
                203,
                proposals=[
                    {
                        "number": 17,
                        "url": "https://example.invalid/pull/17",
                        "state": "OPEN",
                        "mergeable": "MERGEABLE",
                        "mergeStateStatus": "CLEAN",
                    }
                ],
            ),
        ],
    )
    body = client.get("/").text
    awaiting = column(body, "awaiting-review")
    assert "#203" in awaiting
    assert "conflicting" not in awaiting

