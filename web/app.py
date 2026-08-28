"""lab.etadventures.com — the lab front door.

Built with FastAPI + Jinja2 + HTMX: server renders HTML, HTMX swaps in
server-rendered fragments, no client-side framework and no build step. The
site is itself a demonstration of the stack the ADR chooses (docs/adr/).
"""
# The venv here is the system Python (3.9), so `dict | None` in an annotation
# is a runtime TypeError without this. Same import the Selector's own modules
# carry, for the same reason.
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import timezone
from pathlib import Path
from typing import Callable, TypeVar

import markdown as md
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent / "single-user-factory"
ADR_DIR = PROJECT / "docs" / "adr"

# The Selector Journal's writer/reader module lives with the Selector; this
# app is its window (ADR 0015), so import it from there rather than forking
# the SQL.
sys.path.insert(0, str(PROJECT / "selector"))
import board as queue_board  # noqa: E402
import control  # noqa: E402
import cycle  # noqa: E402
import journal  # noqa: E402

import preview  # noqa: E402

app = FastAPI(title="lab.etadventures.com")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
# The single-user-factory project's own files (lessons, notes, ADRs) served
# static; the dynamic home is the FastAPI app itself.
app.mount(
    "/single-user-factory",
    StaticFiles(directory=PROJECT, html=True),
    name="single-user-factory",
)
templates = Jinja2Templates(directory=BASE / "templates")

def _static_base(request: Request) -> str:
    """Where this app's static files are, as the browser should ask for them.

    Deliberately relative and deliberately not `url_for`, which renders an
    absolute URL from the request's own base. This app runs behind Caddy's TLS
    with uvicorn started without `--proxy-headers`, so that base is `http://`
    and every stylesheet on an https page would be blocked as mixed content.
    Reading `root_path` instead keeps the one property a hardcoded `/static/`
    lacks - correctness under a path prefix - without inventing a scheme.
    """
    return _path(request, "/static")


def _path(request: Request, route: str) -> str:
    """A route on THIS instance, as the browser should ask for it.

    Same reasoning as `_static_base`, and the same reason not to use
    `url_for`: relative, prefix-aware, and no invented scheme. It matters more
    here than for a stylesheet - a live region that asked the root instance
    for its fragment would quietly render another deployment's Journal into
    this page.
    """
    return request.scope.get("root_path", "").rstrip("/") + route


def _page(request: Request, name: str, context: dict, **kwargs):
    """Render a page with whatever every page needs.

    Today that is exactly one thing: whether this instance is an Attended
    Preview (ADR 0016). It goes in here rather than in each handler because a
    page that forgot it would look like the live app while serving unreviewed
    code, and the failure would be invisible - the page renders fine.
    """
    return templates.TemplateResponse(
        name,
        {
            "request": request,
            "preview": preview.banner(),
            "static_base": _static_base(request),
            **context,
        },
        **kwargs,
    )


# The pipeline a single-user factory would run. Static here — this endpoint
# demonstrates the HTMX request -> server-rendered-fragment -> swap loop, not a
# real run.
PIPELINE = [
    ("intake", "read the request as written"),
    ("triage", "classify: fix, spec, or clarify"),
    ("implement", "produce a bounded patch in a worktree"),
    ("verify", "run the checks against the patch"),
    ("output", "open a draft PR, evidence attached"),
]

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _parse_adr(path: Path) -> dict:
    """Split frontmatter (status), pull the title, render the body to HTML."""
    text = path.read_text()
    status = ""
    m = _FRONTMATTER.match(text)
    if m:
        for line in m.group(1).splitlines():
            if line.strip().startswith("status:"):
                status = line.split(":", 1)[1].strip()
        body = text[m.end():]
    else:
        body = text
    title_m = _TITLE.search(body)
    title = title_m.group(1) if title_m else path.stem
    # number = leading digits of the filename (0001-... -> 1)
    num_m = re.match(r"(\d+)", path.stem)
    number = int(num_m.group(1)) if num_m else 0
    return {
        "slug": path.stem,
        "number": number,
        "title": title,
        "status": status,
        "body": body,
    }


def _all_adrs() -> list[dict]:
    if not ADR_DIR.is_dir():
        return []
    return sorted(
        (_parse_adr(p) for p in ADR_DIR.glob("*.md")),
        key=lambda a: a["number"],
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _page(request, "index.html", {"stages": PIPELINE, "adrs": _all_adrs()})


@app.post("/demo/run", response_class=HTMLResponse)
async def demo_run(request: Request):
    """HTMX POSTs here; the server returns an HTML fragment, not JSON."""
    return _page(request, "_run.html", {"stages": PIPELINE})


@app.get("/adr", response_class=HTMLResponse)
async def adr_index(request: Request):
    return _page(request, "adr_index.html", {"adrs": _all_adrs()})


@app.get("/adr/{slug}", response_class=HTMLResponse)
async def adr_detail(request: Request, slug: str):
    path = ADR_DIR / f"{slug}.md"
    if not path.is_file() or "/" in slug or ".." in slug:
        return _page(request, "adr_missing.html", {"slug": slug}, status_code=404)
    adr = _parse_adr(path)
    adr["html"] = md.markdown(
        adr["body"], extensions=["fenced_code", "tables", "sane_lists"]
    )
    return _page(request, "adr_detail.html", {"adr": adr})


def _utc(at) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _duration(start, end) -> str | None:
    """How long a Run took, from the two Journal rows that bracket it.

    Computed here rather than reported by the box, because the box reports
    nothing after a Run ends - it persists no record of one at all. The
    dispatch row and the outcome row are the only two timestamps that exist,
    and their gap is the wall-clock the operator actually waited.

    Two units at most: "1h 40m" answers "was that a long Run?" and "1h 40m
    12s" does not answer it any better.
    """
    if start is None or end is None:
        return None
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        # Rows are ordered by id, not by `at`, and a fixture (or a clock step)
        # can put an outcome before its dispatch. Say nothing rather than
        # render a negative duration as if it were a fact about the Run.
        return None
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _cycles(events: list[dict]) -> list[dict]:
    """Group Journal rows into one card per Selector cycle, newest first.

    A cycle's events all carry the id of its `cycle.started` row in
    `payload.cycle`, so the grouping is the Journal's own, not a guess made
    here. This page is a window and a scribe: it re-renders what the Selector
    decided and decides nothing itself (ADR 0015).
    """
    cards: dict[int, dict] = {}
    for event in events:  # newest first
        if event["kind"] == "cycle.started":
            # `started` marks the card as whole. The Journal read is capped,
            # so the oldest cycle on the page is usually cut in half by the
            # limit, and a card built from the leftovers would render as a
            # nameless, timeless cycle rather than as the absence it is.
            card = cards.setdefault(event["id"], {"skips": []})
            card.update(
                started=True,
                id=event["id"],
                at=_utc(event["at"]),
                repo=event["payload"].get("repo"),
                label=event["payload"].get("label"),
                dry_run=event["payload"].get("dry_run"),
            )
            continue
        cycle_id = (event["payload"] or {}).get("cycle")
        if cycle_id is None:
            continue
        card = cards.setdefault(cycle_id, {"skips": []})
        if event["kind"] == "issue.skipped":
            card["skips"].append(event["payload"])
        elif event["kind"] == "cycle.picked":
            card["pick"] = event["payload"]
        elif event["kind"] == "cycle.finished":
            card["summary"] = event["payload"]
        elif event["kind"] == "cycle.failed":
            card["failed"] = event["payload"].get("error")
        elif event["kind"] in ("issue.returned", "issue.return-failed"):
            # The loud skip's second half. Kept beside the skip it belongs to
            # rather than in a list of its own: "skipped, and handed back" is
            # one fact about one issue.
            card.setdefault("returned", {})[event["payload"].get("number")] = (
                event["payload"].get("error") or True
            )
    whole = [card for card in cards.values() if card.get("started")]
    for card in whole:
        # The Journal reads newest first; a cycle's own skips read better in
        # the order it decided them.
        card["skips"].reverse()
    return sorted(whole, key=lambda c: c["id"], reverse=True)


# The Journal rows that say where an outcome put the issue (#155). One kind
# per route so that the Journal is greppable by outcome, which means the page
# has to know the set rather than matching a prefix.
# kind -> the route name the card is styled and worded by. Spelled out rather
# than derived by splitting the kind apart, because the name reaches the page
# as a CSS class (`badge-awaiting-review`): a mapping that is wrong fails when
# this file is read, where deriving it would have failed silently the day an
# event kind was renamed and a badge quietly lost its colour.
ROUTE_NAMES = {
    "issue.awaiting-review": "awaiting-review",
    "issue.handed-to-human": "handed-to-human",
    "issue.given-up": "given-up",
    "issue.retrying": "retrying",
}
ROUTE_KINDS = set(ROUTE_NAMES)

# Every kind a Run card is built from, so the membership test is one lookup
# rather than a set union rebuilt per event.
RUN_KINDS = ROUTE_KINDS | {
    "run.dispatched", "run.outcome", "run.iteration", "run.watch-failed",
}


def _runs(events: list[dict]) -> list[dict]:
    """One card per dispatch, newest first: the Run in flight, and the Runs
    that have ended with what they produced.

    A dispatch and its outcome are paired by issue and attempt, which is the
    pair the Selector journals them with - a retry of an issue that already
    has an outcome is its own Run and gets its own card. A dispatch with no
    outcome yet is the Run in flight; that gap is the same one the Selector
    reads as its in-flight lock, so the page shows the lock rather than
    guessing at it.
    """
    cards: dict[tuple, dict] = {}
    for event in events:  # newest first
        payload = event["payload"] or {}
        if event["kind"] not in RUN_KINDS:
            continue
        if payload.get("issue") is None:
            continue
        key = (payload["issue"], payload.get("attempt"))
        card = cards.setdefault(key, {"in_flight": True})
        if event["kind"] in ROUTE_KINDS:
            # Where the outcome put the issue (#155). Journaled as its own row
            # rather than folded into `run.outcome`, because the route is
            # decided after that row is written - so the card learns it from
            # the routing event or shows no badge at all, which is what an
            # in-flight Run and a route the tracker refused both look like.
            card.update(
                route=ROUTE_NAMES[event["kind"]],
                label=payload.get("label"),
                failing=payload.get("failing"),
                checks=payload.get("checks"),
                # The route's own reading of how the Run ended. A Run that hit
                # its cap and proposed nothing is journaled `no-proposal` here
                # while `run.outcome` still carries the bound, and the card
                # should say the thing the operator was told on the issue.
                routed_outcome=payload.get("outcome"),
            )
        elif event["kind"] == "run.iteration":
            # The watcher's rows (#157): what the Run is doing, while it
            # does it. Named for what they are rather than folded into the
            # card's `iterations`, which is a COUNT the box reported when the
            # Run ended - a different fact, and one that does not exist yet
            # while the Run is in flight.
            card.setdefault("iteration_records", []).append(
                {**payload, "at": _utc(event["at"])}
            )
        elif event["kind"] == "run.watch-failed":
            # Said once per Run by the watcher, and shown, because a Run with
            # no Iterations on its card and a Run whose Progress Log could not
            # be read look identical otherwise.
            card["watch_error"] = payload.get("error")
        elif event["kind"] == "run.dispatched":
            card.update(
                dispatched=True,
                id=event["id"],
                at=_utc(event["at"]),
                started_at=event["at"],
                **{k: payload.get(k) for k in
                   ("issue", "title", "url", "branch", "task_ref", "area",
                    "check", "attempt", "cycle")},
            )
        else:
            card.update(
                in_flight=False,
                ended_at=_utc(event["at"]),
                finished_at=event["at"],
                **{k: payload.get(k) for k in
                   ("outcome", "exit", "iterations", "faults", "proposal",
                    "notified", "error")},
            )
            # Events arrive newest first, so the route was read BEFORE this
            # row and this update would otherwise overwrite its reading with
            # the raw bound. Where the route renamed the outcome, its name
            # wins: it is what the operator was told on the issue.
            if card.get("routed_outcome"):
                card["outcome"] = card["routed_outcome"]
    # Same rule as the cycle cards: a card whose `run.dispatched` scrolled off
    # the read limit is an absence, not a nameless Run.
    whole = [card for card in cards.values() if card.get("dispatched")]
    for card in whole:
        # The Journal reads newest first; a Run's own Iterations read in the
        # order the Run executed them.
        card["iteration_records"] = sorted(
            card.get("iteration_records", []),
            key=lambda record: record.get("iteration") or 0,
        )
        card["duration"] = _duration(
            card.get("started_at"), card.get("finished_at")
        )
    return sorted(whole, key=lambda c: c["id"], reverse=True)


# --- The status strip -------------------------------------------------------
#
# Unattended operation (#156) is a claim, and these four cells are where it is
# proved on the page: what the Selector is doing now, when the timer fires
# next, how much of today's budget is left, and what the box it dispatches to
# is holding.
#
# None of it is computed here. The budget is read through `cycle.spend` - the
# same function the Selector enforces the cap with, so the page cannot
# reassure about a cap it is not the one reading - and the box facts are
# replayed from the Journal row the cycle wrote. The page stays a window.

# `systemctl show` on the timer, as one substitutable command. A read, no
# privilege, and overridable so the strip can be driven in tests without a
# systemd on the other end.
TIMER_UNIT = "selector-cycle.timer"
TIMER_PROPERTIES = ("ActiveState", "NextElapseUSecRealtime")
# Short: this runs inside a request. A systemd that is not answering must make
# the cell say so rather than hold the page open.
TIMER_TIMEOUT_SECONDS = 5


def _timer() -> dict:
    """When the next cycle fires, and whether anything will fire it.

    The second half is the point. A timer installed and never enabled is
    inert, and the box has had exactly that failure before (gsc-etl, dead in
    the failed-units list for two weeks). A strip that showed only a next-run
    time would render nothing at all for it, which reads as "nothing to
    report" rather than "the Selector is dead".
    """
    command = os.environ.get("SELECTOR_TIMER_COMMAND")
    if command:
        argv = [command]
    elif shutil.which("systemctl"):
        argv = ["systemctl", "show", TIMER_UNIT,
                *(f"-p{name}" for name in TIMER_PROPERTIES)]
    else:
        return {"state": None}
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=TIMER_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return {"state": None}
    if done.returncode != 0:
        return {"state": None}
    read = {}
    for line in done.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in TIMER_PROPERTIES:
            read[key] = value.strip()
    return {
        "state": read.get("ActiveState"),
        # systemd answers `n/a` for a timer with nothing scheduled, which is
        # the honest string to show: an empty cell would be indistinguishable
        # from one this page failed to fill in.
        "next": read.get("NextElapseUSecRealtime") or "n/a",
    }


def _box(events: list[dict]) -> dict | None:
    """The newest thing known about the box, observed or failed.

    Newest of EITHER kind rather than the newest success: a box card that kept
    showing last week's good read while every cycle since had failed to reach
    the box would be the page hiding the one fact worth showing.
    """
    for event in events:  # newest first
        if event["kind"] in ("box.observed", "box.unreachable"):
            # The payload first, the two computed keys after it: the Journal
            # is append-only and its rows outlive this code, so a future
            # payload that happened to carry `at` or `reachable` must not be
            # able to overwrite what the page worked out for itself.
            return {
                **(event["payload"] or {}),
                "at": _utc(event["at"]),
                "reachable": event["kind"] == "box.observed",
            }
    return None


def _selector_state(
    runs: list[dict], budget: dict, timer: dict, paused: bool
) -> str:
    """What the Selector is doing, in the words the Journal already uses.

    The timer is read here and not only in its own cell, because "idle" and
    "cannot run" are not the same answer and this is the cell an operator
    reads first. A Selector with nothing to do and a Selector whose timer was
    never enabled look identical from the Journal - that is the failure this
    whole strip exists to make visible, and it would be odd for the headline
    cell to be the one still reporting business as usual.
    """
    in_flight = [r for r in runs if r.get("in_flight")]
    if in_flight:
        # A Run in flight is a Run in flight whatever the timer is doing: the
        # cycle holding it is already running and will record its outcome.
        return f"in flight: #{in_flight[0]['issue']}"
    if paused:
        return "paused - dispatch is off"
    if timer.get("state") != "active":
        return "stopped - nothing will start a cycle"
    if budget["spent"] >= budget["cap"]:
        return "idle - daily cap reached"
    return "idle"


# How far back the history reaches, counted in Runs rather than in Journal
# rows (#160). A row cap is a Run cap of no fixed size: a Run's rows are
# interleaved with every cycle summary and skip written since, so a busy
# fortnight would quietly push the oldest Runs off the one page whose purpose
# is that they stay inspectable. Fifty is about two weeks at the daily cap;
# what falls past it is counted and said, not dropped in silence.
HISTORY_RUNS = 50


_ReadT = TypeVar("_ReadT")


def _read_journal(
    read: Callable[[psycopg.Connection], _ReadT], default: _ReadT
) -> tuple[_ReadT, "cycle.Spend | None", str | None]:
    """One read of the Journal: what `read` asks of it, and what has been spent.

    Both pages start here, so "the Journal is down" is one sentence written
    once, and `default` is what that sentence leaves the page holding.

    `read(conn)` chooses the rows, because the two pages want different ones:
    /loop wants the newest of every kind, the history wants whole Runs however
    far back they are. It is handed the open connection so that the rows and
    the spend come from one connection and one moment - two reads would be a
    page describing two different Journals.

    `spend` comes back as the Selector's own object rather than a count taken
    here: two readings of "Runs today" that could disagree would be a budget
    that reassures about a cap it is not the one enforcing.
    """
    try:
        with journal.connect() as conn:
            return read(conn), cycle.spend(conn), None
    except psycopg.Error as exc:
        return default, None, f"journal unavailable: {exc}"


def _history_rows(conn) -> tuple[list[dict], int]:
    """The rows of the most recent Runs, and how many older Runs there are.

    Two queries rather than one because the second answer is the point of the
    first: a page that ended at a silent edge would look identical to a
    Journal holding nothing older, and this is the page an operator goes to
    precisely when he is looking for something old.
    """
    floor, older = journal.run_window(conn, HISTORY_RUNS)
    if floor is None:
        return [], 0
    return journal.events(conn, since=floor, kinds=sorted(RUN_KINDS)), older


def _loop_rows(conn) -> tuple[list[dict], bool]:
    """The Journal rows and pause flag rendered together on `/loop`."""
    return journal.events(conn), control.is_paused(conn)


def _budget(cap: int, spend) -> dict:
    """Today's cap, and what is left of it.

    `remaining` rather than only `spent` because that is the operator's
    question - "can the Selector still start one?" - and a tile that leaves
    him to subtract is a tile that gets read wrong the day the cap changes.
    None on an unreadable Journal, all the way through: an unknown budget
    rendered as a full one would be the page inventing headroom.
    """
    spent = None if spend is None else spend.recent_dispatches
    return {
        "spent": spent,
        "cap": cap,
        "window": cycle.CAP_WINDOW_HOURS,
        "remaining": None if spent is None else max(0, cap - spent),
    }


def _loop_context(request: Request) -> dict:
    """Everything the live region renders, read now.

    One reader for both routes. The page and the fragment it updates to are
    the same HTML by construction rather than by two handlers being kept in
    step - a fragment that drifted from the page would show one thing on load
    and another the moment a row landed, which is the failure a live page has
    that a static one cannot.
    """
    config = cycle.Config.from_env()
    empty: tuple[list[dict], bool] = ([], False)
    (events, paused), spend, error = _read_journal(_loop_rows, empty)
    budget = _budget(config.daily_cap, spend)
    view = [
        {
            "id": e["id"],
            "at": _utc(e["at"]),
            "kind": e["kind"],
            "payload": e["payload"],
        }
        for e in events
    ]
    runs = _runs(events)
    timer = _timer()
    # The one panel on this page that is not the Journal replayed: the
    # tracker, read now, columned by the Selector's own Eligibility (#158).
    # Outside the try above because the two are independent - a Journal that
    # is down does not make the queue unknowable, and a tracker that is down
    # does not hide the history.
    board_view = queue_board.board(config, spend)
    return {
        "events": view,
        "cycles": _cycles(events),
        "runs": runs,
        "error": error,
        "budget": budget,
        "timer": timer,
        "box": _box(events),
        "board": board_view,
        "paused": paused if error is None else None,
        "state": (
            _selector_state(runs, budget, timer, paused)
            if error is None else None
        ),
        # Both halves of the live mechanism, resolved in one place: the
        # fragment the region re-fetches and the stream that tells it to. The
        # fragment does not use `events_url` - the page opens the stream once,
        # not per swap - but splitting them across two handlers is how the
        # next one gets wired into the wrong half.
        "live_url": _path(request, "/loop/live"),
        "events_url": _path(request, "/loop/events"),
        "history_url": _path(request, "/loop/history"),
        "pause_url": _path(request, "/loop/pause"),
        "resume_url": _path(request, "/loop/resume"),
    }


def _history_context(request: Request) -> dict:
    """The Run history and the budget: the Journal, and nothing else (#160).

    Deliberately not `_loop_context` minus a few keys. The point of this page
    is what it does NOT read: /loop's board reaches the tracker at request
    time, so /loop is only as available as GitHub, and the record of what the
    Selector has already done should not be. Every Run below is rows the
    Selector wrote - which is also what makes a Run outlive the branch it
    worked on, since a merged-and-deleted branch takes the forge's copy of the
    work with it and leaves the Journal's untouched.
    """
    config = cycle.Config.from_env()
    empty: tuple[list[dict], int] = ([], 0)
    (events, older), spend, error = _read_journal(_history_rows, empty)
    return {
        # History is what has ended. A Run still going has no bound, no
        # duration and no Proposal; /loop shows it live on the panel built
        # for it, and a card here would be three empty cells.
        "runs": [run for run in _runs(events) if not run.get("in_flight")],
        # The Runs below the window, counted rather than dropped in silence.
        "older": older,
        "shown": HISTORY_RUNS,
        "budget": _budget(config.daily_cap, spend),
        "error": error,
        "live_url": _path(request, "/loop/history/live"),
        "events_url": _path(request, "/loop/events"),
        "loop_url": _path(request, "/loop"),
    }


@app.get("/loop", response_class=HTMLResponse)
def loop_page(request: Request):
    """The Loop's window: the Selector Journal, read at request time.

    Sync def on purpose: psycopg blocks, so FastAPI runs this handler in its
    threadpool instead of on the event loop.
    """
    return _page(request, "loop.html", _loop_context(request))


@app.get("/loop/live", response_class=HTMLResponse)
def loop_live(request: Request):
    """The live region on its own, for the swap (#159).

    The whole region rather than a panel per row kind, because a Journal row
    moves several of them at once - a `run.outcome` changes the Run card, the
    status strip's state cell, the budget count and the event table - and a
    page that refreshed only the panel matching the event kind would leave the
    other three saying something that stopped being true. One region is one
    truth; the cost is one tracker read per burst, which is what the trigger's
    delay is for.
    """
    return _page(request, "_loop_live.html", _loop_context(request))


def _set_selector_paused(request: Request, paused: bool):
    """Set the page's one control and return the live region it changes."""
    with journal.connect() as conn:
        control.set_paused(conn, paused)
    return _page(request, "_loop_live.html", _loop_context(request))


@app.post("/loop/pause", response_class=HTMLResponse)
def pause_selector(request: Request):
    return _set_selector_paused(request, True)


@app.post("/loop/resume", response_class=HTMLResponse)
def resume_selector(request: Request):
    return _set_selector_paused(request, False)


@app.get("/loop/history", response_class=HTMLResponse)
def history_page(request: Request):
    """Every Run that has ended, and what is left of today's budget (#160)."""
    return _page(request, "history.html", _history_context(request))


@app.get("/loop/history/live", response_class=HTMLResponse)
def history_live(request: Request):
    """The history's live region on its own, for the swap.

    Its own route rather than /loop/live: the two pages show different
    regions, and a history page that re-fetched /loop's would swap a queue
    board it never rendered into the middle of itself - and reach the tracker
    to build it, which is the one thing this page does not do.
    """
    return _page(request, "_history_live.html", _history_context(request))


# --- The push ---------------------------------------------------------------

# What the browser is told to wait before reconnecting, in milliseconds. Short:
# a reconnect costs one LISTEN and one indexed read, and the gap is the window
# in which the page is silently stale.
SSE_RETRY_MS = 3000


def _sse(data: str, *, event: str, ident: int | None = None) -> str:
    lines = [] if ident is None else [f"id: {ident}"]
    lines += [f"event: {event}", f"data: {data}", ""]
    return "\n".join(lines) + "\n"


async def _journal_stream(after: int | None):
    """The Journal's NOTIFY, framed as Server-Sent Events.

    SSE rather than a WebSocket for the reason the rest of this app is
    server-rendered: the traffic is one-directional and the page's only write
    is the pause flag's POST. EventSource also reconnects on its own, and
    carries `Last-Event-ID` when it does, so recovering from a dropped
    connection is a header this endpoint honours rather than client state to
    keep.
    """
    yield f"retry: {SSE_RETRY_MS}\n\n"
    try:
        async for what, payload in journal.listen(after=after):
            if what == "silence":
                # A comment: it keeps the connection warm and is ignored by
                # EventSource, so it cannot be mistaken for a Journal row.
                yield ": keepalive\n\n"
            elif what == "ready":
                yield _sse(json.dumps(payload), event="ready")
            else:
                yield _sse(
                    json.dumps(payload), event="journal", ident=payload["id"]
                )
    except psycopg.Error as exc:
        # Say so and end, rather than hold a connection that will never carry
        # an event. EventSource retries after SSE_RETRY_MS, so a Journal that
        # comes back is picked up without anyone reloading the page.
        yield _sse(json.dumps({"error": str(exc)}), event="error")


@app.get("/loop/events")
async def loop_events(request: Request):
    """Journal rows pushed to an open /loop, the moment they land.

    Async on purpose, the opposite of every other handler here: this one is
    held open for as long as the page is, and a sync handler would hold one of
    the threadpool's threads for those hours.
    """
    last = request.headers.get("Last-Event-ID")
    try:
        after = int(last) if last else None
    except ValueError:
        # Whatever the client sent. An unreadable one means "start from now",
        # which is the same thing a first connection means.
        after = None
    return StreamingResponse(
        _journal_stream(after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Not for Caddy, which is what fronts this app today and which
            # flushes a `text/event-stream` on its own - measured through the
            # real `encode gzip` + `reverse_proxy` pair, events landing the
            # moment their row did. It is for an nginx-family proxy, which
            # buffers by default and would hold every event until the page
            # closed. A failure that looks exactly like a Selector that never
            # runs is worth one header nobody currently reads.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/healthz", response_class=HTMLResponse)
async def healthz():
    return HTMLResponse("ok")
