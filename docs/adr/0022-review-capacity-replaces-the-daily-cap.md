# Review capacity replaces the daily cap

Previously, the Selector bounded unattended dispatch with a rolling daily spend
cap (`SELECTOR_DAILY_CAP`, defaulting to 4 dispatches per rolling 24 hours). That
bound was established when model billing was unmetered and daily spend was
treated as the primary risk. Under subscription billing, the money boundary is
already fixed by the subscription (metered keys remain barred, ADR 0004 and
ADR 0020).

With draining Cycles (ADR 0021), an arbitrary daily spend cap both starves the
pipeline when human review capacity is open and fails to protect the human
operator from a deluge of open Proposals. Review capacity is the binding
constraint that actually limits factory throughput (ETA Factory ruling A31).

## The decision

The daily spend cap and its rolling window are removed from the code,
configuration, and user interface. Unattended dispatch is bounded instead by the
operator's review capacity, configured per target stanza in `targets.toml` as
`review_cap` (defaulting to 20).

Five architectural rules govern review capacity:

1. **Per-target review capacity.**
   Each target declares its own `review_cap` in `targets.toml` (default 20).
   Review capacity is a property of the repository's team and review pace, not a
   global instance limit.
2. **Review column read on each drain pass.**
   The Selector inspects the target's review queue (open issues carrying
   `config.review_label`, excluding issues already in the handover column) on
   each pass of a draining Cycle.
3. **Deterministic halt on capacity.**
   When the number of issues awaiting review reaches or exceeds `review_cap`,
   dispatch halts cleanly with `halted = "review-cap-reached"`. The halt reason
   and the count read are journaled in `cycle.finished` as `awaiting_review` and
   `review_cap`.
4. **Single source of truth for the review budget.**
   `cycle.review_budget()` is the single function used both by `run_cycle` to
   enforce the review cap and by the web application (`/loop`) to render the
   status cell. Run history (`/loop/history`) remains strictly isolated to the
   Selector Journal and does not query the tracker. The live page and the engine cannot drift.
5. **No daily spend counter residue.**
   `SELECTOR_DAILY_CAP`, `daily_cap`, `CAP_WINDOW_HOURS`, `dispatched_in_window`,
   and `recent_dispatches` are removed from the system.

## Consequences

- **Throughput tracks review velocity.** The Selector dispatches work whenever
  the operator has review capacity, and stops when the review column is full.
- **Immediate resumption.** Merging or closing a review item immediately restores
  review headroom for the next cycle without waiting for a 24-hour rolling window
  to elapse.
- **Consistent presentation.** The status strip on `/loop` reports remaining
  review capacity derived directly from the tracker's review column.
- **Journal-only isolation preserved.** `/loop/history` continues to read
  strictly from the Selector Journal, unaffected by tracker network conditions.
