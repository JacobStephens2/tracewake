# single-user-factory

A lightweight software factory with **one trusted operator** — me, submitting
requests for my own repos. Not the ETA factory (that lives in
`../../eta-factory`, is company-governed, and defends a very different threat
model). This is the deliberately-stripped version, and the point of these notes
is to record *what to strip and why*, drawn from building the heavy one.

## Layout

- `notes/` — design thinking, source markdown. `notes/lessons.md` is the
  synthesis these notes started from.
- `site/` — hand-authored HTML, served at `lab.etadventures.com/single-user-factory/`.
  Write HTML here directly; markdown in `notes/` is source, not served-rendered.

## The one idea everything hangs on

The defining constraint is **single-user**, and that constraint is the *cause*,
not a detail. Because I author my own inputs, content-trust collapses; because
the blast radius is me, the safety apparatus collapses; because I rarely
rebuild, the reproducibility apparatus collapses. Almost every difference from
the ETA factory falls out of that one fact. See `notes/lessons.md`.
