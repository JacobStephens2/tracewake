"""lab.etadventures.com — the lab front door.

Built with FastAPI + Jinja2 + HTMX: server renders HTML, HTMX swaps in
server-rendered fragments, no client-side framework and no build step. The
site is itself a demonstration of the stack the ADR chooses (docs/adr/).
"""
import re
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


@app.get("/loop", response_class=HTMLResponse)
def loop_page(request: Request):
    """The Loop's window: the Selector Journal, read at request time.

    Sync def on purpose: psycopg blocks, so FastAPI runs this handler in its
    threadpool instead of on the event loop.
    """
    events, error = [], None
    try:
        with journal.connect() as conn:
            events = journal.events(conn)
    except psycopg.Error as exc:
        error = f"journal unavailable: {exc}"
    view = [
        {
            "id": e["id"],
            "at": e["at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "kind": e["kind"],
            "payload": e["payload"],
        }
        for e in events
    ]
    return templates.TemplateResponse(
        "loop.html", {"request": request, "events": view, "error": error}
    )


@app.get("/healthz", response_class=HTMLResponse)
async def healthz():
    return HTMLResponse("ok")
