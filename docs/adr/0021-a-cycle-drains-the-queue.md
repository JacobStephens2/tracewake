# A Cycle drains the queue

Previously, a Selector Cycle was bounded to dispatching at most one Run. Even
when multiple tasks were handed over and eligible, each subsequent Run waited
for the timer's next half-hour firing. Under an active queue, throughput was
limited by the timer interval rather than by the capacity of the box.

## The decision

A Cycle stops being one Run and becomes as many as there is work for: pick,
dispatch, route, and look again, until nothing is Eligible, a cap holds, or the
operator has paused.

Six architectural rules govern the draining Cycle:

1. **Up to K concurrent Runs, serial within a Target.**
   One Cycle dispatches several Runs, routing each before picking the next on
   that Target. Up to K Dispatches may run at once across Targets (ADR 0027);
   K defaults to 1, which is this rule as originally written. All dispatches
   in the drain share the Cycle's id in the Journal, giving the operator an
   unbroken account of the work completed during that firing.
2. **Deterministic stopping conditions.**
   A draining Cycle ends when nothing is Eligible (`queue-empty` or `none-eligible`),
   a cap holds (`run-in-flight` or `daily-cap-reached`), or the operator has
   paused (`paused`).
3. **Non-destructive pause check.**
   The pause flag is evaluated before each pick. An operator pausing mid-drain
   allows the Run currently in flight to complete and route normally; the pause
   stops the next pick and halts the drain cleanly.
4. **Per-dispatch box observation; the Guardrail once per Cycle.**
   The box's facts are read immediately before each dispatch, rather than once
   per Cycle, so a multi-hour drain reflects live box facts ahead of each Run.
   The Guardrail is read once, when the Cycle starts (ADR 0026 rule 5; see below).
5. **One summary per drain.**
   One `cycle.finished` row is journaled when the drain ends. Its payload names
   every dispatch (`dispatches: list[int]`), the initial pick (`picked`), and the
   reason the drain stopped (`halted`).
6. **Infinite unit start timeout.**
   `TimeoutStartSec` in `deploy/systemd/tracewake-selector-cycle.service` is set to
   `infinity`. A draining Cycle might dispatch multiple Runs in succession and run
   for hours; the service unit lets it drain without a premature timeout. An
   individual Run continues to be bounded by the Termination Contract (90 minutes)
   and the Selector backstop (`SELECTOR_DISPATCH_TIMEOUT_SECONDS`, 7200s).

## Consequences

- **Immediate successor dispatch.** A Run's successor starts the moment it ends
  and routes rather than waiting for the next timer firing.
- **Idle case unchanged.** When no issues are queued or eligible, the Cycle finishes
  immediately after checking the queue and journals its reasoning.
- **Single drain summary.** The dashboard and readers inspect one `cycle.finished`
  record summarizing the full drain.

**Amended by ADR 0027 (issue #37).** Rule 1's "serially" is now "up to K
concurrently, serial within a Target". Review Cap, not thread count,
remains the throughput bound. K unset is still this ADR's original drain.

**Amended by ADR 0026 rule 5; text brought into line 2026-09-20.** Rule 4 as
first written read the Guardrail before each dispatch as well. ADR 0026 moved
it to once per Cycle, before the dispatch loop, because an idle queue
dispatches nothing and so journaled no reading, and the chip on `/loop` went
stale over a healthy Selector. This ADR's text had not followed. The box's facts
stay per-dispatch, because those are what a Run is about to land on.
