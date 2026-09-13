"""The notifier as a process: what it delivers, once, and what it survives.

The decision half is `test_notices.py`. This drives the real `notifier.py`
against a real throwaway Journal with the mail surface scripted, because the
properties worth checking here are all about state that outlives the process:
a first start that does not empty the backlog into an inbox, a row delivered
exactly once across a restart, and a delivery failure that loses nothing.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import events  # noqa: E402

NOTIFIER = Path(__file__).resolve().parents[1] / "notifier.py"

PROPOSAL = "https://github.invalid/acme/widgets/pull/12"

OUTCOME = {
    "cycle": 7,
    "issue": 312,
    "title": "Give the guest a PHP toolchain",
    "task_ref": "acme/widgets#312",
    "attempt": 1,
    "branch": "loop/312-php-guest",
    "ended_by": "iteration-cap",
    "exit": 0,
    "iterations": 5,
    "faults": "none",
    "proposal": PROPOSAL,
    "notified": "sent",
    "outcome": "iteration-cap",
}


def append(dsn, kind, payload, *, hours_ago=0):
    """Write one row, optionally backdated. Backdating needs an explicit `at`:
    journal.events is append-only, so a test that wants history writes it."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO journal.events (at, kind, payload)"
            " VALUES (now() - make_interval(hours => %s), %s, %s) RETURNING id",
            (hours_ago, kind, Jsonb(payload)),
        ).fetchone()[0]


def cursor(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT notified_through FROM selector.notifier"
        ).fetchone()[0]


@pytest.fixture
def notifier(db, tmp_path):
    """A scripted mail surface plus a runner for the real notifier."""
    delivered = tmp_path / "delivered.log"
    fail_flag = tmp_path / "refuse"

    command = tmp_path / "mail.sh"
    command.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ -e "{fail_flag}" ]]; then\n'
        '  echo "the mail surface refused" >&2\n'
        "  exit 1\n"
        "fi\n"
        "{\n"
        '  printf -- "--- subject: %s\\n" "$1"\n'
        '  printf -- "--- link: %s\\n" "${2:-}"\n'
        "  cat\n"
        '  printf -- "\\n--- end ---\\n"\n'
        f'}} >> "{delivered}"\n'
    )
    command.chmod(0o755)

    class Runner:
        dsn = db

        def run(self, *argv, **env):
            environ = dict(os.environ)
            environ.update({
                "SELECTOR_JOURNAL_DSN": db,
                "SELECTOR_NOTIFY_COMMAND": str(command),
                "SELECTOR_LOOP_URL": "https://lab.invalid/loop",
            })
            environ.update({k: str(v) for k, v in env.items()})
            return subprocess.run(
                [sys.executable, str(NOTIFIER), "--once", *argv],
                capture_output=True, text=True, env=environ, timeout=60,
            )

        def daemon(self, **env):
            """Start the notifier the way systemd does - no --once - and hand
            back the process for the caller to stop."""
            environ = dict(os.environ)
            environ.update({
                "SELECTOR_JOURNAL_DSN": db,
                "SELECTOR_NOTIFY_COMMAND": str(command),
                "SELECTOR_LOOP_URL": "https://lab.invalid/loop",
                # The daemon's own keepalive, shortened so a test that waits
                # for one is measured in a second rather than in twenty.
                "SELECTOR_SSE_KEEPALIVE_SECONDS": "1",
            })
            environ.update({k: str(v) for k, v in env.items()})
            return subprocess.Popen(
                [sys.executable, str(NOTIFIER)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environ,
            )

        def delivered(self):
            return delivered.read_text() if delivered.exists() else ""

        def subjects(self):
            return [
                line[len("--- subject: "):]
                for line in self.delivered().splitlines()
                if line.startswith("--- subject: ")
            ]

        def refuse(self):
            fail_flag.write_text("")

    return Runner()


# --- The first start --------------------------------------------------------


def test_a_first_start_mails_nothing_and_starts_from_the_newest_row(db, notifier):
    """The Journal holds months of Runs. A notifier reading it from the
    beginning on the day it is installed would mail every one of them."""
    for _ in range(3):
        append(db, "run.outcome", OUTCOME)
    newest = append(db, "cycle.failed", {"cycle": 7, "error": "boom"})

    result = notifier.run()

    assert result.returncode == 0, result.stderr
    assert notifier.subjects() == []
    assert cursor(db) == newest


def test_the_daemon_keeps_listening_after_its_first_start(db, notifier):
    """The first start sets the cursor; it must not also END the process.

    Live on 2026-08-31 it did: the unit logged "first start: covering the
    Journal from row 1227" and deactivated, and only `Restart=always` brought
    it back ten seconds later. A safety net catching a bug is not the bug
    being absent - the same code under `Restart=on-failure` would have exited
    0 on every start and never listened to anything.
    """
    process = notifier.daemon()
    try:
        _wait_until(lambda: cursor(db) is not None, process)
        append(db, "run.outcome", OUTCOME)
        _wait_until(lambda: notifier.subjects(), process)
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert notifier.subjects() == ["Proposal ready: acme/widgets#312"]


def _wait_until(condition, process, timeout=20.0):
    """Poll until the condition holds, failing loudly if the process died -
    a daemon that exited is the failure this suite is about, and waiting the
    full timeout for it would report as a hang rather than as an exit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise AssertionError(
                f"the notifier exited (rc {process.returncode}) instead of "
                f"listening:\n{stderr}"
            )
        time.sleep(0.1)
    raise AssertionError("timed out waiting for the notifier")


def test_after_the_first_start_the_next_row_is_delivered(db, notifier):
    notifier.run()
    append(db, "run.outcome", OUTCOME)

    notifier.run()

    assert notifier.subjects() == ["Proposal ready: acme/widgets#312"]


# --- Exactly once -----------------------------------------------------------


def test_a_row_already_delivered_is_not_delivered_again(db, notifier):
    notifier.run()
    append(db, "run.outcome", OUTCOME)
    notifier.run()

    notifier.run()

    assert len(notifier.subjects()) == 1


def test_a_new_unenrolled_gap_is_mailed_once(db, notifier):
    """Issue #39: a Handover on an unenrolled repository is one notice, and
    the standing gap that follows is not a second one."""
    notifier.run()
    kind, payload = events.target_unenrolled(
        owner="acme",
        label="ready-for-agent",
        repos=[{
            "repo": "acme/other",
            "issues": [{
                "number": 7,
                "title": "Do the thing",
                "url": "https://github.invalid/acme/other/issues/7",
            }],
        }],
        new=["acme/other"],
        dry_run=False,
    )
    append(db, kind, payload)
    notifier.run()
    append(db, kind, {**payload, "new": []})
    notifier.run()

    assert notifier.subjects() == [
        "Handover on an unenrolled repository: acme/other",
    ]


def test_the_body_and_the_link_reach_the_mail_surface(db, notifier):
    notifier.run()
    append(db, "run.outcome", OUTCOME)

    notifier.run()

    delivered = notifier.delivered()
    assert f"--- link: {PROPOSAL}" in delivered
    assert "loop/312-php-guest" in delivered
    assert PROPOSAL in delivered


def test_rows_that_are_worth_nothing_still_move_the_cursor(db, notifier):
    notifier.run()
    quiet = append(db, "cycle.finished", {"cycle": 7, "halted": "queue-empty"})

    notifier.run()

    assert notifier.subjects() == []
    assert cursor(db) == quiet


def test_rows_past_the_age_floor_are_summarised_rather_than_sent_one_by_one(
        db, notifier):
    """The floor stops a week of downtime emptying into an inbox. It must not
    turn a reported failure back into a silence, so one message stands for the
    lot and the Journal rows it covers are named in it."""
    notifier.run()
    append(db, "run.outcome", OUTCOME, hours_ago=200)
    stale = append(db, "cycle.failed", {"cycle": 7, "error": "boom"},
                   hours_ago=190)

    notifier.run()

    assert notifier.subjects() == ["2 Loop notices were too old to send one by one"]
    assert "Selector cycle failed" in notifier.delivered()
    assert "Proposal ready: acme/widgets#312" in notifier.delivered()
    assert cursor(db) == stale


def test_one_credential_is_one_line_in_the_summary_however_often_it_was_seen(
        db, notifier):
    """A day of backlog is a day of box observations. Listing every one of
    them would be the noise the floor exists to prevent, one level down."""
    notifier.run()
    # One card, appended four times - not four calls to `box`. #304: `box`
    # stamps the expiry from the wall clock to the second, so building it
    # inside the loop made the test ask whether four appends land in the same
    # second, which under load they do not. Four observations of ONE
    # credential is what this test is about.
    observed = box(1)
    for _ in range(4):
        append(db, "box.observed", observed, hours_ago=200)

    notifier.run()

    assert notifier.subjects() == ["1 Loop notice was too old to send on its own"]


def test_a_backlog_of_one_stale_row_still_gets_its_summary(db, notifier):
    notifier.run()
    append(db, "run.outcome", OUTCOME, hours_ago=200)

    notifier.run()

    assert notifier.subjects() == ["1 Loop notice was too old to send on its own"]


def test_a_row_inside_the_floor_is_still_sent_on_its_own(db, notifier):
    notifier.run()
    append(db, "run.outcome", OUTCOME, hours_ago=48)

    notifier.run()

    assert notifier.subjects() == ["Proposal ready: acme/widgets#312"]


# --- Delivery that failed ---------------------------------------------------


def test_a_refused_delivery_leaves_the_cursor_where_it_was(db, notifier):
    notifier.run()
    before = cursor(db)
    append(db, "run.outcome", OUTCOME)
    notifier.refuse()

    result = notifier.run()

    assert result.returncode != 0
    assert cursor(db) == before


def test_what_a_refused_delivery_left_behind_is_delivered_on_the_next_start(
        db, notifier, tmp_path):
    notifier.run()
    append(db, "run.outcome", OUTCOME)
    notifier.refuse()
    notifier.run()
    (tmp_path / "refuse").unlink()

    notifier.run()

    assert notifier.subjects() == ["Proposal ready: acme/widgets#312"]


# --- The credential, once per credential ------------------------------------


def box(expires_in_hours):
    return {
        "cycle": 7,
        "scripts_hash": "8c1f3a90d2",
        "agent": "claude",
        "credential_expires_at": _in_hours(expires_in_hours),
    }


def _in_hours(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_a_credential_inside_the_window_is_mailed_once_not_once_per_cycle(
        db, notifier):
    notifier.run()
    facts = box(1)
    append(db, "box.observed", facts)
    notifier.run()
    append(db, "box.observed", dict(facts))  # the next cycle, same credential

    notifier.run()

    assert len(notifier.subjects()) == 1


def test_a_renewed_credential_can_warn_again(db, notifier):
    notifier.run()
    append(db, "box.observed", box(1))
    notifier.run()
    append(db, "box.observed", box(1.5))  # a fresh mint, a different instant

    notifier.run()

    assert len(notifier.subjects()) == 2


# --- Reading it by hand -----------------------------------------------------


def test_a_dry_run_prints_what_it_would_send_and_changes_nothing(db, notifier):
    notifier.run()
    before = cursor(db)
    append(db, "run.outcome", OUTCOME)

    result = notifier.run("--dry-run", "--since", "0")

    assert result.returncode == 0, result.stderr
    assert "Proposal ready: acme/widgets#312" in result.stdout
    assert notifier.subjects() == []
    assert cursor(db) == before


# --- The mail surface is the instance's, not the product's -------------------


def test_an_unconfigured_mail_surface_stops_the_notifier_by_name(db, notifier):
    """No shipped default: how this instance sends mail is the instance's fact.

    The mail surface used to default to a script that reached into ETA's
    status dashboard for its relay and its credentials, which is exactly the
    kind of company fact this repository does not hold. With the default gone,
    an unset `SELECTOR_NOTIFY_COMMAND` has to stop the notifier saying which
    variable is missing - because the alternative is a notifier that starts,
    advances its cursor past every notice, and tells nobody.
    """
    result = notifier.run(SELECTOR_NOTIFY_COMMAND="")

    assert result.returncode != 0
    assert "SELECTOR_NOTIFY_COMMAND" in result.stderr
    assert notifier.subjects() == []


def test_the_window_url_has_no_default_either(db, notifier):
    """Where an instance publishes its window is the instance's fact too, and
    it is the address a person clicks out of an email. A default would send
    the operator to somebody else's host, which is worse than no link."""
    result = notifier.run(SELECTOR_LOOP_URL="")

    assert result.returncode != 0
    assert "SELECTOR_LOOP_URL" in result.stderr
    assert notifier.subjects() == []


def test_the_mail_surface_takes_the_subject_on_argv_and_the_body_on_stdin(
    db, notifier
):
    """The whole of the contract, asserted rather than described: a subject as
    the first argument, an optional link as the second, and the body on stdin.

    Nothing about a mailer is in it. Stdin for the body is not style - it is
    multi-line markdown, and an argument would put the whole of every notice
    in the controller's process listing.
    """
    notifier.run()
    append(db, "run.outcome", OUTCOME)

    notifier.run()

    delivered = notifier.delivered()
    # The scripted surface echoes $1, $2 and stdin under three labels, so
    # which channel each part arrived on is readable from the log.
    assert "--- subject: " in delivered
    assert f"--- link: {PROPOSAL}" in delivered
    subject = notifier.subjects()[0]
    assert "312" in subject, subject
    # The body is the part that is NOT in either argument.
    body = delivered.split("--- link: ", 1)[1].split("\n", 1)[1]
    assert "loop/312-php-guest" in body
    assert subject not in body.splitlines()
