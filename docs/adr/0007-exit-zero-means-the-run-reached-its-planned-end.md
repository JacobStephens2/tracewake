# Exit zero means the Run reached its planned end, not that no bound fired

Spec issue #73 asks for two things that cannot both hold literally: the Run
"exits non-zero whenever any bound fired", and a Completion Promise "never ends a
Run by itself". Together they mean every Run ends on a bound - the cap, the Run
clock, consecutive No-ops, or a failed step - and so every Run would exit
non-zero, which makes the exit code carry no information at all. That defeats
story 14, whose stated purpose is for the Run to be "honest about whether it
worked".

We split the two facts the exit code was being asked to carry. Which bound ended
the Run is reported separately and always, on the first line of stdout as
`LOOP_RUN_ENDED_BY=<bound>` and again in the Progress Log, for every outcome
including the clean one. The exit code then answers only the question a caller
can act on: did anything go wrong? Reaching the iteration cap with no Iteration
killed and no agent exiting non-zero is the Run's planned end and exits 0.
Everything else exits non-zero, with the code naming the ending bound: 2
run-clock, 3 consecutive-noops, 4 agent-failed, 5 cap reached but an Iteration was
killed, 1 preflight failed before the Run started.

## Consequences

The Loop is usable from a wrapper - a notification, a cron entry, `set -e` - that
reads the exit code and nothing else, which is the normal way a caller consumes an
unattended job. Under the literal reading no such wrapper could distinguish a
healthy Run from a runaway one.

The cost is that "the cap fired" and "the Run finished cleanly" are the same
observable outcome in the exit code, distinguished only by whether a fault was
recorded alongside it. That is honest: with the Completion Promise deliberately
non-terminal, the Loop has no evidence that the *work* is done, only that the Run
executed its planned Iterations without incident. Exit 0 should be read as "the
Run behaved", never as "the task is finished" - the draft pull request is what
answers the second question, and a human answers it.

This is the first of the Contract's shapes likely to be revised by the first Run,
alongside its five numbers. If a later Run wants "the cap stopped work that was
still going" separated from "the work ran out", the evidence for the distinction
has to come from somewhere other than a model's claim about itself.
