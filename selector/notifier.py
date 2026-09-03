"""The Selector's email notifier: the Journal, read as it is written (#280).

ADR 0018 put the Loop's notification surface here rather than on the box, and
holds the argument for it. What it notifies about is every event that ADR
names; `notices.py` is where the choice of event and the wording live, and
this file is the process around it.

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

**Nothing at once, and nothing dropped.** A notifier that was down for days
replays what it missed, and `notices.NoticeConfig.max_age_hours` is the floor
under that: rows older than it are collected into one summary message rather
than mailed one by one - and rather than discarded, which would trade a
silence for a quieter silence.

The mail surface is one substitutable command, the convention every other
outward reach here follows (ADR 0004):

    $SELECTOR_NOTIFY_COMMAND <subject> [link]      # body on stdin

There is no default. How an instance sends mail - which relay, whose
credentials, to whom - is the instance's fact and not the product's, so an
unset `SELECTOR_NOTIFY_COMMAND` stops the notifier by name rather than being
filled in with somebody's. The body is on stdin rather than in an argument for
the reason the issue comments are: it is multi-line text, and an argument
would put the whole of it in the controller's process listing.
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


class NotConfigured(Exception):
    """A required instance value is not set. Raised at start, before any row is
    read, so the notifier refuses to run rather than running blind: a notifier
    with nowhere to send advances its cursor past every notice it was supposed
    to deliver and the backlog is gone."""


class NotifyFailed(Exception):
    """The mail surface refused. The cursor is not advanced and the process
    exits non-zero, so systemd restarts it and the notice is sent again."""


def _missing(name: str, what: str) -> "str":
    """Stop, naming the variable. Never returns; typed as `str` so it reads as
    the value it stands in for at the one call site that has one."""
    raise NotConfigured(
        f"{name} is not set, and there is no default for it: {what} is a fact"
        " about this instance, not about Tracewake."
    )


@dataclass(frozen=True)
class NotifierConfig:
    notify_command: str
    command_timeout_seconds: int
    notices: notices.NoticeConfig

    @classmethod
    def from_env(cls) -> "NotifierConfig":
        env = os.environ.get
        return cls(
            # `or` rather than a default argument, the idiom the rest of the
            # Selector reads env with: an override set to the empty string is
            # an unset override, not a command named "" that cannot be run.
            # No default. `or` rather than a plain read for the reason
            # the rest of the Selector uses it: an override set to the empty
            # string is an unset override, not a command named "" that cannot
            # be run - and here both mean the same refusal.
            notify_command=(
                env("SELECTOR_NOTIFY_COMMAND")
                or _missing("SELECTOR_NOTIFY_COMMAND", "the mail surface")
            ),
            # An SMTP call that answers in seconds or not at all. Long enough
            # for a slow relay, short enough that a wedged one cannot hold the
            # notifier off the rest of its backlog for the rest of the day.
            command_timeout_seconds=int(
                env("SELECTOR_NOTIFY_TIMEOUT_SECONDS") or 120
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
            *, dry_run: bool, previewed: set | None = None,
            deferred: list | None = None) -> bool:
    """Decide, send, and record. Returns whether anything was sent.

    The order is send, then record the dedupe key, then let the caller move
    the cursor - so every way this can be interrupted leaves a message
    repeated rather than a message lost.

    `previewed` is the dry run's stand-in for `selector.notifier_sent`, which
    it must not write to. Without it a preview shows the same credential
    warning once per cycle observed, which is the behaviour the real table
    exists to prevent - a preview that lies about what would be sent is worse
    than no preview.

    `deferred` collects the notices whose rows are past the age floor. They
    are not sent one by one and they are not dropped either: the caller mails
    one summary for the lot, because #280 exists to end silences and a
    silently discarded backlog would be a new one.
    """
    now = datetime.now(timezone.utc)
    notice = notices.for_event(row, now=now, config=config.notices)
    if notice is None:
        return False
    if notices.too_old(row, now=now, config=config.notices):
        # One-per-thing applies inside the summary too, and by the list rather
        # than by `selector.notifier_sent`: a backlog holding a day of box
        # observations is thirty copies of one credential warning, and a
        # summary that listed all thirty would be the noise the floor exists
        # to prevent, one level down. Not recorded as sent, because it has not
        # been - once the replay catches up, a credential still expiring is
        # still worth its own message.
        if deferred is not None and not any(
            notice.dedupe_key and row_seen["key"] == notice.dedupe_key
            for row_seen in deferred
        ):
            deferred.append({
                "at": _stamp(row["at"]),
                "subject": notice.subject,
                "key": notice.dedupe_key,
            })
        log.info("row %s: past the age floor, summarised instead", row["id"])
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
    # Rows past the age floor, held until the backlog is drained so that one
    # message can stand for all of them. Flushed at every exit from the loop -
    # including the transition to live, which is the ordinary case after a
    # restart - because a summary that waited for the process to end would
    # never be sent by a daemon.
    deferred: list = []

    def flush() -> int:
        if not deferred:
            return sent
        notice = notices.backlog_notice(deferred, config=config.notices)
        deferred.clear()
        if dry_run:
            print(f"--- would send: {notice.subject}")
            print(notice.body)
            print("--- end ---")
        else:
            send(config, notice)
            log.info("sent the backlog summary: %r", notice.subject)
        return sent + 1

    async for what, payload in journal.listen(after=start):
        if what == "ready":
            newest = payload["newest"] or 0
            if start is None:
                # The first start. Cover what happens next, not what already
                # happened - see the module docstring.
                if not dry_run:
                    set_cursor(conn, newest)
                log.info("first start: covering the Journal from row %s", newest)
                # Carry on listening. Returning here is what the live unit did
                # on 2026-08-31: it set the cursor, logged this line and
                # exited 0 on the very start that was supposed to begin
                # watching. `Restart=always` brought it back ten seconds later
                # with a cursor set, so it recovered - but a safety net
                # catching a bug is not the bug being absent, and the same
                # code under `Restart=on-failure` would exit 0 forever and
                # watch nothing. `--once` still stops here: there is no
                # backlog to drain by definition, and nothing to wait for.
                if once:
                    return sent
                start = target = newest
                continue
            if once and start >= newest:
                return sent
            target = newest
            continue
        if what == "silence":
            # Nothing arrived for a keepalive, so whatever was replayed is
            # replayed: the backlog is drained and its summary is due.
            sent = flush()
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
        if deliver(conn, config, row, dry_run=dry_run, previewed=previewed,
                   deferred=deferred):
            sent += 1
        if not dry_run:
            set_cursor(conn, row["id"])
        if row["id"] >= (target or 0):
            # The replay has caught up with where the Journal was when the
            # LISTEN was established. Everything after this arrives one row at
            # a time as it happens, so the summary is due now rather than
            # whenever the next quiet moment is.
            sent = flush()
            if once:
                return sent
    return flush()


def _stamp(at) -> str:
    """A row's time, in UTC, the way the box writes instants. The summary is
    read against the Journal, and two spellings of one timestamp would make
    that harder than it needs to be."""
    try:
        return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (AttributeError, ValueError):
        return str(at)


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
        help="with --dry-run: read from this Journal row rather than from the "
             "stored cursor, to see what the Journal would have said",
    )
    args = parser.parse_args(argv)
    # A reading tool, and only that. Without --dry-run it would re-send every
    # notice from that row forward AND move the cursor, so a hand `--since 0`
    # meant as "let me look" would mail the whole Journal at the operator. A
    # deliberate re-send is an UPDATE of selector.notifier by hand, which is
    # explicit about being one.
    if args.since is not None and not args.dry_run:
        parser.error("--since is for reading: pass --dry-run with it")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = NotifierConfig.from_env()
    except NotConfigured as exc:
        # Before the Journal is opened and before a cursor exists: nothing has
        # been read, so nothing has been passed over.
        log.error("%s", exc)
        return 1
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
