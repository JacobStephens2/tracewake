---
status: superseded by ADR 0014
---

# [Retired: 2026-09-20, superseded by ADR 0010 and ADR 0014] Web dashboard from day one, driven by multimodal (text + image) intake

> **Status:** Retired. The window is not an intake surface.
> Handover is applying `ready-for-agent` (ADR 0014). Seeding is a setup step
> that fetches one operator-chosen task, not issue intake (ADR 0010). A
> dashboard form that captured text and images and handed them to a Run would
> feed an unattended agent input the operator never attested, which is the
> content-trust floor ADR 0003 closed.
> What remains of the "dashboard from day one" is the FastAPI window (ADR 0001):
> it renders the Journal — the queue board, a Run's history, these decisions —
> and is not the front door for work. Issue #13 removed the demonstration
> intake this ADR described. Image upload, a SQLite request store, and a
> multipart form were never built, and must not be.

The single-user factory gets a web dashboard as its primary interface from the
start — overriding the CLI-first default in `notes/lessons.md` — because
requests routinely include images (screenshots) alongside text, and image
submission is exactly what a CLI handles badly and a web form handles naturally.
The dashboard captures text and images, stores them, and hands them to the run.

## Considered options

- **CLI-first, add web later** — the documented default (`notes/lessons.md`:
  "add the web layer when you feel the specific pull"). The pull arrived early
  and concretely: the first routine intake need is images, which a CLI is worst
  at. A CLI stays useful for scripted or one-off runs, but it is not the front
  door.
- **CLI plus attached image paths** — `factory run "…" --image a.png`. Works,
  but clumsy for the common case (screenshot something, drag it in) and gives no
  browsing or history. A stopgap, not the interface.
- **Web dashboard from the start** — accepted. A browser form with a textarea
  and an image drop-zone is the natural shape for multimodal intake, on the same
  FastAPI + Jinja2 + HTMX stack already chosen (ADR 0001).

## Consequences

- Intake is a multipart form (`python-multipart`, already installed). Images are
  stored to a per-request artifact folder; the request is recorded in a small
  local store — SQLite, one file, the minimal state layer for a single operator.
- The triage/implement model must be **multimodal** for images to matter. The
  dashboard captures and passes them, but the factory core's model choice has to
  accept images, and the two must agree on how images are handed across (the run
  reads the request's image files and includes them in the model call).
- Content-trust stays collapsed: the operator authors the images — own
  screenshots of own work — exactly as with the text, so there is no
  untrusted-relay path and none of the ETA content-trust apparatus returns. The
  only additions are hygiene: an upload size cap and an image-type check.
- The dashboard grows adjacent views from here — a request list, per-request
  status, and the resulting draft PR and evidence.
