# single-user-factory

A lightweight software factory with **one trusted operator** — me, submitting
requests for my own repos. Not the ETA factory (that lives in
`../../eta-factory`, is company-governed, and defends a very different threat
model). This is the deliberately-stripped version, and the point of these notes
is to record *what to strip and why*, drawn from building the heavy one.

## Layout

- `loop/` - the Loop itself: the Termination Contract, one Run, the agent
  adapter, the completeness check that grades the first task, and an offline
  suite that drives all of it through a scripted fake. Start at
  `loop/README.md`.
- `notes/` - design thinking, source markdown. `notes/lessons.md` is the
  synthesis these notes started from.
- `docs/adr/` - the decisions, numbered. ADRs 0003-0008 govern the Loop.
- `research/` - the source-cited investigations the notes and ADRs rest on.
- `wizards/` - runnable walkthroughs for the steps only a human can take.
  `loop-sbx-login.sh` signs the Loop's Execution Boundary in, which needs a
  browser the box does not have.
- `site/` - hand-authored HTML, served at `lab.etadventures.com/single-user-factory/`.
  Write HTML here directly; markdown in `notes/` is source, not served-rendered.

## The one idea everything hangs on

The defining constraint is **single-user**, and that constraint is the *cause*,
not a detail. Because I author my own inputs, content-trust collapses; because
the blast radius is me, the safety apparatus collapses; because I rarely
rebuild, the reproducibility apparatus collapses. Almost every difference from
the ETA factory falls out of that one fact. See `notes/lessons.md`.
