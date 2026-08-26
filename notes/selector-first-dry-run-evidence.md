# First live Selector dry-run

*2026-08-26, issue #153's second acceptance criterion. Journal cycle id 2 on
this VM's `selector` database; reproduce with
`psql -d selector -c "SELECT * FROM journal.events WHERE payload->>'cycle' = '2'"`.*

```
$ .venv/bin/python cycle.py --dry-run
considered   28
eligible     none
  skipped    15 x blocked-by-open-dependency
  skipped    3 x has-open-sub-issues
  skipped    10 x missing-section
pick         none (none-eligible)
budget       0/4 dispatches in the last 24h
```

Journaled summary:

```json
{"considered": 28, "eligible": [], "picked": null, "halted": "none-eligible",
 "skipped": {"blocked-by-open-dependency": 15, "has-open-sub-issues": 3,
             "missing-section": 10},
 "in_flight": [], "dispatched_in_window": 0, "daily_cap": 4, "dry_run": true}
```

Nothing changed on GitHub: the cycle's only outward reach was one
`gh api graphql` read, and the offline suite's tripwire PATH (`gh`, `git`,
`ssh`, `seed-run.sh` shimmed to log and fail) holds that line in every
scenario. Spot-checked afterwards: #596 still carries `ready-for-agent`
alone, with no new comment.

## What it found

**Not one issue in the queue is Eligible today, and the reason is not the
blocking edges.** 15 of 28 are blocked and 3 are parent specs - both correct,
both expected, both resolved by working the chain bottom-up. The other 10 are
skipped for `missing-section`, and every one of them is missing `Owning area`.
Six also lack `Acceptance criteria`.

That is ADR 0014 landing on a queue written before it: `ready-for-agent` now
promises sections that no issue labeled before 2026-08-26 was asked for. The
tracker hygiene run of 2026-08-26 fixed the dependency edges and did not touch
bodies.

So before #154 can dispatch anything there is operator work: add an
`## Owning area` section (one phrase - the part of the issue one Run is scoped
to) to the issues that are otherwise ready. The unblocked ones are the ones
worth doing first: **#471, #596, #597, #598, #599, #606, #626, #645, #647,
#648**. Of those, #471, #645, #647 and #648 already carry acceptance criteria
and need only the area.

This is the design working rather than failing - the loud skip exists so an
underspecified issue is returned to the operator instead of guessed at. It is
recorded here because the first dry-run being "nothing eligible" would
otherwise read as a broken Selector.
