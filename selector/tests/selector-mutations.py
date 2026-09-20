"""The deliberate breaks tests/mutation-check.sh applies to the Selector.

The Selector picks work for an unattended agent with production-adjacent
credentials on the same VM, so what matters is that every clause of
Eligibility, every cap, and the journaling of the reasoning are each verified
by something. A guard whose removal leaves the suite green is a guard the
suite does not check, and on a single-operator project with no adversarial
reviewer that is the highest-value verification available.

Kept beside the suite so that adding a guard means adding its mutation, and a
guard nobody mutated is visible as an absence. Same shape as the Loop's
mutation files (loop/tests/*-mutations.py).

Each entry names the file it breaks and the suite that must notice, because
the Selector is now two modules with two suites: a mutation checked against
the wrong suite would be reported as caught by tests that never exercised
it.
"""
import pathlib
import sys

CYCLE = "cycle.py"
CONTROL = "control.py"
DISPATCH = "dispatch.py"
WATCHER = "watcher.py"
BOARD = "board.py"
CYCLE_SUITE = "tests/test_cycle.py"
DISPATCH_SUITE = "tests/test_dispatch.py"
OUTCOMES_SUITE = "tests/test_outcomes.py"
UNATTENDED_SUITE = "tests/test_unattended.py"
RECONCILE_SUITE = "tests/test_reconcile.py"
WATCHER_SUITE = "tests/test_watcher.py"
BOARD_SUITE = "../web/tests/test_queue_board.py"
CYCLE_SERVICE = "../deploy/systemd/tracewake-selector-cycle.service"
CONTROLLER_UNITS_SUITE = "tests/test_controller_units.py"

# The write protection over the executed paths (#165). Two targets, because
# the guardrail is two things: the command that reads the forge and the tree,
# and the verdict `cycle.py` makes of what it read.
PROTECTION = "guardrail-sources/protection.sh"
GUARDRAIL_SUITE = "tests/test_guardrail.py"
PROTECTION_SUITE = "tests/test_protection_source.py"

# The tracker-writing surface, and the reading of a Proposal's checks. Every
# other suite replaces this script with a scripted fake, which is what let its
# own argument handling reach production unexercised.
ISSUE_SOURCE = "issue-sources/github.sh"
ISSUE_SOURCE_SUITE = "tests/test_issue_source.py"
SEARCH_SOURCE = "search-sources/github.sh"
SEARCH_SUITE = "tests/test_search_source.py"

# The window (#156). Its path is relative to the Selector, and its suite is
# the window's own - run from `web/`, which mutation-check.sh handles by
# naming both.
WINDOW = "../web/app.py"
WINDOW_SUITE = "../web/tests/test_loop_page.py"
AUTH = "../web/auth.py"
AUTH_SUITE = "../web/tests/test_sign_in.py"
MAIL = "../web/mail.py"
INVITE_SUITE = "../web/tests/test_invite.py"
RESET_SUITE = "../web/tests/test_reset.py"
ROLES_SUITE = "../web/tests/test_roles.py"
HOST = "../web/host.py"
HOST_WIDGET = "../web/templates/_host.html"
HOST_SUITE = "../web/tests/test_host_telemetry.py"

# The push (#159). The stream is Journal SQL and lives with the Journal; the
# region it re-fetches is a template, which is a mutation target like any
# other - a swap that drops its own trigger is one attribute deleted.
JOURNAL = "journal.py"
LIVE_REGION = "../web/templates/_loop_live.html"
LIVENESS_SUITE = "../web/tests/test_liveness.py"

# The throwaway-database harness (#178). Its guards are not about what the
# Selector picks - they are about the suite's own residue not accumulating
# unseen on the box, which is a property no other suite can check for it.
TESTDB = "testdb.py"
TESTDB_SUITE = "tests/test_testdb.py"

# Run history and the budget (#160). The same window module, a suite of its
# own: history's claim is what it does NOT read, and a mutation checked
# against test_loop_page.py - which drives a page that reads the tracker on
# every request - would be checked by tests that cannot tell the difference.
HISTORY_REGION = "../web/templates/_history_live.html"
HISTORY_SUITE = "../web/tests/test_run_history.py"

# The notifier (#280, ADR 0018). Two targets and two suites, because it is two
# things: which rows are worth an email and what they say (`notices.py`,
# driven directly), and the delivery around that - the cursor, the once-only
# record, the failure that loses nothing (`notifier.py`, driven as a process
# against a real Journal). A mutation of the second checked against the first
# would be checked by tests that never open a database.
NOTICES = "notices.py"
NOTIFIER = "notifier.py"
NOTICES_SUITE = "tests/test_notices.py"
NOTIFIER_SUITE = "tests/test_notifier.py"

# The Journal Event vocabulary. Its own suite drives the constructors, the
# readers and the naming rules directly, and also holds the sweep - the guard
# that no shipping module spells a kind outside events.py. The seed fixture is
# a mutation target of its own because it impersonates the writer, and the
# suite that grades it is the window's.
EVENTS = "events.py"
EVENTS_SUITE = "tests/test_events.py"
SEED = "seed.sql"
SEED_SUITE = "../web/tests/test_staging_seed.py"
RUNS_REGION = "../web/templates/_runs.html"
LOOP_CSS = "../web/static/loop.css"

# The instance's configuration (issue #3). Its guards are all refusals - a
# required value that is absent, a key nobody reads, a landing mode nothing
# implements - and a refusal that stopped refusing would not fail: it would
# run, against something nobody configured. Two suites, because the loader is
# driven directly and what reaches the box is only visible through a dispatch.
TARGETS = "targets.py"
TARGETS_SUITE = "tests/test_targets.py"

# The box surface: the one place a target's checkout, token and image become
# something the Run can read. `ssh` forwards no environment, so a value not
# carried here is silently absent on the box - and every target's Run then
# uses whatever the box was last configured with.
BOX_SOURCE = "box-sources/ssh.sh"
LOCAL_SOURCE = "box-sources/local.sh"
FACTS_SOURCE = "box-sources/facts.sh"
BOX_SOURCE_SUITE = "tests/test_box_source.py"

MUTATIONS = {
    # Selection stops being lowest-first, so which issue gets worked depends
    # on tracker ordering rather than on a rule the operator can predict.
    "highest-picked-first": (CYCLE, CYCLE_SUITE, "record = eligible[0]", "record = eligible[-1]"),
    # Anyone who can apply the label spends the operator's Runs - the whole of
    # ADR 0014's trust argument, deleted.
    "labeler-not-checked": (CYCLE, CYCLE_SUITE,
        'if record.get("labeledBy") not in config.allowlist:',
        "if False:",
    ),
    # A chain gets worked top-down: an issue with open blocking edges is
    # started before the work it depends on exists.
    "blockers-ignored": (CYCLE, CYCLE_SUITE, "if blocked:", "if False:"),
    # A parent spec is mistaken for a unit of work and seeded whole.
    "sub-issues-ignored": (CYCLE, CYCLE_SUITE, "if sub_issues:", "if False:"),
    # An issue already in flight is picked again, so the same work is started
    # twice on two branches.
    "open-proposal-ignored": (CYCLE, CYCLE_SUITE, "if open_proposals:", "if False:"),
    # The retry budget never runs out, so a task that cannot be done is
    # re-dispatched forever instead of reaching the operator.
    "retry-budget-unbounded": (EVENTS, CYCLE_SUITE, "MAX_ATTEMPTS = 2", "MAX_ATTEMPTS = 9999"),
    # The budget stops being scoped to the current Handover, so re-labeling a
    # given-up issue - the operator saying "try that again" - does nothing.
    "budget-ignores-the-handover": (CYCLE, CYCLE_SUITE,
        "and (after is None or d.at > after)",
        "and True",
    ),
    # In flight is counted per issue rather than per attempt, so a retry of an
    # issue that already has an outcome does not hold the lock and a second
    # Run is dispatched underneath it.
    "in-flight-counted-per-issue": (CYCLE, CYCLE_SUITE,
        "key for key, n in started.items() if n > ended.get(key, 0)",
        "key for key, n in started.items() if key[1] not in {i for _, i in ended}",
    ),
    # Concurrency arrives by accident: a second Run is dispatched while one is
    # still in flight.
    "in-flight-cap-ignored": (CYCLE, CYCLE_SUITE,
        "elif config.drain_concurrency == 1 and cycle_spend.in_flight:",
        "elif False:",
    ),
    # The review cap stops bounding the review pile.
    "review-cap-ignored": (CYCLE, CYCLE_SUITE,
        'elif budget["remaining"] == 0:',
        "elif False:",
    ),
    # An underspecified issue is seeded anyway, which is the case ADR 0014
    # says must be returned to the operator rather than guessed at.
    # Anchored with its indentation: `guardrail_verdict` has an `if missing:`
    # of its own, and an anchor that matches twice is one the harness refuses.
    "sections-not-required": (CYCLE, CYCLE_SUITE,
        "\n    if missing:", "\n    if False:",
    ),
    # A section heading with nothing under it satisfies the requirement, so an
    # empty `## Owning area` seeds a Run scoped to the empty string.
    "empty-section-counts-as-present": (CYCLE, CYCLE_SUITE,
        'if not _first_line(present.get(name.lower(), ""))',
        "if name.lower() not in present",
    ),
    # Headings inside fenced blocks count, so an issue quoting another
    # issue's template passes the section check on the quote.
    "fenced-headings-count": (CYCLE, CYCLE_SUITE,
        "heading = None if fence else _HEADING.match(line)",
        "heading = _HEADING.match(line)",
    ),
    # A tracker that failed reads as an empty queue: the Selector goes quiet
    # and nothing pages the operator (spec story 31).
    "tracker-failure-swallowed": (CYCLE, CYCLE_SUITE,
        "if completed.returncode != 0:",
        "if False:",
    ),
    # Skips stop being journaled under the name the window reads, so "why not
    # #646 yesterday?" has no recorded answer.
    "skips-not-journaled": (EVENTS, CYCLE_SUITE,
        'ISSUE_SKIPPED = "issue.skipped"', 'ISSUE_SKIPPED = "issue.considered"',
    ),
    # The in-flight lock never expires, so one cycle killed between
    # dispatching and recording the outcome wedges every later cycle forever.
    "in-flight-lock-never-expires": (CYCLE, CYCLE_SUITE,
        "IN_FLIGHT_STALE_HOURS = 8",
        "IN_FLIGHT_STALE_HOURS = 99999",
    ),
    # Ordering the queue moves outside the guarded read, so a malformed
    # record crashes with nothing journaled instead of failing the cycle.
    "malformed-record-crashes-unjournaled": (CYCLE, CYCLE_SUITE,
        '        return sorted(payload["issues"], key=lambda record: int(record["number"]))\n'
        "    except (ValueError, KeyError, TypeError) as exc:\n"
        '        raise CycleFailed(f"tracker command did not return a queue: {exc}") from exc',
        '        issues = payload["issues"]\n'
        "    except (ValueError, KeyError, TypeError) as exc:\n"
        '        raise CycleFailed(f"tracker command did not return a queue: {exc}") from exc\n'
        '    return sorted(issues, key=lambda record: int(record["number"]))',
    ),
    # --- Dispatch (#154) ---------------------------------------------------
    #
    # From here on the Selector is not only choosing work but doing things:
    # writing to the tracker, pushing branches, starting Runs on a box. Every
    # one of these mutations is something that would happen unattended and be
    # discovered afterwards.

    # An underspecified issue is passed over in silence every half hour
    # forever, which is the case ADR 0014 says must be handed back.
    "loud-skip-goes-quiet": (CYCLE, DISPATCH_SUITE,
        'if reason == "missing-section" and not dry_run:',
        "if False:",
    ),
    # A dry run writes to the tracker: the mode that exists to change nothing
    # comments on issues and swaps their labels.
    "dry-run-hands-issues-back": (CYCLE, DISPATCH_SUITE,
        'if reason == "missing-section" and not dry_run:',
        'if reason == "missing-section":',
    ),
    # A dry run starts a Run. The one mode an operator uses to see what WOULD
    # happen spends a Run finding out.
    "dry-run-dispatches": (CYCLE, DISPATCH_SUITE,
        "if pick and not dry_run:", "if pick:",
    ),
    # The in-flight lock stops being written under the name every cycle reads,
    # so the ninety minutes a Run takes are unguarded and a second cycle
    # dispatches underneath the first.
    # Spend reads outcomes where it should read dispatches, so the ninety
    # minutes between the two rows - the in-flight lock itself - goes unread.
    "in-flight-lock-not-taken": (CYCLE, DISPATCH_SUITE,
        "(IN_FLIGHT_STALE_HOURS, events.RUN_DISPATCHED),",
        "(IN_FLIGHT_STALE_HOURS, events.RUN_OUTCOME),",
    ),
    # The comment naming the gap says nothing, so an issue is taken out of the
    # queue with no record of why - the silence the loud skip exists to stop.
    # Pre-existing stale anchor, repointed 2026-08-27: the call gained
    # `+ SIGNATURE` and this was never updated, so it had stopped mutating
    # anything and the comment's contents were unverified.
    "return-comment-says-nothing": (CYCLE, DISPATCH_SUITE,
        "            _missing_section_comment(config, detail) + SIGNATURE,",
        '            "" + SIGNATURE,',
    ),
    # A hand-back GitHub refused exits 0, so the timer never pages and the
    # issue sits in the queue with nothing on it.
    "return-failure-swallowed": (CYCLE, DISPATCH_SUITE,
        "    return 1 if failures else 0", "    return 0",
    ),
    # Every dispatch is attempt 1, so the Journal cannot tell a first Run from
    # a retry and #155's give-up has nothing to count.
    "every-dispatch-is-the-first": (CYCLE, DISPATCH_SUITE,
        '                attempt = cycle_spend.attempts(\n'
        '                    pick["number"], picked_record.get("labeledAt"),\n'
        '                    repo=config.task_repo,\n'
        '                ) + 1',
        "                attempt = 1",
    ),
    # A Run that ended on a bound is reported as a dispatch failure, so the
    # Termination Contract working pages the operator every time.
    "a-bound-reads-as-a-broken-dispatch": (DISPATCH, DISPATCH_SUITE,
        'if "LOOP_RUN_ENDED_BY" not in fields:',
        "if completed.returncode != 0:",
    ),
    # A retry resets the branch to the base, discarding what the first attempt
    # committed and proposing an empty diff.
    "retry-discards-the-first-attempt": (DISPATCH, DISPATCH_SUITE,
        "    if remote_has_branch(config, branch):",
        "    if False:",
    ),
    # An earlier attempt's Progress Log is left where it is, so Seeding refuses
    # to overwrite it and every retry of an issue that actually ran dies at
    # Seeding - which is #301, and is invisible to a suite whose box writes no
    # Progress Log.
    "the-previous-progress-log-is-not-moved-aside": (DISPATCH, DISPATCH_SUITE,
        '    if not re.search(f"^{re.escape(config.run_heading)}", text, re.MULTILINE):\n'
        "        return None",
        "    if True:\n"
        "        return None",
    ),
    # It is moved aside but written whole, so a third attempt keeps the second
    # by discarding the first - the loss the move exists to avoid.
    "keeping-a-progress-log-overwrites-the-one-before-it": (DISPATCH, DISPATCH_SUITE,
        '    target.write_text(f"{earlier.rstrip()}\\n\\n{text.strip()}\\n")',
        '    target.write_text(f"{text.strip()}\\n")',
    ),
    # A branch cut from the base inherits and appends to whatever kept log the
    # base carries, so one file accumulates unrelated issues' Runs for the life
    # of the repository.
    "the-kept-log-is-inherited-across-branches": (DISPATCH, DISPATCH_SUITE,
        "    continuing = remote_has_branch(config, branch) and target.exists()",
        "    continuing = target.exists()",
    ),
    # The Plan never reaches the box: the branch is seeded here and the Run is
    # started over there against whatever the remote already had.
    "plan-never-pushed": (DISPATCH, DISPATCH_SUITE,
        '    _git(config, "push", "--set-upstream", config.remote, branch)',
        "    return",
    ),
    # Seeding's refusal - the component that would otherwise have to guess
    # what done means - is worked around and the Run starts anyway.
    "seeding-refusal-ignored": (DISPATCH, DISPATCH_SUITE,
        "    if completed.returncode != 0:\n"
        '        raise DispatchFailed(f"Seeding refused #{number}: {_said(completed)}")',
        "    if False:\n"
        '        raise DispatchFailed(f"Seeding refused #{number}: {_said(completed)}")',
    ),
    # The branch stops naming the issue, so two Runs on the same area collide
    # and a branch list stops answering "what is this for?".
    "branch-does-not-name-the-issue": (DISPATCH, DISPATCH_SUITE,
        'return f"{config.branch_prefix}{number}-{slug}" if slug else \\',
        'return f"{config.branch_prefix}{slug}" if slug else \\',
    ),

    # --- Outcome routing (#155) ---------------------------------------------
    #
    # The half after the Run. Each of these is a way for work to end up in the
    # wrong queue silently, which on an unattended Selector means either the
    # operator never learns a Run failed or unreviewed work is presented as
    # ready to merge.

    # A Run cut short by the Termination Contract is read as a success, so a
    # failed Run's branch is offered for review as though it had finished.
    "run-failures-not-detected": (EVENTS, OUTCOMES_SUITE,
        'RUN_FAILURE_BOUNDS = ("run-clock", "consecutive-noops", "agent-failed")',
        "RUN_FAILURE_BOUNDS = ()",
    ),
    # A Run that proposed nothing is treated as one that did, so the Selector
    # reads checks for a Proposal that does not exist.
    # The predicate is spelled once now, in the vocabulary, so this breaks it
    # there - and the router's suite must still notice.
    "no-proposal-treated-as-a-proposal": (EVENTS, OUTCOMES_SUITE,
        "return ended_by in RUN_FAILURE_BOUNDS or not proposal",
        "return ended_by in RUN_FAILURE_BOUNDS",
    ),
    # The retry budget is never spent on failures, so a Run that fails every
    # time is retried forever and the operator is never told.
    "failure-retried-forever": (CYCLE, OUTCOMES_SUITE,
        "if failure and attempt < MAX_ATTEMPTS:",
        "if failure:",
    ),
    # The first failure gives up immediately, so story 14's self-healing
    # retry never happens and every transient failure reaches the operator.
    "first-failure-never-retried": (CYCLE, OUTCOMES_SUITE,
        "if failure and attempt < MAX_ATTEMPTS:",
        "if False:",
    ),
    # Checks are never consulted: every clean Run goes to review, red or not.
    "checks-ignored": (CYCLE, OUTCOMES_SUITE,
        '    if state == "green":',
        "    if True:",
    ),
    # CI that has not finished is treated as CI that passed, so unverified
    # work is put in the review queue - the failure the wait bound exists for.
    "pending-treated-as-decided": (CYCLE, OUTCOMES_SUITE,
        '    if state == "pending":',
        "    if False:",
    ),
    # The Selector waits for nothing, so a Proposal whose checks are merely
    # slow is routed to a human as though CI had stalled.
    "checks-never-waited-for": (DISPATCH, OUTCOMES_SUITE,
        'if answer["state"] != "pending":',
        "if True:",
    ),
    # A Proposal that no check ran against is called green, so unverified
    # work reaches the review queue - the false pass a permission error once
    # produced, arrived at by a different road.
    "no-checks-treated-as-green": (CYCLE, OUTCOMES_SUITE,
        '    if state == "green":',
        '    if state in ("green", "none"):',
    ),
    # The journaled label stops being the label that was applied, so the
    # Journal can say one queue while the tracker says another.
    # The route's one value used to be split into a journaled label and an
    # applied one; the constructors closed that seam, so the divergence left
    # to guard is the relabel itself applying something other than the route.
    "journaled-label-is-not-the-applied-one": (CYCLE, OUTCOMES_SUITE,
        "            add=route.label, remove=config.label,",
        "            add=config.review_label, remove=config.label,",
    ),
    # Bookkeeping the tracker refused is swallowed, so a Run whose result
    # never reached the issue looks like a cycle that worked (story 31).
    "route-failure-not-paged": (CYCLE, OUTCOMES_SUITE,
        "journal.append(conn, *failed(str(exc)))\n        raise CycleFailed(str(exc)) from exc",
        "journal.append(conn, *failed(str(exc)))\n        return route.name",
    ),
    # The comment is posted after the swap rather than before, so a swap that
    # landed and a comment that failed leaves the issue out of every queue
    # with nothing on it saying why.
    "handover-relabels-before-commenting": (CYCLE, OUTCOMES_SUITE,
        "        if body is not None:\n            dispatch.comment(\n                dispatch_config, config.task_repo, number, body + SIGNATURE\n            )\n        dispatch.relabel(\n            dispatch_config, config.task_repo, number,\n            add=route.label, remove=config.label,\n        )",
        "        dispatch.relabel(\n            dispatch_config, config.task_repo, number,\n            add=route.label, remove=config.label,\n        )\n        if body is not None:\n            dispatch.comment(\n                dispatch_config, config.task_repo, number, body + SIGNATURE\n            )",
    ),
    # Two cycles reason at once. Under a thirty-minute timer and a
    # ninety-minute Run this is the ordinary case, not a rare race: the
    # overlapping cycle reads the same spend and can dispatch the same issue a
    # second time.
    "cycles-may-overlap": (CYCLE, UNATTENDED_SUITE,
        'if not conn.execute(\n'
        '                "SELECT pg_try_advisory_lock(%s)", (CYCLE_LOCK_KEY,)\n'
        '            ).fetchone()[0]:',
        "if False:",
    ),
    # A dry run reaches the box. The property that a dry run touches the
    # tracker and nothing else is what makes it safe to run against
    # production from a keyboard.
    "a-dry-run-reaches-the-box": (CYCLE, UNATTENDED_SUITE,
        "            if not dry_run:\n                facts, box_error = observe_box(config)",
        "            if True:\n                facts, box_error = observe_box(config)",
    ),
    # The owning area stops falling back to the issue title, so every issue
    # without the section - which is most of them, and is why the requirement
    # was dropped - reaches Seeding with an empty `--area` and is refused.
    "area-is-not-defaulted": (CYCLE, CYCLE_SUITE,
        '    return named or str(record.get("title") or "").strip()'
        ' or f"issue #{record[\'number\']}"',
        "    return named",
    ),
    # A box that answered non-zero is read as a box that answered nothing, so
    # the card renders a blank-fact success and an outage looks like a box
    # holding no Loop scripts, no template and no agent.
    # A status command that answered non-zero is read as one that answered
    # nothing, so an outage renders as a successful read with every fact
    # blank. One break in `read_facts`, checked TWICE - once in each suite
    # that watches a caller - because the two cells fail differently: the box
    # card would show a box holding no Loop scripts, and the guardrail chip
    # would grade a silence instead of journaling it.
    "an-unreadable-box-reads-as-an-empty-one": (CYCLE, UNATTENDED_SUITE,
        "    if done.returncode != 0:", "    if False:",
    ),
    # --- The Iteration watcher (#157) ---------------------------------------
    #
    # The watcher is the only thing that can see a Run while it is running, so
    # every one of these is a way for the page to lie about what is happening
    # on the box - either by inventing activity or by hiding it.

    # Every poll journals every Iteration it can see, so one Iteration becomes
    # a row a minute for the rest of the Run and the Journal the page renders
    # from is buried under its own re-reading.
    "iterations-journaled-every-poll": (WATCHER, WATCHER_SUITE,
        '            if record["iteration"] in seen:\n                continue',
        "            if False:\n                continue",
    ),
    # A retry journals the FIRST attempt's Iterations as its own: the log it
    # reads while the box is still checking out is the one that attempt wrote,
    # and the page shows a Run that has not started as five Iterations deep.
    "another-runs-iterations-are-journaled": (WATCHER, WATCHER_SUITE,
        "        if started is None or started < floor:",
        "        if False:",
    ),
    # A heading with nothing under it yet is journaled as an Iteration, so an
    # append caught mid-write becomes a permanent row saying nothing - and the
    # Journal is append-only, so it can never be corrected.
    "half-written-records-are-journaled": (WATCHER, WATCHER_SUITE,
        '        if record is not None and record.get("agent_exit") is not None:',
        "        if record is not None:",
    ),
    # The last Iteration of every Run is lost: it is written after the final
    # poll and the Run ends before the next one, so the page permanently shows
    # a Run one Iteration shorter than it was.
    "the-final-read-is-skipped": (WATCHER, WATCHER_SUITE,
        "            self._poll(conn, seen)\n        finally:",
        "            pass\n        finally:",
    ),
    # A box that cannot be read says so once a minute for ninety minutes,
    # which is an outage reported as ninety outages.
    "a-watch-failure-is-repeated-every-poll": (WATCHER, WATCHER_SUITE,
        "        if self._reported_failure:\n            return None",
        "        if False:\n            return None",
    ),
    # --- The Contract the Run is under (#162) -------------------------------

    # The Contract is journaled on every poll, so one Run's terms become a row
    # a minute for ninety minutes - the same burial as the Iterations above,
    # in a Journal that can only be appended to.
    "the-contract-is-journaled-every-poll": (WATCHER, WATCHER_SUITE,
        "        if self._contract_journaled:\n            return",
        "        if False:\n            return",
    ),
    # A summary caught mid-write is journaled, so the panel shows a Run bound
    # by however many of its five bounds had been printed when the poll landed
    # - and the row can never be corrected.
    "half-written-contracts-are-journaled": (WATCHER, WATCHER_SUITE,
        "    if not (run_started and lines and complete):",
        "    if not (run_started and lines):",
    ),
    # A retry shows the terms of the attempt before it: the block its watcher
    # reads while the box is still checking out is that attempt's, and its
    # Contract is not necessarily this dispatch's.
    "another-runs-contract-is-journaled": (WATCHER, WATCHER_SUITE,
        "        if not of_this_run([record], self.started,"
        " self.config.clock_skew_seconds):\n            return",
        "        if False:\n            return",
    ),
    # The Contract summary runs on past the line that ends it, so Iteration
    # 1's own fields - its agent exit, its head - are rendered on the panel as
    # bounds the Run is executing under.
    "the-contract-swallows-the-first-record": (WATCHER, WATCHER_SUITE,
        "        if run_started is None or complete:",
        "        if run_started is None:",
    ),
    # The panel stops carrying the Contract the box reported, so a Run in
    # flight says nothing about what it is allowed to do - which is the whole
    # of what the card is for.
    "the-contract-never-reaches-the-card": (WINDOW, WINDOW_SUITE,
        '            card["contract"] = events.run_contract_record(row).contract',
        "            pass",
    ),
    # The Run's Iterations render newest first, so a Run reads as counting
    # backwards and the page disagrees with the log it is showing.
    "iterations-render-newest-first": (WINDOW, WINDOW_SUITE,
        '        card["iteration_records"] = sorted(\n'
        '            card.get("iteration_records", []),\n'
        '            key=lambda record: record.get("iteration") or 0,\n'
        "        )",
        '        card["iteration_records"] = card.get("iteration_records", [])',
    ),

    # The strip stops saying anything about a timer that is not running, so a
    # Selector that cannot start a cycle renders the same as one with nothing
    # to do - the exact confusion the strip was built to end.
    "a-dead-timer-reads-as-idle": (WINDOW, WINDOW_SUITE,
        '    if timer.get("state") != "active":\n'
        '        return "stopped - nothing will start a cycle"',
        "    if False:\n        pass",
    ),
    # The page says dispatch is paused, but the next cycle ignores the flag
    # and starts a Run anyway.
    "the-pause-flag-is-ignored": (CYCLE, UNATTENDED_SUITE,
        "        if control.is_paused(conn):\n"
        "            # The timer keeps running while paused.",
        "        if False:\n"
        "            # The timer keeps running while paused.",
    ),
    # A Cycle stops after one dispatch rather than draining the queue.
    "cycle-stops-after-one-dispatch": (CYCLE, UNATTENDED_SUITE,
        "            dispatches.append(pick[\"number\"])",
        "            dispatches.append(pick[\"number\"])\n            break",
    ),
    # K=2 never overlaps: the drain stays sequential across Targets.
    "parallel-drain-never-starts": (CYCLE, UNATTENDED_SUITE,
        "    parallel = (not dry_run) and concurrency > 1 and len(configs) > 1",
        "    parallel = False",
    ),
    # K of zero is accepted, so a misconfigured instance drains nothing and
    # looks like a quiet queue.
    "zero-drain-concurrency-accepted": (CYCLE, CYCLE_SUITE,
        "    if value < 1:",
        "    if False:",
    ),
    # Per-Target leftover no longer halts, so two Runs start on one Target.
    "per-target-in-flight-ignored": (CYCLE, UNATTENDED_SUITE,
        "        elif config.drain_concurrency > 1 and cycle_spend.in_flight_on(config.task_repo):",
        "        elif False:",
    ),
    # The slot cap is not taken, so K=2 with three Targets starts all three.
    "drain-slots-not-acquired": (CYCLE, UNATTENDED_SUITE,
        "            if slots is not None:",
        "            if False:",
    ),
    # Pause is only honoured before the first dispatch, not between runs.
    "pause-not-checked-between-runs": (CYCLE, UNATTENDED_SUITE,
        "        if control.is_paused(conn):\n"
        "            # The timer keeps running while paused.",
        "        if control.is_paused(conn) and not dispatches:\n"
        "            # The timer keeps running while paused.",
    ),
    # The systemd unit reverts to bounded start timeout rather than infinity.
    "cycle-unit-timeout-not-infinite": (CYCLE_SERVICE, CONTROLLER_UNITS_SUITE,
        "TimeoutStartSec=infinity",
        "TimeoutStartSec=8100",
    ),
    # Clicking Pause writes `false`, so the response quietly returns the
    # ordinary active page and the operator believes a control did nothing.
    "the-pause-control-does-nothing": (CONTROL, WINDOW_SUITE,
        "        (paused,),",
        "        (False,),",
    ),
    # The flag is set but the page hides the banner and the Resume control.
    "the-paused-banner-is-hidden": (LIVE_REGION, WINDOW_SUITE,
        "    {% elif paused %}",
        "    {% elif False %}",
    ),
    # The box card shows the last SUCCESSFUL read instead of the last read, so
    # a box that has been unreachable for a week still renders last week's
    # hash, template and version as though they were current.
    # Both cards show the last SUCCESSFUL read instead of the last read, so a
    # box unreachable for a week still renders last week's hash and a
    # guardrail that has stopped answering still renders green.
    "the-cards-hide-an-outage": (WINDOW, WINDOW_SUITE,
        '        if row["kind"] in kinds:',
        '        if row["kind"] == kinds[0]:',
    ),

    # --- The queue board (#158) ---------------------------------------------
    #
    # The board's whole claim is that it is the Selector's own lens pointed at
    # the tracker rather than a second opinion about it. Every mutation here
    # is a way for it to keep looking like a board while saying something the
    # Selector does not say - which is worse than showing nothing, because the
    # operator would act on it.

    # The board stops applying Eligibility: every labeled issue reads as
    # eligible, so a blocked chain and an underspecified issue are shown as
    # the next thing that will be worked.
    "the-board-invents-its-own-eligibility": (BOARD, BOARD_SUITE,
        "        reason = cycle.eligibility(record, config, attempts)",
        "        reason = None",
    ),
    # A blocked card names no blockers, which is the count again: "blocked by
    # 1" with no way to find out by what.
    "blocked-cards-name-no-blockers": (BOARD, BOARD_SUITE,
        '        "blockers": record.get("blockers") or [],',
        '        "blockers": [],',
    ),
    # The board reads the Handover label only, so the two columns that are
    # waiting on the OPERATOR rather than on the Selector are permanently
    # empty and the queue looks drained.
    "the-board-reads-one-label": (BOARD, BOARD_SUITE,
        "    labels = (config.label, config.review_label, config.human_label)",
        "    labels = (config.label, config.label, config.label)",
    ),
    # A tracker that failed reads as a tracker that answered nothing, so an
    # outage renders as an empty queue - the same silence story 31 exists to
    # prevent, arrived at from the page rather than from the cycle.
    "a-tracker-failure-empties-the-board": (BOARD, BOARD_SUITE,
        "    except cycle.CycleFailed as exc:\n        return [], str(exc)",
        "    except cycle.CycleFailed:\n        return [], None",
    ),
    # The Journal's half of Eligibility is dropped: an issue the Selector is
    # running right now shows as eligible, because a dispatch with no outcome
    # is a lock nothing on the tracker records.
    "the-in-flight-lock-is-invisible-to-the-board": (BOARD, BOARD_SUITE,
        "        if number in in_flight_numbers:",
        "        if False:",
    ),

    # --- The push (#159) ----------------------------------------------------
    #
    # A live page fails quietly: it renders, it says `live`, and it is simply
    # not told about the row that landed. Every mutation here leaves a page
    # that looks exactly like one watching a Selector with nothing to do.

    # Nothing is listening, so no row ever reaches an open page - and the page
    # goes on claiming it is live, because the stream connected.
    "the-stream-listens-to-nothing": (JOURNAL, LIVENESS_SUITE,
        "        await conn.execute(f\"LISTEN {CHANNEL}\")",
        "        pass",
    ),
    # The replay stops at one batch. A page that missed more than that is
    # handed the first batch and left holding a stale id, with no later NOTIFY
    # coming to correct it - permanently stale rather than slow.
    "a-long-replay-is-truncated": (JOURNAL, LIVENESS_SUITE,
        "        if len(rows) < _BATCH:\n            return",
        "        return",
    ),
    # A reconnecting page is re-sent everything it already had, because the
    # stream forgets what the Journal's newest row was when it started.
    "a-reconnect-replays-what-was-seen": (JOURNAL, LIVENESS_SUITE,
        "        sent = newest or 0",
        "        sent = 0",
    ),
    # The swapped-in region drops the attribute that asks for the next one, so
    # the page updates exactly once and then looks live forever after.
    "the-region-does-not-rearm": (LIVE_REGION, LIVENESS_SUITE,
        '     hx-get="{{ live_url }}"\n',
        "",
    ),
    # --- Run history and the budget (#160) ---------------------------------
    #
    # The history is the page that answers "what has the Selector already
    # done?" from the Journal alone. Each mutation here leaves a page that
    # still renders Run cards while quietly ceasing to be that.

    # The history reaches the tracker after all, by rendering /loop's context
    # instead of its own - so the record of what the Selector has done goes
    # down whenever GitHub does, and takes a queue board it never asked for
    # with it.
    "the-history-reads-the-tracker": (WINDOW, HISTORY_SUITE,
        "    (events, older), _, error = _read_journal(_history_rows, empty)",
        "    return _loop_context(request)\n"
        "    (events, older), _, error = _read_journal(_history_rows, empty)",
    ),
    # The Run in flight is filed as history: a card with no bound, no duration
    # and no Proposal, shown as though the Run had ended.
    "an-unfinished-run-is-filed-as-history": (WINDOW, HISTORY_SUITE,
        '        "runs": [run for run in _runs(events) if not run.get("in_flight")],',
        '        "runs": _runs(events),',
    ),
    # An unreadable tracker renders as a full budget, so the page promises
    # 20 Runs remaining on the strength of a queue it never read.
    "an-unknown-budget-reads-as-full": (CYCLE, HISTORY_SUITE,
        "    remaining = None if count is None or cap is None else max(0, cap - count)",
        "    remaining = cap - (count or 0)",
    ),
    # A Run stops reporting how long it took, which is the one fact about it
    # that exists nowhere else: the box persists no record of a finished Run.
    "a-run-reports-no-duration": (WINDOW, HISTORY_SUITE,
        '        card["duration"] = _duration(\n'
        '            card.get("started_at"), card.get("finished_at")\n'
        "        )",
        '        card["duration"] = None',
    ),
    # The history reads rows instead of Runs, so the oldest Runs fall off it
    # as later rows accumulate - silently, on the one page whose purpose is
    # that past Runs stay inspectable.
    "the-history-is-capped-in-rows": (WINDOW, HISTORY_SUITE,
        "    return journal.events(conn, since=floor, kinds=sorted(RUN_KINDS)), older",
        "    return journal.events(conn), older",
    ),
    # A Run's rows are read from its own dispatch onward, so a floor taken one
    # Run too high cuts the oldest card in half: the dispatch is missing, and
    # `_runs` drops the card without trace.
    "the-window-starts-above-its-oldest-run": (JOURNAL, HISTORY_SUITE,
        "    floor = rows[-1][0]", "    floor = rows[0][0]",
    ),
    # The Runs below the window stop being counted, so a page that is showing
    # part of the history looks exactly like one showing all of it.
    "older-runs-are-dropped-in-silence": (JOURNAL, HISTORY_SUITE,
        "    return floor, older", "    return floor, 0",
    ),
    # The history swaps in /loop's region instead of its own, so the first
    # Journal row to land replaces the page with a queue board - and reaches
    # the tracker to build it.
    "the-history-swaps-in-the-loops-region": (HISTORY_REGION, HISTORY_SUITE,
        '     hx-get="{{ live_url }}"', '     hx-get="/loop/live"',
    ),

    # Not mutated, and deliberately: the guard that the row re-read happens
    # OUTSIDE `conn.notifies()` - which holds the connection's lock while it is
    # iterated - is structural, not a clause. There is no one line to break.
    # Dropping `stop_after=1` alone no longer deadlocks; it only delays each
    # event by up to a keepalive, which the suite catches on its wait rather
    # than on the fault. Recorded here so the absence is visible.

    # --- The write protection over the executed paths (#165) ----------------
    #
    # The guardrail's whole worth is that it goes red. Every mutation here is
    # a way for the chip to stay green over an executor anybody could change:
    # a half nobody read, a rule nobody required, a tree nobody compared.

    # A dry run reads the guardrail - a `gh` call and a walk of the deployed
    # tree, on the mode whose property is that it reaches the tracker and
    # nothing else.
    "a-dry-run-reads-the-guardrail": (CYCLE, GUARDRAIL_SUITE,
        "    if not dry_run:\n        guardrail, guardrail_error = observe_guardrail(config)",
        "    if True:\n        guardrail, guardrail_error = observe_guardrail(config)",
    ),
    # Two declared trees are read in one cycle and the chip is green only when
    # both are: one green tree is not enough.
    "one-green-tree-protects-the-whole-guardrail": (CYCLE, GUARDRAIL_SUITE,
        '    all_protected = all(t["protected"] for t in tree_results)',
        '    all_protected = any(t["protected"] for t in tree_results)',
    ),
    # A tree the command did not answer for counts against the verdict; unknown
    # is never green.
    "an-unreadable-tree-is-treated-as-protected": (CYCLE, GUARDRAIL_SUITE,
        '                "protected": False,\n'
        '                "detail": f"{tree.repo}: could not be read ({err_msg})",',
        '                "protected": True,\n'
        '                "detail": f"{tree.repo}: could not be read ({err_msg})",',
    ),
    # The second half of the pair above: the same break, checked by the suite
    # that watches the guardrail rather than the one that watches the box.
    "an-unreadable-guardrail-reads-as-an-answer": (CYCLE, GUARDRAIL_SUITE,
        "    if done.returncode != 0:", "    if False:",
    ),
    # A required rule can go missing and the chip stays green - the branch the
    # executed paths are deployed from stops needing a review and nothing on
    # the page says so.
    "a-missing-rule-is-still-protected": (CYCLE, GUARDRAIL_SUITE,
        "        if missing:",
        "        if False:",
    ),
    # An executed path ahead of the protected ref is green: the deployed tree
    # holds code nobody reviewed and the chip asserts that it cannot.
    "an-unreviewed-path-is-still-protected": (CYCLE, GUARDRAIL_SUITE,
        "    elif unreviewed:",
        "    elif False:",
    ),
    # A comparison that never ran reads as one that found nothing, which is
    # the difference between `UNREVIEWED=` and no line at all.
    "an-unrun-comparison-reads-as-a-clean-one": (CYCLE, GUARDRAIL_SUITE,
        "    if unreviewed is None:",
        "    if False:",
    ),
    # The command stops looking for files that are in the tree but in no
    # commit, so a script dropped in by hand runs with the chip green.
    "untracked-files-are-not-compared": (PROTECTION, PROTECTION_SUITE,
        '            git -C "${tree}" ls-files --others --exclude-standard'
        ' -- "${paths[@]}"\n',
        "",
    ),
    # Commits the checkout is carrying stop being compared at all, so a branch
    # left checked out in the shared tree runs with the chip green.
    "unmerged-commits-are-not-compared": (PROTECTION, PROTECTION_SUITE,
        '            git -C "${tree}" diff --name-only "${base}...HEAD"'
        ' -- "${paths[@]}"\n',
        "",
    ),
    # Two-dot instead of three-dot: every path where the protected ref has
    # moved on and this checkout has not pulled is reported unreviewed, so the
    # chip is red for being stale and nobody reads it.
    "stale-reads-as-unreviewed": (PROTECTION, PROTECTION_SUITE,
        '"${base}...HEAD" -- "${paths[@]}"',
        '"${base}" -- "${paths[@]}"',
    ),
    # A reading old enough that the cycle behind it may never have run again
    # still stands for now, so a dead Selector keeps asserting protection it
    # has not checked - the failure the timer cell guards against, one panel
    # along.
    "an-old-reading-still-stands-for-now": (WINDOW, WINDOW_SUITE,
        '        stale=card["age"] > GUARDRAIL_MAX_AGE,',
        "        stale=False,",
    ),
    # The chip renders the protected branch whatever the verdict was.
    "the-chip-is-green-regardless": (LIVE_REGION, WINDOW_SUITE,
        "        {% elif guardrail.protected %}",
        "        {% elif True %}",
    ),
    # `checks` demands a number again, which is the guard that broke the first
    # unattended dispatch: the Run works, and the cycle dies reading the
    # checks of the Proposal it just produced.
    "checks-refuses-a-proposal-url": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        "if [[ ${action} == checks ]]; then",
        "if false; then",
    ),
    # A Proposal URL from any repository is accepted, so `gh pr view` - which
    # resolves the repository from the URL and ignores --repo - can be made to
    # answer about somebody else's pull request as though it were this Run's.
    "a-foreign-proposal-answers-for-this-one": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        'if [[ ${action} == checks ]]; then\n'
        '    if [[ ${number} =~ ^https://github\\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/[1-9][0-9]*$ ]]; then\n'
        '        [[ ${BASH_REMATCH[1]} == "${task_repo}" ]] ||\n'
        '            die "the proposal ${number} is not in ${task_repo}"\n',
        'if [[ ${action} == checks ]]; then\n'
        '    if [[ ${number} =~ ^https://github\\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/[1-9][0-9]*$ ]]; then\n'
        '        :\n',
    ),
    # "No workflow run ran" becomes "every check passed", which is the false
    # pass a broken workflow file or a disabled Actions produces - routed to
    # review as though CI had vouched for it.
    "no-runs-reads-green": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        '{state: "none", failing: []}',
        '{state: "green", failing: []}',
    ),
    # Red stops being the fallthrough: a conclusion GitHub adds after this was
    # written - or one this script simply does not know - counts as a pass.
    "an-unknown-conclusion-reads-green": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        "select(.conclusion as $c | green | index($c) | not)",
        "select(false)",
    ),
    # A run that has completed with no conclusion yet is graded rather than
    # waited for, which makes a transient the API reports for a moment into a
    # terminal red: `ready-for-human`, with a comment naming a check that did
    # not actually fail.
    "a-conclusion-not-yet-reported-is-red": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        'select(.status != "completed" or .conclusion == null)',
        'select(.status != "completed")',
    ),
    # Only the first page of workflow runs is graded, so a failure on page two
    # of a busy commit ships as green.
    "only-the-first-page-of-runs-counts": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        "[ .[].workflow_runs[] ] as $runs",
        "[ .[0].workflow_runs[] ] as $runs",
    ),
    # The head commit goes into the API path untested. `head_sha=null` is not
    # an error to GitHub, just a query matching nothing - which arrives as
    # "none" and reads as a Proposal whose CI never ran.
    "a-head-commit-that-is-not-a-sha-is-trusted": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        '        [[ ${head_sha} =~ ^[0-9a-f]{40}$ ]] ||\n'
        '            die "gh gave no head commit for the Proposal ${task_repo}: ${number}"\n',
        "",
    ),

    # --- The notifier (#280) ------------------------------------------------

    # A Run cut short by its Contract is mailed as though it had finished
    # cleanly, so the one message the operator gets about a failed Run says it
    # succeeded - and the Journal and the mail disagree about one Run.
    "a-failed-run-is-mailed-as-green": (NOTICES, NOTICES_SUITE,
        "if is_failure(ended_by, proposal):",
        "if False:",
    ),
    # The credential warning stops being about a credential and becomes about
    # a row: the box is observed every thirty minutes, so the same expiry
    # mails on every cycle until somebody renews it or mutes the channel.
    "the-credential-warning-is-not-deduplicated": (NOTICES, NOTICES_SUITE,
        "dedupe_key=_credential_key(expires_at),",
        "dedupe_key=None,",
    ),
    # The key goes back to being the box's SPELLING of the expiry rather than
    # the instant (#304), so an adapter that reports `+01:00` where it used to
    # report `Z` - the same credential, said differently - is a new key, and
    # the warning that was already sent is sent again.
    "the-credential-key-is-a-spelling-not-an-instant": (NOTICES, NOTICES_SUITE,
        "    utc = expires_at.astimezone(timezone.utc)",
        "    utc = expires_at",
    ),
    # An expiry hours away alarms as though it were minutes away, which is the
    # same channel-muting failure reached from the other side.
    "every-credential-is-close-to-expiry": (NOTICES, NOTICES_SUITE,
        "if hours > config.credential_warn_hours:",
        "if False:",
    ),
    # The age floor goes, so a notifier that was down for a week empties its
    # whole backlog into the operator's inbox on its next start.
    "stale-rows-are-mailed": (NOTICES, NOTICES_SUITE,
        "    return at is not None and _hours_between(at, now) > config.max_age_hours",
        "    return False",
    ),
    # The name a Run's result is CALLED stops being derived, so a Run that
    # reached its cap and proposed nothing is mailed as "cut short by
    # iteration-cap" - the opposite of what happened, and a different story
    # from the one the issue's own row tells.
    "the-run-s-result-is-not-renamed": (NOTICES, NOTICES_SUITE,
        "    ended_by = outcome_name(raw, proposal)",
        "    ended_by = raw",
    ),
    # One credential stops collapsing to one line, so a backlog holding a day
    # of box observations lists the same warning thirty times - the noise the
    # floor exists to prevent, one level down.
    "the-summary-lists-one-thing-many-times": (NOTIFIER, NOTIFIER_SUITE,
        "            notice.dedupe_key and row_seen[\"key\"] == notice.dedupe_key",
        "            False",
    ),
    # The backlog past the floor is dropped instead of summarised, which turns
    # a reported failure back into the silence #280 exists to end.
    "the-deferred-backlog-is-dropped": (NOTIFIER, NOTIFIER_SUITE,
        "        if deferred is not None and not any(",
        "        if False and not any(",
    ),
    # The first start replays the Journal instead of covering what happens
    # next: every Run there has ever been, mailed at once, on install day.
    "a-first-start-replays-everything": (NOTIFIER, NOTIFIER_SUITE,
        "if start is None:\n"
        "                # The first start. Cover what happens next, not what already\n"
        "                # happened - see the module docstring.\n"
        "                if not dry_run:\n"
        "                    set_cursor(conn, newest)",
        "if False:\n"
        "                if not dry_run:\n"
        "                    set_cursor(conn, newest)",
    ),
    # The daemon stops watching the moment it starts: it sets its cursor and
    # exits 0, which is what the live unit did on 2026-08-31 and what
    # Restart=always covered up for ten seconds.
    "the-first-start-ends-the-daemon": (NOTIFIER, NOTIFIER_SUITE,
        "                if once:\n"
        "                    return sent\n"
        "                start = target = newest\n"
        "                continue",
        "                return sent",
    ),
    # Delivery stops being deduplicated at all, so the credential key is
    # written and never read.
    "the-once-only-record-is-not-read": (NOTIFIER, NOTIFIER_SUITE,
        "            else already_sent(conn, notice.dedupe_key)",
        "            else False",
    ),
    # A dry run writes the cursor, so reading what the Journal would have said
    # silently consumes it and the real notices are never sent.
    "a-dry-run-consumes-the-backlog": (NOTIFIER, NOTIFIER_SUITE,
        "        if not dry_run:\n"
        '            set_cursor(conn, row["id"])',
        "        if True:\n"
        '            set_cursor(conn, row["id"])',
    ),
    # The mail command's exit code is ignored, so a relay that refused is
    # recorded as delivered and the notice is lost.
    "a-refused-delivery-counts-as-sent": (NOTIFIER, NOTIFIER_SUITE,
        "if completed.returncode != 0:",
        "if False:",
    ),
    # The mail surface gets a default back, so an instance that configured no
    # notify command starts anyway, runs a script that is not there, and
    # advances its cursor past every notice it was supposed to deliver.
    "an-unconfigured-mail-surface-gets-a-default": (NOTIFIER, NOTIFIER_SUITE,
        'or targets.missing(\n'
        '                    "SELECTOR_NOTIFY_COMMAND", "the mail surface"\n'
        "                )",
        'or "notify-sources/mail.sh"',
    ),
    # --- The Journal Event vocabulary --------------------------------------
    # A legacy row's spelling stops being normalized, so a pre-vocabulary
    # dispatch failure reads back with no ended_by and the notifier calls it
    # a Run without a Proposal - the exact misread the reader exists to end.
    "legacy-outcome-spelling-unread": (EVENTS, EVENTS_SUITE,
        'ended_by=payload.get("ended_by") or payload.get("outcome"),',
        'ended_by=payload.get("ended_by"),',
    ),
    # The route kinds stop deriving from the route names, so the writer and
    # every reader of `issue.*` rows silently disagree about the kind.
    "route-kind-derivation-broken": (EVENTS, EVENTS_SUITE,
        'return f"issue.{name}"',
        'return f"issues.{name}"',
    ),
    # A constructor misspells its own key, which is the writer-side drift the
    # whole vocabulary exists to make impossible.
    "a-constructor-key-misspelled": (EVENTS, EVENTS_SUITE,
        '        "ended_by": ended_by, "exit": exit, "iterations": iterations,',
        '        "endedby": ended_by, "exit": exit, "iterations": iterations,',
    ),
    # A shipping module spells a kind by hand again - behavior identical, and
    # only the sweep can notice, which is what the sweep is for.
    "a-kind-spelled-outside-the-vocabulary": (CYCLE, EVENTS_SUITE,
        "            journal.append(conn, *events.cycle_failed(cycle=cycle_id, error=str(exc)))\n            raise\n",
        '            journal.append(conn, "cycle.failed", {"cycle": cycle_id, "error": str(exc)})\n            raise\n',
    ),
    # The staging fixture drifts from the writer - a key today's writer always
    # journals goes missing from a seeded row, the state a preview would
    # silently stop rendering.
    "the-fixture-drifts-from-the-writer": (SEED, SEED_SUITE,
        "'seed', 'seeded', 'criteria', '3'))",
        "'seed', 'seeded'))",
    ),
    # The none state folds back into red on the card, contradicting the
    # comment the Selector posted on the issue - the defect this branch fixed.
    "the-none-state-renders-as-red": (RUNS_REGION, WINDOW_SUITE,
        '{% elif r.checks == "none" %}',
        "{% elif false %}",
    ),
    # A route's badge loses its stylesheet rule, which no Python suite can
    # see - the pin over loop.css is the guard, and this is its mutation.
    "a-route-badge-loses-its-stylesheet": (LOOP_CSS, WINDOW_SUITE,
        ".run .badge-retrying {",
        ".run .badge-retried {",
    ),
    # The decision module drags the dispatcher back in, and the purity its
    # docstring claims - drivable with no database - quietly stops being true.
    "notices-drags-the-dispatcher-back-in": (NOTICES, NOTICES_SUITE,
        "import events\nimport targets\nfrom events import",
        "import cycle  # noqa: F401\nimport events\nimport targets\nfrom events import",
    ),
    # The harness stops keeping its books, so a database it failed to drop is
    # gone from the run's memory the moment the drop raises - which is the
    # state #178 was reported in: residue on the box and nothing that knew.
    "leak-not-tracked": (TESTDB, TESTDB_SUITE, "    _created.add(name)\n", ""),
    # A failed drop is swallowed, so the name is discarded as though the drop
    # had worked. Silence in exactly the case worth hearing about.
    "leak-survives-a-failed-drop": (TESTDB, TESTDB_SUITE,
        "        _drop(name)\n",
        "        try:\n            _drop(name)\n        except Exception:\n            pass\n",
    ),
    # The sweep's dry run drops for real - an operator asking what WOULD go,
    # and being answered by it going.
    "dry-sweep-drops": (TESTDB, TESTDB_SUITE, "            if dry_run:", "            if False:"),
    # Nothing leaked, says the report, whatever the run actually left.
    "leak-report-silent": (TESTDB, TESTDB_SUITE, "    names = leaked()", "    names = []"),
    # The keeper connection closes as soon as the schema is applied, which is
    # what the harness used to do. A live throwaway database then has nothing
    # attached to it between statements, is indistinguishable from residue,
    # and the sweep takes a concurrent run's database out from under it.
    "sweep-takes-a-live-run": (TESTDB, TESTDB_SUITE,
        "        keeper.execute(SCHEMA.read_text())\n",
        "        keeper.execute(SCHEMA.read_text())\n        keeper.close()\n",
    ),

    # --- The instance is configured, never coded (issue #3) ---------------
    #
    # A value absent from the stanza stops being a refusal, so the cycle runs
    # against a target that is missing its checkout, its token or its image -
    # and the first sign is a Run that cannot push.
    "required-target-value-defaulted": (TARGETS, TARGETS_SUITE,
        "            if not stanza.get(key):",
        "            if False:",
    ),
    # A key nobody reads is accepted silently: `review-cap` spelled with a
    # hyphen leaves the default in force and nothing says so.
    "misspelled-target-key-ignored": (TARGETS, TARGETS_SUITE,
        "        if unknown:",
        "        if False:",
    ),
    # A landing mode nothing implements is accepted, so an instance believes
    # its Runs are merging themselves while they are opening Proposals.
    "unimplemented-landing-accepted": (TARGETS, TARGETS_SUITE,
        "        if landing not in LANDING_MODES:",
        "        if False:",
    ),
    # A review cap of zero - "never dispatch" - is accepted as a cap rather
    # than refused as a pause nobody journaled a reason for.
    "review-cap-not-a-number": (TARGETS, TARGETS_SUITE,
        "        if review_cap < 1:",
        "        if False:",
    ),
    # Two stanzas for one repository are accepted: two review caps and two
    # work checkouts for one queue.
    "duplicate-target-accepted": (TARGETS, TARGETS_SUITE,
        "    if duplicated:",
        "    if False:",
    ),
    # The per-target values stop reaching the commands, so every target's Run
    # works whatever checkout and token the box happened to be left with.
    "target-values-do-not-reach-the-box": (TARGETS, DISPATCH_SUITE,
        '            "SELECTOR_BOX_REPO": self.box_repo,',
        "",
    ),
    # The target's repository token stops crossing the hop, so a Run pushes
    # with whatever credential the box was last configured with - which on a
    # box serving two targets is the other target's.
    "token-file-not-carried": (BOX_SOURCE, BOX_SOURCE_SUITE,
        'exports=""\nif [[ -n ${LOOP_GITHUB_TOKEN_FILE:-} ]]; then\n    exports+="$(printf \'export LOOP_GITHUB_TOKEN_FILE=%q\' "${LOOP_GITHUB_TOKEN_FILE}")"$\'\\n\'\nfi\n',
        'exports=""\n',
    ),
    # The box's address stops being required, so an instance that configured
    # none reaches whatever `ssh` makes of an empty host.
    "box-host-not-required": (BOX_SOURCE, BOX_SOURCE_SUITE,
        "require SELECTOR_BOX_HOST\n", "",
    ),
    # The status read stops reporting the target's image, so the box card
    # claims every target's Iterations are built inside the box's default.
    "status-read-ignores-the-targets-image": (FACTS_SOURCE, BOX_SOURCE_SUITE,
        '"${LOOP_GUEST_TEMPLATE:+$(printf \'export LOOP_GUEST_TEMPLATE=%q\\n\' "${LOOP_GUEST_TEMPLATE}")}")"',
        '"")"',
    ),
    # The watcher stops being handed the target's checkout, so the real
    # progress.sh has nothing telling it which Progress Log to read and
    # every poll of every real Run journals `run.watch-failed` beside a
    # dispatch that worked. Found by review, 2026-09-04.
    "the-watcher-loses-the-targets-checkout": (WATCHER, WATCHER_SUITE,
        '                env=targets.overlaid(self.config.command_env),',
        "",
    ),
    # The per-target exports go back to carrying their newline inside a
    # command substitution, which strips it: the exports and the `exec`
    # run together on one line and the dispatch starts nothing. Found by
    # review, 2026-09-04.
    "the-exports-run-into-the-exec": (BOX_SOURCE, BOX_SOURCE_SUITE,
        '    exports+="$(printf \'export LOOP_GITHUB_TOKEN_FILE=%q\' "${LOOP_GITHUB_TOKEN_FILE}")"$\'\\n\'',
        '    exports+="$(printf \'export LOOP_GITHUB_TOKEN_FILE=%q\\n\' "${LOOP_GITHUB_TOKEN_FILE}")"',
    ),
    # The one variable that cannot live in a file grows a default, so an
    # instance that configured nothing works whatever targets happen to be at
    # a conventional path - which is the whole failure this ticket is about,
    # reached from underneath the stanza guards above.
    "targets-file-has-a-default": (TARGETS, TARGETS_SUITE,
        "        os.environ.get(TARGETS_FILE_VAR)\n        or missing(",
        "        os.environ.get(TARGETS_FILE_VAR)\n"
        '        or "/etc/tracewake/targets.toml"\n        or missing(',
    ),
    # Remove drops the first stanza instead of the named one, so the
    # window's control reports success and a Cycle still works the repo
    # the operator asked to unenroll.
    "remove-leaves-the-stanza": (TARGETS, TARGETS_SUITE,
        "    remaining = tuple(target for target in current if target.repo != repo)\n",
        "    remaining = current[1:] if current else current\n",
    ),
    # An undeclared name is written through as "removed", so a typo empties
    # nothing and the operator thinks the Target is gone.
    "remove-undeclared-is-silent": (TARGETS, TARGETS_SUITE,
        "    if len(remaining) == len(current):\n",
        "    if False and len(remaining) == len(current):\n",
    ),
    # Unenrolling the last Target deletes the file, so the next load names
    # a missing path instead of an instance with nothing to work.
    "remove-last-deletes-the-file": (TARGETS, TARGETS_SUITE,
        "    _write(where, remaining)\n    return remaining\n",
        "    if remaining:\n"
        "        _write(where, remaining)\n"
        "    else:\n"
        "        where.unlink()\n"
        "    return remaining\n",
    ),
    # The local box's checkout stops being required, so an instance that
    # configured none runs against whatever directory happens to be empty or current.
    "local-box-repo-not-required": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        "require SELECTOR_BOX_REPO\n", "",
    ),
    # The target's repository token is not passed to the credential inventory,
    # so assert-credentials.sh checks whatever token default is on the box.
    "local-token-file-not-passed-to-inventory": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        'if [[ -n "${LOOP_GITHUB_TOKEN_FILE:-}" ]]; then\n    assert_args+=(--token-file "${LOOP_GITHUB_TOKEN_FILE}")\nfi\n',
        "",
    ),
    # An empty repository token is not unset, so an empty value is exported to
    # the Run environment overriding the credential helper.
    "local-empty-token-file-not-unset": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        "else\n    unset LOOP_GITHUB_TOKEN_FILE || true\n",
        "else\n    true\n",
    ),
    # The local dispatch skips the credential inventory check, allowing an
    # operator machine holding production credentials to run un-gated.
    "local-dispatch-skips-credential-inventory": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        'if (( cred_status == 2 )); then\n'
        '    printf \'box-sources/local.sh: credential inventory reported violations:\\n%s\\n\' "${cred_output}" >&2\n'
        '    exit 2\n'
        'elif (( cred_status != 0 )); then\n'
        '    printf \'box-sources/local.sh: assert-credentials.sh failed to run (exit %d):\\n%s\\n\' "${cred_status}" "${cred_output}" >&2\n'
        '    exit 1\n'
        'fi\n',
        "",
    ),
    # The local dispatch ignores credential violations reported by assert-credentials.sh
    # and proceeds anyway.
    "local-dispatch-ignores-credential-violations": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        "if (( cred_status == 2 )); then\n"
        '    printf \'box-sources/local.sh: credential inventory reported violations:\\n%s\\n\' "${cred_output}" >&2\n'
        "    exit 2\n",
        "if (( cred_status == 2 )); then\n"
        '    printf \'box-sources/local.sh: credential inventory reported violations:\\n%s\\n\' "${cred_output}" >&2\n'
        "    true\n",
    ),
    # The local dispatch treats assert-credentials.sh failure to run as violations (exiting 2)
    # rather than failure to run (exiting 1).
    "local-dispatch-conflates-check-failure-with-violations": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        "elif (( cred_status != 0 )); then\n"
        '    printf \'box-sources/local.sh: assert-credentials.sh failed to run (exit %d):\\n%s\\n\' "${cred_status}" "${cred_output}" >&2\n'
        "    exit 1\n",
        "elif (( cred_status != 0 )); then\n"
        '    printf \'box-sources/local.sh: assert-credentials.sh failed to run (exit %d):\\n%s\\n\' "${cred_status}" "${cred_output}" >&2\n'
        "    exit 2\n",
    ),
    # An assert-credentials.sh that is not executable is permitted to pass preflight check.
    "local-assert-script-executable-not-enforced": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        '[[ -x "${assert_script}" ]] || die "assert-credentials.sh not executable or not found at ${assert_script}"\n',
        '[[ -f "${assert_script}" ]] || die "assert-credentials.sh not executable or not found at ${assert_script}"\n',
    ),
    # Freshness: open proposals behind their base are not updated during a drain.
    "proposal-behind-not-updated": (CYCLE, UNATTENDED_SUITE,
        "        if not dry_run:\n"
        "            update_proposals_freshness(\n"
        "                conn,\n"
        "                cycle_id,\n"
        "                config,\n"
        "                dispatch_config,\n"
        "                queue + (review or []),\n"
        "                updated=updated_proposals,\n"
        "                failed=failed_proposals,\n"
        "            )\n",
        "        pass\n",
    ),
    # Freshness: conflicting proposals are erroneously updated.
    "conflicting-proposal-updated": (CYCLE, UNATTENDED_SUITE,
        "    if is_conflicting(proposal):\n        return False\n",
        "    if False:\n        return False\n",
    ),
    # Freshness: forge refusal of proposal update halts drain instead of journaling and continuing.
    "refused-proposal-update-halts-drain": (CYCLE, UNATTENDED_SUITE,
        "            except dispatch.DispatchFailed as exc:\n"
        "                journal.append(\n"
        "                    conn,\n"
        "                    *events.proposal_update_failed(\n",
        "            except ValueError as exc:\n"
        "                journal.append(\n"
        "                    conn,\n"
        "                    *events.proposal_update_failed(\n",
    ),
    # Freshness: conflicting proposals are hidden rather than marked on the board.
    "proposal-conflicts-hidden-on-board": (BOARD, BOARD_SUITE,
        "    is_conflict = (\n"
        "        _has_conflicting_proposal(record) if conflicting is None else conflicting\n"
        "    )\n",
        "    is_conflict = False\n",
    ),
    # Freshness: update-branch verb in issue source is disabled.
    "update-branch-verb-unsupported": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        '    update-branch)\n'
        '        gh pr update-branch "${number}" --repo "${task_repo}" >/dev/null ||\n'
        '            die "GitHub refused update-branch on ${task_repo} proposal ${number}"\n'
        '        ;;\n',
        '    update-branch)\n'
        '        die "update-branch is unsupported"\n'
        '        ;;\n',
    ),
    # Reconcile (#34): the drain never dispatches one, so conflicting
    # Proposals rot under their badge forever.
    "reconcile-pass-skipped": (CYCLE, RECONCILE_SUITE,
        "            reconcile_conflicting_proposals(\n"
        "                conn,\n"
        "                cycle_id,\n"
        "                config,\n"
        "                dispatch_config,\n"
        "                queue,\n"
        "                review or [],\n"
        "                reconciled=reconciled_proposals,\n"
        "                failed=reconcile_failed_proposals,\n"
        "            )\n",
        "            pass\n",
    ),
    # Reconcile (#34): a failed reconcile leaves the issue where it was,
    # so the next cycle dispatches another Run at the same conflicts.
    "reconcile-failure-not-escalated": (CYCLE, RECONCILE_SUITE,
        "            add=config.human_label,\n",
        "            add=config.label,\n",
    ),
    # Reconcile (#34): a paused Selector starts reconcile Runs anyway,
    # spending agent Runs the pause flag exists to suspend.
    "paused-reconcile-starts-anyway": (CYCLE, RECONCILE_SUITE,
        "    if control.is_paused(conn):\n        return\n",
        "    pass\n",
    ),
    # Reconcile (#34): the owning issue's Check never reaches the box, so
    # the merged branch is pushed unverified.
    "reconcile-check-never-passed": (DISPATCH, RECONCILE_SUITE,
        "    argv = [config.box_command, \"reconcile\", str(proposal)]\n"
        "    if check:\n"
        "        argv += [\"--check\", check]\n",
        "    argv = [config.box_command, \"reconcile\", str(proposal)]\n",
    ),
    # Reconcile (#34): success is journaled as an ordinary fast-forward, so
    # the Journal cannot tell which path brought the Proposal current.
    "reconciled-kind-is-updated": (EVENTS, EVENTS_SUITE,
        'PROPOSAL_RECONCILED = "proposal.reconciled"',
        'PROPOSAL_RECONCILED = "proposal.updated"',
    ),
    # Reconcile (#34): the local box surface drops the Check on the floor
    # instead of handing it to loop/reconcile.sh.
    "local-reconcile-drops-the-check": (LOCAL_SOURCE, BOX_SOURCE_SUITE,
        "    reconcile_args=(--repo \"${box_repo}\" --proposal \"${proposal}\")\n"
        "    if [[ -n ${check} ]]; then\n"
        "        reconcile_args+=(--check \"${check}\")\n"
        "    fi\n",
        "    reconcile_args=(--repo \"${box_repo}\" --proposal \"${proposal}\")\n",
    ),
    # Unenrolled-Target warning (#39): the Cycle stops looking, so a Handover
    # on a repository with no stanza vanishes silently again.
    "unenrolled-search-skipped": (CYCLE, CYCLE_SUITE,
        "                observe_unenrolled(\n"
        "                    conn,\n"
        "                    targets.Instance.from_env(),\n"
        "                    targets.load(),\n"
        "                    dry_run=args.dry_run,\n"
        "                )\n",
        "                pass\n",
    ),
    # Declared Targets are flagged as unenrolled, so the warning cries wolf
    # about the repositories the instance already works.
    "declared-targets-flagged-as-unenrolled": (CYCLE, CYCLE_SUITE,
        "        if not repo or repo in declared:\n            continue\n",
        "        if not repo:\n            continue\n",
    ),
    # A standing gap is marked new every Cycle, so the operator is mailed
    # every thirty minutes about a repository they already know about.
    "standing-gap-always-new": (CYCLE, CYCLE_SUITE,
        '    new = [entry["repo"] for entry in gap if entry["repo"] not in seen]\n',
        '    new = [entry["repo"] for entry in gap]\n',
    ),
    # A gap that goes away leaves no row, so a later reappearance matches the
    # last non-empty set and never notifies.
    "cleared-gap-not-recorded": (CYCLE, CYCLE_SUITE,
        "        if not seen:\n            return\n",
        "        if True:\n            return\n",
    ),
    # The searched owner gets a default, which is a company fact in the
    # product - the refusal issue #3 and #39 both require.
    "search-owner-not-refused-when-missing": (TARGETS, TARGETS_SUITE,
        '        if not os.environ.get("SELECTOR_SEARCH_OWNER"):\n'
        "            missing(\n"
        '                "SELECTOR_SEARCH_OWNER",\n'
        '                REQUIRED_INSTANCE_VARS["SELECTOR_SEARCH_OWNER"],\n'
        "            )\n",
        "        if False:\n"
        "            missing(\n"
        '                "SELECTOR_SEARCH_OWNER",\n'
        '                REQUIRED_INSTANCE_VARS["SELECTOR_SEARCH_OWNER"],\n'
        "            )\n",
    ),
    # A standing gap is mailed every time, which is the noise the Journal-
    # keyed dedup exists to stop.
    "standing-unenrolled-gap-is-mailed": (NOTICES, NOTICES_SUITE,
        "    new = [name for name in (record.new or []) if name]\n"
        "    if not new:\n"
        "        return None\n",
        "    new = [name for name in (record.new or []) if name]\n"
        "    if False and not new:\n"
        "        return None\n",
    ),
    # The owner-wide search writes. A comment from this path is the Selector
    # mutating a repository it has not been enrolled to work.
    "owner-search-writes-a-comment": (SEARCH_SOURCE, SEARCH_SUITE,
        'hits="$(gh search issues \\\n',
        'gh issue comment 1 --repo "${owner}/widgets" --body warn >/dev/null 2>&1 || true\n'
        'hits="$(gh search issues \\\n',
    ),
    # A repository slug is accepted as an owner, so the search is pointed at
    # one repo and looks like a successful empty gap.
    "owner-search-accepts-a-repo-slug": (SEARCH_SOURCE, SEARCH_SUITE,
        '[[ ${owner} =~ ^[A-Za-z0-9._-]+$ ]] ||\n'
        '    die "owner must be a GitHub user or organization, got ${owner}"\n',
        "true ||\n"
        '    die "owner must be a GitHub user or organization, got ${owner}"\n',
    ),
    # Freshness: foreign proposal URL in update-branch is accepted.
    "update-branch-foreign-proposal-answers-for-this-one": (ISSUE_SOURCE, ISSUE_SOURCE_SUITE,
        'elif [[ ${action} == update-branch ]]; then\n'
        '    if [[ ${number} =~ ^https://github\\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/[1-9][0-9]*$ ]]; then\n'
        '        [[ ${BASH_REMATCH[1]} == "${task_repo}" ]] ||\n'
        '            die "the proposal ${number} is not in ${task_repo}"\n',
        'elif [[ ${action} == update-branch ]]; then\n'
        '    if [[ ${number} =~ ^https://github\\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/[1-9][0-9]*$ ]]; then\n'
        '        :\n',
    ),
    # Sign-in (issue #38). An unauthenticated visitor is served the page.
    "unauthenticated-pages-are-served": (AUTH, AUTH_SUITE,
        "        if session is None or session.account is None:\n",
        "        if False:\n",
    ),
    # CSRF comparison always succeeds, so a POST without the token is accepted.
    "csrf-not-checked": (AUTH, AUTH_SUITE,
        "    session = getattr(request.state, \"session\", None)\n"
        "    if session is None or not offered:\n"
        "        return False\n"
        "    return secrets.compare_digest(offered, session.csrf_token)",
        "    return True",
    ),
    # The session cookie is set without Secure, so it rides plain HTTP.
    "session-cookie-not-secure": (AUTH, AUTH_SUITE,
        "    response.set_cookie(\n"
        "        key=cookie_name(),\n"
        "        value=token,\n"
        "        max_age=12 * 60 * 60,\n"
        "        path=\"/\",\n"
        "        secure=cookie_secure(),\n",
        "    response.set_cookie(\n"
        "        key=cookie_name(),\n"
        "        value=token,\n"
        "        max_age=12 * 60 * 60,\n"
        "        path=\"/\",\n"
        "        secure=False,\n",
    ),
    # Idle expiry is dropped from the read, so a stale session still opens a page.
    "idle-expiry-not-enforced": (AUTH, AUTH_SUITE,
        "                \"   AND s.created_at > now() - %s::interval\"\n"
        "                \"   AND s.last_seen_at > now() - %s::interval\",\n"
        "                (digest, ABSOLUTE, IDLE),\n",
        "                \"   AND s.created_at > now() - %s::interval\",\n"
        "                (digest, ABSOLUTE),\n",
    ),
    # Seeding an existing email succeeds instead of failing loudly.
    "seed-admin-overwrites": (AUTH, AUTH_SUITE,
        "    except psycopg.errors.UniqueViolation as exc:\n"
        "        raise AccountExists(email) from exc\n",
        "    except psycopg.errors.UniqueViolation as exc:\n"
        "        return 0\n",
    ),
    # Invite (issue #41). The raw token is stored, so a leaked table is the
    # invite itself.
    "invite-tokens-stored-plaintext": (AUTH, INVITE_SUITE,
        "            (hash_token(raw), account_id, INVITE_TTL),\n",
        "            (raw, account_id, INVITE_TTL),\n",
    ),
    # The mail command is not invoked, so the invitee never receives the link.
    "invite-skips-the-mail": (MAIL, INVITE_SUITE,
        '    """Hand one message to the mail command. Raises on anything but success."""\n'
        "    timeout = int(os.environ.get(\"WINDOW_MAIL_TIMEOUT_SECONDS\") or 120)\n",
        '    """Hand one message to the mail command. Raises on anything but success."""\n'
        "    return\n"
        "    timeout = int(os.environ.get(\"WINDOW_MAIL_TIMEOUT_SECONDS\") or 120)\n",
    ),
    # The accounts router drops its admin dependency, so a reader reaches it.
    "reader-reaches-accounts": (WINDOW, INVITE_SUITE,
        "admin_pages = APIRouter(dependencies=[Depends(auth.require_admin)])\n",
        "admin_pages = APIRouter()\n",
    ),
    # Deactivation leaves sessions live, so the cookie still opens a page.
    "deactivate-leaves-sessions": (AUTH, INVITE_SUITE,
        "        conn.execute(\n"
        "            \"DELETE FROM web.sessions WHERE account_id = %s\",\n"
        "            (account_id,),\n"
        "        )\n",
        "",
    ),
    # A used invite can be redeemed again.
    "invite-tokens-reusable": (AUTH, INVITE_SUITE,
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'invite'\"\n"
        "            \"   AND used_at IS NULL AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'invite'\"\n"
        "            \"   AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
    ),
    # An expired invite still sets a password.
    "invite-expiry-ignored": (AUTH, INVITE_SUITE,
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'invite'\"\n"
        "            \"   AND used_at IS NULL AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'invite'\"\n"
        "            \"   AND used_at IS NULL\"\n"
        "            \" RETURNING account_id\",\n",
    ),
    # A never-activated account can sign in without redeeming.
    "unactivated-can-sign-in": (AUTH, INVITE_SUITE,
        "    if hashed is None:\n"
        "        _dummy_verify(password)\n"
        "        return None\n",
        "    if hashed is None:\n"
        "        return Account(id=account_id, email=stored_email, role=role)\n",
    ),
    # An unset window mail command is filled in, so an invite sends with
    # nobody configured.
    "unconfigured-window-mail-gets-a-default": (MAIL, INVITE_SUITE,
        "    return os.environ.get(\"WINDOW_MAIL_COMMAND\") or targets.missing(\n"
        "        \"WINDOW_MAIL_COMMAND\", \"the window's mail surface\"\n"
        "    )\n",
        "    return os.environ.get(\"WINDOW_MAIL_COMMAND\") or \"/bin/true\"\n",
    ),
    # Deactivation leaves unused invite tokens live, so the link still works.
    "deactivate-leaves-invite-tokens": (AUTH, INVITE_SUITE,
        "        conn.execute(\n"
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE account_id = %s AND used_at IS NULL\",\n"
        "            (account_id,),\n"
        "        )\n",
        "",
    ),
    # Roles (issue #40). A reader may POST pause/resume, so the gate is layout.
    "reader-may-use-controls": (AUTH, ROLES_SUITE,
        "        if account is None or account.role != self.role:\n"
        "            raise NotAuthorised()\n",
        "        if False:\n"
        "            raise NotAuthorised()\n",
    ),
    # An unauthenticated POST to a control 303s to sign-in instead of refusing.
    "unauthenticated-control-redirects": (AUTH, ROLES_SUITE,
        "            if scope.get(\"method\", \"GET\") not in (\"GET\", \"HEAD\"):\n"
        "                response = refuse()\n"
        "                await response(scope, receive, send)\n"
        "                return\n",
        "            if False:\n"
        "                response = refuse()\n"
        "                await response(scope, receive, send)\n"
        "                return\n",
    ),
    # The board always renders pause/resume, so a reader sees the controls.
    "reader-sees-the-pause-control": (WINDOW, ROLES_SUITE,
        '"can_control": account is not None and account.role == "admin",\n',
        '"can_control": True,\n',
    ),

    # --- The Host on the Queue Board (#42) ---------------------------------
    #
    # The widget is the operator's headroom reading. Every mutation here
    # leaves a board that still draws while quietly ceasing to be that.

    # A /proc read that failed takes the queue with it, so the page the
    # operator opened to see the queue is a 500 instead.
    "a-sampler-failure-takes-the-board-down": (WINDOW, HOST_SUITE,
        "    try:\n"
        "        facts = host.sample()\n"
        "    except Exception as exc:\n"
        "        return {**unknown, \"error\": str(exc)}\n",
        "    facts = host.sample()\n",
    ),
    # The count is a constant, so a Run the Journal has in flight reads as
    # none and the host looks idle.
    "in-flight-count-ignores-the-journal": (WINDOW, HOST_SUITE,
        "    in_flight = None if spend is None else spend.runs_in_flight()\n",
        "    in_flight = 0\n",
    ),
    # Ended and stale dispatches still count, so the figure is not Eligibility's
    # in-flight predicate - it is every dispatch the Journal has ever written.
    "in-flight-count-counts-ended-runs": (WINDOW, HOST_SUITE,
        "    in_flight = None if spend is None else spend.runs_in_flight()\n",
        "    in_flight = None if spend is None else len(spend._dispatches)\n",
    ),
    # Unique issue numbers collapse two Targets sharing a number into one Run.
    "in-flight-count-collapses-two-targets": (WINDOW, HOST_SUITE,
        "    in_flight = None if spend is None else spend.runs_in_flight()\n",
        "    in_flight = None if spend is None else len(spend.in_flight)\n",
    ),
    # Same collapse, from Spend's own count rather than from the window.
    "spend-count-collapses-two-targets": (CYCLE, HOST_SUITE,
        "        return len(self._in_flight_keys)\n",
        "        return len(self.in_flight)\n",
    ),
    # The widget leaves the live region, so a Journal row landing does not
    # refresh the figures and a reload is required.
    "host-widget-not-in-the-live-region": (LIVE_REGION, HOST_SUITE,
        '{% include "_host.html" %}\n',
        "",
    ),
    # The page invents a CPU figure instead of showing the sampler's.
    "host-figures-are-not-the-sampler-s": (WINDOW, HOST_SUITE,
        '        "cpu": f"{round(facts.cpu_percent)}%",\n',
        '        "cpu": "0%",\n',
    ),
    # The default adapter stops reading the machine, so production is a
    # permanently degraded widget.
    "the-host-sampler-does-not-read-the-machine": (HOST, HOST_SUITE,
        "        cpu = _cpu_percent()\n",
        '        raise SamplerError("no")\n        cpu = _cpu_percent()\n',
    ),
    # The template ignores the sampler's CPU and prints a constant.
    "host-cpu-is-hardcoded-in-the-template": (HOST_WIDGET, HOST_SUITE,
        '      <span class="cell-value">{{ host.cpu }}</span>\n',
        '      <span class="cell-value">0%</span>\n',
    ),

    # --- Forgot password (#43) ---------------------------------------------
    #
    # A leaked table, a reusable link, or a form that talks are the ways
    # this flow stops being a recovery and becomes an oracle or a takeover.

    # The raw token is stored, so a leaked table is the reset itself.
    "reset-tokens-stored-plaintext": (AUTH, RESET_SUITE,
        "            (hash_token(raw), account_id, RESET_TTL),\n",
        "            (raw, account_id, RESET_TTL),\n",
    ),
    # The mail command is not invoked, so the account never receives the link.
    "reset-skips-the-mail": (WINDOW, RESET_SUITE,
        "            mail.send(\n"
        "                to=email.strip().lower(),\n"
        '                subject="Reset your Tracewake window password",\n'
        "                link=link,\n"
        "                body=body,\n"
        "            )\n",
        "            pass\n",
    ),
    # A used reset can be redeemed again.
    "reset-tokens-reusable": (AUTH, RESET_SUITE,
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'reset'\"\n"
        "            \"   AND used_at IS NULL AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'reset'\"\n"
        "            \"   AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
    ),
    # An expired reset still sets a password.
    "reset-expiry-ignored": (AUTH, RESET_SUITE,
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'reset'\"\n"
        "            \"   AND used_at IS NULL AND expires_at > now()\"\n"
        "            \" RETURNING account_id\",\n",
        "            \"UPDATE web.account_tokens SET used_at = now()\"\n"
        "            \" WHERE token_hash = %s AND purpose = 'reset'\"\n"
        "            \"   AND used_at IS NULL\"\n"
        "            \" RETURNING account_id\",\n",
    ),
    # A never-activated account receives a reset, so an unclaimed invite
    # can be hijacked through the public form.
    "never-activated-gets-a-reset": (AUTH, RESET_SUITE,
        "        if hashed is None or deactivated_at is not None:\n"
        "            return None\n",
        "        if deactivated_at is not None:\n"
        "            return None\n",
    ),
    # The form names the miss, so it is an account-exists oracle.
    "reset-response-leaks-unknown": (WINDOW, RESET_SUITE,
        "    response = _page(request, \"forgot.html\", {\n"
        "        \"sent\": True,\n"
        "        \"error\": None,\n"
        "    })\n",
        "    response = _page(request, \"forgot.html\", {\n"
        "        \"sent\": True,\n"
        "        \"error\": None if raw else \"No account with that address.\",\n"
        "    })\n",
    ),
    # A completed reset leaves other sessions live, so a stolen cookie
    # still opens the window.
    "reset-leaves-other-sessions": (AUTH, RESET_SUITE,
        "        conn.execute(\n"
        "            \"DELETE FROM web.sessions\"\n"
        "            \" WHERE account_id = %s\",\n"
        "            (account_id,),\n"
        "        )\n",
        "",
    ),
}


def main(argv):
    if len(argv) == 2 and argv[1] == "--list":
        for name, (target, suite, _, _) in MUTATIONS.items():
            print(f"{name}\t{target}\t{suite}")
        return 0
    if len(argv) != 3:
        print("usage: selector-mutations.py (--list | <name> <file>)", file=sys.stderr)
        return 2
    name, target = argv[1], pathlib.Path(argv[2])
    _, _, old, new = MUTATIONS[name]
    text = target.read_text()
    if text.count(old) != 1:
        print(
            f"selector-mutations.py: {name} matches {text.count(old)} times in "
            f"{target.name}, expected exactly 1 - a mutation that misses is "
            "coverage that does not exist",
            file=sys.stderr,
        )
        return 1
    target.write_text(text.replace(old, new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
