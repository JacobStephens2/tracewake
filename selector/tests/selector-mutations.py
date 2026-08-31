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
WATCHER_SUITE = "tests/test_watcher.py"
BOARD_SUITE = "../../webapp/tests/test_queue_board.py"

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

# The window (#156). Its path is relative to the Selector, and its suite is
# the dashboard's - run from the webapp directory, which mutation-check.sh
# handles by naming both.
WINDOW = "../../webapp/app.py"
WINDOW_SUITE = "../../webapp/tests/test_loop_page.py"

# The push (#159). The stream is Journal SQL and lives with the Journal; the
# region it re-fetches is a template, which is a mutation target like any
# other - a swap that drops its own trigger is one attribute deleted.
JOURNAL = "journal.py"
LIVE_REGION = "../../webapp/templates/_loop_live.html"
LIVENESS_SUITE = "../../webapp/tests/test_liveness.py"

# Run history and the budget (#160). The same window module, a suite of its
# own: history's claim is what it does NOT read, and a mutation checked
# against test_loop_page.py - which drives a page that reads the tracker on
# every request - would be checked by tests that cannot tell the difference.
HISTORY_REGION = "../../webapp/templates/_history_live.html"
HISTORY_SUITE = "../../webapp/tests/test_run_history.py"

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
    "retry-budget-unbounded": (CYCLE, CYCLE_SUITE, "MAX_ATTEMPTS = 2", "MAX_ATTEMPTS = 9999"),
    # The budget stops being scoped to the current Handover, so re-labeling a
    # given-up issue - the operator saying "try that again" - does nothing.
    "budget-ignores-the-handover": (CYCLE, CYCLE_SUITE,
        "if d.issue == issue and (after is None or d.at > after)",
        "if d.issue == issue",
    ),
    # In flight is counted per issue rather than per attempt, so a retry of an
    # issue that already has an outcome does not hold the lock and a second
    # Run is dispatched underneath it.
    "in-flight-counted-per-issue": (CYCLE, CYCLE_SUITE,
        "issue for issue, n in started.items() if n > ended.get(issue, 0)",
        "issue for issue, n in started.items() if issue not in ended",
    ),
    # Concurrency arrives by accident: a second Run is dispatched while one is
    # still in flight.
    "in-flight-cap-ignored": (CYCLE, CYCLE_SUITE, "elif cycle_spend.in_flight:", "elif False:"),
    # The daily cap stops bounding spend and the review pile.
    "daily-cap-ignored": (CYCLE, CYCLE_SUITE,
        "elif cycle_spend.recent_dispatches >= config.daily_cap:",
        "elif False:",
    ),
    # The cap window widens to never, so yesterday's dispatches stop aging out
    # and the Selector wedges itself after four Runs, forever.
    "cap-window-never-expires": (CYCLE, CYCLE_SUITE,
        "CAP_WINDOW_HOURS = 24",
        "CAP_WINDOW_HOURS = 99999",
    ),
    # An underspecified issue is seeded anyway, which is the case ADR 0014
    # says must be returned to the operator rather than guessed at.
    "sections-not-required": (CYCLE, CYCLE_SUITE, "if missing:", "if False:"),
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
    "skips-not-journaled": (CYCLE, CYCLE_SUITE,
        '"issue.skipped",', '"issue.considered",',
    ),
    # The in-flight lock never expires, so one cycle killed between
    # dispatching and recording the outcome wedges every later cycle forever.
    "in-flight-lock-never-expires": (CYCLE, CYCLE_SUITE,
        "IN_FLIGHT_STALE_HOURS = 4",
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
    "in-flight-lock-not-taken": (CYCLE, DISPATCH_SUITE,
        '        "run.dispatched",', '        "run.attempted",',
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
        'return 1 if summary.get("return_failures") else 0', "return 0",
    ),
    # Every dispatch is attempt 1, so the Journal cannot tell a first Run from
    # a retry and #155's give-up has nothing to count.
    "every-dispatch-is-the-first": (CYCLE, DISPATCH_SUITE,
        '        attempt = cycle_spend.attempts(\n'
        '            pick["number"], picked_record.get("labeledAt")\n'
        '        ) + 1',
        "        attempt = 1",
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
        '    if _run(["git", "-C", str(config.work_repo), "rev-parse", "--verify",\n'
        '             "--quiet", remote_branch],\n'
        "            timeout=config.command_timeout_seconds).returncode == 0:",
        "    if False:",
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
    "run-failures-not-detected": (CYCLE, OUTCOMES_SUITE,
        'RUN_FAILURE_BOUNDS = ("run-clock", "consecutive-noops", "agent-failed")',
        "RUN_FAILURE_BOUNDS = ()",
    ),
    # A Run that proposed nothing is treated as one that did, so the Selector
    # reads checks for a Proposal that does not exist.
    "no-proposal-treated-as-a-proposal": (CYCLE, OUTCOMES_SUITE,
        "failure = ended_by in RUN_FAILURE_BOUNDS or not proposal",
        "failure = ended_by in RUN_FAILURE_BOUNDS",
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
    "journaled-label-is-not-the-applied-one": (CYCLE, OUTCOMES_SUITE,
        '    payload = {**payload, "label": route.label}',
        '    payload = {**payload, "label": None}',
    ),
    # Bookkeeping the tracker refused is swallowed, so a Run whose result
    # never reached the issue looks like a cycle that worked (story 31).
    "route-failure-not-paged": (CYCLE, OUTCOMES_SUITE,
        'journal.append(conn, "issue.route-failed", {**payload, "error": str(exc)})\n        raise CycleFailed(str(exc)) from exc',
        'journal.append(conn, "issue.route-failed", {**payload, "error": str(exc)})\n        return route.name',
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
        "    if not dry_run:\n        facts, box_error = observe_box(config)",
        "    if True:\n        facts, box_error = observe_box(config)",
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
        '            card["contract"] = payload.get("contract")',
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
        "    if control.is_paused(conn):",
        "    if False:",
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
        '        if event["kind"] in (good, bad):',
        '        if event["kind"] == good:',
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
        "    (events, older), spend, error = _read_journal(_history_rows, empty)",
        "    return _loop_context(request)\n"
        "    (events, older), spend, error = _read_journal(_history_rows, empty)",
    ),
    # The Run in flight is filed as history: a card with no bound, no duration
    # and no Proposal, shown as though the Run had ended.
    "an-unfinished-run-is-filed-as-history": (WINDOW, HISTORY_SUITE,
        '        "runs": [run for run in _runs(events) if not run.get("in_flight")],',
        '        "runs": _runs(events),',
    ),
    # An unreadable Journal renders as a full budget, so the page promises
    # four Runs remaining on the strength of rows it never read.
    "an-unknown-budget-reads-as-full": (WINDOW, HISTORY_SUITE,
        '        "remaining": None if spent is None else max(0, cap - spent),',
        '        "remaining": cap - (spent or 0),',
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
        "        guardrail, guardrail_error = observe_guardrail(config)",
        "        guardrail, guardrail_error = observe_guardrail(config)\n"
        "    if dry_run:\n"
        "        guardrail, guardrail_error = observe_guardrail(config)",
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
        '        reading["stale"] = reading["age"] > GUARDRAIL_MAX_AGE',
        '        reading["stale"] = False',
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
        '        [[ ${BASH_REMATCH[1]} == "${task_repo}" ]] ||\n'
        '            die "the proposal ${number} is not in ${task_repo}"\n',
        "",
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
        "if ended_by in RUN_FAILURE_BOUNDS or not proposal:",
        "if False:",
    ),
    # The credential warning stops being about a credential and becomes about
    # a row: the box is observed every thirty minutes, so the same expiry
    # mails on every cycle until somebody renews it or mutes the channel.
    "the-credential-warning-is-not-deduplicated": (NOTICES, NOTICES_SUITE,
        'dedupe_key=f"credential:{raw}",',
        "dedupe_key=None,",
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
