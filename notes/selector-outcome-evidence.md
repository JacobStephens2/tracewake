# Outcomes move the labels: what was verified, and the one thing that blocks

*2026-08-26, issue #155. The offline suites are the bulk of the evidence and
they are in the repository (`selector/tests/test_outcomes.py`, 33 scenarios).
What is written down here is what only the real GitHub could say - including a
false-pass this ticket found and fixed, and a token permission the live
Selector does not yet have.*

## The false green, found against a real Proposal

The first version of `issue-sources/github.sh checks` read the Proposal's CI
state like this:

```bash
rows="$(gh pr checks "${number}" --repo "${task_repo}" --json name,state 2>/dev/null || true)"
[[ -n ${rows} ]] || rows='[]'
```

which is wrong in a way no offline test could catch, because every offline
test answers with a file rather than with `gh`. Driven against a real
Proposal - tourbot#690, opened by this same ticket - it returned:

```
$ ./issue-sources/github.sh Educational-Travel-Adventures/tourbot checks 690
{"state":"green","failing":[]}
```

The Proposal's checks had not passed. They had not been read at all:

```
$ gh pr checks 690 --repo Educational-Travel-Adventures/tourbot --json name,state
rc=1
stdout=[]
stderr=[GraphQL: Resource not accessible by personal access token
        (node.statusCheckRollup.nodes.0.commit.statusCheckRollup.contexts.nodes.0), ...]
```

`gh` exits 1 with empty stdout, the `|| true` swallowed it, the `[]` default
turned "I could not look" into "there is nothing that can fail", and the
Selector would have swapped an unverified Proposal to `awaiting-review`. A
token quietly losing a permission is exactly the failure that path exists to
prevent, and it read as the all-clear.

**Three different things produce an empty stdout with exit 1**, and only one
of them is green:

| what happened | stderr |
| --- | --- |
| no check is configured on the Proposal | `no checks reported on the '<branch>' branch` |
| the token may not read check runs | `GraphQL: Resource not accessible by personal access token ...` |
| any other API failure | whatever gh says |

So they are now told apart by stderr, and anything that is not "no checks
reported" is a failure the Selector pages for. Both branches verified against
real Proposals:

```
$ ./issue-sources/github.sh Educational-Travel-Adventures/tourbot checks 690
issue-sources/github.sh: could not read the checks on ...#690: GraphQL: Resource
not accessible by personal access token ...
exit=1

$ ./issue-sources/github.sh Educational-Travel-Adventures/orchestration checks 170
{"state": "none", "failing": []}
exit=0
```

The second is a Proposal on a repository with no CI at all. It was first
written as **green** - "no check can fail, so nothing is wrong" - and the code
review was right that this is the same false pass by another road: on a
repository that does have CI, "no checks reported" usually means a workflow
did not trigger, and story 16 authorises `awaiting-review` for *green checks*,
not for the absence of any. It is now its own state, `none`, routed to the
operator with a comment saying that no check ran and that this is not the same
as passing.

## The blocker: the token cannot read tourbot's checks

The permission error above is not a bug in this ticket's code. It is the live
state of the credential the Selector runs under. `gh auth status` here is the
operator's fine-grained PAT (`JacobStephens2`, `GH_TOKEN`), and it holds no
permission to read check runs on tourbot.

**Consequence for the green path: it cannot run yet.** With the fix above, a
clean Run on tourbot reaches `dispatch.settled_checks`, the command fails, the
cycle journals `issue.route-failed` and exits 1, and the timer's `OnFailure`
pages the operator. That is the correct behaviour for a fact the Selector
cannot establish - it never mislabels - but it means every green Run pages
until the token is widened.

What is needed is **Checks: Read** on the fine-grained PAT (and `Actions: Read`
if workflow-run conclusions are wanted alongside check runs). Adding it is a
browser action on the token, which is the operator's to do - the credential
inventory notes that PAT scopes are UI-only.

Until then, the other three routes are unaffected: a first failure retries, a
second failure gives up, and a missing section is still handed back, none of
which read checks.

## What the offline suite covers, and what it deliberately does not

33 scenarios in `tests/test_outcomes.py` drive the real `cycle.py` and assert
only what a route can be seen to do from outside - which label swap was
issued, what the comment said, which Journal row was appended. The four routes
are covered, and so are the two that matter most for safety:

- **No issue is dispatched a third time**, asserted twice: once by the label
  swap taking it out of the queue, and once with the swap deliberately
  failing (`ISSUE_EXIT=1`), because the retry budget is read from the Journal
  rather than from the label and has to hold on its own.
- **Pending is not green.** CI is scripted to answer `pending`, `pending`,
  then `green`, and the Selector's polling is driven rather than assumed - the
  test counts three `checks` invocations.
- **The journaled label is the label that was applied.** One value reaches
  both the tracker and the Journal, so a row cannot say one queue while the
  issue sits in another. Mutating it apart turns five tests red.

What the suite cannot grade is the jq translation from `gh pr checks` rows to
`{state, failing}`, because no test here runs `gh`. Its eight branches were
checked by hand against canned rows:

```
no checks         -> {"state":"none","failing":[]}
all SUCCESS       -> {"state":"green","failing":[]}
one FAILURE       -> {"state":"red","failing":["lint"]}
IN_PROGRESS       -> {"state":"pending","failing":[]}
SUCCESS+PENDING   -> {"state":"pending","failing":[]}
FAILURE+PENDING   -> {"state":"red","failing":["a"]}
SKIPPED+NEUTRAL   -> {"state":"green","failing":[]}
WEIRD_NEW_STATE   -> {"state":"red","failing":["a"]}
```

The last two are the deliberate ones. `SKIPPED` and `NEUTRAL` are green
because a check that did not need to run has not failed. An unrecognised state
is **red**, not green: a state GitHub adds later must never reach
`awaiting-review` as though it had passed, and `cycle.py` falls through the
same way.

Reading those rows against a real tourbot Proposal is blocked on the same
token permission, and is the one acceptance-criterion-adjacent check this
ticket could not close.

## Viewing

`/loop` was rendered against a seeded Journal holding all four routes. Each
Run card carries the label the issue actually carries - `awaiting-review` in
the accent colour, `ready-for-human` in the error colour, `retrying` in grey -
and a line saying what happens next. The green card reads "Checks green. The
issue is swapped to `awaiting-review` and is waiting on you."

The badge carries the label string verbatim rather than a friendlier word, so
that what the page says and what the operator sees on the issue are the same
token.

## Postscript, 2026-08-31: the blocker was not a missing grant

The section above says the green path waits on **Checks: Read** being added to
the fine-grained PAT. That grant does not exist. GitHub's fine-grained personal
access tokens offer no Checks permission at all - the token's permission picker
lists Attestations, then Code quality, and a search for "check" returns nothing;
the fine-grained-permissions reference has no Checks section and does not cover
`GET /repos/{owner}/{repo}/commits/{ref}/check-runs`. The Checks API is readable
only by GitHub Apps, OAuth tokens, and classic PATs with `repo`. So the 403 was
permanent for this token type, whatever anyone granted (#273).

What is readable is the Actions API, which the token's existing Actions
permission covers. On `40349e3f26bc2bb0a85d92e010e241033ff62bcd`, the head of
tourbot#713 - the Proposal the first unattended Run produced - under the
Selector's own `GH_TOKEN`:

```
$ gh api repos/Educational-Travel-Adventures/tourbot/commits/$SHA/check-runs
403 Resource not accessible by personal access token

$ gh api "repos/Educational-Travel-Adventures/tourbot/actions/runs?head_sha=$SHA"
200, total_count 3 - Tourbot CI, ADR numbering x2, all completed/success
```

All five check runs on that commit are `github-actions`, so the two sources say
the same thing about tourbot today. `checks` now reads the head commit with
`gh pr view` and grades `actions/runs?head_sha=`, and the jq translation the
section above could only check by hand is driven by `tests/test_issue_source.py`
with `gh` faked on PATH - green, red, pending, none, an unknown conclusion, a
second page, and both refusals. End to end against the real Proposal:

```
$ ./issue-sources/github.sh Educational-Travel-Adventures/tourbot \
      checks https://github.com/Educational-Travel-Adventures/tourbot/pull/713
{"state":"green","failing":[]}
exit=0
```

The cost, recorded here because it is invisible from the output: this reads
only Actions-based checks. A required check posted by a non-Actions app would
not be seen, and a Proposal waiting on one could read green. None exists on
tourbot. Buying that edge case back means a classic PAT with `repo` scope or
installation tokens from a GitHub App - a broad credential, or new moving
parts.
