"""The Selector's email notifier: the Journal, read as it is written (#280).

ADR 0018 put the Loop's notification surface here rather than on the box. The
box's whole external reach stays "the repository" - no mail host on its egress
allowlist, no fifth credential in its inventory - and this VM already holds
mail infrastructure for the status dashboard. What it notifies about is every
event ADR 0018 names; `notices.py` is where the choice of event and the
wording live, and this file is the process around it.

Since the "include your own updates" account setting went back off
(2026-08-31), this is the **single** email channel for the Loop. A green Run
is mailed from here too, and ADR 0013's comment on the Proposal stays as the
on-PR record rather than as a delivery mechanism.

Three properties, and each is why a piece of state exists:

**Delivered once.** The cursor in `selector.notifier` is written after the
mail surface accepted a notice, so a restart resumes where delivery got to
rather than where reading got to. At-least-once, deliberately, on the one
seam where it can be: a process killed between the send and the write repeats
one message, and repeating a message the operator has already read is a
smaller failure than losing the only notice a silent Run ever produces.

**Nothing on the first start.** `notified_through` is NULL until this has run
once, and a first start takes it to the newest row without sending. The
Journal holds every Run there has ever been, and installing a notifier is not
a reason to mail all of them.

**Nothing stale.** A notifier that was down for a week replays what it missed,
and `notices.NoticeConfig.max_age_hours` is the floor under that: rows older
than it are passed over, with the cursor still advancing past them.

The mail surface is one substitutable command, the convention every other
outward reach here follows (ADR 0004):

    $SELECTOR_NOTIFY_COMMAND <subject> [link]      # body on stdin

The shipped default is `notify-sources/email.sh`, which hands the notice to
the status dashboard's mailer - the same Mandrill relay, the same credentials,
the same target as every other alert this VM sends. The body is on stdin
rather than in an argument for the reason the issue comments are: it is
multi-line text, and an argument would put the whole of it in this VM's
process listing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

import journal
import notices

HERE = Path(__file__).resolve().parent

log = logging.getLogger("selector.notifier")


class NotifyFailed(Exception):
    """The mail surface refused. The cursor is not advanced and the process
    exits non-zero, so systemd restarts it and the notice is sent again."""


@dataclass(frozen=True)
class NotifierConfig:
    notify_command: str
    command_timeout_seconds: int
    notices: notices.NoticeConfig

    @classmethod
    def from_env(cls) -> "NotifierConfig":
        env = os.environ.get
        return cls(
            notify_command=env(
                "SELECTOR_NOTIFY_COMMAND",
                str(HERE / "notify-sources" / "email.sh"),
            ),
            # An SMTP call that answers in seconds or not at all. Long enough
            # for a slow relay, short enough that a wedged one cannot hold the
            # notifier off the rest of its backlog for the rest of the day.
            command_timeout_seconds=int(
                env("SELECTOR_NOTIFY_TIMEOUT_SECONDS", "120")
            ),
            notices=notices.NoticeConfig.from_env(),
        )


# --- The cursor and the one-per-thing record --------------------------------


def cursor(conn: psycopg.Connection) -> int | None:
    """How far delivery has got, or None if this has never run."""
    row = conn.execute(
        "SELECT notified_through FROM selector.notifier WHERE singleton = true"
    ).fetchone()
    return row[0] if row else None


def set_cursor(conn: psycopg.Connection, event_id: int) -> None:
    conn.execute(
        "UPDATE selector.notifier SET notified_through = %s WHERE singleton = true",
        (event_id,),
    )


def already_sent(conn: psycopg.Connection, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM selector.notifier_sent WHERE dedupe_key = %s", (key,)
    ).fetchone() is not None


def record_sent(conn: psycopg.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO selector.notifier_sent (dedupe_key) VALUES (%s)"
        " ON CONFLICT (dedupe_key) DO NOTHING",
        (key,),
    )


# --- The mail surface -------------------------------------------------------


def send(config: NotifierConfig, notice: notices.Notice) -> None:
    """Hand one notice to the mail command. Raises on anything but success."""
    try:
        completed = subprocess.run(
            [config.notify_command, notice.subject, notice.link],
            input=notice.body,
            capture_output=True,
            text=True,
            timeout=config.command_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyFailed(f"the mail command could not run: {exc}") from exc
    if completed.returncode != 0:
        said = (completed.stderr or completed.stdout or "").strip()
        raise NotifyFailed(
            f"the mail command refused (exit {completed.returncode}): "
            f"{said[:400]}"
        )


# --- The loop ---------------------------------------------------------------


def deliver(conn: psycopg.Connection, config: NotifierConfig, row: dict,
            *, dry_run: bool, previewed: set | None = None) -> bool:
    """Decide, send, and record. Returns whether anything was sent.

    The order is send, then record the dedupe key, then let the caller move
    the cursor - so every way this can be interrupted leaves a message
    repeated rather than a message lost.

    `previewed` is the dry run's stand-in for `selector.notifier_sent`, which
    it must not write to. Without it a preview shows the same credential
    warning once per cycle observed, which is the behaviour the real table
    exists to prevent - a preview that lies about what would be sent is worse
    than no preview.
    """
    notice = notices.for_event(
        row, now=datetime.now(timezone.utc), config=config.notices
    )
    if notice is None:
        return False
    if notice.dedupe_key:
        seen = (
            notice.dedupe_key in previewed if dry_run and previewed is not None
            else already_sent(conn, notice.dedupe_key)
        )
        if seen:
            log.info("row %s: %r already sent", row["id"], notice.dedupe_key)
            return False
        if dry_run and previewed is not None:
            previewed.add(notice.dedupe_key)
    if dry_run:
        print(f"--- would send: {notice.subject}")
        print(f"--- link: {notice.link}")
        print(notice.body)
        print("--- end ---")
        return True
    send(config, notice)
    if notice.dedupe_key:
        record_sent(conn, notice.dedupe_key)
    log.info("row %s: sent %r", row["id"], notice.subject)
    return True


async def serve(conn: psycopg.Connection, config: NotifierConfig, *,
                once: bool = False, dry_run: bool = False,
                since: int | None = None) -> int:
    """Consume the Journal until told to stop. Returns what was sent.

    `once` drains the backlog and exits, which is what the tests drive and
    what a hand run wants. Without it this runs until the process is stopped:
    the LISTEN is the point, and a notifier that polled would be a second
    timer on a box that already has the one that matters.
    """
    start = since if since is not None else cursor(conn)
    target: int | None = None
    sent = 0
    previewed: set = set()

    async for what, payload in journal.listen(after=start):
        if what == "ready":
            newest = payload["newest"] or 0
            if start is None:
                # The first start. Cover what happens next, not what already
                # happened - see the module docstring.
                if not dry_run:
                    set_cursor(conn, newest)
                log.info("first start: covering the Journal from row %s", newest)
                return sent
            if once and start >= newest:
                return sent
            target = newest
            continue
        if what == "silence":
            if once:
                return sent
            continue

        row = journal.event(conn, payload["id"])
        if row is None:
            # Cannot happen through the schema's trigger, which fires after
            # the insert. Guarded anyway: a notifier that raised on a row it
            # could not read would stop delivering every later one.
            log.warning("row %s named by NOTIFY is not there", payload["id"])
            continue
        if deliver(conn, config, row, dry_run=dry_run, previewed=previewed):
            sent += 1
        if not dry_run:
            set_cursor(conn, row["id"])
        if once and target is not None and row["id"] >= target:
            return sent
    return sent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--once", action="store_true",
        help="deliver the backlog and exit, rather than staying on the LISTEN",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the notices instead of sending them; writes nothing",
    )
    parser.add_argument(
        "--since", type=int, default=None, metavar="ID",
        help="read from this Journal row rather than from the stored cursor "
             "(for --dry-run: what the Journal would have said)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    config = NotifierConfig.from_env()
    with journal.connect() as conn:
        try:
            sent = asyncio.run(serve(
                conn, config,
                once=args.once, dry_run=args.dry_run, since=args.since,
            ))
        except NotifyFailed as exc:
            # Loud and non-zero. The cursor was not advanced past whatever
            # this was, so a restart delivers it; the unit's OnFailure says
            # so out loud, through the dashboard's own alert path rather than
            # through the one that just refused.
            log.error("%s", exc)
            return 1
    log.info("delivered %d notice(s)", sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
