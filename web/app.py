"""Tracewake's window — the front door of an instance.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import markdown as md
import psycopg
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
# The repository root. The window renders the project it is part of - the
# ADRs, the lessons page, the notes - so the project is one directory up
# from `web/` rather than a sibling checkout to be found.
PROJECT = BASE.parent
ADR_DIR = PROJECT / "docs" / "adr"

# The Selector Journal's writer/reader module lives with the Selector; this
# app is its window (ADR 0015), so import it from there rather than forking
# the SQL.
sys.path.insert(0, str(PROJECT / "selector"))
import board as queue_board  # noqa: E402
import control  # noqa: E402
import cycle  # noqa: E402
import events  # noqa: E402
import journal  # noqa: E402
import targets  # noqa: E402

import preview  # noqa: E402
import auth  # noqa: E402
import host  # noqa: E402
import mail  # noqa: E402

# Named for the product, not for the host it is published on: where an
# instance publishes its window is a fact about that instance (issue #3),
# and it is carried in SELECTOR_LOOP_URL where a notice needs it.
app = FastAPI(title="Tracewake")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
# The project's own files served static; the dynamic home is the FastAPI app
# itself. One mount per content tree rather than one at the repository root,
# because the root now holds `.git/` and every credential-shaped thing a
# checkout carries, and StaticFiles serves whatever is under the directory it
# is given. Naming the content trees is the difference between serving the
# documents and serving the repository.
for _tree in ("docs", "notes", "research", "site"):
    app.mount(
        f"/{_tree}",
        StaticFiles(directory=PROJECT / _tree, html=True),
        name=_tree,
    )
templates = Jinja2Templates(directory=BASE / "templates")
app.add_middleware(auth.RequireSignIn)

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

    Preview banner (ADR 0016), the session's CSRF token (ADR 0028), and
    whether the account may use the page's controls (issue #40).
    """
    session = getattr(request.state, "session", None)
    account = getattr(request.state, "account", None)
    return templates.TemplateResponse(
        name,
        {
            "request": request,
            "preview": preview.banner(),
            "static_base": _static_base(request),
            "csrf_token": session.csrf_token if session else "",
            "logout_url": _path(request, "/logout"),
            "accounts_url": _path(request, "/accounts"),
            "account": account,
            "can_control": account is not None and account.role == "admin",
            **context,
        },
        **kwargs,
    )


async def require_csrf(request: Request) -> None:
    offered = request.headers.get("x-csrf-token")
    if not offered:
        form = await request.form()
        offered = form.get("csrf_token")
    if not auth.csrf_ok(request, offered):
        raise auth.CSRFDenied()


@app.exception_handler(auth.CSRFDenied)
async def _csrf_denied(request: Request, exc: auth.CSRFDenied):
    return HTMLResponse("CSRF token missing or invalid", status_code=403)


@app.exception_handler(auth.NotAuthorised)
async def _not_authorised(request: Request, exc: auth.NotAuthorised):
    return auth.refuse()


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


@app.get("/sign-in", response_class=HTMLResponse)
def sign_in_form(request: Request, next: str = "/"):
    """Sign-in page. Public; mints an anonymous session to hold the CSRF token."""
    session = getattr(request.state, "session", None)
    minted = None
    if session is None:
        minted = auth.new_anonymous_session()
        session = auth.load_session(minted)
        request.state.session = session
    response = _page(
        request, "sign_in.html",
        {"error": None, "next_url": auth.safe_next(next)},
    )
    if minted:
        auth.set_session_cookie(response, minted)
    return response


@app.post("/sign-in", dependencies=[Depends(require_csrf)])
def sign_in_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    account = auth.authenticate(email, password)
    if account is None:
        return _page(
            request, "sign_in.html",
            {"error": auth.SIGN_IN_ERROR, "next_url": auth.safe_next(next)},
        )
    presented = auth.session_token_from_request(request)
    raw = auth.create_session(account.id, replacing=presented)
    root = request.scope.get("root_path", "") or ""
    response = RedirectResponse(root + auth.safe_next(next), status_code=303)
    auth.set_session_cookie(response, raw)
    return response


@app.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request):
    session = getattr(request.state, "session", None)
    if session is not None:
        auth.destroy_session(session.token_hash)
    root = request.scope.get("root_path", "") or ""
    response = RedirectResponse(root + "/sign-in", status_code=303)
    auth.clear_session_cookie(response)
    return response


def _mint_anonymous(request: Request):
    """Give a public form a CSRF session if the visitor has none."""
    session = getattr(request.state, "session", None)
    minted = None
    if session is None:
        minted = auth.new_anonymous_session()
        session = auth.load_session(minted)
        request.state.session = session
    return minted


def _token_url(request: Request, kind: str, token: str) -> str:
    """Absolute redeem URL. Prefer the instance's public origin when set."""
    public = os.environ.get("SELECTOR_LOOP_URL", "").strip()
    if public:
        parsed = urlparse(public)
        if parsed.scheme and parsed.netloc:
            root = request.scope.get("root_path", "") or ""
            return f"{parsed.scheme}://{parsed.netloc}{root}/{kind}/{token}"
    return str(request.base_url).rstrip("/") + f"/{kind}/{token}"


def _accounts_page(request: Request, *, error: str | None = None):
    return _page(request, "accounts.html", {
        "accounts": auth.list_accounts(),
        "error": error,
        "roles": auth.ROLES,
    })


admin_pages = APIRouter(dependencies=[Depends(auth.require_admin)])


@admin_pages.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    return _accounts_page(request)


@admin_pages.post("/accounts", dependencies=[Depends(require_csrf)])
def accounts_invite(
    request: Request,
    email: str = Form(""),
    role: str = Form("reader"),
):
    email = email.strip()
    if not email:
        return _accounts_page(request, error="An email address is required.")
    try:
        mail.command()
        _, raw = auth.create_invite(email, role)
    except auth.InvalidRole:
        return _accounts_page(request, error="Role must be admin or reader.")
    except auth.AccountExists:
        return _accounts_page(
            request, error="That email is already an account.",
        )
    except targets.NotConfigured as exc:
        return _accounts_page(request, error=str(exc))
    link = _token_url(request, "invite", raw)
    body = (
        f"You have been invited to this Tracewake window as {role}.\n\n"
        f"Set your password at:\n{link}\n\n"
        "This link works once and expires in about 72 hours.\n"
    )
    try:
        mail.send(
            to=email.strip().lower(),
            subject="You're invited to this Tracewake window",
            link=link,
            body=body,
        )
    except mail.MailFailed as exc:
        return _accounts_page(request, error=str(exc))
    root = request.scope.get("root_path", "") or ""
    return RedirectResponse(root + "/accounts", status_code=303)


@admin_pages.post(
    "/accounts/{account_id}/role", dependencies=[Depends(require_csrf)],
)
def accounts_role(
    request: Request, account_id: int, role: str = Form(""),
):
    try:
        auth.set_role(account_id, role)
    except auth.InvalidRole:
        return _accounts_page(request, error="Role must be admin or reader.")
    root = request.scope.get("root_path", "") or ""
    return RedirectResponse(root + "/accounts", status_code=303)


@admin_pages.post(
    "/accounts/{account_id}/deactivate", dependencies=[Depends(require_csrf)],
)
def accounts_deactivate(request: Request, account_id: int):
    auth.deactivate(account_id)
    root = request.scope.get("root_path", "") or ""
    return RedirectResponse(root + "/accounts", status_code=303)


app.include_router(admin_pages)


@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request):
    minted = _mint_anonymous(request)
    response = _page(request, "forgot.html", {"sent": False})
    if minted:
        auth.set_session_cookie(response, minted)
    return response


@app.post("/forgot", dependencies=[Depends(require_csrf)])
def forgot_post(request: Request, email: str = Form("")):
    minted = _mint_anonymous(request)
    raw = None
    try:
        mail.command()
        raw = auth.create_reset(email)
    except targets.NotConfigured:
        raw = None
    if raw:
        link = _token_url(request, "reset", raw)
        body = (
            "A password reset was requested for this Tracewake window.\n\n"
            f"Set a new password at:\n{link}\n\n"
            "This link works once and expires in about an hour. "
            "If you did not request it, you can ignore this.\n"
        )
        try:
            mail.send(
                to=email.strip().lower(),
                subject="Reset your Tracewake window password",
                link=link,
                body=body,
            )
        except mail.MailFailed:
            pass
    # The same page for every address: known, unknown, never-activated.
    response = _page(request, "forgot.html", {
        "sent": True,
        "error": None,
    })
    if minted:
        auth.set_session_cookie(response, minted)
    return response


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str):
    minted = _mint_anonymous(request)
    live = auth.reset_is_live(token)
    response = _page(request, "reset.html", {
        "live": live,
        "error": None if live else "This reset is not valid.",
        "token": token,
    })
    if minted:
        auth.set_session_cookie(response, minted)
    return response


@app.post("/reset/{token}", dependencies=[Depends(require_csrf)])
def reset_redeem(
    request: Request, token: str, password: str = Form(""),
):
    try:
        account_id = auth.consume_reset(token, password)
    except auth.ResetInvalid:
        return _page(request, "reset.html", {
            "live": False,
            "error": "This reset is not valid.",
            "token": token,
        })
    presented = auth.session_token_from_request(request)
    raw = auth.create_session(account_id, replacing=presented)
    root = request.scope.get("root_path", "") or ""
    response = RedirectResponse(root + "/", status_code=303)
    auth.set_session_cookie(response, raw)
    return response


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_form(request: Request, token: str):
    minted = _mint_anonymous(request)
    live = auth.invite_is_live(token)
    response = _page(request, "invite.html", {
        "live": live,
        "error": None if live else "This invite is not valid.",
        "token": token,
    })
    if minted:
        auth.set_session_cookie(response, minted)
    return response


@app.post("/invite/{token}", dependencies=[Depends(require_csrf)])
def invite_redeem(
    request: Request, token: str, password: str = Form(""),
):
    try:
        account_id = auth.consume_invite(token, password)
    except auth.InviteInvalid:
        return _page(request, "invite.html", {
            "live": False,
            "error": "This invite is not valid.",
            "token": token,
        })
    presented = auth.session_token_from_request(request)
    raw = auth.create_session(account_id, replacing=presented)
    root = request.scope.get("root_path", "") or ""
    response = RedirectResponse(root + "/", status_code=303)
    auth.set_session_cookie(response, raw)
    return response


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


# The operator's clock. Everything stored and exchanged stays UTC; this is
# display only, and the abbreviation the strftime renders (EDT/EST) says so on
# every timestamp.
DISPLAY_TZ = ZoneInfo("America/New_York")


def _local(at) -> str:
    return at.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _local_timer_next(value: str) -> str:
    """systemd's NextElapseUSecRealtime, moved to the operator's clock.

    The box answers in its own timezone (UTC there), as a formatted string
    rather than an instant. Anything that does not parse as that shape -
    `n/a`, a box whose clock is not UTC, a format change - is shown as
    answered rather than guessed at.
    """
    try:
        at = datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return value
    return at.astimezone(DISPLAY_TZ).strftime("%a %Y-%m-%d %H:%M:%S %Z")


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


def _cycles(rows: list[dict]) -> list[dict]:
    """Group Journal rows into one card per Selector cycle, newest first.

    A cycle's events all carry the id of its `cycle.started` row in
    `payload.cycle`, so the grouping is the Journal's own, not a guess made
    here. This page is a window and a scribe: it re-renders what the Selector
    decided and decides nothing itself (ADR 0015) - and what each row holds is
    the vocabulary's knowledge, read through its records rather than re-guessed
    here as key tuples.
    """
    cards: dict[int, dict] = {}
    for row in rows:  # newest first
        kind = row["kind"]
        if kind == events.CYCLE_STARTED:
            # `started` marks the card as whole. The Journal read is capped,
            # so the oldest cycle on the page is usually cut in half by the
            # limit, and a card built from the leftovers would render as a
            # nameless, timeless cycle rather than as the absence it is.
            started = events.cycle_started_record(row)
            card = cards.setdefault(started.id, {"skips": []})
            card.update(
                started=True,
                id=started.id,
                at=_local(started.at),
                repo=started.repo,
                label=started.label,
                dry_run=started.dry_run,
            )
            continue
        cycle_id = (row["payload"] or {}).get("cycle")
        if cycle_id is None:
            continue
        card = cards.setdefault(cycle_id, {"skips": []})
        if kind == events.ISSUE_SKIPPED:
            card["skips"].append(events.issue_skipped_record(row))
        elif kind == events.CYCLE_PICKED:
            card["pick"] = events.cycle_picked_record(row)
        elif kind == events.CYCLE_FINISHED:
            card["summary"] = events.cycle_finished_record(row)
        elif kind == events.CYCLE_FAILED:
            card["failed"] = events.cycle_failed_record(row).error
        elif kind in (events.ISSUE_RETURNED, events.ISSUE_RETURN_FAILED):
            # The loud skip's second half. Kept beside the skip it belongs to
            # rather than in a list of its own: "skipped, and handed back" is
            # one fact about one issue.
            handed = events.issue_returned_record(row)
            card.setdefault("returned", {})[handed.number] = (
                handed.error or True
            )
    whole = [card for card in cards.values() if card.get("started")]
    for card in whole:
        # The Journal reads newest first; a cycle's own skips read better in
        # the order it decided them.
        card["skips"].reverse()
    return sorted(whole, key=lambda c: c["id"], reverse=True)


# The Journal rows that say where an outcome put the issue (#155). One kind
# per route so that the Journal is greppable by outcome, which means the page
# has to know the set rather than matching a prefix. The set is the
# vocabulary's own declaration now - this used to be spelled out here so a
# rename would fail loudly, and the failure that spelling could not catch (a
# renamed Route quietly un-matching the map) is caught earlier still by the
# constructors. The names also reach the page as CSS classes
# (`badge-awaiting-review`); the badge-pin test in test_loop_page.py is what
# keeps a rename from quietly unstyling a card.
ROUTE_NAMES = dict(events.ROUTE_KIND_NAMES)
ROUTE_KINDS = set(ROUTE_NAMES)

# Every kind a Run card is built from, so the membership test is one lookup
# rather than a set union rebuilt per event.
RUN_KINDS = ROUTE_KINDS | {
    events.RUN_DISPATCHED, events.RUN_OUTCOME, events.RUN_ITERATION,
    events.RUN_WATCH_FAILED, events.RUN_CONTRACT,
}


def _runs(rows: list[dict]) -> list[dict]:
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
    for row in rows:  # newest first
        kind = row["kind"]
        payload = row["payload"] or {}
        if kind not in RUN_KINDS:
            continue
        if payload.get("issue") is None:
            continue
        key = (payload["issue"], payload.get("attempt"))
        card = cards.setdefault(key, {"in_flight": True})
        if kind in ROUTE_KINDS:
            # Where the outcome put the issue (#155). Journaled as its own row
            # rather than folded into `run.outcome`, because the route is
            # decided after that row is written - so the card learns it from
            # the routing event or shows no badge at all, which is what an
            # in-flight Run and a route the tracker refused both look like.
            routed = events.route_record(row)
            card.update(
                route=routed.route,
                label=routed.label,
                failing=routed.failing,
                checks=routed.checks,
            )
            # The route's own reading of how the Run ended: a Run that hit
            # its cap and proposed nothing is `no-proposal` here while
            # `run.outcome` carries the bound, and the card says the thing
            # the operator was told on the issue. Rows arrive newest first,
            # so this lands before the outcome row's fallback - and only when
            # the route actually carries a reading, because a thin route row
            # (a hand append; readers are total over those) must not pin the
            # card's outcome to nothing.
            if routed.outcome is not None:
                card["outcome"] = routed.outcome
        elif kind == events.RUN_ITERATION:
            # The watcher's rows (#157): what the Run is doing, while it
            # does it. Named for what they are rather than folded into the
            # card's `iterations`, which is a COUNT the box reported when the
            # Run ended - a different fact, and one that does not exist yet
            # while the Run is in flight.
            record = events.run_iteration_record(row)
            card.setdefault("iteration_records", []).append({
                "iteration": record.iteration,
                "started": record.started,
                "agent_exit": record.agent_exit,
                "exit_note": record.exit_note,
                "turn_bound": record.turn_bound,
                "noop": record.noop,
                "head_before": record.head_before,
                "head_after": record.head_after,
                "promise": record.promise,
                "dirty": record.dirty,
                "at": _local(record.at),
            })
        elif kind == events.RUN_CONTRACT:
            # The terms this Run is executing under (#162), read by the
            # watcher out of the summary the box writes at Run start. It is
            # the BOX's Contract and not this side's configuration: the two
            # can differ, and a panel showing the wrong one would reassure
            # about bounds nothing is enforcing.
            card["contract"] = events.run_contract_record(row).contract
        elif kind == events.RUN_WATCH_FAILED:
            # Said once per Run by the watcher, and shown, because a Run with
            # no Iterations on its card and a Run whose Progress Log could not
            # be read look identical otherwise.
            card["watch_error"] = events.run_watch_failed_record(row).error
        elif kind == events.RUN_DISPATCHED:
            record = events.run_dispatched_record(row)
            card.update(
                dispatched=True,
                id=record.id,
                at=_local(record.at),
                started_at=record.at,
                issue=record.issue,
                title=record.title,
                url=record.url,
                branch=record.branch,
                task_ref=record.task_ref,
                area=record.area,
                check=record.check,
                attempt=record.attempt,
                cycle=record.cycle,
            )
        else:
            record = events.run_outcome_record(row)
            card.update(
                in_flight=False,
                ended_at=_local(record.at),
                finished_at=record.at,
                exit=record.exit,
                iterations=record.iterations,
                faults=record.faults,
                proposal=record.proposal,
                notified=record.notified,
                error=record.error,
            )
            # The route row - read above, because rows arrive newest first -
            # owns the outcome's NAME. The raw bound stands in only when no
            # route was journaled, which is what an in-flight Run and a route
            # the tracker refused both look like.
            card.setdefault("outcome", record.ended_by)
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
        "next": _local_timer_next(read.get("NextElapseUSecRealtime") or "n/a"),
    }


def _newest(rows: list[dict], kinds: tuple, reader):
    """The newest of a status read's two rows, whichever kind it is.

    Both cells the cycle fills in - the box card and the guardrail chip - are
    one reading replayed, and both need the same rule: newest of EITHER kind
    rather than newest success. A card that kept showing last week's good read
    while every cycle since had failed would be the page hiding the one fact
    worth showing.

    Returns `(record, card)`: the vocabulary's record of the row, and the
    page's own additions - the localized `at` and the reading's age. What the
    record knows is attributes, so no payload key, present or future, can
    overwrite it; the old dict-splat here had to defend that with a reserved-
    key convention nothing recorded.
    """
    for row in rows:  # newest first
        if row["kind"] in kinds:
            return reader(row), {
                "at": _local(row["at"]),
                "age": datetime.now(timezone.utc) - row["at"],
            }
    return None


def _box(rows: list[dict]) -> dict | None:
    """The newest thing known about the box, observed or failed.

    The credential's remaining life is worked out HERE rather than journaled,
    and that is the one place this page does arithmetic on a fact instead of
    replaying it. The distinction that makes it legitimate: the Journal holds
    the absolute instant the box gave, which is an observation, and what is
    left of it is a pure function of that instant and the clock on the wall
    when somebody looks. A duration recorded at read time would be wrong by
    however long the page sat open - and wrong in the reassuring direction,
    which is the failure #260 is about.

    That is not the guardrail chip's rule being broken. `cycle.py` grades the
    guardrail because whether the paths are protected is a judgement with
    rejected alternatives in it; whether an instant has passed is not.
    """
    found = _newest(
        rows, (events.BOX_OBSERVED, events.BOX_UNREACHABLE), events.box_record
    )
    if found is None:
        return None
    record, card = found
    card.update(
        reachable=record.reachable,
        error=record.error,
        scripts_hash=record.scripts_hash,
        guest_template=record.guest_template,
        agent=record.agent,
        agent_version=record.agent_version,
        credential_expires_at=record.credential_expires_at,
        credential_expired=None,
        credential_remaining=None,
    )
    expires_at = record.credential_expires_at
    if expires_at:
        try:
            expiry = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            # An instant this cannot parse is left as unknown rather than
            # guessed at. The box is the only thing that knows the shape, and
            # a card that rendered an unparsed string as "expired" would page
            # for a format change.
            return card
        now = datetime.now(timezone.utc)
        card["credential_expired"] = expiry <= now
        card["credential_remaining"] = _duration(now, expiry)
        # Parsed, so it can be shown on the operator's clock like every other
        # instant on the page. The unparsed branch above keeps the raw string.
        card["credential_expires_at"] = _local(expiry)
    return card


# How old a guardrail reading may be and still stand for now. The timer fires
# every thirty minutes, so this is two missed cycles: recent enough that an
# ordinary reading is never called stale, short enough that a dead Selector
# stops asserting protection it has not checked since. The failure is the one
# the timer cell already guards against, one panel along - a page whose newest
# row is from a cycle that never ran again would otherwise show green for as
# long as it was left open.
GUARDRAIL_MAX_AGE = timedelta(minutes=90)


def _guardrail(rows: list[dict]) -> dict | None:
    """The newest reading of the write protection over the executed paths.

    The box card's rule, turned up one notch by `stale`: a chip is a claim
    about the present, and this one is only as good as the cycle that took it.

    The verdict itself is NOT computed here. `cycle.py` decided it when it
    read the guardrail, and a page that graded the facts a second time would
    be a second opinion about whether the Selector is protected, with no way
    to tell which of the two had been reviewed.
    """
    found = _newest(
        rows,
        (events.GUARDRAIL_OBSERVED, events.GUARDRAIL_UNREADABLE),
        events.guardrail_record,
    )
    if found is None:
        return None
    record, card = found
    card.update(
        readable=record.readable,
        protected=record.protected,
        error=record.error,
        ref=record.ref,
        ref_head=record.ref_head,
        rules=record.rules,
        paths=record.paths,
        unreviewed=record.unreviewed,
        detail=record.detail,
        trees=record.trees,
        stale=card["age"] > GUARDRAIL_MAX_AGE,
    )
    return card


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
    if budget.get("remaining") == 0:
        return "idle - review cap reached"
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

    `spend` comes back as the Selector's own object for in-flight and attempt
    queries on the board.
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


def _config() -> tuple["cycle.Config | None", str | None]:
    """The first declared target's configuration, or why there is none.

    The window renders one target today - `/` growing a target switcher is
    the board's own ticket - so it takes the first stanza in the targets
    file. What it must not do is fail: an instance whose configuration is
    missing or malformed is exactly when somebody opens the page, so the
    refusal is carried as a sentence into the board's columns rather than
    raised as a 500 (issue #3).
    """
    try:
        configs = cycle.Config.load()
    except targets.NotConfigured as exc:
        return None, str(exc)
    return configs[0], None


def _gib(n: int) -> str:
    """Bytes as a GiB figure the widget can print.

    Under 10 GiB keeps one decimal so 2.0 and 8.0 stay distinct from 2 and 8;
    at 10 and above the tenth is noise on a disk measured in tens.
    """
    value = n / (1024 ** 3)
    if value >= 10:
        return f"{value:.0f} GiB"
    return f"{value:.1f} GiB"


def _host(spend: "cycle.Spend | None") -> dict:
    """The Host's headroom, plus how many Runs the Journal has in flight.

    Sampler failure is a degraded widget, never a missing board: the queue
    is the page, and a /proc read that failed is not a reason to hide it.
    The in-flight count is the Journal's, through Eligibility's own Spend,
    even when the sampler could not answer. Figures are formatted here so
    the template does not branch three times on the same ok flag.
    """
    in_flight = None if spend is None else spend.runs_in_flight()
    unknown = {
        "ok": False,
        "cpu": "unknown",
        "memory": "unknown",
        "disk": "unknown",
        "disk_path": None,
        "in_flight": in_flight,
    }
    try:
        facts = host.sample()
    except Exception as exc:
        return {**unknown, "error": str(exc)}
    return {
        "ok": True,
        "error": None,
        "cpu": f"{round(facts.cpu_percent)}%",
        "memory": f"{_gib(facts.memory_used)} of {_gib(facts.memory_total)}",
        "disk": f"{_gib(facts.disk_used)} of {_gib(facts.disk_total)}",
        "disk_path": facts.disk_path,
        "in_flight": in_flight,
    }


def _loop_context(request: Request) -> dict:
    """Everything the live region renders, read now.

    One reader for both routes. The page and the fragment it updates to are
    the same HTML by construction rather than by two handlers being kept in
    step - a fragment that drifted from the page would show one thing on load
    and another the moment a row landed, which is the failure a live page has
    that a static one cannot.
    """
    config, unconfigured = _config()
    empty: tuple[list[dict], bool] = ([], False)
    (events, paused), spend, error = _read_journal(_loop_rows, empty)
    view = [
        {
            "id": e["id"],
            "at": _local(e["at"]),
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
    board_view = (
        queue_board.board(config, spend) if config
        else queue_board.unconfigured(unconfigured)
    )
    host_view = _host(spend)
    review_col = next(
        (c for c in board_view.get("columns", []) if c.get("key") == "awaiting-review"),
        None,
    )
    budget = cycle.review_budget(
        config,
        review=review_col["cards"] if review_col and not review_col.get("error") else None,
        error=review_col.get("error") if review_col else None,
        timeout=config.board_timeout_seconds if config else None,
    )
    return {
        "events": view,
        "cycles": _cycles(events),
        "runs": runs,
        "error": error,
        "budget": budget,
        "timer": timer,
        "box": _box(events),
        "guardrail": _guardrail(events),
        "board": board_view,
        "host": host_view,
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
        "history_url": _path(request, "/history"),
        "pause_url": _path(request, "/loop/pause"),
        "resume_url": _path(request, "/loop/resume"),
    }


def _history_context(request: Request) -> dict:
    """The Run history: the Journal, and nothing else (#160).

    Deliberately not `_loop_context` minus a few keys. The point of this page
    is what it does NOT read: /loop's board reaches the tracker at request
    time, so /loop is only as available as GitHub, and the record of what the
    Selector has already done should not be. Every Run below is rows the
    Selector wrote - which is also what makes a Run outlive the branch it
    worked on, since a merged-and-deleted branch takes the forge's copy of the
    work with it and leaves the Journal's untouched.
    """
    empty: tuple[list[dict], int] = ([], 0)
    (events, older), _, error = _read_journal(_history_rows, empty)
    return {
        # History is what has ended. A Run still going has no bound, no
        # duration and no Proposal; /loop shows it live on the panel built
        # for it, and a card here would be three empty cells.
        "runs": [run for run in _runs(events) if not run.get("in_flight")],
        # The Runs below the window, counted rather than dropped in silence.
        "older": older,
        "shown": HISTORY_RUNS,
        "error": error,
        "live_url": _path(request, "/loop/history/live"),
        "events_url": _path(request, "/loop/events"),
        "loop_url": _path(request, "/loop"),
        "board_url": _path(request, "/"),
    }


@app.get("/", response_class=HTMLResponse)
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


# Controls live on their own router so a later POST is gated by construction
# rather than by remembering to add Depends(require_admin) to the decorator
# (issue #40). CSRF rides the same list.
controls = APIRouter(
    dependencies=[Depends(auth.require_admin), Depends(require_csrf)],
)


@controls.post("/loop/pause", response_class=HTMLResponse)
def pause_selector(request: Request):
    return _set_selector_paused(request, True)


@controls.post("/loop/resume", response_class=HTMLResponse)
def resume_selector(request: Request):
    return _set_selector_paused(request, False)


app.include_router(controls)


@app.get("/history", response_class=HTMLResponse)
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


@app.get("/events")
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
