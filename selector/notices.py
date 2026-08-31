"""What one Journal row is worth telling the operator, and in what words.

The decision half of the notifier (#280, ADR 0018). A row goes in and a
`Notice` or `None` comes out: no database, no mail, no clock of its own. The
process half - the LISTEN loop, the cursor that makes delivery survive a
restart, the sending - is `notifier.py`, and it is here that the reasoning
about *which* events are worth an email lives, because that is the part worth
reading back.

ADR 0018's four events, and the one row each is read off:

    (0) a Run finished with a green Proposal        run.outcome
    (1) a Run ended without one                     run.outcome
    (2) a dispatch or preflight failed              run.outcome, cycle.failed
    (3) the box's credential is close to expiring   box.observed

Green and not-green are decided here with `cycle.py`'s own predicate rather
than a second one. A Run that ended on a failure bound can still be holding a
Proposal - `agent-failed` after a push is exactly that - and cycle.py routes
it as a failure and retries it. An email that called the same Run green would
be the Selector saying two different things about one Run, and the operator
would have no way to tell which was true. So `RUN_FAILURE_BOUNDS` is imported,
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
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from cycle import MAX_ATTEMPTS, NO_PROPOSAL, RUN_FAILURE_BOUNDS

# How close to expiry the box's credential has to be before it is worth an
# email. The subscription login lapses eight hours after a human mints it and
# the box is read once every thirty minutes, so two hours is four cycles of
# warning - enough to renew before a dispatch is refused, and late enough that
# a credential minted at the start of a working day does not mail at lunchtime.
_DEFAULT_CREDENTIAL_WARN_HOURS = 2.0

# How stale a row may be and still be mailed. The notifier replays what it
# missed while it was down (that is what its cursor is for), and without a
# floor a restart after a week would empty the backlog into the operator's
# inbox at once. A Run that ended three days ago is not something to go and
# look at, and thirty messages arriving together is what mutes a channel.
_DEFAULT_MAX_AGE_HOURS = 24.0

_DEFAULT_LOOP_URL = "https://lab.etadventures.com/loop"


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
            loop_url=env("SELECTOR_LOOP_URL") or _DEFAULT_LOOP_URL,
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
    out - and it is keyed on the expiry instant, so renewing the credential is
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
    """
    at = event.get("at")
    if at is not None and _hours_between(at, now) > config.max_age_hours:
        return None

    kind = event["kind"]
    payload = event.get("payload") or {}
    if kind == "run.outcome":
        return _run_notice(payload, config)
    if kind == "cycle.failed":
        return _cycle_failure_notice(payload, config)
    if kind == "box.observed":
        return _credential_notice(payload, now, config)
    return None


# --- Runs -------------------------------------------------------------------


def _run_notice(payload: dict, config: NoticeConfig) -> Notice:
    """Every `run.outcome` row is worth exactly one email; which one it is
    depends on what the Run left behind.

    There is one row of this kind per dispatch, by construction (cycle.py
    journals a failed dispatch as an outcome too, so that the Journal has one
    row to pair with every `run.dispatched`). That is what makes this the
    right row to mail off: no Run is missed and none is reported twice.
    """
    ended_by = payload.get("outcome") or payload.get("ended_by") or "unknown"
    proposal = payload.get("proposal")
    if ended_by == "dispatch-failed":
        return _dispatch_failure_notice(payload, config)
    if ended_by in RUN_FAILURE_BOUNDS or not proposal:
        return _failed_run_notice(payload, ended_by, config)
    return _green_run_notice(payload, ended_by, config)


def _ref(payload: dict) -> str:
    """How a Run is named in a subject line. `task_ref` is the repository and
    the number together, which is what the operator searches for; the bare
    number is the fallback for a row written before there was one."""
    return payload.get("task_ref") or f"#{payload.get('issue')}"


def _run_facts(payload: dict) -> list[str]:
    """The Run's own report, in the shape `run.sh` printed it. Every line is
    something the box said - nothing here is derived - so a reader comparing
    this against the Progress Log is comparing two copies of one fact."""
    lines = [
        f"  issue:      {_ref(payload)} - {payload.get('title') or ''}".rstrip(),
        f"  branch:     {payload.get('branch') or 'unknown'}",
        f"  ended by:   {payload.get('outcome') or payload.get('ended_by')}",
        f"  exit code:  {payload.get('exit')}",
        f"  iterations: {payload.get('iterations')}",
        f"  faults:     {payload.get('faults') or 'none'}",
    ]
    if payload.get("notified"):
        lines.append(f"  notified:   {payload['notified']}")
    return lines


def _green_run_notice(payload: dict, ended_by: str,
                      config: NoticeConfig) -> Notice:
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
    proposal = payload["proposal"]
    body = "\n".join([
        f"A Run finished and left a Proposal for {_ref(payload)}.",
        "",
        *_run_facts(payload),
        f"  proposal:   {proposal}",
        "",
        "Its checks are read by the cycle after this, and the label the issue",
        "ends up carrying is what says whether they passed. Nothing is merged,",
        "deployed or applied: read the Progress Log on the branch first.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject=f"Proposal ready: {_ref(payload)}",
        body=body,
        link=proposal,
    )


def _failed_run_notice(payload: dict, ended_by: str,
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
    attempt = payload.get("attempt")
    proposal = payload.get("proposal")
    what = (
        "reached its iteration cap without proposing anything"
        if ended_by == NO_PROPOSAL else
        f"was cut short by `{ended_by}`"
    )
    lines = [
        f"A Run for {_ref(payload)} {what}.",
        "",
        *_run_facts(payload),
    ]
    if proposal:
        lines.append(f"  proposal:   {proposal}")
    lines.append("")
    if payload.get("notified") == "no-surface":
        lines += [
            "The Run reported `no-surface`: it had no Proposal to comment on,",
            "and the box has no second way to reach anybody. This message is it.",
            "",
        ]
    if isinstance(attempt, int):
        lines.append(f"This was attempt {attempt} of {MAX_ATTEMPTS} since the")
        lines.append("issue was last handed over. " + (
            "The budget is spent, so no further Run will be started for it "
            "until\n`ready-for-agent` is applied again."
            if attempt >= MAX_ATTEMPTS else
            "A later cycle may pick it up again."
        ))
        lines.append("")
    lines.append(f"The Loop's board: {config.loop_url}")
    # Two subjects rather than one, because they are opposite facts about how
    # far the Run got: a Run cut short by its Contract may still be holding a
    # partial Proposal worth reading, and one that ran to its cap and proposed
    # nothing has left the operator nothing at all.
    subject = (
        f"Run ended without a Proposal: {_ref(payload)}"
        if ended_by == NO_PROPOSAL else
        f"Run cut short by {ended_by}: {_ref(payload)}"
    )
    return Notice(
        subject=subject,
        body="\n".join(lines),
        link=proposal or config.loop_url,
    )


def _dispatch_failure_notice(payload: dict, config: NoticeConfig) -> Notice:
    """(2a) The box started no Run at all - an SSH that did not connect, a
    Seeding step that refused, a push GitHub rejected.

    Distinct from a Run that ran and ended badly, and the subject says which,
    because the operator's next command differs: this one is about the
    machinery rather than about the work.
    """
    body = "\n".join([
        f"A dispatch for {_ref(payload)} failed before any Run started.",
        "",
        f"  issue:      {_ref(payload)} - {payload.get('title') or ''}".rstrip(),
        f"  branch:     {payload.get('branch') or 'not prepared'}",
        f"  attempt:    {payload.get('attempt')}",
        "",
        f"  {payload.get('error') or 'no error was recorded'}",
        "",
        "The cycle exited non-zero, so selector-cycle.service has failed and",
        "its own unit alert names the unit. This one names the issue.",
        "",
        f"The Loop's board: {config.loop_url}",
    ])
    return Notice(
        subject=f"Dispatch failed: {_ref(payload)}",
        body=body,
        link=config.loop_url,
    )


def _cycle_failure_notice(payload: dict, config: NoticeConfig) -> Notice:
    """(2b) The cycle failed before it had a Run to fail - the tracker did not
    answer, the queue did not parse, the branch could not be prepared."""
    body = "\n".join([
        "A Selector cycle failed. No Run was dispatched by it.",
        "",
        f"  cycle:      {payload.get('cycle')}",
        "",
        f"  {payload.get('error') or 'no error was recorded'}",
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


def _credential_notice(payload: dict, now: datetime,
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
    raw = payload.get("credential_expires_at")
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
        dedupe_key=f"credential:{raw}",
    )


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
