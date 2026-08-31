"""What the notifier would say about one Journal row, and when it says nothing.

The decision half of #280, kept pure so it can be driven directly: a row in,
a Notice or None out, with no database, no mail and no clock of its own. The
process half - the LISTEN loop, the cursor, the sending - is test_notifier.py.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notices  # noqa: E402

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = notices.NoticeConfig(
    credential_warn_hours=2,
    loop_url="https://lab.invalid/loop",
    max_age_hours=24,
)

PROPOSAL = "https://github.invalid/acme/widgets/pull/12"


def event(kind, payload, *, at=NOW):
    return {"id": 41, "at": at, "kind": kind, "payload": payload}


def outcome(**overrides):
    """A `run.outcome` payload in the shape cycle.py writes it."""
    payload = {
        "cycle": 7,
        "issue": 312,
        "title": "Give the guest a PHP toolchain",
        "url": "https://github.invalid/acme/widgets/issues/312",
        "task_ref": "acme/widgets#312",
        "attempt": 1,
        "branch": "loop/312-php-guest",
        "ended_by": "iteration-cap",
        "exit": 0,
        "iterations": 5,
        "faults": "none",
        "proposal": PROPOSAL,
        "proposed": "proposed",
        "notified": "sent",
        "outcome": "iteration-cap",
        "seed": "seeded",
        "criteria": "3",
    }
    payload.update(overrides)
    return payload


def notice(kind, payload, *, at=NOW, now=NOW):
    return notices.for_event(event(kind, payload, at=at), now=now, config=CONFIG)


# --- (0) A Run that finished with a green Proposal --------------------------


def test_a_run_that_left_a_proposal_is_the_green_notice():
    said = notice("run.outcome", outcome())
    assert said is not None
    assert "acme/widgets#312" in said.subject
    assert PROPOSAL in said.body
    assert said.link == PROPOSAL


def test_the_green_notice_names_the_issue_it_was_for():
    said = notice("run.outcome", outcome())
    assert "Give the guest a PHP toolchain" in said.body


def test_the_green_notice_carries_the_run_s_own_report():
    said = notice("run.outcome", outcome())
    for fact in ("iteration-cap", "loop/312-php-guest", "5"):
        assert fact in said.body


def test_the_green_notice_says_nothing_is_merged():
    """ADR 0013's comment said so and the operator read it there. This is now
    the only mail the Loop sends, so it has to carry the same sentence."""
    assert "merged" in notice("run.outcome", outcome()).body


# --- (1) A Run that ended without one ---------------------------------------


def test_a_run_that_ended_on_a_failure_bound_is_not_green():
    """Even holding a Proposal: `agent-failed` is the Contract cutting the Run
    short, and cycle.py routes it as a failure. Two readers of one fact."""
    said = notice("run.outcome", outcome(
        outcome="agent-failed", ended_by="agent-failed", exit=4,
    ))
    assert "agent-failed" in said.subject or "agent-failed" in said.body
    assert said.subject != notice("run.outcome", outcome()).subject


def test_a_run_that_proposed_nothing_says_so():
    said = notice("run.outcome", outcome(
        outcome="no-proposal", proposal=None, proposed="failed",
        notified="no-surface", exit=6,
    ))
    assert "no Proposal" in said.body
    assert said.link == CONFIG.loop_url


def test_a_run_with_no_surface_to_report_on_names_that():
    said = notice("run.outcome", outcome(
        outcome="no-proposal", proposal=None, notified="no-surface",
    ))
    assert "no-surface" in said.body


def test_a_first_failed_attempt_says_the_retry_budget_is_not_spent():
    said = notice("run.outcome", outcome(
        outcome="no-proposal", proposal=None, attempt=1,
    ))
    assert "attempt 1 of 2" in said.body


def test_a_last_failed_attempt_says_the_budget_is_spent():
    said = notice("run.outcome", outcome(
        outcome="no-proposal", proposal=None, attempt=2,
    ))
    assert "attempt 2 of 2" in said.body
    assert "no further run" in said.body.lower()


# --- (2) A dispatch or preflight that failed --------------------------------


def test_a_dispatch_that_started_no_run_is_a_failure_notice():
    said = notice("run.outcome", outcome(
        outcome="dispatch-failed", proposal=None,
        error="the box started no Run (exit 255): ssh: connect refused",
    ))
    assert "connect refused" in said.body
    assert "312" in said.subject


def test_a_cycle_that_failed_before_any_dispatch_is_a_failure_notice():
    said = notice("cycle.failed", {
        "cycle": 7, "error": "tracker command did not return a queue: 'issues'",
    })
    assert said is not None
    assert "did not return a queue" in said.body
    assert said.link == CONFIG.loop_url


# --- (3) A credential approaching expiry ------------------------------------


def box(expires_at):
    return {
        "cycle": 7,
        "scripts_hash": "8c1f3a90d2",
        "guest_template": "loop-php:1",
        "agent": "claude",
        "agent_version": "2.1.221 (Claude Code)",
        "credential_expires_at": expires_at,
    }


def in_hours(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_credential_with_hours_left_says_nothing():
    assert notice("box.observed", box(in_hours(6))) is None


def test_a_credential_inside_the_window_is_a_notice():
    said = notice("box.observed", box(in_hours(1)))
    assert said is not None
    assert "credential" in said.subject.lower()


def test_an_expired_credential_says_expired_rather_than_a_remaining_time():
    said = notice("box.observed", box(in_hours(-3)))
    assert said is not None
    assert "expired" in (said.subject + said.body).lower()


def test_the_credential_notice_is_keyed_on_the_instant_it_is_about():
    """One notice per credential, not one per cycle: the box is read every
    thirty minutes and the same expiry would otherwise mail four times before
    it arrived."""
    said = notice("box.observed", box(in_hours(1)))
    assert said.dedupe_key == "credential:" + in_hours(1)


def test_a_box_that_reports_no_expiry_says_nothing():
    assert notice("box.observed", box(None)) is None


def test_a_box_whose_expiry_will_not_parse_says_nothing():
    assert notice("box.observed", box("soon-ish")) is None


# --- Silence ----------------------------------------------------------------


def test_the_kinds_that_are_not_failure_shaped_say_nothing():
    for kind, payload in (
        ("cycle.started", {"repo": "acme/widgets"}),
        ("cycle.finished", {"cycle": 7, "halted": "queue-empty"}),
        ("cycle.picked", {"cycle": 7, "number": 312}),
        ("issue.skipped", {"number": 312, "reason": "proposal-open"}),
        ("issue.returned", {"number": 312}),
        ("issue.awaiting-review", {"issue": 312}),
        ("run.dispatched", {"issue": 312}),
        ("run.iteration", {"issue": 312, "iteration": 3}),
        ("run.contract", {"issue": 312}),
        ("guardrail.observed", {"cycle": 7}),
    ):
        assert notice(kind, payload) is None, kind


def test_a_row_older_than_the_max_age_says_nothing():
    """A notifier that was down for a week must not empty its backlog into the
    operator's inbox: a Run that ended three days ago is not something to go
    and look at, and thirty of them at once is what mutes a channel."""
    stale = NOW - timedelta(hours=30)
    assert notice("run.outcome", outcome(), at=stale) is None


def test_a_row_inside_the_max_age_still_speaks():
    assert notice("run.outcome", outcome(), at=NOW - timedelta(hours=2)) is not None
