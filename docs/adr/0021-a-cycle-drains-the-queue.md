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

1. **Serial Runs under one Cycle.**
   One Cycle dispatches several Runs serially, routing each before picking the
   next. All dispatches in the drain share the Cycle's id in the Journal, giving
   the operator an unbroken account of the work completed during that firing.
2. **Deterministic stopping conditions.**
   A draining Cycle ends when nothing is Eligible (`queue-empty` or `none-eligible`),
   a cap holds (`run-in-flight` or `daily-cap-reached`), or the operator has
   paused (`paused`).
3. **Non-destructive pause check.**
   The pause flag is evaluated before each pick. An operator pausing mid-drain
   allows the Run currently in flight to complete and route normally; the pause
   stops the next pick and halts the drain cleanly.
4. **Per-dispatch status observation.**
   The box's facts and the Guardrail status are read immediately before each
   dispatch, rather than once per Cycle. A multi-hour drain reflects live box facts
   and repository protection state ahead of each Run.
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
