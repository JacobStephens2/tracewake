# A Cycle holds up to K concurrent Dispatches

ADR 0021 made a Cycle drain the queue, but the drain was strictly serial: a
Run on one Target made every other Target wait, even though their work shares
nothing. Review Cap, not thread count, is the throughput bound, so the
serial cap was an accidental one.

## The decision

A Cycle may hold up to K concurrent Dispatches, where K is instance
configuration defaulting to 1. Two Runs never execute on the same Target;
within a Target the drain stays strictly serial, riding the existing in-flight
Journal event. Unset K preserves today's serial drain bit-for-bit, including
the Journal shape of one `cycle.started` / `cycle.finished` pair per Target.

This amends ADR 0021 rule 1: "serially" becomes "up to K concurrently, serial
within a Target". Pause remains non-destructive: an operator pausing mid-drain
stops new picks while every in-flight Run completes and Routes. Box facts,
Review Cap, and the pause flag are still read before each Dispatch; the
Guardrail is read once per Cycle (ADR 0026 rule 5). One
`cycle.finished` row still accounts for every Dispatch of that
Target's drain and the reason it stopped.

Zero or a negative K is a configuration error at preflight. The knob has a
semantic default because serial is the established behavior, unlike host and
repository facts which have no right answer in the product.

## Considered options

- **One Cycle across every Target.** Rejected for K=1: today's Journal shape
  is one started/finished pair per Target, and changing that would be a
  behavior change for instances that never opt in.
- **Parallel Runs within a Target.** Rejected: two Runs on one repository race
  each other's branches. The in-flight predicate already excludes that.
- **No default, refuse if unset.** Rejected: serial is the behavior every
  existing Instance already has; making them set a number to keep it would
  be a flag day for no gain.
