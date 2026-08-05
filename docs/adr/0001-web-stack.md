---
status: accepted
---

# Web stack: FastAPI + Jinja2 + HTMX, server-rendered, no build step

The single-user factory's web layer is FastAPI (Python) rendering HTML with
Jinja2, and HTMX for interactivity — the server returns HTML fragments, not
JSON, and there is no client-side framework or build step. Chosen because the
factory's orchestration is already Python (one language end to end) and
iteration speed, not client richness, is the binding constraint for a single
operator.

## Considered options

- **SPA (React/Vue) + JSON API** — rejected. Maintains two artifacts in two
  languages behind an npm/bundler pipeline; the client-side richness it buys is
  overhead for a tool with one user and simple screens (run list, trigger, live
  progress, evidence view).
- **Flask + Jinja2** (the ETA status-dashboard on this box) — viable and proven
  here, but FastAPI's async model fits the I/O-bound LLM calls a factory makes,
  and typed request models are a small ongoing win. Flask would have been fine;
  FastAPI is the marginally better fit at no real cost.
- **Rust + Askama SSR** (the ETA factory dashboard) — rejected. Correct at
  company/production stakes; the justifications for it (security surface,
  adversarial correctness) evaporate for a single-user tool, and it would break
  the "Python everywhere" consistency that is this project's main velocity lever.

## Consequences

- One language across orchestration and UI; no JS toolchain, no build step.
  HTMX is vendored locally (`static/htmx.min.js`), no CDN dependency.
- Interactions take a server round-trip that an SPA would do client-side. For a
  local, single-user tool this is imperceptible and worth the simplicity.
- The build-light front-end discipline is inherited from the ETA factory
  (SSR-only, no WASM/npm/TS) but for the cheaper reason — velocity, not a
  security boundary. See `../../notes/lessons.md`.
- Deployed as a `uvicorn` systemd unit behind Caddy, gated by the dashboard
  session — mirroring the status-dashboard's Flask-behind-Caddy pattern. On this
  SELinux host the venv's `.venv/bin` needs a `bin_t` fcontext so systemd may
  exec it (the established pattern for `/srv/orchestration/*/.venv/bin`).
