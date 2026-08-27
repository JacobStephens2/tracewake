"""The /loop window at HTTP level: FastAPI test client over a seeded Journal."""
from fastapi.testclient import TestClient

import journal
from app import app

client = TestClient(app)


def test_loop_page_renders_journal_newest_first(db):
    with journal.connect(db) as conn:
        journal.append(conn, "cycle.summary", {"eligible": 3})
        journal.append(conn, "run.dispatched", {"issue": 646})
    resp = client.get("/loop")
    assert resp.status_code == 200
    assert "terminal.css" in resp.text
    body = resp.text
    assert body.index("run.dispatched") < body.index("cycle.summary")
    assert "646" in body


def test_loop_page_serves_when_journal_is_unreachable(monkeypatch):
    monkeypatch.setenv("SELECTOR_JOURNAL_DSN", "dbname=selector_test_no_such_db")
    resp = client.get("/loop")
    assert resp.status_code == 200
    assert "unavailable" in resp.text.lower()


def test_terminal_css_is_served():
    resp = client.get("/static/terminal.css")
    assert resp.status_code == 200
    assert "--global-font-size" in resp.text


def test_home_links_to_loop():
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/loop"' in resp.text


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
                "dispatched_in_window": 0,
                "daily_cap": 4,
                "dry_run": True,
            },
        )
    body = client.get("/loop").text
    assert "Widen the sync window" in body
    assert "The nightly sync script" in body
    assert "blocked-by-open-dependency" in body
    assert "1 open blocking edge(s) on the tracker" in body
    assert "dry run" in body
    assert "0/4" in body, "the daily-cap budget is shown"


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
                "dispatched_in_window": 1,
                "daily_cap": 4,
                "dry_run": True,
            },
        )
    body = client.get("/loop").text
    assert "picked nothing" in body
    assert "run-in-flight" in body


def test_events_outside_a_cycle_still_reach_the_page(db):
    """The raw event table stays the Journal's unabridged view: a hand
    `psql` INSERT with no cycle id must not vanish behind the cards."""
    with journal.connect(db) as conn:
        journal.append(conn, "test.hand", {"note": "appended by hand"})
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    body = client.get("/loop").text
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
    assert '<span class="badge badge-handed-to-human">ready-for-human</span>' in runs
    assert "phpunit" in runs and "lint" in runs


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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
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
    runs = client.get("/loop").text.split("<h2>Cycles</h2>")[0]
    assert "no-proposal" in runs
    assert "iteration-cap" not in runs


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


def test_the_strip_counts_todays_runs_against_the_cap(db, dispatch):
    for number in (640, 641):
        dispatch(db, number, outcome="clean")
    assert "2 of 4" in strip(client.get("/loop").text)


def test_a_dispatch_older_than_the_window_is_not_a_run_today(db, dispatch):
    """The same rolling window the Selector enforces, because a strip that
    counted a different day would reassure about a cap it is not reading."""
    dispatch(db, 640, outcome="clean", hours_ago=30)
    assert "0 of 4" in strip(client.get("/loop").text)


def test_the_strip_names_the_run_in_flight(db, dispatch, monkeypatch, tmp_path):
    _running_timer(monkeypatch, tmp_path)
    dispatch(db, 646, outcome=None)
    cell = strip(client.get("/loop").text)
    assert "in flight" in cell
    assert "646" in cell


def test_the_strip_says_idle_when_no_run_is_in_flight(db, monkeypatch, tmp_path):
    _running_timer(monkeypatch, tmp_path)
    assert "idle" in strip(client.get("/loop").text)


def test_a_selector_that_cannot_run_does_not_read_as_idle(db, monkeypatch, tmp_path):
    """The failure the whole strip exists for. With the timer down nothing
    will start a cycle, and a headline cell reading `idle` would be the page
    reporting business as usual about a dead Selector."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(tmp_path, "ActiveState=inactive\nNextElapseUSecRealtime=n/a"),
    )
    cell = strip(client.get("/loop").text)
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
    assert "in flight: #646" in strip(client.get("/loop").text)


def test_the_strip_shows_the_next_cycle_the_timer_will_fire(db, monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(
            tmp_path,
            "ActiveState=active\nNextElapseUSecRealtime=Thu 2026-08-27 14:31:00 UTC",
        ),
    )
    assert "Thu 2026-08-27 14:31:00 UTC" in strip(client.get("/loop").text)


def test_a_timer_that_is_not_running_is_said_so_rather_than_left_blank(
    db, monkeypatch, tmp_path
):
    """The failure this cell exists for: a timer installed and never enabled
    is a Selector that looks quiet and is in fact dead (story 31)."""
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND",
        _timer_command(tmp_path, "ActiveState=inactive\nNextElapseUSecRealtime=n/a"),
    )
    assert "not running" in strip(client.get("/loop").text).lower()


def test_the_page_still_renders_when_the_timer_cannot_be_read(
    db, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "SELECTOR_TIMER_COMMAND", _timer_command(tmp_path, "", exit_code=1)
    )
    resp = client.get("/loop")
    assert resp.status_code == 200
    assert "unknown" in strip(resp.text).lower()


def test_the_box_card_shows_the_scripts_hash_and_the_agent_version(db):
    with journal.connect(db) as conn:
        journal.append(
            conn,
            "box.observed",
            {
                "scripts_hash": "8c1f3a90d2",
                "guest_template": "claude",
                "agent": "claude",
                "agent_version": "2.1.221 (Claude Code)",
            },
        )
    cell = strip(client.get("/loop").text)
    assert "8c1f3a90d2" in cell
    assert "2.1.221 (Claude Code)" in cell
    assert "claude" in cell


def test_the_box_card_shows_the_newest_observation(db):
    with journal.connect(db) as conn:
        journal.append(conn, "box.observed", {"scripts_hash": "olderhash1"})
        journal.append(conn, "box.observed", {"scripts_hash": "newerhash2"})
    cell = strip(client.get("/loop").text)
    assert "newerhash2" in cell
    assert "olderhash1" not in cell


def test_a_fact_the_box_did_not_report_is_blank_rather_than_guessed(db):
    with journal.connect(db) as conn:
        journal.append(
            conn, "box.observed", {"scripts_hash": "deadbeef01", "agent_version": None}
        )
    cell = strip(client.get("/loop").text)
    assert "deadbeef01" in cell
    assert "not reported" in cell


def test_a_box_that_could_not_be_read_says_so_on_the_card(db):
    with journal.connect(db) as conn:
        journal.append(conn, "box.unreachable", {"error": "no route to host"})
    assert "no route to host" in strip(client.get("/loop").text)


def test_a_box_never_observed_is_absent_rather_than_invented(db):
    assert "not read yet" in strip(client.get("/loop").text).lower()
