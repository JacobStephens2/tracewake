# The model credential's clock: what was run rather than assumed

*2026-08-30, issue #260. The behaviour is carried by the offline suites -
fifteen tests in `loop/tests/boundary.bats` (the adapter's two answers and the
renewal at Iteration start), five in `loop/tests/assert-credentials.bats` (the
row graded on validity), two in `selector/tests/test_unattended.py` (the fact
reaching the Journal) and six in `lab/webapp/tests/test_loop_page.py` (the
card). What is written down here is what only this box could say, and - more
importantly - the one link in the chain that is **not** verified.*

## The shape of the credential, measured

Read off this VM's own Claude Code login on 2026-08-30, which is the same kind
of credential the Loop's box holds:

```
expiresAt              2026-08-30 19:19:05Z
refreshTokenExpiresAt  2026-09-20 20:15:28Z
now                    2026-08-30 13:33:20Z
```

Two things follow, and the second was not in the ticket.

The access token's life is the eight hours the ticket describes. But the
**refresh token beside it is twenty-one days**, which is why renewal on the
host is possible at all: the box is not holding a dead secret between logins,
it is holding a live refresh token and a short-lived access token minted from
it. The ticket's option (a) assumed the host would have to do something
expensive; what it actually has to do is make one authenticated request.

The second is a trap rather than a finding. `refreshTokenExpiresAt` ends in
the same word as `expiresAt`, and a reader that matched the wrong one would
report a box as good until 20 September when its access token dies tonight -
the failure this whole ticket is about, reported as its own opposite. It is
guarded three ways: the leading quote in the `grep` pattern, a test in
`boundary.bats` and a test in `assert-credentials.bats` that both write the
long name three weeks out and assert the short one is the answer, and a
mutation (`expiry-reads-refresh-token`) that swaps the pattern and turns nine
tests red.

## What `claude auth status` does not do

The obvious candidate for "renew without running an agent" was the vendor's
own auth subcommand. It does not renew. Run against a credential with 5h46m
left:

```
$ claude auth status --json
{ "loggedIn": true, "authMethod": "claude.ai", ... }

$ # expiresAt, before and after
2026-08-30 19:19:05.084000+00:00
2026-08-30 19:19:05.084000+00:00
```

Unchanged to the millisecond. It reports the session; it does not touch it,
and it does not report the expiry either. So there is no read-only incantation
that renews, which is what forces the renewal to be an actual request - and
therefore an agent process on the host, which is the cost ADR 0017 accepts.

## The unverified link

**`claude -p ok --max-turns 1` is not confirmed to renew the credential.** It
is the default `LOOP_CLAUDE_REFRESH_COMMAND`, and the reasoning for it is in
ADR 0017: an API request obliges the client to present a live access token, so
the renewal falls out of the vendor's own resolution order. That reasoning is
sound and it is not evidence.

Verifying it needs a credential that is actually inside its renewal window,
driven against an isolated `CLAUDE_CONFIG_DIR` so a live session is not the
subject. That was attempted on this VM on 2026-08-30 and refused by the
harness - pointing an agent's config directory at a copied credential is
indistinguishable, from the outside, from tampering with one. The check is a
human's to run:

```bash
# On the Loop's box, as the Run account, when the credential has < 2h left:
loop/agents/claude.sh --credential-expiry     # note it
loop/agents/claude.sh --refresh-credential    # should print a later instant
```

A later instant is the whole confirmation. Until somebody runs that, what is
verified is everything **around** the incantation:

- a renewal that mints nothing and exits 0 fails the Iteration before a
  boundary is built, naming the command and the login wizard
  (`a renewal that quietly did nothing fails here rather than inside a Run`);
- a renewal that mints something expiring inside the margin also fails;
- a renewal that exits non-zero but *did* mint is not treated as a failure,
  because what decides it is whether the expiry moved;
- the renewed copy, not the stale one, is what crosses into the microVM
  (`the renewed credential is the one that goes into the boundary`).

So a wrong incantation is a **loud** failure at the first Iteration of the
first Run that needs it, on the box, in the Run's own output - not a silent
one. That is a much better failure than the one #260 is about, and it is not
the same as being right.

## Why the renewal is per Iteration and not per dispatch

Run 645 is the reason, and it is worth being precise about it because the
ticket's framing ("expired at dispatch time") would not have caught it:

```
### Iteration 1 - ...        committed
### Iteration 2 - 2026-08-29T17:05:25Z
- Agent exit: 1
    Failed to authenticate: OAuth session expired and could not be refreshed
```

The credential was alive when the Run was dispatched and dead by Iteration 2.
A check at dispatch would have passed. A Run is bounded at ninety minutes and
the session at eight hours, so the boundary a Run can cross is real, and the
two-hour margin exists so it cannot: an Iteration that starts with less than
two hours left renews before it starts rather than after it fails.

## Viewing

`/loop` was rendered against two seeded Journals - one box card holding a
credential with 6h 19m left, one holding a credential that lapsed an hour ago
- and both were looked at in a browser rather than only asserted on.

The live card keeps the shape it had: the scripts hash is still the headline,
and the credential is one more item on the detail line.

```
THE BOX
loop scripts `8c1f3a90d2`
guest template `loop-php:1` · agent claude `2.1.221 (Claude Code)`
  · credential `2026-08-30T21:17:41Z` (6h 19m left) · read 2026-08-30 14:57:43Z
```

The lapsed card takes the headline off the scripts hash, and
`the model credential has expired` renders in `--error-color` - the same red
the timer cell uses for `timer not running (inactive)`, via `.strip .alarm` in
`loop.css`. Confirmed in a screenshot, not only in the markup: the words and
the colour both change, so the difference survives a glance.

```
THE BOX
the model credential has expired                    <- red
it lapsed at `2026-08-30T13:57:41Z` - a Run dispatched now would fail at its
first API call. The next Iteration renews it before it starts (ADR 0017); if
this reading persists, the renewal is not working - run
`wizards/loop-claude-login.sh`. · loop scripts `8c1f3a90d2` · guest template
`loop-php:1` · agent claude `2.1.221 (Claude Code)` · read 2026-08-30 14:57:43Z
```

Rendering it is what caught the sentence being wrong. It read "A dispatch
renews it", which is the ticket's framing and not what was built - the renewal
is per Iteration. The card now says what the code does, and says what a
*persistent* red reading means, which is the state that actually needs a
human.

The rest of the card survives on the lapsed page. An operator diagnosing this
still needs to know which box he is looking at, and a cell that replaced
everything with the alarm would have taken that away.

## Mutation coverage

Both affected scripts were mutation-checked after the change:

```
$ loop/tests/mutation-check.sh --only agents/claude.sh
  model-credential-unchecked   caught,  4 red
  iteration-does-not-renew     caught,  4 red
  renewal-unverified           caught,  3 red
  renewal-margin-ignored       caught,  5 red
  expiry-reads-refresh-token   caught,  9 red
All 16 mutations caught.

$ loop/tests/mutation-check.sh --only assert-credentials.sh
  credential-presence-only     caught,  2 red
  credential-unreadable-passes caught,  1 red
All 22 mutations caught.
```

`credential-unreadable-passes` survived the first time it was run, and the
reason is worth keeping: with the unreadable branch removed, a credential
whose expiry cannot be parsed falls through to the date comparison, compares
as epoch zero, and is reported as **expired**. Still a violation, still not
`[held]` - so the exit code and the family name both looked right. What it
lost was the distinction between "this login lapsed" (renew it) and "nobody
can parse this file" (a human is needed), which is the whole reason the two
states are named separately. The test now asserts the word rather than only
the exit code.
