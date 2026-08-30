# The model credential is renewed on the box at every Iteration

The box's subscription login is an OAuth session whose access token stops
working eight hours after a human mints it. ADR 0011 put that credential
inside each Iteration's microVM, and ADR 0003 is why nothing comes back out of
the boundary - so the only copy that persists is the host's, and it ages from
the moment of the last login with nothing renewing it.

The Selector fires every thirty minutes, around the clock. Sixteen of every
twenty-four hours it was dispatching Runs against a credential that had
already lapsed.

Run 645 on 2026-08-29 is what that looks like:

```
### Iteration 2 - 2026-08-29T17:05:25Z
- Agent exit: 1
Agent output, last 20 lines:
    Failed to authenticate: OAuth session expired and could not be refreshed
```

Iteration 1 had committed, so the Run still opened a Proposal and the failure
appeared in exactly one place: that Run's own Progress Log. A Run that expired
at Iteration 1 would have produced nothing at all.

## The decision

`agents/claude.sh` renews the host's copy at the start of **every Iteration**,
before the copy is placed inside the microVM. If the credential has more than
two hours left it is returned unchanged and nothing runs; otherwise the
vendor's own client is invoked, the expiry is re-read, and an Iteration whose
credential did not actually move past the margin fails before a boundary is
built.

Three things about that placement are load-bearing.

**Per Iteration, not per Run.** Run 645 authenticated for Iteration 1 and died
on Iteration 2. A credential checked once when the Run was dispatched would
have passed and the Run would have failed exactly as it did. A Run is bounded
at ninety minutes by the Termination Contract and the session at eight hours,
so the window a Run can cross is real, and the two-hour margin exists so that
a credential can never expire *during* an Iteration - which fails a Run
halfway and leaves a half-built branch, and is worse than not starting it.

**In the adapter, not in the Selector.** Which file holds the credential, what
shape it is in, and what renews it are vendor facts, and ADR 0004 puts vendor
facts in the adapter. It is also the only place that *can* do it: the Selector
runs on the orchestration VM and reaches the box over SSH, so renewing from
there would mean a second hop and a second command on the box surface, to do
on a timer what the thing that needs the credential can do for itself.

**The vendor's client, not its token endpoint.** Reimplementing somebody
else's OAuth is a copy of an undocumented contract that breaks the day they
change it, and it breaks by minting nothing while reporting success. Running
the client makes an API request, and an API request is what obliges it to
present a live access token, so the renewal is a side effect of the vendor's
own resolution order. The incantation is substitutable
(`LOOP_CLAUDE_REFRESH_COMMAND`) so the offline suite drives it with no
network.

What is **not** trusted is that any of it worked. The expiry is re-read after
the attempt and has to have moved past the margin. A renewal that exits zero
and mints nothing fails at the Iteration's first line rather than inside the
boundary, which is the difference between a fault an operator can see and Run
645.

## What was rejected

**Refuse to dispatch against an expired credential, and page.** Honest, and it
needs no agent process on the host at all - the Selector would simply halt
with a new reason beside `paused` and `daily-cap-reached`. It was rejected on
what it costs: the credential is dead for roughly sixteen hours of every
twenty-four, so this drains no queue overnight, which is most of the time the
Loop exists to be working. It also pages for something a machine can fix,
which trains an operator to ignore the page. It remains the right fallback if
the renewal itself proves unreliable, and the adapter already fails loudly
enough to make that switch a small one.

**Copy the refreshed credential back out of the guest.** Refused outright
rather than weighed. It is a hole in ADR 0003 - the boundary's whole claim is
that nothing crosses back - and the thing it would carry back is a live
credential written by an unattended agent. The `sbx cp` in the adapter runs
one way, and that is the property, not an omission.

## Consequences

**There is now an agent process on the host, and `loop_agent`'s comment that
nothing unattended belongs there is narrower than it was.** What runs is the
vendor's client for one turn with a prompt that asks for nothing, and only in
the hours before a Run would otherwise have failed - on the common path
nothing runs at all. It is still a model process on the box outside the
boundary, and that is a real cost paid deliberately: the alternative was
sixteen hours a day of a queue that does not drain.

**The renewal is a network call inside an Iteration's setup.** An Iteration
whose renewal cannot reach the vendor now fails before its boundary is built,
where before it would have failed inside one. That is louder and earlier, but
it does mean a vendor outage takes the box out at a slightly different point
than it used to.

**`[held]` now means something.** `assert-credentials.sh` grades the model
credential on validity as well as presence, so a login that lapsed overnight
is `[expired]` rather than held - and `[expired]` is reported separately from
`[absent]` because they are different things to do about it: absent wants the
login wizard, expired wants a renewal.

**The box card carries a fourth fact.** The other three say what the box *is*;
`LOOP_BOX_CREDENTIAL_EXPIRES_AT` says whether it can currently do anything,
and an expired one takes the headline off the scripts hash. It is journaled as
an absolute instant and never as a remaining time - the read happens once a
cycle and the page is viewed whenever, so a duration recorded at read time
would be wrong by however long the page sat open, and wrong in the reassuring
direction.
