# The Iteration watcher: what was verified, and how

*2026-08-27, issue #157. The offline suite is the bulk of the evidence and it
is in the repository (`selector/tests/test_watcher.py`). What is written down
here is what only the real box could say - including the thing it said that
changed the parser - plus the acceptance criterion this ticket cannot close on
its own and why.*

## The real Progress Log, read over SSH

`box-sources/progress.sh` is the same two-shells-deep hop as `ssh.sh`, so its
quoting is the part no offline test grades. Against the live box, with the
Run from #648 still on its checkout:

```
$ ./box-sources/progress.sh loop/648-dashboards-and-reports | head -20
# Progress Log

Task: Educational-Travel-Adventures/tourbot#648 - Audit every tblEmailMessage read and classify it
Owning area: dashboards and reports
...
## Run started 2026-08-25T16:32:48Z
```

Read-only, as `loop`, no working tree touched, exit 0.

## What that read changed

The parser was written against `run.sh`'s Iteration record and nothing else,
and the real log is not only that. **Most of a Progress Log is written by the
agent**, which is the point of it - and the agent writes markdown headings of
its own. In the #648 log:

```
### Iteration 1 - 2026-08-25T16:32:48Z     <- the Loop's record

- Agent exit: 0
- Turn bound: 100
- Head: f0f4d749e966 -> 06d7a82a8309
- Completion Promise: not recorded

## Iteration 2                             <- the AGENT's heading

Classified all 32 `mtourbot/reports/sales_dashboard.php` occurrences ...

### DECIDED

- Last-comm / days-stale lines that `GREATEST()` ...
```

A parser that ran a record from its heading to the next heading would have
done two wrong things at once with that file: pulled the agent's own bullet
lists onto the record, and - far worse - not reported Iteration 1 until
Iteration 2's record was written, which is a watcher that reports each
Iteration one Iteration late. Since the whole ticket is "Iterations appear
while the Run runs", that would have shipped as working and been wrong by
exactly the amount that matters.

So a record is now the run of `- ` lines under a `### Iteration N - <stamp>`
heading, ending at the first line that is not one, and it is only taken once
that terminating line exists - which is also what keeps a block caught
mid-write out of an append-only Journal. The first twenty lines of the real
log are checked in as `tests/fixtures/box-progress-run-648.md` and the suite
parses them.

## What the offline suite proves

Thirteen tests, at two boundaries. The parser is driven with snapshots written
the way `run.sh` writes them; the watcher is driven through the **real**
`cycle.py` dispatching against a scripted box that grows a Progress Log while
it "runs", with the interval collapsed from a minute to 50ms and each snapshot
held still for 350ms - so every snapshot is read about seven times, and

> the watcher journals exactly the new Iteration records, never duplicates

is an assertion about re-reading rather than a coincidence of timing. Also
checked there: the final read after the Run ends (an Iteration written in a
Run's last seconds has no next poll coming), a previous attempt's Run block
not being journaled as this attempt's, and a Progress Log that cannot be read
neither failing the dispatch nor being reported more than once.

Six mutations were added to `tests/selector-mutations.py`, one per guard.

## What the review added

Two findings from `/code-review` were worth code rather than a note:

- **A Run heading the agent wrote was a block boundary.** The lesson above was
  applied to `### Iteration` records and not to `## Run started`, and the
  fixture proves the hazard is real - it holds an agent-written `## Iteration
  2` at column 0. A `## Run started` line the agent wrote would have discarded
  every record found so far and left the block undatable, which is dropped
  whole: no Iterations for the rest of the Run, and no `run.watch-failed` row
  either, because nothing failed. A boundary now needs a stamp that parses.
- **The turn bound was on the line and thrown away.** `run.sh` writes `- Turn
  bound: N` on every record and it is one of the Termination Contract's five;
  spec #151's story 25 wants a Run's bounds visible while it runs, and this is
  the only place they reach this side before the Run ends. It is carried on
  the record now.

## The criterion that needs a live Run

> Viewing: during a live Run, refreshing /loop shows Iteration records landing
> within about two minutes of the box writing them.

**Not closed here, and not for a reason in the code.** It needs a Run, and a
Run is a real dispatch against a real tourbot issue under the operator's
identity - which is the same act #154's evidence note left with the operator,
for the same reason. What can be said without one: the poll is 60s, the read
is one `cat` over an already-warm SSH path, and the page renders at request
time from the Journal - so the lag between the box writing a record and a
refresh showing it is one poll interval plus a round trip, which is inside the
two minutes the criterion allows.

The check to run during the next supervised dispatch is one line:

```bash
psql -d selector -c "SELECT at, payload->>'iteration', payload->>'agent_exit' \
    FROM journal.events WHERE kind = 'run.iteration' ORDER BY id DESC LIMIT 10;"
```

Each row's `at` should sit within a minute of the `started` stamp the box
wrote on the same Iteration, and `/loop` should show the same rows without
anything else being done to it.
