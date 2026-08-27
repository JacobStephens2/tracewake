"""lab.etadventures.com — the lab front door.

Built with FastAPI + Jinja2 + HTMX: server renders HTML, HTMX swaps in
server-rendered fragments, no client-side framework and no build step. The
site is itself a demonstration of the stack the ADR chooses (docs/adr/).
"""
# The venv here is the system Python (3.9), so `dict | None` in an annotation
# is a runtime TypeError without this. Same import the Selector's own modules
# carry, for the same reason.
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import timezone
from pathlib import Path

import markdown as md
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent / "single-user-factory"
ADR_DIR = PROJECT / "docs" / "adr"

# The Selector Journal's writer/reader module lives with the Selector; this
# app is its window (ADR 0015), so import it from there rather than forking
# the SQL.
sys.path.insert(0, str(PROJECT / "selector"))
import cycle  # noqa: E402
import journal  # noqa: E402

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
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "stages": PIPELINE, "adrs": _all_adrs()},
    )


@app.post("/demo/run", response_class=HTMLResponse)
async def demo_run(request: Request):
    """HTMX POSTs here; the server returns an HTML fragment, not JSON."""
    return templates.TemplateResponse(
        "_run.html", {"request": request, "stages": PIPELINE}
    )


@app.get("/adr", response_class=HTMLResponse)
async def adr_index(request: Request):
    return templates.TemplateResponse(
        "adr_index.html", {"request": request, "adrs": _all_adrs()}
    )


@app.get("/adr/{slug}", response_class=HTMLResponse)
async def adr_detail(request: Request, slug: str):
    path = ADR_DIR / f"{slug}.md"
    if not path.is_file() or "/" in slug or ".." in slug:
        return templates.TemplateResponse(
            "adr_missing.html", {"request": request, "slug": slug}, status_code=404
        )
    adr = _parse_adr(path)
    adr["html"] = md.markdown(
        adr["body"], extensions=["fenced_code", "tables", "sane_lists"]
    )
    return templates.TemplateResponse(
        "adr_detail.html", {"request": request, "adr": adr}
    )


def _utc(at) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


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
RUN_KINDS = ROUTE_KINDS | {"run.dispatched", "run.outcome"}


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
        elif event["kind"] == "run.dispatched":
            card.update(
                dispatched=True,
                id=event["id"],
                at=_utc(event["at"]),
                **{k: payload.get(k) for k in
                   ("issue", "title", "url", "branch", "task_ref", "area",
                    "check", "attempt", "cycle")},
            )
        else:
            card.update(
                in_flight=False,
                ended_at=_utc(event["at"]),
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
    argv = (
        [command]
        if command
        else [
            "systemctl", "show", TIMER_UNIT,
            *(f"-p{name}" for name in TIMER_PROPERTIES),
        ]
    )
    if not command and not shutil.which("systemctl"):
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


def _selector_state(runs: list[dict], budget: dict) -> str:
    """What the Selector is doing, in the words the Journal already uses."""
    in_flight = [r for r in runs if r.get("in_flight")]
    if in_flight:
        return f"in flight: #{in_flight[0]['issue']}"
    if budget["spent"] >= budget["cap"]:
        return "idle - daily cap reached"
    return "idle"


@app.get("/loop", response_class=HTMLResponse)
def loop_page(request: Request):
    """The Loop's window: the Selector Journal, read at request time.

    Sync def on purpose: psycopg blocks, so FastAPI runs this handler in its
    threadpool instead of on the event loop.
    """
    config = cycle.Config.from_env()
    events, error = [], None
    budget = {"spent": None, "cap": config.daily_cap,
              "window": cycle.CAP_WINDOW_HOURS}
    try:
        with journal.connect() as conn:
            events = journal.events(conn)
            # Read through the Selector's own function rather than counted
            # here: two readings of "Runs today" that could disagree would be
            # a strip that reassures about a cap it is not the one enforcing.
            budget["spent"] = cycle.spend(conn).recent_dispatches
    except psycopg.Error as exc:
        error = f"journal unavailable: {exc}"
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
    return templates.TemplateResponse(
        "loop.html",
        {
            "request": request,
            "events": view,
            "cycles": _cycles(events),
            "runs": runs,
            "error": error,
            "budget": budget,
            "timer": _timer(),
            "box": _box(events),
            "state": _selector_state(runs, budget) if error is None else None,
        },
    )


@app.get("/healthz", response_class=HTMLResponse)
async def healthz():
    return HTMLResponse("ok")
