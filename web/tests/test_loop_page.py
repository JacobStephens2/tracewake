"""The home board at HTTP level: FastAPI test client over a seeded Journal."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Json

import journal
from app import app
from conftest import csrf_from

client = TestClient(app)


def test_loop_page_renders_journal_newest_first(db):
    # Two real kinds - this file once invented `cycle.summary` here, which no
    # writer has ever produced, and nothing could notice. `cycle.skipped`
    # keeps the old fixture's property: it carries no `cycle` key, so it
    # reaches the event table and builds no card.
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.skipped", {"reason": "cycle-in-progress"})
        journal.append(conn, "run.dispatched", {"issue": 646})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "terminal.css" in resp.text
    body = resp.text
    assert body.index("run.dispatched") < body.index("cycle.skipped")
    assert "646" in body


def test_loop_page_serves_when_journal_is_unreachable(monkeypatch):
    """Sessions and the Journal share a database. A DSN that cannot be
    reached cannot validate a session either, so the visitor is sent to
    sign-in rather than served a 500."""
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_test_no_such_db")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert "/sign-in" in resp.headers["location"]


def test_terminal_css_is_served():
    resp = client.get("/static/terminal.css")
    assert resp.status_code == 200
    assert "--global-font-size" in resp.text


def test_home_renders_queue_board():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "terminal.css" in resp.text
    assert "Selector Journal" in resp.text


def test_home_page_title_is_tracewake():
    """The tab names the product. 'The Loop' is one half of it (CONTEXT.md)."""
    assert "<title>Tracewake</title>" in client.get("/").text


def test_clicking_pause_raises_the_banner_and_resume_clears_it(db):
    token = csrf_from(client.get("/").text)
    paused = client.post(
        "/loop/pause",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )

    assert paused.status_code == 200
    assert 'data-selector-pause="paused"' in paused.text
    assert "Resume dispatch" in paused.text
    assert 'data-selector-pause="paused"' in client.get("/").text

    token = csrf_from(client.get("/").text)
    resumed = client.post(
        "/loop/resume",
        data={"csrf_token": token},
        headers={"HX-Request": "true"},
    )

    assert resumed.status_code == 200
    assert 'data-selector-pause="paused"' not in resumed.text
    assert "Pause dispatch" in resumed.text
    assert 'data-selector-pause="paused"' not in client.get("/").text


def test_the_pause_control_posts_back_to_the_same_mounted_instance(db):
    prefixed = TestClient(app, root_path="/loop-staging")
    body = prefixed.get("/").text

    assert 'hx-post="/loop-staging/loop/pause"' in body


def test_the_cycle_card_shows_the_pick_and_every_skip_with_its_reason(db):
    """Issue #153's viewing criterion, at HTTP level over a seeded Journal."""
    with journal.connect(db) as conn:
        cycle = journal.append(
            conn,
            "cycle.started",
            {"repo": "acme/widgets", "label": "ready-for-agent", "dry_run": True},
        )
        journal.append(
            conn,
            "issue.skipped",
            {
                "cycle": cycle,
                "number": 646,
                "url": "https://example.invalid/646",
                "reason": "blocked-by-open-dependency",
                "detail": "1 open blocking edge(s) on the tracker",
            },
        )
        journal.append(
            conn,
            "cycle.picked",
            {
                "cycle": cycle,
                "number": 645,
                "title": "Widen the sync window",
                "url": "https://example.invalid/645",
                "area": "The nightly sync script",
                "check": "scripts/check.sh",
            },
        )
        journal.append(
            conn,
            "cycle.finished",
            {
                "cycle": cycle,
                "considered": 2,
                "eligible": [645],
                "skipped": {"blocked-by-open-dependency": 1},
                "picked": 645,
                "halted": None,
                "awaiting_review": 0,
                "review_cap": 20,
                "dry_run": True,
            },
        )
    body = client.get("/").text
    assert "Widen the sync window" in body
    assert "The nightly sync script" in body
    assert "blocked-by-open-dependency" in body
    assert "1 open blocking edge(s) on the tracker" in body
    assert "dry run" in body
    assert "0/20" in body, "the review-capacity budget is shown"


def test_a_cycle_that_picked_nothing_says_why(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": True})
        journal.append(
            conn,
            "cycle.finished",
            {
                "cycle": cycle,
                "considered": 1,
                "eligible": [],
                "skipped": {},
                "picked": None,
                "halted": "run-in-flight",
                "awaiting_review": 1,
                "review_cap": 20,
                "dry_run": True,
            },
        )
    body = client.get("/").text
    assert "picked nothing" in body
    assert "run-in-flight" in body


def test_a_paused_cycle_card_says_paused(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        journal.append(
            conn,
            "cycle.finished",
            {
                "cycle": cycle,
                "considered": 1,
                "eligible": [645],
                "skipped": {},
                "picked": None,
                "halted": "paused",
                "awaiting_review": 0,
                "review_cap": 20,
                "dry_run": False,
            },
        )

    body = client.get("/").text
    assert "picked nothing" in body
    assert "paused" in body


def test_events_outside_a_cycle_still_reach_the_page(db):
    """The raw event table stays the Journal's unabridged view: a hand
    `psql` INSERT with no cycle id must not vanish behind the cards."""
    with journal.connect(db) as conn:
        journal.append(conn, "test.hand", {"note": "appended by hand"})
    body = client.get("/").text
    assert "test.hand" in body
    assert "appended by hand" in body


def test_a_cycle_cut_in_half_by_the_read_limit_is_not_rendered(db):
    """The Journal read is capped, so the oldest cycle on a busy page has
    lost its `cycle.started` row. A card built from what survived would
    render as a nameless, timeless cycle instead of as the absence it is."""
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "issue.skipped",
            {
                "cycle": 999999,
                "number": 646,
                "reason": "blocked-by-open-dependency",
                "detail": "from a cycle whose start scrolled off the page",
            },
        )
    body = client.get("/").text
    assert "from a cycle whose start scrolled off" not in body.split("Every event")[0]
    assert "from a cycle whose start scrolled off" in body, (
        "the raw event table still shows it"
    )


# --- The Run card (#154) ----------------------------------------------------


def _dispatch_row(conn, cycle, **over):
    payload = {
        "cycle": cycle,
        "issue": 645,
        "title": "Widen the sync window",
        "url": "https://example.invalid/645",
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "area": "The nightly sync script",
        "attempt": 1,
    }
    payload.update(over)
    return journal.append(conn, "run.dispatched", payload)


def test_a_run_in_flight_has_a_card_of_its_own(db):
    """Issue #154's viewing criterion, first half: the in-flight card appears
    while the Run is running, which is the whole of what the page can show
    before the box says anything."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
    body = client.get("/").text
    assert "in flight" in body
    assert "loop/645-the-nightly-sync-script" in body
    assert "Widen the sync window" in body


def test_the_card_ends_showing_its_proposal_link(db):
    """The second half: when the outcome lands, the same Run reads as ended
    and carries the Proposal a reviewer opens."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        journal.append(
            conn,
            "run.outcome",
            {
                "cycle": cycle,
                "issue": 645,
                "attempt": 1,
                "outcome": "iteration-cap",
                "ended_by": "iteration-cap",
                "exit": 0,
                "iterations": 5,
                "faults": "none",
                "notified": "sent",
                "proposal": "https://github.invalid/acme/widgets/pull/12",
            },
        )
    body = client.get("/").text
    runs = body.split("<h2>Cycles</h2>")[0]
    assert 'class="run run-in-flight"' not in runs
    assert "iteration-cap" in runs
    assert "https://github.invalid/acme/widgets/pull/12" in runs


def test_a_retry_is_its_own_card(db):
    """Paired by issue AND attempt. A retry of an issue that already has an
    outcome is its own Run, and folding the two together would show the first
    Run's Proposal beside the second Run's state."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "agent-failed", "exit": 4, "iterations": 1,
             "faults": "agent-failed", "notified": "sent",
             "proposal": "https://github.invalid/acme/widgets/pull/12"},
        )
        _dispatch_row(conn, cycle, attempt=2)
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert runs.count('<section class="run') == 2
    assert 'class="run run-in-flight"' in runs
    assert "agent-failed" in runs
    assert "attempt 2" in runs


def test_a_dispatch_that_started_no_run_says_so(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "dispatch-failed", "error": "ssh: no route to host"},
        )
    body = client.get("/").text
    assert "no Run was started" in body
    assert "ssh: no route to host" in body


def test_a_returned_issue_is_marked_on_the_skip_that_returned_it(db):
    """The loud skip, seen: the comment and the label swap are one fact about
    one issue, so the page shows them on the skip row rather than in a list
    of their own."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        journal.append(
            conn, "issue.skipped",
            {"cycle": cycle, "number": 596, "url": "https://example.invalid/596",
             "reason": "missing-section", "detail": "no `Owning area` section"},
        )
        journal.append(
            conn, "issue.returned",
            {"cycle": cycle, "number": 596, "reason": "missing-section",
             "added_label": "needs-info", "removed_label": "ready-for-agent"},
        )
    body = client.get("/").text
    assert "commented, swapped to needs-info" in body


def test_a_return_that_failed_is_not_shown_as_a_return(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        journal.append(
            conn, "issue.skipped",
            {"cycle": cycle, "number": 596, "url": "https://example.invalid/596",
             "reason": "missing-section", "detail": "no `Owning area` section"},
        )
        journal.append(
            conn, "issue.return-failed",
            {"cycle": cycle, "number": 596, "error": "GitHub refused the label swap"},
        )
    body = client.get("/").text
    assert "could not hand it back" in body
    assert "GitHub refused the label swap" in body
    assert "commented, swapped to needs-info" not in body


def test_a_run_whose_dispatch_scrolled_off_the_page_is_not_rendered(db):
    """Same rule as the cycle cards: an outcome with no dispatch above it is
    an absence, not a nameless Run."""
    with journal.connect(db) as conn:
        journal.append(
            conn, "run.outcome",
            {"cycle": 999999, "issue": 111, "attempt": 1,
             "outcome": "iteration-cap", "proposal": "https://example.invalid/pull/1"},
        )
    body = client.get("/").text
    assert "<h2>Runs</h2>" not in body
    assert "111" in body, "the raw event table still shows it"


# --- Where the outcome put the issue (#155) ---------------------------------


def _ended(conn, cycle, outcome="iteration-cap", **over):
    payload = {
        "cycle": cycle, "issue": 645, "attempt": 1, "outcome": outcome,
        "exit": 0, "iterations": 5, "faults": "none", "notified": "sent",
        "proposal": "https://github.invalid/acme/widgets/pull/12",
    }
    payload.update(over)
    return journal.append(conn, "run.outcome", payload)


def test_a_green_run_shows_the_awaiting_review_badge(db):
    """Issue #155's viewing criterion. The badge carries the label string
    verbatim, because its job is to match what the operator sees on the issue
    - a friendlier word here would be a second vocabulary for one state."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
        journal.append(
            conn, "issue.awaiting-review",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "label": "awaiting-review", "checks": "green",
             "proposal": "https://github.invalid/acme/widgets/pull/12"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-awaiting-review">awaiting-review</span>' in runs
    assert "waiting on you" in runs


def test_a_red_run_names_the_failing_checks_on_its_card(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
        journal.append(
            conn, "issue.handed-to-human",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "label": "ready-for-human", "checks": "red",
             "failing": ["phpunit", "lint"]},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-handed-to-human">ready-for-human</span>' in runs
    assert "phpunit" in runs and "lint" in runs


def test_every_route_name_has_a_badge_rule_in_the_stylesheet():
    """The route names reach the page as CSS classes (`badge-<name>`), and
    the stylesheet cannot import a constant - so this is the pin that makes a
    Route rename fail a suite instead of quietly unstyling a card."""
    from pathlib import Path

    import events
    css = (Path(__file__).resolve().parents[1] / "static" / "loop.css").read_text()
    missing = [name for name in events.ROUTE_NAMES
               if f".badge-{name}" not in css]
    assert not missing, f"loop.css has no rule for badge-{missing}"


def test_a_run_whose_checks_never_ran_is_not_called_red(db):
    """`none` and `red` are opposite facts about how far a Proposal was
    verified - the Selector's own comment on the issue says so - and the card
    must not contradict the comment. This rendered as "The Proposal's checks
    are red: ." until the checks vocabulary got one owner."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
        journal.append(
            conn, "issue.handed-to-human",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "label": "ready-for-human", "checks": "none", "failing": []},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "checks are red" not in runs
    assert "No check ran" in runs


def test_a_route_row_with_no_outcome_falls_back_to_the_bound(db):
    """Hand appends are legitimate and readers are total over thin rows, so a
    route row that carries no `outcome` must not pin the card's outcome to
    None - the run.outcome row's bound stands in, as it did before the
    vocabulary landed."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
        journal.append(
            conn, "issue.handed-to-human",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "label": "ready-for-human", "checks": "red", "failing": []},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert ">None<" not in runs
    assert "iteration-cap" in runs


def test_a_run_waiting_on_a_retry_says_so(db):
    """A retry has no label swap, so the card has to say why an issue with a
    failed Run is still in the agent queue."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle, outcome="agent-failed", exit=4)
        journal.append(
            conn, "issue.retrying",
            {"cycle": cycle, "issue": 645, "attempt": 1, "of": 2,
             "outcome": "agent-failed"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-retrying">retrying</span>' in runs
    assert "ready-for-agent" in runs


def test_a_given_up_run_shows_the_human_label(db):
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=2)
        _ended(conn, cycle, outcome="agent-failed", exit=4, attempt=2)
        journal.append(
            conn, "issue.given-up",
            {"cycle": cycle, "issue": 645, "attempt": 2,
             "label": "ready-for-human", "outcome": "agent-failed"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-given-up">ready-for-human</span>' in runs
    assert "will not be dispatched" in runs


def test_a_route_is_paired_to_its_own_attempt(db):
    """Same pairing rule as the outcome. A first attempt that was retried and
    a second that was given up are two cards, and the give-up badge belongs on
    the second."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        _ended(conn, cycle, outcome="agent-failed", exit=4, attempt=1)
        journal.append(
            conn, "issue.retrying",
            {"cycle": cycle, "issue": 645, "attempt": 1, "of": 2,
             "outcome": "agent-failed"},
        )
        _dispatch_row(conn, cycle, attempt=2)
        _ended(conn, cycle, outcome="agent-failed", exit=4, attempt=2)
        journal.append(
            conn, "issue.given-up",
            {"cycle": cycle, "issue": 645, "attempt": 2,
             "label": "ready-for-human", "outcome": "agent-failed"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert runs.count('<section class="run') == 2
    # Newest first, so the given-up card is the one above the retried one.
    given, retried = runs.split('<section class="run')[1:3]
    assert "badge-given-up" in given and "badge-retrying" not in given
    assert "badge-retrying" in retried and "badge-given-up" not in retried


def test_an_unrouted_run_shows_no_label_badge(db):
    """A Run whose bookkeeping the tracker refused has an outcome and no
    route. The card shows what is known rather than inventing a queue."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle)
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "badge-awaiting-review" not in runs
    assert "badge-handed-to-human" not in runs


def test_a_no_proposal_run_reads_as_no_proposal_not_as_its_bound(db):
    """The route renamed the outcome, and the card says what the operator was
    told on the issue. Events arrive newest first, so the `run.outcome` row is
    read after the route's and would otherwise overwrite it with the bound."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _ended(conn, cycle, outcome="iteration-cap", proposal=None)
        journal.append(
            conn, "issue.retrying",
            {"cycle": cycle, "issue": 645, "attempt": 1, "of": 2,
             "outcome": "no-proposal"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "no-proposal" in runs
    assert "iteration-cap" not in runs



# --- Iterations, while the Run runs (#157) ----------------------------------


def _iteration_row(conn, cycle, number, **over):
    payload = {
        "cycle": cycle,
        "issue": 645,
        "attempt": 1,
        "branch": "loop/645-the-nightly-sync-script",
        "task_ref": "acme/widgets#645",
        "iteration": number,
        "started": f"2026-08-27T12:0{number}:05Z",
        "run_started": "2026-08-27T12:00:00Z",
        "agent_exit": 0,
        "exit_note": None,
        "noop": False,
        "head_before": "111111111111",
        "head_after": "222222222222",
        "promise": False,
        "dirty": False,
    }
    payload.update(over)
    return journal.append(conn, "run.iteration", payload)


def test_iterations_appear_on_the_card_of_the_run_in_flight(db):
    """Issue #157's viewing criterion: a Run that has said nothing but its
    Iterations still shows them, which is the whole point of the watcher -
    before this the page had nothing to say for ninety minutes."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _iteration_row(conn, cycle, 1)
        _iteration_row(conn, cycle, 2, noop=True, agent_exit=124,
                       exit_note="killed at its 900s wall clock")
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert 'class="run run-in-flight"' in runs
    assert "killed at its 900s wall clock" in runs
    assert "no-op" in runs


def test_iterations_read_in_the_order_the_run_ran_them(db):
    """The Journal reads newest first, and an Iteration list in that order
    would have the Run counting backwards."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _iteration_row(conn, cycle, 1, head_after="aaaaaaaaaaaa")
        _iteration_row(conn, cycle, 2, head_after="bbbbbbbbbbbb")
        _iteration_row(conn, cycle, 3, head_after="cccccccccccc")
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert runs.index("aaaaaaaaaaaa") < runs.index("bbbbbbbbbbbb") < \
        runs.index("cccccccccccc")


def test_an_iteration_is_shown_on_the_run_that_produced_it(db):
    """Paired by issue AND attempt, like every other row on a card: a retry's
    Iteration 1 is not the first attempt's."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        _iteration_row(conn, cycle, 1, attempt=1, head_after="aaaaaaaaaaaa")
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "agent-failed", "exit": 4, "iterations": 1,
             "faults": "agent-failed"},
        )
        _dispatch_row(conn, cycle, attempt=2)
        _iteration_row(conn, cycle, 1, attempt=2, head_after="bbbbbbbbbbbb")
    cards = client.get("/").text.split("<h2>Cycles</h2>")[0].split(
        '<section class="run'
    )[1:]
    assert len(cards) == 2
    newest, oldest = cards
    assert "bbbbbbbbbbbb" in newest and "aaaaaaaaaaaa" not in newest
    assert "aaaaaaaaaaaa" in oldest and "bbbbbbbbbbbb" not in oldest


def test_a_run_whose_progress_log_could_not_be_read_says_so(db):
    """A Run with no Iterations on its card and a Run whose log the watcher
    could not read look identical otherwise, and they are opposite facts about
    whether anything is happening."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        journal.append(
            conn, "run.watch-failed",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "branch": "loop/645-the-nightly-sync-script",
             "error": "no Progress Log at /home/loop/tourbot/PROGRESS.md"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "could not be read" in runs
    assert "no Progress Log at /home/loop/tourbot/PROGRESS.md" in runs


def test_a_watch_failure_alone_is_not_a_run(db):
    """The same rule every card is built by: a Run whose dispatch row scrolled
    off the read limit is an absence, not a nameless Run."""
    with journal.connect(db) as conn:
        journal.append(
            conn, "run.watch-failed",
            {"issue": 645, "attempt": 1, "error": "the box did not answer"},
        )
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert '<section class="run' not in runs


# --- The status strip (#156) -------------------------------------------------
#
# Unattended operation is a claim, and the strip is where it is proved on the
# page: the timer that will fire next, the budget it will spend from, and what
# the box it dispatches to is holding.
#
# Every assertion here reads the STRIP rather than the page, and that is not
# fussiness: the page ends with a table that dumps every Journal payload
# verbatim, so `assert "8c1f3a90d2" in resp.text` would pass for a strip that
# rendered nothing at all.


def strip(body):
    """The status strip alone, cut out of the page."""
    start = body.index('<section class="strip"')
    return body[start:body.index("</section>", start)]


def _timer_command(tmp_path, text, *, exit_code=0):
    script = tmp_path / "timer.sh"
    script.write_text(
        f"#!/usr/bin/env bash\ncat <<'EOF'\n{text}\nEOF\nexit {exit_code}\n"
    )
    script.chmod(0o755)
    return str(script)


def _running_timer(monkeypatch, tmp_path):
    """A timer that is up, so the tests that are about something else are not
    also about the timer being down."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(
            tmp_path,
            "ActiveState=active\nNextElapseUSecRealtime=Thu 2026-08-27 14:31:00 UTC",
        ),
    )


def test_the_strip_shows_review_capacity(tracker):
    tracker.queue("awaiting-review", [tracker.issue(640), tracker.issue(641)])
    cell = strip(client.get("/").text)
    assert "18 remaining" in cell
    assert "2 of 20" in cell
    assert "awaiting review" in cell


def test_the_strip_says_idle_when_review_cap_reached(db, tracker, monkeypatch, tmp_path):
    _running_timer(monkeypatch, tmp_path)
    tracker.queue("awaiting-review", [tracker.issue(i) for i in range(20)])
    cell = strip(client.get("/").text)
    assert "idle - review cap reached" in cell
    assert "0 remaining" in cell


def test_the_strip_names_the_run_in_flight(db, dispatch, monkeypatch, tmp_path):
    _running_timer(monkeypatch, tmp_path)
    dispatch(db, 646, outcome=None)
    cell = strip(client.get("/").text)
    assert "in flight" in cell
    assert "646" in cell


def test_the_strip_says_idle_when_no_run_is_in_flight(db, monkeypatch, tmp_path):
    _running_timer(monkeypatch, tmp_path)
    assert "idle" in strip(client.get("/").text)


def test_a_selector_that_cannot_run_does_not_read_as_idle(db, monkeypatch, tmp_path):
    """The failure the whole strip exists for. With the timer down nothing
    will start a cycle, and a headline cell reading `idle` would be the page
    reporting business as usual about a dead Selector."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(tmp_path, "ActiveState=inactive\nNextElapseUSecRealtime=n/a"),
    )
    cell = strip(client.get("/").text)
    assert "idle" not in cell
    assert "nothing will start a cycle" in cell


def test_a_run_in_flight_outranks_a_timer_that_is_down(db, dispatch, monkeypatch,
                                                       tmp_path):
    """The cycle holding it is already running and will record its outcome,
    whatever the timer is doing."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(tmp_path, "ActiveState=inactive\nNextElapseUSecRealtime=n/a"),
    )
    dispatch(db, 646, outcome=None)
    assert "in flight: #646" in strip(client.get("/").text)


def test_the_strip_shows_the_next_cycle_the_timer_will_fire(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(
            tmp_path,
            "ActiveState=active\nNextElapseUSecRealtime=Thu 2026-08-27 14:31:00 UTC",
        ),
    )
    # systemd answers in the box's UTC; the page shows the operator's clock.
    assert "Thu 2026-08-27 10:31:00 EDT" in strip(client.get("/").text)


def test_a_timer_that_is_not_running_is_said_so_rather_than_left_blank(
    db, monkeypatch, tmp_path
):
    """The failure this cell exists for: a timer installed and never enabled
    is a Selector that looks quiet and is in fact dead (story 31)."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(tmp_path, "ActiveState=inactive\nNextElapseUSecRealtime=n/a"),
    )
    assert "not running" in strip(client.get("/").text).lower()


def test_the_page_still_renders_when_the_timer_cannot_be_read(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND", _timer_command(tmp_path, "", exit_code=1)
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "unknown" in strip(resp.text).lower()


def test_the_box_card_shows_the_scripts_hash_and_the_agent_version(db):
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {
                "scripts_hash": "8c1f3a90d2",
                "guest_template": "loop-php:1",
                "agent": "claude",
                "agent_version": "2.1.221 (Claude Code)",
            },
        )
    cell = strip(client.get("/").text)
    assert "8c1f3a90d2" in cell
    assert "2.1.221 (Claude Code)" in cell
    # The guest template, and it is asserted separately from the agent for a
    # reason the old fixture hid: both were the string `claude`, so one `in`
    # check passed whichever of the two cells rendered. They are different
    # things - the template is an image now (#164) - and the card has to show
    # the one it says it shows.
    assert "loop-php:1" in cell
    assert "claude" in cell


def test_the_box_card_shows_the_newest_observation(db):
    with journal.connect(db) as conn:
        journal.append(conn, "box.observed", {"scripts_hash": "olderhash1"})
        journal.append(conn, "box.observed", {"scripts_hash": "newerhash2"})
    cell = strip(client.get("/").text)
    assert "newerhash2" in cell
    assert "olderhash1" not in cell


def test_a_fact_the_box_did_not_report_is_blank_rather_than_guessed(db):
    with journal.connect(db) as conn:
        journal.append(
            conn, "box.observed", {"scripts_hash": "deadbeef01", "agent_version": None}
        )
    cell = strip(client.get("/").text)
    assert "deadbeef01" in cell
    assert "not reported" in cell


# --- The model credential's clock on the card (#260) ------------------------
#
# The other three facts say what the box IS. This one says whether it can
# currently do anything, which is why it can take the headline off them.


def _expiring_in(seconds: int) -> str:
    """The instant the box would report for a credential with `seconds` left,
    in the shape the adapter prints."""
    when = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_box_card_shows_when_the_credential_expires_and_what_is_left(db):
    """Both halves. The instant is what the box actually said and is what a
    second reader can check; the remaining time is what the operator opened
    the page to find out. Neither answers on its own. The instant is shown on
    the operator's clock, so the assertion converts the same way app._local
    does."""
    expires = _expiring_in(6 * 3600 + 20 * 60)
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {"scripts_hash": "8c1f3a90d2", "credential_expires_at": expires},
        )
    shown = (
        datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo("America/New_York"))
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    cell = strip(client.get("/").text)
    assert shown in cell
    assert "6h 19m left" in cell or "6h 20m left" in cell


def test_the_remaining_time_is_measured_from_the_request_not_from_the_read(db):
    """The reason the Journal holds an instant rather than a duration. This
    row was written by a cycle that ran hours ago; if the card were replaying
    a recorded "8h left" it would still be saying so now, which is the
    reassuring direction to be wrong in."""
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {"credential_expires_at": _expiring_in(3600)},
        )
    cell = strip(client.get("/").text)
    assert "59m" in cell or "1h 0m" in cell


def test_an_expired_credential_is_visibly_distinct_from_a_live_one(db):
    """The viewing criterion. An operator must not have to open a Run's
    Progress Log to find out the box cannot authenticate."""
    lapsed = _expiring_in(-3600)
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {"scripts_hash": "8c1f3a90d2", "credential_expires_at": lapsed},
        )
    body = client.get("/").text
    cell = strip(body)
    assert "expired" in cell.lower()
    # Distinct in the markup as well as in the words: the alarm class is what
    # the strip's other red cells use, and a difference only a careful reader
    # of the sentence would notice is not a difference on a status card.
    #
    # The class and the words are asserted TOGETHER rather than `"alarm" in
    # body`, which cannot fail: the timer cell on this strip is already in
    # alarm throughout this suite, because no systemd is answering it.
    assert 'alarm">the model credential has expired' in body
    # The rest of the card survives. An operator diagnosing this still needs
    # to know which box he is looking at.
    assert "8c1f3a90d2" in cell


def test_a_live_credential_does_not_raise_the_alarm(db):
    """The complement, and it is the test that would catch the comparison
    being the wrong way round - which would put every healthy box in alarm and
    make the cell one nobody reads."""
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {"scripts_hash": "8c1f3a90d2", "credential_expires_at": _expiring_in(28800)},
        )
    cell = strip(client.get("/").text)
    assert "expired" not in cell.lower()


def test_a_box_that_did_not_report_an_expiry_says_so_rather_than_assuming(db):
    """Absent is not expired and is not fine. A box whose adapter could not
    answer must not be rendered as either - one would page for nothing, the
    other would assert a working login nobody observed."""
    with journal.connect(db) as conn:
        journal.append(conn, "box.observed", {"scripts_hash": "deadbeef01"})
    cell = strip(client.get("/").text)
    assert "expired" not in cell.lower()
    assert "not reported" in cell


def test_an_unparsable_expiry_is_left_unknown_rather_than_called_expired(db):
    """The box owns the shape of this string. A card that read anything it
    could not parse as "expired" would page the operator for a format change
    on a box that is working."""
    with journal.connect(db) as conn:
        journal.append(
            conn, "box.observed", {"credential_expires_at": "sometime next Tuesday"}
        )
    cell = strip(client.get("/").text)
    assert "expired" not in cell.lower()


def test_a_box_that_could_not_be_read_says_so_on_the_card(db):
    with journal.connect(db) as conn:
        journal.append(conn, "box.unreachable", {"error": "no route to host"})
    assert "no route to host" in strip(client.get("/").text)


def test_a_box_never_observed_is_absent_rather_than_invented(db):
    assert "not read yet" in strip(client.get("/").text).lower()


# --- The guardrail chip (#165) ----------------------------------------------
#
# The box card says what is RUNNING; this says whether it could have got there
# without a review. Both halves of the answer are journaled by the cycle, so
# the chip is a replay like everything else on this page - and, like the timer
# cell, its whole worth is that it goes red rather than quiet.


def chip(body):
    """The guardrail cell alone, cut out of the strip."""
    cell = strip(body)
    start = cell.index('data-guardrail=')
    return cell[cell.rindex("<div", 0, start):]


PROTECTED = {
    "ref": "master",
    "ref_head": "44a596d0cbb3",
    "rules": ["deletion", "non_fast_forward", "pull_request"],
    "paths": ["loop", "selector"],
    "unreviewed": [],
    "protected": True,
    "detail": None,
}


def _guardrail(conn, **over):
    payload = {**PROTECTED, **over}
    return journal.append(conn, "guardrail.observed", payload)


def test_the_chip_is_green_when_the_executed_paths_are_review_gated(db):
    with journal.connect(db) as conn:
        _guardrail(conn)
    cell = chip(client.get("/").text)
    assert 'data-guardrail="green"' in cell
    assert "master" in cell
    assert "alarm" not in cell


def test_the_chip_names_the_rule_that_went_missing(db):
    """Red rather than absent, and specific rather than red. A chip that only
    said "not protected" would leave the operator to go and find out which of
    the three rules stopped applying."""
    with journal.connect(db) as conn:
        _guardrail(
            conn, rules=["deletion"], protected=False,
            detail="master is missing pull_request, non_fast_forward",
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="red"' in cell
    assert "alarm" in cell
    assert "pull_request" in cell


def test_the_chip_names_an_executed_path_that_is_ahead_of_the_protected_ref(db):
    with journal.connect(db) as conn:
        _guardrail(
            conn,
            unreviewed=["selector/cycle.py"],
            protected=False,
            detail=(
                "1 executed path(s) differ from master: "
                "selector/cycle.py"
            ),
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="red"' in cell
    assert "cycle.py" in cell


def test_the_chip_shows_the_newest_reading(db):
    """The same rule as the box card, for the sharper reason: a chip that kept
    showing this morning's green after the protection came off would be worse
    than no chip at all."""
    with journal.connect(db) as conn:
        _guardrail(conn)
        _guardrail(conn, protected=False, detail="master is missing pull_request")
    cell = chip(client.get("/").text)
    assert 'data-guardrail="red"' in cell


def test_a_guardrail_that_could_not_be_read_is_not_green(db):
    with journal.connect(db) as conn:
        journal.append(
            conn, "guardrail.unreadable", {"error": "gh: API rate limit exceeded"}
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="green"' not in cell
    assert "rate limit" in cell


def test_the_chip_is_green_when_multiple_declared_trees_are_review_gated(db):
    """Criterion 1: Two declared trees are read in one Cycle, and the chip is
    green only when both are."""
    trees = [
        {"repo": "acme/tracewake", "ref": "main", "ref_head": "abc1234", "rules": ["deletion", "non_fast_forward", "pull_request"], "paths": ["loop", "selector"], "unreviewed": [], "protected": True, "detail": None},
        {"repo": "acme/config", "ref": "master", "ref_head": "def5678", "rules": ["deletion", "non_fast_forward", "pull_request"], "paths": ["etc"], "unreviewed": [], "protected": True, "detail": None},
    ]
    with journal.connect(db) as conn:
        _guardrail(
            conn,
            ref="main, master",
            ref_head="abc1234, def5678",
            trees=trees,
            paths=["loop", "selector", "etc"],
            protected=True,
            detail=None,
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="green"' in cell
    assert "acme/tracewake" in cell
    assert "acme/config" in cell
    assert "alarm" not in cell


def test_the_chip_names_the_tree_and_rule_that_failed_when_one_tree_is_unprotected(db):
    """Criterion 3: The chip names which tree and which rule failed."""
    trees = [
        {"repo": "acme/tracewake", "ref": "main", "ref_head": "abc1234", "rules": ["deletion", "non_fast_forward", "pull_request"], "paths": ["loop"], "unreviewed": [], "protected": True, "detail": None},
        {"repo": "acme/config", "ref": "master", "ref_head": "def5678", "rules": ["deletion"], "paths": ["etc"], "unreviewed": [], "protected": False, "detail": "acme/config: master is missing pull_request, non_fast_forward"},
    ]
    with journal.connect(db) as conn:
        _guardrail(
            conn,
            ref="main, master",
            ref_head="abc1234, def5678",
            trees=trees,
            protected=False,
            detail="acme/config: master is missing pull_request, non_fast_forward",
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="red"' in cell
    assert "alarm" in cell
    assert "acme/config" in cell
    assert "pull_request" in cell


def test_the_chip_names_the_tree_when_one_tree_could_not_be_read(db):
    """Criterion 2: A tree the command did not answer for counts against the
    verdict; unknown is never green."""
    trees = [
        {"repo": "acme/tracewake", "ref": "main", "ref_head": "abc1234", "rules": ["deletion", "non_fast_forward", "pull_request"], "paths": ["loop"], "unreviewed": [], "protected": True, "detail": None},
        {"repo": "acme/config", "ref": "master", "ref_head": None, "rules": None, "paths": None, "unreviewed": None, "protected": False, "detail": "acme/config: could not be read (gh: not found)", "error": "gh: not found"},
    ]
    with journal.connect(db) as conn:
        _guardrail(
            conn,
            ref="main, master",
            ref_head="abc1234, unknown",
            trees=trees,
            protected=False,
            detail="acme/config: could not be read (gh: not found)",
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="red"' in cell
    assert "alarm" in cell
    assert "acme/config" in cell



def test_a_reading_too_old_to_stand_for_now_is_not_green(db):
    """The failure the timer cell already guards against, one panel along. A
    guardrail is a claim about the present, and the newest reading is only as
    good as the cycle that took it: if the timer dies, the last green reading
    would otherwise sit there asserting protection for as long as the page is
    up."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO journal.events (at, kind, payload)"
            " VALUES (now() - interval '5 hours', %s, %s)",
            ("guardrail.observed", Json(PROTECTED)),
        )
    cell = chip(client.get("/").text)
    assert 'data-guardrail="green"' not in cell
    assert "5 hours ago" in cell or "no cycle has read it" in cell


def test_a_reading_from_the_last_cycle_is_still_green(db):
    """The other side of the bound: the timer fires every thirty minutes, so a
    reading one cycle old is the ordinary case and must not read as a
    failure."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO journal.events (at, kind, payload)"
            " VALUES (now() - interval '31 minutes', %s, %s)",
            ("guardrail.observed", Json(PROTECTED)),
        )
    assert 'data-guardrail="green"' in chip(client.get("/").text)


def test_a_guardrail_never_read_is_not_green_either(db):
    """Unknown is not protected. The chip asserts something, and a page that
    asserted it before anything had checked would be the reassurance the
    ticket was written to avoid."""
    cell = chip(client.get("/").text)
    assert 'data-guardrail="green"' not in cell
    assert "not checked yet" in cell.lower()


# --- The Contract the Run is under (#162) -----------------------------------


CONTRACT = [
    "Iterations per Run: 5",
    "Iteration wall clock: 900s",
    "Turns per Iteration: 100",
    "Run wall clock: 5400s",
    "Consecutive No-op Iterations that abort: 2",
    "Completion Promise: recorded, never terminal",
    "Agent command: /home/loop/loop/agents/claude.sh",
    "Discipline skills: /tdd for code work, /diagnosing-bugs for something "
    "broken or slow, /code-review before every commit",
]


def _contract_row(conn, cycle, **over):
    payload = {
        "cycle": cycle,
        "issue": 645,
        "attempt": 1,
        "branch": "loop/645-the-nightly-sync-script",
        "run_started": "2026-08-27T12:00:00Z",
        "contract": CONTRACT,
    }
    payload.update(over)
    return journal.append(conn, "run.contract", payload)


def test_the_card_of_the_run_in_flight_names_the_discipline_skills(db):
    """Issue #162's viewing criterion. The three skills are on the card
    because they are on the Contract the box wrote into its Progress Log -
    the page is showing what THAT Run was told, not what this side's own
    configuration would have told it."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _contract_row(conn, cycle)
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "/tdd" in runs
    assert "/diagnosing-bugs" in runs
    assert "/code-review" in runs


def test_the_contract_card_shows_the_bounds_the_box_reported(db):
    """The bounds, from the same row. A Run in flight showed nothing about its
    terms before this: they are the box's environment, and the summary in its
    log is the only place they cross the hop (spec #151, story 25)."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
        _contract_row(conn, cycle)
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "Iterations per Run: 5" in runs
    assert "Run wall clock: 5400s" in runs


def test_a_run_with_no_contract_row_shows_no_contract_card(db):
    """A watcher that could not read the log, or a box on an older `run.sh`,
    leaves the card off rather than inventing terms from this side."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle)
    runs = client.get("/").text.split("<h2>Cycles</h2>")[0]
    assert "Termination Contract" not in runs


def test_a_contract_is_shown_on_the_run_it_was_read_for(db):
    """Paired by issue and attempt like every other row on a card: a retry can
    be dispatched under different terms from the attempt before it."""
    with journal.connect(db) as conn:
        cycle = journal.append(conn, "cycle.started", {"dry_run": False})
        _dispatch_row(conn, cycle, attempt=1)
        _contract_row(conn, cycle, attempt=1,
                      contract=["Iterations per Run: 5"])
        journal.append(
            conn, "run.outcome",
            {"cycle": cycle, "issue": 645, "attempt": 1,
             "outcome": "agent-failed", "exit": 4, "iterations": 1,
             "faults": "agent-failed"},
        )
        _dispatch_row(conn, cycle, attempt=2)
        _contract_row(conn, cycle, attempt=2,
                      contract=["Iterations per Run: 9"])
    cards = client.get("/").text.split("<h2>Cycles</h2>")[0].split(
        '<section class="run'
    )[1:]
    assert len(cards) == 2
    newest, oldest = cards
    assert "Iterations per Run: 9" in newest and "Run: 5" not in newest
    assert "Iterations per Run: 5" in oldest and "Run: 9" not in oldest
