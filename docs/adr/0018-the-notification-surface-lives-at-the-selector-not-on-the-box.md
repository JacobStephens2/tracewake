# The notification surface lives at the Selector, not on the box

ADR 0013 left a marker: a finished Run tells the operator through a comment on
its own proposal, GitHub's email carries it, and there is deliberately no
second surface - "the argument to reach for if a real notification surface is
ever worth its host and its credential." Unattended operation (ADR 0014) has
now produced the events that argument was waiting for, and every one of them
is failure-shaped: a Run that exited nonzero or whose proposal failed has
nothing to comment on, a dispatch or preflight that fails under the Selector
fails with nobody at a keyboard, and a credential quietly expiring cancels
Runs that were never dispatched. All of them are silences today.

The decision: the second surface is **email, sent from the Selector's side on
this VM** - not from the Loop's box. The Selector already observes every
dispatch and outcome in the Journal, the Journal was chosen for Postgres
precisely so a write can notify (ADR 0015), and this VM already holds mail
infrastructure and its credentials for the status dashboard. The box's whole
external reach stays "the repository": no mail host on its egress allowlist,
no fifth credential in its inventory (ADR 0009), and ADR 0013's mechanism is
untouched - a green Run still announces itself through its proposal, and
GitHub still carries that email.

Scope, settled with the operator (2026-08-31): email only, no paging - nothing
in the Loop is urgent the way a down production host is, and the dashboard's
SMS path is one call away if that changes. The events are (0) a Run that
finished with a green Proposal, (1) a Run that ended without one, (2) a
Selector dispatch or preflight failure, and (3) an approaching credential
expiry.

*Widened later the same day.* Event (0) was originally excluded - GitHub's
own email carried the green case, contingent on the "include your own
updates" account setting ADR 0013 depended on. Turned on, that setting
flooded: it is account-global and every agent session acts as the operator,
so it delivered all of their activity everywhere. It is now off for good
(ADR 0013, amended), and the Selector is the **single** email channel for the
Loop. The `run.outcome` row already carries the proposal URL, so the green
email costs one more kind-match in the same LISTEN consumer. Accepted
trade: success emails now depend on the Selector being up, where GitHub's
did not - the timer cell and /loop page already alarm on a dead Selector,
and a missed success email is the low-stakes miss (the proposal itself
waits on GitHub regardless). ADR 0013's comment stays, as the on-PR record. Notifying on a *stuck* Run waits for
stuck-detection to exist (research/2026-08-31-stuck-run-detection-spend-caps-
timeouts.md), and notifying on board-state transitions waits for the derived
board facets - detection precedes notification, or the mail is a guess.

The rejected alternative - a mail credential on the box so the Run can email
even when the Selector is down - buys coverage of exactly one failure (both
the Run and the Selector's observation of it failing together) at the price of
reopening the four-credential inventory and the egress allowlist, the two
lists whose whole value is being short. The Selector being down is already the
timer cell's failure to catch, on a page the operator reads.
