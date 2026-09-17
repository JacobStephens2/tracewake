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


def test_the_decision_module_is_importable_without_the_dispatcher():
    """Purity, checked rather than claimed: the docstring says "no database",
    and importing this module used to execute the whole Selector's import
    graph anyway (cycle, psycopg, dispatch, watcher) for four names. The
    vocabulary module is the dependency now, and it is pure."""
    import subprocess
    import sys as _sys
    done = subprocess.run(
        [_sys.executable, "-c",
         "import sys; import notices; "
         "leaked = [m for m in ('cycle', 'psycopg', 'dispatch', 'watcher')"
         " if m in sys.modules]; "
         "sys.exit(f'notices dragged in {leaked}' if leaked else 0)"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert done.returncode == 0, done.stdout + done.stderr

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
CONFIG = notices.NoticeConfig(
    credential_warn_hours=336,
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


def test_every_failure_bound_with_a_proposal_is_still_not_green():
    """Router/notifier parity for the whole bound set: a Run the Contract cut
    short is a failure even holding a draft Proposal, on every bound - so no
    failed Run is ever mailed as green."""
    green_subject = notice("run.outcome", outcome()).subject
    for bound in ("run-clock", "consecutive-noops", "agent-failed"):
        said = notice("run.outcome", outcome(
            ended_by=bound, exit=4, proposal=PROPOSAL, proposed="proposed",
        ))
        assert said is not None
        assert said.subject != green_subject, bound
        assert bound in (said.subject + said.body), bound


def test_a_failed_run_notice_names_the_branch_holding_the_record():
    """Where the evidence lives, in mail as on the issue: the branch whose
    history carries the Run's record."""
    said = notice("run.outcome", outcome(
        ended_by="agent-failed", exit=4,
    ))
    assert "loop/312-php-guest" in said.body


def test_a_run_that_proposed_nothing_says_so():
    """The row carries `iteration-cap`, not `no-proposal`: cycle.py writes the
    raw bound into `run.outcome` and only renames it later, on the issue's own
    row. A notifier reading the raw bound literally would mail "cut short by
    iteration-cap" for a Run that was not cut short at all."""
    said = notice("run.outcome", outcome(
        outcome="iteration-cap", proposal=None, proposed="failed",
        notified="no-surface", exit=6,
    ))
    assert "without a Proposal" in said.subject
    assert "cut short" not in said.subject
    assert "left no Proposal" in said.body
    assert said.link == CONFIG.loop_url


def test_a_run_with_no_surface_to_report_on_names_that():
    said = notice("run.outcome", outcome(
        outcome="iteration-cap", proposal=None, notified="no-surface",
    ))
    assert "no-surface" in said.body


def test_a_first_failed_attempt_says_the_retry_budget_is_not_spent():
    said = notice("run.outcome", outcome(
        outcome="iteration-cap", proposal=None, attempt=1,
    ))
    assert "attempt 1 of 2" in said.body


def test_a_last_failed_attempt_says_the_budget_is_spent():
    said = notice("run.outcome", outcome(
        outcome="iteration-cap", proposal=None, attempt=2,
    ))
    assert "attempt 2 of 2" in said.body
    assert "no further run" in said.body.lower()


# --- (2) A dispatch or preflight that failed --------------------------------


def test_a_dispatch_that_started_no_run_is_not_read_as_a_run_with_no_proposal():
    """It has no Proposal either, and the two are opposite facts about how far
    the machinery got: the operator's next command differs."""
    said = notice("run.outcome", outcome(
        ended_by="dispatch-failed", proposal=None,
        error="ssh: connect refused",
    ))
    assert "Dispatch failed" in said.subject


def test_a_dispatch_that_started_no_run_is_a_failure_notice():
    said = notice("run.outcome", outcome(
        ended_by="dispatch-failed", proposal=None,
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


def in_hours_offset(hours):
    """The same instant `in_hours` names, spelled `+01:00` rather than `Z` -
    what a differently-configured box would report."""
    at = (NOW + timedelta(hours=hours)).astimezone(timezone(timedelta(hours=1)))
    return at.isoformat()


def test_a_credential_with_hours_left_says_nothing():
    assert notice("box.observed", box(in_hours(400))) is None


def test_a_credential_a_fortnight_out_is_inside_the_warning_window():
    # A fortnight is 336 hours (#7)
    assert notice("box.observed", box(in_hours(335))) is not None
    assert notice("box.observed", box(in_hours(337))) is None


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


def test_two_spellings_of_one_expiry_are_one_credential():
    """#304. The key is the instant, not the box's spelling of it: an adapter
    that starts reporting `+01:00` where it used to report `Z` is reporting
    the same credential, and a key that changed with the spelling would mail
    the same warning a second time."""
    zulu = notice("box.observed", box(in_hours(1)))
    offset = notice("box.observed", box(in_hours_offset(1)))
    assert offset is not None
    assert offset.dedupe_key == zulu.dedupe_key


def test_a_sub_second_expiry_keys_the_same_as_its_whole_second():
    """The box prints whole seconds; a fractional one is the same credential
    and not a second warning. Held by the key's format rather than by a
    truncation of its own - which is what this pins, against a later
    `isoformat()` that would let the fraction back in."""
    said = notice("box.observed", box(in_hours(1).replace("Z", ".472Z")))
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
        ("target.unenrolled", unenrolled(new=[])),
    ):
        assert notice(kind, payload) is None, kind


# --- Unenrolled-Target warning (#39) ----------------------------------------


def unenrolled(**over):
    payload = {
        "owner": "acme",
        "label": "ready-for-agent",
        "repos": [
            {
                "repo": "acme/other",
                "issues": [
                    {
                        "number": 7,
                        "title": "Do the thing",
                        "url": "https://github.invalid/acme/other/issues/7",
                    }
                ],
            }
        ],
        "new": ["acme/other"],
        "dry_run": False,
    }
    payload.update(over)
    return payload


def test_a_new_unenrolled_gap_is_worth_one_notice():
    said = notice("target.unenrolled", unenrolled())
    assert said is not None
    assert "acme/other" in said.subject
    assert "acme/other" in said.body
    assert "#7" in said.body
    assert "Do the thing" in said.body
    assert "unenrolled" in said.body.lower() or "no Target" in said.body
    assert "human" in said.body.lower()
    assert said.link == "https://github.invalid/acme/other/issues/7"


def test_a_standing_unenrolled_gap_is_silent():
    assert notice("target.unenrolled", unenrolled(new=[])) is None


def test_a_dry_run_unenrolled_warning_is_silent():
    """A dry-run journals the gap so the operator can see it; mail is for
    the live Cycle that would otherwise leave it silent."""
    assert notice("target.unenrolled", unenrolled(dry_run=True)) is None


def test_a_newly_appearing_repo_names_only_what_is_new():
    said = notice(
        "target.unenrolled",
        unenrolled(
            repos=[
                {
                    "repo": "acme/other",
                    "issues": [{"number": 7, "title": "Old", "url": "u1"}],
                },
                {
                    "repo": "acme/stray",
                    "issues": [{"number": 3, "title": "Stray work", "url": "u2"}],
                },
            ],
            new=["acme/stray"],
        ),
    )
    assert said is not None
    assert "acme/stray" in said.subject
    assert "Stray work" in said.body
    assert "acme/other" not in said.body


# --- The age floor ----------------------------------------------------------
#
# Worth sending and still worth delivering one by one are separate questions.
# A row past the floor keeps its notice - the notifier counts it into one
# summary - so this asks only the second question.


def old_row(hours):
    return {"id": 41, "at": NOW - timedelta(hours=hours), "kind": "run.outcome",
            "payload": outcome()}


def test_a_row_older_than_the_floor_is_not_sent_on_its_own():
    """A notifier that was down for a week must not empty its backlog into the
    operator's inbox one message at a time - that is what mutes a channel."""
    assert notices.too_old(old_row(100), now=NOW, config=CONFIG) is True


def test_a_row_inside_the_floor_is_sent_on_its_own():
    assert notices.too_old(old_row(2), now=NOW, config=CONFIG) is False


def test_a_row_past_the_floor_still_has_a_notice_to_summarise():
    """Dropped and deferred are different: #280 exists to end silences, so
    what falls past the floor has to still be nameable."""
    said = notices.for_event(old_row(100), now=NOW, config=CONFIG)
    assert said is not None


def test_the_backlog_summary_counts_with_its_scope():
    """A count is a number plus a scope (ETA/counting.md): the summary says
    how many notices and which span of Journal rows they are about."""
    said = notices.backlog_notice(
        [
            {"at": "2026-08-28T09:12:00Z", "subject": "Proposal ready: a#1"},
            {"at": "2026-08-29T11:00:00Z", "subject": "Selector cycle failed"},
        ],
        config=CONFIG,
    )
    assert "2 Loop notices" in said.subject
    assert "2026-08-28T09:12:00Z" in said.body
    assert "2026-08-29T11:00:00Z" in said.body
    assert "Selector cycle failed" in said.body
