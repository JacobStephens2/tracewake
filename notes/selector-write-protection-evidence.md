# Write protection: what was run rather than assumed

*2026-08-30, issue #165. The behaviour is carried by the offline suites -
`selector/tests/test_guardrail.py` (what the cycle journals), 
`selector/tests/test_protection_source.py` (what the command reports, `gh`
faked and the tree throwaway) and the chip's eight HTTP-level tests in
`lab/webapp/tests/test_loop_page.py`. What is written down here is what only
GitHub and this VM could say: that a direct push at a protected path is really
refused, and what the guardrail really reports about the tree that is really
running.*

## The push, refused

The acceptance criterion is a push, so a push was made. A commit touching a
protected path (`lab/single-user-factory/selector/cycle.py`), on top of
`origin/master`, pushed straight at `master`:

```
$ git commit -am "TEST: a direct push at a protected path (#165)"
[detached HEAD 36d5eaa0] TEST: a direct push at a protected path (#165)

$ git push origin HEAD:master
remote: error: GH013: Repository rule violations found for refs/heads/master.
remote: - Changes must be made through a pull request.
 ! [remote rejected]   HEAD -> master (push declined due to repository rule violations)
error: failed to push some refs to
    'https://github.com/Educational-Travel-Adventures/orchestration.git'
```

Pushed as `JacobStephens2`, who is an **admin** on the repository
(`gh api repos/.../orchestration --jq .permissions` →
`{"admin":true,...}`). That is the interesting half: the ruleset's one bypass
actor is the repository-admin role in `pull_request` mode, which lets an admin
merge a pull request without waiting for a second approval and does **not**
let anyone push. `current_user_can_bypass` reads `pull_requests_only`. So
there is no identity on this repository that can put a byte into an executed
path without a pull request.

The rules in force on `refs/heads/master`, from the ruleset `Protect master`
(id 21599249, created 2026-08-26, enforcement `active`):

```
$ gh api repos/Educational-Travel-Adventures/orchestration/rules/branches/master \
      --jq '.[].type'
deletion
non_fast_forward
pull_request
```

All three are required by the guardrail, and each for its own reason: a review
a force-push can replace is not a review, and neither is one on a branch that
can be deleted and re-created.

## What a path-scoped ruleset would have done

The ticket offers "a ruleset over those paths" as the mechanism. GitHub's
path-scoped rule is `file_path_restriction`, and it belongs to a **push**
ruleset: it blocks pushes touching those paths on *every* branch of the
repository. That is the opposite of what this build needs - the Selector's own
Run branches, and every branch a Proposal is made from, are pushes touching
`lab/single-user-factory/**`. A branch ruleset on the deployment ref is the
instrument that fits, and one was already in place.

## The other half: what is actually running

A ruleset says nothing about what a shared, group-writable checkout is holding
- and `/srv/orchestration` is exactly that: `conductor:srvwrite`, setgid, three
accounts editing it directly, sixteen systemd units exec'ing out of it. So the
guardrail also compares the deployed tree against the protected ref. Run
against the live tree on the day the ticket was built:

```
$ SELECTOR_PROTECTED_TREE=/srv/orchestration \
      lab/single-user-factory/selector/guardrail-sources/protection.sh
SELECTOR_GUARDRAIL_REF=master
SELECTOR_GUARDRAIL_REF_HEAD=c895c2fe3dd9
SELECTOR_GUARDRAIL_RULES=deletion,non_fast_forward,pull_request
SELECTOR_GUARDRAIL_PATHS=lab/single-user-factory/loop,lab/single-user-factory/selector,scripts/selector-cycle.service,scripts/selector-cycle.timer,scripts/with-orchestration-env.sh
SELECTOR_GUARDRAIL_UNREVIEWED=scripts/selector-cycle.service
```

Red, and correctly so. The shared tree was checked out on
`issue-248-scripts-selinux-relabel`, and that branch adds an `ExecStartPre=`
to the Selector's own unit - a line that runs as **root** (`+` prefix) before
every cycle:

```
+ExecStartPre=+/bin/sh -c '/usr/sbin/restorecon -v /srv/orchestration/scripts/*.sh'
```

Perfectly reasonable work, and unmerged: exactly the state story 34 is about.
Nothing on the page said so before this ticket, and the box card could not -
it reports what the *box* is holding, not what this VM is about to exec.

## Stale is not unreviewed - the bug the live tree found

The first version of the comparison was two-dot (`git diff <ref> -- <paths>`)
and reported **twenty-four** unreviewed paths against that same tree. Every
one of them was a path where `origin/master` had moved on and the checkout had
not pulled: content that was reviewed when it landed and is simply newer than
the tree. A chip that went red for that would be red permanently, and a chip
that is always red is one nobody reads.

The comparison is now three reads - working tree against `HEAD`, untracked
files, and `<ref>...HEAD` - so it reports what the tree is *carrying* that the
protected ref's history has not seen, never what the ref has that the tree has
not caught up with. `test_a_tree_behind_the_protected_ref_has_nothing_unreviewed`
holds the distinction; it was written against the live reading above.

## The chip, rendered - green, from a real reading

The live `/loop` runs from `master` and cannot show a branch's page, so the
page was served on a loopback port and screenshotted headless. What it
rendered is **not** the fixture: `cycle.observe_guardrail` ran the real
`protection.sh` against a throwaway worktree checked out at `origin/master`,
against the real ruleset, and the row journaled is the one a cycle would have
written:

```json
{"ref": "master", "ref_head": "ddb8d522559d",
 "rules": ["deletion", "non_fast_forward", "pull_request"],
 "paths": ["lab/single-user-factory/loop", "lab/single-user-factory/selector",
           "scripts/selector-cycle.service", "scripts/selector-cycle.timer",
           "scripts/with-orchestration-env.sh"],
 "unreviewed": [], "protected": true, "detail": null}
```

On the page that is `the executed paths are review-gated`, then `master` at
`ddb8d522559d`, the three rules, `5 declared path(s) unchanged`, and when it
was read - in the palette's affirmative colour.

That matters more than a fixture render, because it answers the question the
fixture cannot: **is green reachable against reality?** It is, for a tree at
the protected ref. The live shared tree is not at the protected ref today (see
above), which is the honest state of that tree rather than a limitation of the
chip.

The red state was rendered the same way, from the live tree's own reading:
`unprotected`, and the verdict's own sentence,
`1 executed path(s) differ from master: scripts/selector-cycle.service`.

The colour is `--primary-color`, the vendored palette's affirmative one - the
same colour the `awaiting-review` badge carries and with the same meaning.
terminal.css has three colours and no green among them; a fourth introduced
for one chip would be this page leaving the factory's visual identity.

## The mutation check, partially run

The mutations this ticket adds were all caught - the four halves of the
verdict, the three reads the comparison makes, the chip's two guards, and the
two-dot/three-dot distinction above:

```
  a-dry-run-reads-the-guardrail              caught,  8 red
  an-unreadable-guardrail-reads-as-an-answer caught,  1 red
  an-unreadable-box-reads-as-an-empty-one    caught,  1 red
  a-missing-rule-is-still-protected          caught,  2 red
  an-unreviewed-path-is-still-protected      caught,  1 red
  an-unrun-comparison-reads-as-a-clean-one   caught,  1 red
  untracked-files-are-not-compared           caught,  1 red
  unmerged-commits-are-not-compared          caught,  1 red
  stale-reads-as-unreviewed                  caught,  1 red
  the-cards-hide-an-outage                   caught,  2 red
  an-old-reading-still-stands-for-now        caught,  1 red
  the-chip-is-green-regardless               caught,  2 red
```

Three of those are re-anchored rather than new. The code review found the two
status reads duplicating twenty lines of each other, and the page's two cards
duplicating their "newest of either kind" loop; both were extracted
(`cycle.read_facts`, `app._newest_reading`), which is also what had made the
box's existing mutation match twice and be refused. One break in the shared
reader now stands for both, and the pair above runs it against each suite in
turn - `the-cards-hide-an-outage` goes red in two tests, one per card.

**The whole set was not run.** `tests/mutation-check.sh` is 88 mutations and
each re-runs a suite; the Selector's suites have grown to 166 tests and about
two minutes, so a full run is measured in hours rather than in the six minutes
the README claimed. The claim is corrected there. A `--only <name>...`
argument would make running a subset something other than copying the runner's
loop by hand, and is named in the README as a gap rather than built here.

## A reading goes stale

The chip is a claim about the present, so the newest reading is only as good
as the cycle that took it. A reading older than ninety minutes - two firings
of the thirty-minute timer - reports its own silence
(`not checked recently`, and what the last one said) rather than the answer it
is holding. Without that, a Selector whose timer had died would keep asserting
protection it had not checked since, which is the exact failure the
next-cycle cell was built to make visible.

## What this does not cover

- **The ruleset is not declared in code.** `Protect master` lives in GitHub's
  settings; nothing in this repository would recreate it. The guardrail turns
  its removal into a red chip within one cycle, and cannot undo it. Codifying
  it would mean a GitHub provider and a token in `tofu/`, which is a decision
  about how this org's settings are managed rather than part of this build.
- **The guardrail attests to itself.** `protection.sh`, `paths.txt` and
  `cycle.py` are inside the declared paths, and it is the deployed copies that
  run - so anyone who can write to the shared tree can edit the checker to
  print `UNREVIEWED=` as easily as they can edit what it checks. This is a
  detector of drift and accident, not a defence against an operator with write
  access; who has that access is ADR 0014's question, and the answer here is
  the operator's own accounts.
- **The box's copy of the Loop.** `ansible/roles/loop_scripts` copies
  `lab/single-user-factory/loop/` to `loop.etadventures.com` at apply time, so
  the guardrail covers what the next apply would deploy, not what the box is
  holding now. That is the box card's job (`scripts_hash`), and the two
  together are the whole answer.
- **Dispatch is not gated.** A red chip stops nothing. The protection is about
  who can change the Selector, and a Selector that refused to work when its
  own guardrail went red would hand an unprotected repository a way to switch
  it off.
- **The venv.** `selector/.venv/` is gitignored and so is invisible to the
  comparison, and the unit execs its interpreter. Rebuilding it is an ansible
  role, not a merge. Accepted, and named here rather than left to be
  discovered.
