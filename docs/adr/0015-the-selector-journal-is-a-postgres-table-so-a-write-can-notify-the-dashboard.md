# The Selector Journal is a Postgres table, so a write can notify the dashboard

The Journal's access pattern - single writer, append-only, scan-to-render -
fits a JSONL file, and that was the recommendation on the table: greppable,
zero dependencies, the lab dashboard's read-a-file-at-request-time pattern.
The operator chose PostgreSQL instead, for the one thing a flat file cannot
do: `LISTEN/NOTIFY`, so an INSERT reaches the dashboard as an SSE push
instead of a poll - the ETA Factory's live-visibility pattern (its ruling
A63), and setting Postgres up was among the least painful parts of that
build.

Local instance on the orchestration VM, peer auth over the unix socket: no
network exposure, no new credential anywhere, nothing changes on the Loop's
box or in its four-credential inventory.

What Postgres does not change: the Journal stays append-only by discipline,
it decides no work - the tracker remains the only work source - and in v1
`NOTIFY` drives SSE only. Triggering work through the database (a "Run this
now" command channel) is a recorded idea with its seam ready, not a feature.

SQLite was the middle option and loses to both ends: stdlib-simple, but with
no notify channel it buys schema ceremony without the push.
