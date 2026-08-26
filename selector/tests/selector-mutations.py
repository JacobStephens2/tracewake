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
DISPATCH = "dispatch.py"
CYCLE_SUITE = "tests/test_cycle.py"
DISPATCH_SUITE = "tests/test_dispatch.py"

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
    "in-flight-cap-ignored": (CYCLE, CYCLE_SUITE, "elif spend.in_flight:", "elif False:"),
    # The daily cap stops bounding spend and the review pile.
    "daily-cap-ignored": (CYCLE, CYCLE_SUITE,
        "elif spend.recent_dispatches >= config.daily_cap:",
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
    "return-comment-says-nothing": (CYCLE, DISPATCH_SUITE,
        "            _missing_section_comment(config, detail),", '            "",',
    ),
    # A hand-back GitHub refused exits 0, so the timer never pages and the
    # issue sits in the queue with nothing on it.
    "return-failure-swallowed": (CYCLE, DISPATCH_SUITE,
        'return 1 if summary.get("return_failures") else 0', "return 0",
    ),
    # Every dispatch is attempt 1, so the Journal cannot tell a first Run from
    # a retry and #155's give-up has nothing to count.
    "every-dispatch-is-the-first": (CYCLE, DISPATCH_SUITE,
        '            spend.attempts(pick["number"], picked_record.get("labeledAt")) + 1,',
        "            1,",
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
        '             "--quiet", remote_branch]).returncode == 0:',
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
        '    if completed.returncode != 0:\n'
        '        raise DispatchFailed(\n'
        '            f"Seeding refused #{number}: "',
        "    if False:\n"
        '        raise DispatchFailed(\n'
        '            f"Seeding refused #{number}: "',
    ),
    # The branch stops naming the issue, so two Runs on the same area collide
    # and a branch list stops answering "what is this for?".
    "branch-does-not-name-the-issue": (DISPATCH, DISPATCH_SUITE,
        'return f"{config.branch_prefix}{number}-{slug}" if slug else \\',
        'return f"{config.branch_prefix}{slug}" if slug else \\',
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
