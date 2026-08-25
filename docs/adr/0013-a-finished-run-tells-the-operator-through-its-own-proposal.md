# A finished Run tells the operator through its own proposal

Spec issue #73's user story 6 asks that a Run tell the operator when it has
finished, so that he does not have to poll it. Everything up to #110 left that
undelivered: a Run printed its result and exited, and all four Runs on record
were started by hand and read afterwards - which is not the same thing, and is
exactly what the Termination Contract's premise rules out. If the operator has
walked away, a result he has to come back for is a result nobody has.

**A Run started with `--notify` comments on the draft pull request it just
opened.** No new host on the egress allowlist, no new credential in the
inventory, and the news lands on the thing there is to review.

## Why not a surface that arrives on a phone

Mail, SMS, a chat webhook and a push service are all better notifications and
all cost the same two things: a host through the Execution Boundary's `deny-all`
egress (#100), and a credential on a box whose entire value is holding four
(ADR 0009). The box reaches `github.com` and `api.github.com` and holds a
repository-scoped token for exactly those, so the proposal's own conversation is
the one surface already paid for.

The token gains nothing by being used this way. A pull request's conversation is
posted to `POST /repos/{owner}/{repo}/issues/{number}/comments` - a pull request
is an issue to that half of the API - and a fine-grained token reaches it with
`Pull requests: write`, which the box already holds. It still has no Issues
permission at all (ADR 0010), which is why the surface validates that what it
was handed is a **pull request** URL rather than posting to whatever number
arrived: `/issues/` in, and the same request would be an issue comment the box
is not supposed to be able to make.

It happens where the push and the pull request happen: on the host, after every
agent process is gone. An Iteration has no GitHub token and cannot send a
notification any more than it can open a proposal, so `--notify` widens what is
inside the Execution Boundary by nothing. `api.github.com` is already on the
boundary's egress allowlist for the Run's own repository traffic (#100), and
this adds no entry to it.

## The one thing this depends on that is not in the repository

The token is the operator's, so the comment is authored by the operator, and
**GitHub does not notify you about your own activity unless you ask it to**. The
setting is in GitHub's own notification settings, under email preferences: *your
own updates, such as when you open, comment on, or close an issue or pull
request*. Without it the comment is written and nothing arrives.

That is recorded here rather than left as a surprise because it is the single
point on which the whole mechanism turns, it lives in an account rather than in
a file this repository can assert, and its failure mode is silence - the Run
reports `LOOP_RUN_NOTIFIED=sent` and it will be true, because the comment was
made. What was not established is that anybody read it.

Setting it is a stage of `wizards/loop-github-credentials.sh`, which is where
the rest of the operator's GitHub-side setup already is.

## What it does not reach

A Run whose proposal failed has nothing to comment on, and there is no second
surface - the box's whole external reach is the repository. It reports
`LOOP_RUN_NOTIFIED=no-surface` rather than a failed notification, because the
two are different: one is a surface that refused and the other is a Run that had
none. A Run that fails its preflight notifies nobody either; it never started,
and it fails at second zero in front of whoever typed it.

Both are honest limits of "nothing new on the allowlist and nothing new in the
inventory" rather than defects, and both are the argument to reach for if a real
notification surface is ever worth its host and its credential.

## Telling somebody is not part of what happened

The notification is the last act of a Run, after the proposal, and it cannot
change the Run:

- It does not move the exit code. A Run that reached its planned end and could
  not be reported still exits `0` - unlike a proposal that failed, which exits
  `6` (ADR 0007), because a Run whose proposal is missing has produced nothing
  to review while a Run nobody was told about has produced everything.
- It is not written into the Progress Log. The log was committed and pushed with
  the proposal, so a commit made now would leave the branch on GitHub
  disagreeing with the checkout on the box - and the log is the Run's record,
  while whether somebody was told about it is not part of the Run.

Where it is reported is stdout, alongside the ending bound and the exit code, as
`LOOP_RUN_NOTIFIED=sent | failed | no-surface | skipped`.

## Consequences

Notifying is per Run, not per box: `run.sh --notify`, refused at second zero
without `--propose`. A Run started in a terminal somebody is watching sends
nothing, which is what the story asks for - the operator polling a Run he is
looking at is not a problem to solve.

The surface is one substitutable command (`LOOP_NOTIFY_COMMAND`, ADR 0004),
which is what makes the paragraph above reversible: a Run that should page a
phone instead is a different script in `notify-sources/` and no change to
`run.sh`, and the offline suite drives the real Run through a scripted fake, so
none of this costs a token, a network call or a model.
