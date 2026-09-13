"""What one Journal row is worth telling the operator, and in what words.

The decision half of the notifier (#280, ADR 0018). A row goes in and a
`Notice` or `None` comes out: no database, no mail, no clock of its own. The
process half - the LISTEN loop, the cursor that makes delivery survive a
restart, the sending - is `notifier.py`, and it is here that the reasoning
about *which* events are worth an email lives, because that is the part worth
reading back.

ADR 0018's four events, and the one row each is read off, plus the
unenrolled-Target warning (issue #39):

    (0) a Run finished with a green Proposal        run.outcome
    (1) a Run ended without one                     run.outcome
    (2) a dispatch or preflight failed              run.outcome, cycle.failed
    (3) the box's credential is close to expiring   box.observed
    (4) a Handover label on an unenrolled Target    target.unenrolled

Green and not-green are decided with the vocabulary's own naming rules
(`events.RUN_FAILURE_BOUNDS`, `events.outcome_name`) rather than a second
predicate. A Run that ended on a failure bound can still be holding a
Proposal - `agent-failed` after a push is exactly that - and the cycle routes
it as a failure and retries it. An email that called the same Run green would
be the Selector saying two different things about one Run, and the operator
would have no way to tell which was true. One spelling of the rule, imported,
for the reason `board.py` imports `eligibility`.

What is deliberately silent, so that the silence is a decision rather than an
oversight:

- `issue.route-failed` and `issue.return-failed`. Both are the tracker
  refusing bookkeeping, both exit the cycle non-zero, and the cycle's own
  `OnFailure=notify-unit-failure@` already mails on that. The `run.outcome`
  row underneath a route failure has mailed here too, so a third message
  about one Run would be noise about an incident already twice reported.
- `box.unreachable`. A box that cannot be read is not a failed Run: the
  dispatch behind it fails on its own and pages on its own, which is the
  reasoning `cycle.observe_box` is written around. One outage, one page.
- every `cycle.*` row but `cycle.failed`, and every `issue.*` and
  `run.iteration`/`run.contract` row. These are the page's material. `/loop`
  is where the Loop is watched; mail is for what happens while nobody is
  watching.
- a standing `target.unenrolled` row (`new` empty) and a dry-run one. The
  gap is already in the Journal; mailing it every Cycle is the noise the
  Journal-keyed dedup exists to stop.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import events
import targets
from events import MAX_ATTEMPTS, NO_PROPOSAL, is_failure, outcome_name

# How close to expiry the box's credential has to be before it is worth an
# email (#7). The box's yearly credentials share an expiry date, and the
# box is read once every thirty minutes, so a fortnight (336 hours) gives
# the operator two weeks of warning - enough to run wizards/loop-credentials.sh
# before any credential lapses.
_DEFAULT_CREDENTIAL_WARN_HOURS = 336.0

# How old a row may be and still be mailed on its own. The notifier replays
# what it missed while it was down (that is what its cursor is for), and
# without a floor a restart after a week would empty the backlog into the
# operator's inbox at once, which is what mutes a channel.
#
# Seventy-two hours rather than a working day, because the outage this is
# actually about is a weekend: a notifier that died on Friday evening should
# still report Saturday's Runs one by one on Monday. What falls past it is
# summarised in a single message rather than dropped - nothing the Loop was
# silent about stays silent, which is the whole point of #280.
_DEFAULT_MAX_AGE_HOURS = 72.0

# Deliberately no default for the window's URL: where an instance's window is
# published is a fact about that instance, not about Tracewake (issue #3). A
# notice that linked to somebody else's host would be worse than one with no
# link, so an unset value stops the notifier by name at start rather than
# mailing a wrong address.


@dataclass(frozen=True)
class NoticeConfig:
    credential_warn_hours: float
    loop_url: str
    max_age_hours: float

    @classmethod
    def from_env(cls) -> "NoticeConfig":
        env = os.environ.get
        return cls(
            credential_warn_hours=float(
                env("SELECTOR_CREDENTIAL_WARN_HOURS")
                or _DEFAULT_CREDENTIAL_WARN_HOURS
            ),
            loop_url=(
                env("SELECTOR_LOOP_URL")
                or targets.missing(
                    "SELECTOR_LOOP_URL", "where this instance's window is"
                )
            ),
            max_age_hours=float(
                env("SELECTOR_NOTIFY_MAX_AGE_HOURS") or _DEFAULT_MAX_AGE_HOURS
            ),
        )


@dataclass(frozen=True)
class Notice:
    """One email: what the subject line says, what the body says, and the one
    link worth following from it.

    `dedupe_key` is how a notice says "this is about a thing, not about a
    row". The credential is the only one that needs it - the box is observed
    every cycle, so the same expiry would otherwise mail four times on its way
    out - and it is keyed on the expiry instant rather than on the box's
    spelling of it (`_credential_key`, #304), so renewing the credential is
    what makes the next warning sendable again. `None` means every occurrence
    is its own event, which is true of Runs.
    """

    subject: str
    body: str
    link: str
    dedupe_key: str | None = None


def for_event(event: dict, *, now: datetime,
              config: NoticeConfig) -> Notice | None:
    """The notice this Journal row is worth, or None for the rows that are not.

    `event` is a row as `journal.events` returns it: `id`, `at`, `kind`,
    `payload`.

    Whether a notice is worth sending is a separate question from whether it
    is still worth *delivering*, which is `too_old` and the notifier's to ask:
    a row that is too old to mail one-by-one is still worth counting, and
    deciding both here would leave the notifier nothing to count.
    """
    kind = event["kind"]
    if kind == events.RUN_OUTCOME:
        return _run_notice(events.run_outcome_record(event), config)
    if kind == events.CYCLE_FAILED:
        return _cycle_failure_notice(events.cycle_failed_record(event), config)
    if kind == events.BOX_OBSERVED:
        return _credential_notice(events.box_record(event), now, config)
    if kind == events.TARGET_UNENROLLED:
        return _unenrolled_notice(events.target_unenrolled_record(event), config)
    return None


def too_old(event: dict, *, now: datetime, config: NoticeConfig) -> bool:
    """Whether this row is past the age at which it is mailed on its own.

    The notifier replays what it missed while it was down, so a long outage
    could otherwise put a week of Runs into one inbox at once - which is what
    mutes a channel. Rows past the floor are counted into one summary instead
    of dropped: the spec asked for these events to stop being silent, and
    trading a silence for a quieter silence would be missing the point.
    """
    at = event.get("at")
    return at is not None and _hours_between(at, now) > config.max_age_hours


def backlog_notice(skipped: list[dict], *, config: NoticeConfig) -> Notice:
    """The one message that stands in for the notices left unsent by age.

    `skipped` is `{"at", "subject"}` per row, oldest first. Counted with its
    scope rather than as a bare number, because a count with no scope is the
    thing this repository's counting rules exist to stop.
    """
    oldest, newest = skipped[0]["at"], skipped[-1]["at"]
    count = len(skipped)
    many = count != 1
    body = "\n".join([
        f"{count} Loop {'notices were' if many else 'notice was'} not sent on"
        f" {'their' if many else 'its'} own, because the",
        f"Journal {'rows' if many else 'row'} {'they are' if many else 'it is'}"
        f" about {'are' if many else 'is'} older than the"
        f" {config.max_age_hours:g}-hour",
        "floor the notifier sends within. It was down, or newly pointed at an",
        "older row, for at least that long.",
        "",
        (f"They cover Journal rows written between {oldest} and {newest}:"
         if many else f"It covers the Journal row written at {oldest}:"),
        "",
        *(f"  {row['at']}  {row['subject']}" for row in skipped),
        "",
        "Nothing is lost - every one of them is a row in the Journal, and the",
        "board shows the Runs they belong to.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    subject = (
        f"{count} Loop notices were too old to send one by one" if many else
        "1 Loop notice was too old to send on its own"
    )
    return Notice(subject=subject, body=body, link=config.loop_url)


# --- Runs -------------------------------------------------------------------


def _run_notice(record: events.RunOutcome, config: NoticeConfig) -> Notice:
    """Every `run.outcome` row is worth exactly one email; which one it is
    depends on what the Run left behind.

    There is one row of this kind per dispatch, by construction (cycle.py
    journals a failed dispatch as an outcome too, so that the Journal has one
    row to pair with every `run.dispatched`). That is what makes this the
    right row to mail off: no Run is missed and none is reported twice.
    """
    raw = record.ended_by or "unknown"
    proposal = record.proposal
    if raw == events.DISPATCH_FAILED:
        return _dispatch_failure_notice(record, config)
    # The row carries the raw bound - it is written before the cycle's routing
    # step draws this distinction - so the name has to be derived here, with
    # the vocabulary's one rule. Without it a Run that reached its cap and
    # proposed nothing would be mailed as "cut short by iteration-cap", which
    # is the opposite of what happened.
    ended_by = outcome_name(raw, proposal)
    if is_failure(ended_by, proposal):
        return _failed_run_notice(record, ended_by, config)
    return _green_run_notice(record, config)


def _ref(record: events.RunOutcome) -> str:
    """How a Run is named in a subject line. `task_ref` is the repository and
    the number together, which is what the operator searches for; the bare
    number is the fallback for a row written before there was one."""
    return record.task_ref or f"#{record.issue}"


def _run_facts(record: events.RunOutcome) -> list[str]:
    """The Run's own report, in the shape `run.sh` printed it. Every line is
    something the box said - nothing here is derived - so a reader comparing
    this against the Progress Log is comparing two copies of one fact."""
    lines = [
        f"  issue:      {_ref(record)} - {record.title or ''}".rstrip(),
        f"  branch:     {record.branch or 'unknown'}",
        f"  ended by:   {record.ended_by}",
        f"  exit code:  {record.exit}",
        f"  iterations: {record.iterations}",
        f"  faults:     {record.faults or 'none'}",
    ]
    if record.notified:
        lines.append(f"  notified:   {record.notified}")
    return lines


def _green_run_notice(record: events.RunOutcome, config: NoticeConfig) -> Notice:
    """(0) The Run ended within its bounds and left a Proposal.

    GitHub carried this message until 2026-08-31 - ADR 0013's comment on the
    Proposal, emailed by the "include your own updates" account setting. That
    setting is off for good (it was account-global and delivered every agent
    session's activity), so this is now the only mail a finished Run sends.
    The comment itself still goes on the Proposal as the on-PR record.

    What the checks make of the Proposal is NOT in here, and that is the one
    thing worth saying out loud: they are read afterwards, by the cycle's
    routing step, and this row is written before it. "Green" here means the
    Run finished and proposed, not that CI passed.
    """
    proposal = record.proposal
    body = "\n".join([
        f"A Run finished and left a Proposal for {_ref(record)}.",
        "",
        *_run_facts(record),
        f"  proposal:   {proposal}",
        "",
        "Its checks are read by the cycle after this, and the label the issue",
        "ends up carrying is what says whether they passed. Nothing is merged,",
        "deployed or applied: read the Progress Log on the branch first.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject=f"Proposal ready: {_ref(record)}",
        body=body,
        link=proposal,
    )


def _failed_run_notice(record: events.RunOutcome, ended_by: str,
                       config: NoticeConfig) -> Notice:
    """(1) The Run was cut short, or reached its cap with nothing to show.

    The silence this replaces: a Run with no Proposal has nothing to comment
    on, so ADR 0013's surface does not exist for it and today nobody hears
    anything at all unless the /loop page happens to be open.

    The retry budget is stated rather than predicted. Which route the cycle
    takes is decided after this row is written, and a message that promised a
    retry the routing then declined would be worse than one that says how many
    attempts the Handover has left.
    """
    attempt = record.attempt
    proposal = record.proposal
    what = (
        "ended within its bounds and left no Proposal"
        if ended_by == NO_PROPOSAL else
        f"was cut short by `{ended_by}`"
    )
    lines = [
        f"A Run for {_ref(record)} {what}.",
        "",
        *_run_facts(record),
    ]
    if proposal:
        lines.append(f"  proposal:   {proposal}")
    lines.append("")
    if record.notified == "no-surface":
        lines += [
            "The Run reported `no-surface`: it had no Proposal to comment on,",
            "and the box has no second way to reach anybody. This message is it.",
            "",
        ]
    if isinstance(attempt, int):
        lines += [
            f"This was attempt {attempt} of {MAX_ATTEMPTS} since the issue was",
            "last handed over.",
        ]
        lines += (
            ["The budget is spent, so no further Run will be started for it",
             "until `ready-for-agent` is applied again."]
            if attempt >= MAX_ATTEMPTS else
            ["A later cycle may pick it up again."]
        )
        lines.append("")
    lines.append(f"The Loop's board: {config.loop_url}")
    # Two subjects rather than one, because they are opposite facts about how
    # far the Run got: a Run cut short by its Contract may still be holding a
    # partial Proposal worth reading, and one that ran to its cap and proposed
    # nothing has left the operator nothing at all.
    subject = (
        f"Run ended without a Proposal: {_ref(record)}"
        if ended_by == NO_PROPOSAL else
        f"Run cut short by {ended_by}: {_ref(record)}"
    )
    return Notice(
        subject=subject,
        body="\n".join(lines),
        link=proposal or config.loop_url,
    )


def _dispatch_failure_notice(record: events.RunOutcome,
                             config: NoticeConfig) -> Notice:
    """(2a) The box started no Run at all - an SSH that did not connect, a
    Seeding step that refused, a push GitHub rejected.

    Distinct from a Run that ran and ended badly, and the subject says which,
    because the operator's next command differs: this one is about the
    machinery rather than about the work.
    """
    body = "\n".join([
        f"A dispatch for {_ref(record)} failed before any Run started.",
        "",
        f"  issue:      {_ref(record)} - {record.title or ''}".rstrip(),
        f"  branch:     {record.branch or 'not prepared'}",
        f"  attempt:    {record.attempt}",
        "",
        f"  {record.error or 'no error was recorded'}",
        "",
        "The cycle exited non-zero, so selector-cycle.service has failed and",
        "its own unit alert names the unit. This one names the issue.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject=f"Dispatch failed: {_ref(record)}",
        body=body,
        link=config.loop_url,
    )


def _cycle_failure_notice(record: events.CycleFailure,
                          config: NoticeConfig) -> Notice:
    """(2b) The cycle failed before it had a Run to fail - the tracker did not
    answer, the queue did not parse, the branch could not be prepared."""
    body = "\n".join([
        "A Selector cycle failed. No Run was dispatched by it.",
        "",
        f"  cycle:      {record.cycle}",
        "",
        f"  {record.error or 'no error was recorded'}",
        "",
        "The timer fires again in thirty minutes and will try the whole cycle",
        "afresh. A second message about the same failure means it is not",
        "transient, and the queue has not moved since the first.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject="Selector cycle failed",
        body=body,
        link=config.loop_url,
    )


# --- The credential ---------------------------------------------------------


def _credential_notice(reading: events.BoxReading, now: datetime,
                       config: NoticeConfig) -> Notice | None:
    """(3) The box's model credential is about to stop working, or has.

    Read off the box card the cycle already writes every thirty minutes
    (#260). The Journal records the absolute instant the box gave; the
    remaining time is arithmetic against this reader's clock, for the reason
    `cycle.BOX_FACT_KEYS` states - a duration recorded at observation time
    would be stale by however long it sat there.

    A box whose adapter reports no expiry, or reports one this cannot parse,
    is silent rather than alarming. Not every agent has a credential with an
    expiry, and inventing an alarm out of a field that was never filled in is
    how a channel earns being ignored.
    """
    raw = reading.credential_expires_at
    expires_at = _instant(raw)
    if expires_at is None:
        return None
    hours = _hours_between(now, expires_at)
    if hours > config.credential_warn_hours:
        return None

    if hours <= 0:
        headline = "has expired"
        when = f"It expired at {raw} ({_span(-hours)} ago)."
    else:
        headline = f"expires in {_span(hours)}"
        when = f"It expires at {raw}."
    body = "\n".join([
        f"The Loop box's model credential {headline}.",
        "",
        when,
        "",
        "A box with no working credential starts no Iteration, so every Run",
        "dispatched to it fails and every issue it was dispatched for spends an",
        "attempt. Renewing is a human at a keyboard on the box - the login",
        "cannot be minted from here, which is why this is mail and not a fix.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject=f"The Loop box's credential {headline}",
        body=body,
        link=config.loop_url,
        dedupe_key=_credential_key(expires_at),
    )


def _unenrolled_notice(record: events.UnenrolledTargets,
                       config: NoticeConfig) -> Notice | None:
    """(4) A Handover label sits on a repository with no Target stanza.

    Only newly appearing repos are mailed. The Cycle journals the standing
    gap every time so the Journal is the account; mailing it every thirty
    minutes is the noise issue #39 forbids. A dry-run journals too, and
    stays silent here, so a keyboard check cannot eat the live notice.
    """
    if record.dry_run:
        return None
    new = [name for name in (record.new or []) if name]
    if not new:
        return None
    new_set = set(new)
    entries = [
        entry for entry in (record.repos or [])
        if isinstance(entry, dict) and entry.get("repo") in new_set
    ]
    if len(new) == 1:
        subject = f"Handover on an unenrolled repository: {new[0]}"
        headline = (
            "A Handover label sits on a repository this instance has no"
            " Target stanza for."
        )
    else:
        subject = f"Handover on {len(new)} unenrolled repositories"
        headline = (
            "A Handover label sits on repositories this instance has no"
            " Target stanza for."
        )
    lines = [headline, ""]
    first_url = None
    for entry in entries:
        lines.append(f"  {entry['repo']}")
        for issue in entry.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            number = issue.get("number")
            title = issue.get("title") or ""
            url = issue.get("url") or ""
            lines.append(f"    #{number} {title}".rstrip())
            if url:
                lines.append(f"      {url}")
                if first_url is None:
                    first_url = url
        lines.append("")
    lines += [
        "Nothing was enrolled. Minting a Target's token is a human act; the",
        "Selector only warns.",
        "",
        f"The Loop's board: {config.loop_url}",
    ]
    return Notice(
        subject=subject,
        body="\n".join(lines),
        link=first_url or config.loop_url,
    )


def _credential_key(expires_at: datetime) -> str:
    """The dedupe key for one credential: the expiry INSTANT, not the box's
    spelling of it.

    Decided in #304. The key used to be the raw string the box reported, and
    two observations of one credential collapsed only while the box spelled
    the expiry identically every time - true today, because the value is
    stored and echoed verbatim, and silently false the day an adapter
    re-derives it, reports `+00:00` instead of `Z`, or gains a fractional
    second. The failure mode was quiet in the worst way: not a crash but a
    second copy of a warning `selector.notifier_sent` exists to send once.

    Normalising to UTC whole seconds keeps the key BYTE-IDENTICAL for every
    box spelling it `2026-08-30T01:13:44Z` today, so the rows already in
    `selector.notifier_sent` still match and nobody is mailed twice by this
    change landing.

    The format is whole seconds, which is also what drops the fraction a
    `01:13:44.472Z` would carry: the box prints seconds, and two credentials
    expiring inside one second is not a thing that happens. Keep it - reaching
    for `isoformat()` here would make a fractional expiry its own credential.
    """
    utc = expires_at.astimezone(timezone.utc)
    return "credential:" + utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Instants ---------------------------------------------------------------


def _instant(raw) -> datetime | None:
    """Parse the box's `2026-08-30T01:13:44Z`, or None if it will not parse.

    `fromisoformat` does not accept a trailing `Z` before Python 3.11 and this
    runs on 3.9, so the offset is spelled out before it is handed over. A
    value with no offset at all is read as UTC: the box prints UTC, and
    guessing this VM's zone instead would move the alarm by four hours.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _hours_between(earlier: datetime, later: datetime) -> float:
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    if later.tzinfo is None:
        later = later.replace(tzinfo=timezone.utc)
    return (later - earlier).total_seconds() / 3600.0


def _span(hours: float) -> str:
    """A duration a person reads at a glance: `1h 42m`, or minutes alone."""
    minutes = max(0, int(round(hours * 60)))
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"
