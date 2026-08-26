# Unreviewed code is not model output, and Attendedness is what separates them

Reviewing a `/loop` change means merging it. `lab-webapp.service` runs from
`/srv/orchestration` on `master`, so the only way to see a branch's page is to
land it first - backwards for exactly the changes where looking is the review.
The fix is a second instance serving an unmerged branch, and the reason it
needs a ruling before a build is that spec #151 story 21 asks for the opposite
of it: "no model output executes on the VM that holds production credentials."

A previewed branch may well *be* model output - a Proposal branch is what the
Loop produces. So the question is whether unreviewed code falls under the same
prohibition. It does not, and ADR 0003 says why. That prohibition was never
about the bytes; it is about the unattended agent. ADR 0003's finding is that
the blast-radius collapse rests on the premise "I notice and fix", and that the
premise fails for an unattended Run because an unattended Run is *defined* by
nobody noticing for thirty to forty-five minutes. A preview is the opposite
case: you started it, you are looking at it, and the failure mode is a page
rendering wrong. Attendedness holds, so the collapse holds, and the code may
run here.

We call the thing an **Attended Preview**, and the name carries the reason
because the failure mode is somebody leaving one running until it is no longer
attended.

## What the ruling licenses, and what it does not

It licenses rendering. It does not license reaching outside the VM. An Attended
Preview runs with faked tracker and box edges: `cycle.py` and `dispatch.py`
execute end to end against a real Postgres with real triggers and real NOTIFY,
writing real events, and nothing leaves the box. Letting a preview dispatch for
real was considered and rejected on this ADR's own logic - you are attended for
the dispatch and then a Run executes for thirty to forty-five minutes with
nobody watching, which is ADR 0003's failing premise verbatim, reached by the
back door.

## Containment, which is not the same as configuration

`journal.dsn()` already reads `SELECTOR_JOURNAL_DSN` and falls back to
`dbname=selector`, so pointing a preview at `selector_staging` is one
`Environment=` line. That line is configuration, and the code being previewed is
precisely the code that might not honour it. A branch that writes to the live
Journal is a branch that ignored the env var, and `schema.sql` makes those rows
permanent by trigger, in the record whose job is answering "why did the Selector
do that?"

So the DSN is how it works and the grant is why it is safe. The preview runs as
a dedicated `labstage` account with `CONNECT` on `selector_staging` and no
`CONNECT` on `selector`, so a hardcoded `dbname=selector` gets a permission
error instead of an undeletable row. The same account holds no SSH key and no
vault environment, which is what actually stops `dispatch.py` - the Selector is
importable from the webapp's path today by design (ADR 0015: the webapp is the
Journal's window), so the import cannot be removed and containment has to sit
below it. The unit enumerates its environment explicitly rather than reading an
`EnvironmentFile`, so a later edit cannot quietly hand it credentials.

`labstage` is a **runtime** identity, not an ownership one. The worktree at
`/srv/lab-webapp-staging` stays `conductor:srvwrite` and `labstage` holds only
read and execute on it. Making the tree `labstage`-owned would need a sudo rule
for every checkout, and an agent that can `sudo -u labstage` has a route around
the Postgres grant this whole design rests on.

## Its own host, not a path and not the live URL

Serving branches at `lab.etadventures.com/loop` was the operator's first
instinct and it fails on a fact: sixteen systemd units exec from
`/srv/orchestration`, including `status-dashboard.service`, which is the
`forward_auth` gate guarding `lab.etadventures.com` itself. Checking the shared
tree out to a branch is not a preview, it is a fleet-wide change.

Running the live instance from a dedicated worktree instead avoids that and was
genuinely close. It loses on trust: nothing on the page would say whether you
are looking at `master` or at a branch left checked out three days ago, and the
job in hand - compare a branch's `/loop` against what is live - needs both
reachable at once, which one instance structurally cannot do.

A path prefix under the live host is workable (`root_path` plus `url_for` on the
four hardcoded `/static/...` lines in `base.html` and `loop.html`) and loses on
same-origin: it hands code you have not read the live app's session and cookies.
Fix those four lines anyway; they are a latent bug either way.

The host costs one Route53 record, one Caddy block copied from
`requirements-staging`, and one unit. It costs **no** dashboard code:
`/api/auth-check`'s host dispatch ends in an `else` that fails closed to
Admin-only, and `lab.etadventures.com` is gated by that default today rather
than by a named arm.

## Staying attended

A preview shows a banner naming its branch, short SHA and uptime, and the unit
carries `RuntimeMaxSec=4h` - longer than any review, shorter than a workday. The
banner alone would fail, because a banner nobody is looking at is the same
premise ADR 0003 rejects; the timer alone would fail the other way, stopping
silently so a timed-out preview looks like a crashed one.

One preview at a time, held by a visible lease: branch, SHA, when, and who
started it. `lab-preview.sh` refuses to take a live lease without `--force`. The
cost of first-come is not the collision, it is that the displaced agent keeps
reporting on a page that is now somebody else's branch. Numbered slots, as the
ETA preview host does it, cost a record, a vhost and a database each, forever,
for one operator who can look at one page at a time.

The operator or an agent runs `lab-preview.sh <branch>`, which fetches, checks
out, restarts and prints the SHA it landed on. A control in the live lab app to
switch staging's branch is rejected: it would need the live app to hold write on
the preview tree and the right to restart a unit, and it would make starting a
preview something that can happen with nobody at a terminal, which is the
attendedness property leaking out through a convenience.

## Accepted residuals

- **An unattended Run may not start an Attended Preview.** Today this holds
  because the Selector's work source is the tourbot tracker, so no unattended
  Run executes on this VM at all. Agents working #151's sub-issues here are
  interactive sessions, and the operator is the attended party. Point the Loop
  at the orchestration tracker and this stops being true silently, which is why
  it is written down rather than assumed. #151 lists "serving the orchestration
  repo's own `ready-for-agent` queue" under Out of Scope, so today the spec and
  this residual agree; that line is the one to watch.
- `selector_staging` is disposable and rebuilt from `schema.sql` plus a
  checked-in `seed.sql`. The fixture is load-bearing, not decoration: the live
  Journal holds 83 `issue.skipped`, 3 `cycle.started`, 3 `cycle.finished` and
  **zero** `run.dispatched` or `run.outcome`, so no outcome state has ever been
  rendered by anything. Copying live's events was rejected - it drags real issue
  numbers into the one database unreviewed code may write to, and is stale on
  arrival.

## Noted while scoping, fixed elsewhere

Neither `lab` nor `requirements-staging` appears in `tofu/hosts/dns.tf`'s
managed list of eleven hostnames; both records were made out of band and a
droplet replacement would not recreate them. This work adds `lab-staging` to the
managed list rather than creating a fourth unmanaged sibling. The existing drift
is its own issue.
