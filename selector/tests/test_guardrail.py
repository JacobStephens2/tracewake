"""The write protection over the executed paths, at the cycle's boundary (#165).

The Selector's own code, and the Loop scripts an apply copies to the box, run
unattended from this repository - so they had better be no easier to change
than the repository a Run makes Proposals against. That is a property of
GitHub's rules and of what is sitting in the deployed tree, and neither is
visible from the Journal unless something reads them and writes them down.

Every assertion here is at the same boundary as the rest of the suite: the
command the cycle issued, and the rows it wrote. The guardrail command itself
is scripted, so what the Selector journaled it can only have learned from what
that command printed - and the verdict, which is the part reviewed Python
decides, is asserted against answers that differ in exactly one way from the
protected one.
"""
from conftest import GUARDRAIL, events, issue, last, one


def _without(key):
    """`GUARDRAIL` minus one line - the answer that is silent about one half."""
    return "".join(
        line for line in GUARDRAIL.splitlines(keepends=True)
        if not line.startswith(f"SELECTOR_GUARDRAIL_{key}=")
    )


# --- What the cycle reads and journals --------------------------------------


def test_a_cycle_journals_the_protection_standing_over_the_executed_paths(db, box):
    result = box.run(db, [issue(645)])

    assert result.returncode == 0, result.stderr
    assert "guardrail " in box.commands()
    row = one(db, "guardrail.observed")
    assert row["ref"] == "master"
    assert row["rules"] == ["deletion", "non_fast_forward", "pull_request"]
    assert row["paths"] == [
        "lab/single-user-factory/loop",
        "lab/single-user-factory/selector",
    ]
    assert row["unreviewed"] == []
    assert row["protected"] is True


def test_a_dry_run_reads_no_guardrail(db, box):
    """A dry run reaches the tracker and nothing else. The guardrail read is a
    `gh` call and a walk of the deployed tree, and that property is worth more
    than a status chip on a cycle that changed nothing."""
    box.run(db, [issue(645)], dry_run=True)

    assert "guardrail " not in box.commands()
    assert events(db, "guardrail.observed") == []


# --- The verdict ------------------------------------------------------------


def test_a_ref_that_does_not_require_review_is_not_protected(db, box):
    """The rule the whole guardrail is about. Without it a push straight to
    the branch the executed paths are deployed from is accepted, and the
    unattended executor is easier to change than the repository it changes."""
    box.guardrail(GUARDRAIL.replace(
        "SELECTOR_GUARDRAIL_RULES=deletion,non_fast_forward,pull_request",
        "SELECTOR_GUARDRAIL_RULES=deletion",
    ))

    box.run(db, [issue(645)])

    row = one(db, "guardrail.observed")
    assert row["protected"] is False
    assert "pull_request" in row["detail"]
    assert "non_fast_forward" in row["detail"]


def test_history_that_can_be_rewritten_is_not_protected(db, box):
    """A review that a force-push can replace afterwards is not a review."""
    box.guardrail(GUARDRAIL.replace(
        "SELECTOR_GUARDRAIL_RULES=deletion,non_fast_forward,pull_request",
        "SELECTOR_GUARDRAIL_RULES=deletion,pull_request",
    ))

    box.run(db, [issue(645)])

    row = one(db, "guardrail.observed")
    assert row["protected"] is False
    assert "non_fast_forward" in row["detail"]


def test_an_executed_path_that_differs_from_the_protected_ref_is_not_protected(
    db, box
):
    """The half a ruleset cannot see. The code that runs is what is in the
    deployed tree, and the tree is a shared working copy that anything with a
    shell can edit - so a rule on the branch says nothing about the bytes
    systemd is about to exec."""
    box.guardrail(GUARDRAIL.replace(
        "SELECTOR_GUARDRAIL_UNREVIEWED=",
        "SELECTOR_GUARDRAIL_UNREVIEWED=lab/single-user-factory/selector/cycle.py",
    ))

    box.run(db, [issue(645)])

    row = one(db, "guardrail.observed")
    assert row["protected"] is False
    assert row["unreviewed"] == ["lab/single-user-factory/selector/cycle.py"]
    assert "cycle.py" in row["detail"]


def test_a_half_the_command_did_not_answer_is_unknown_rather_than_assumed(db, box):
    """An absent key is not an empty one. `UNREVIEWED=` means nothing differs;
    no `UNREVIEWED` line at all means the comparison did not happen, and a
    guardrail that read that as "nothing differs" would report green for the
    one state it exists to catch."""
    box.guardrail(_without("UNREVIEWED"))

    box.run(db, [issue(645)])

    row = one(db, "guardrail.observed")
    assert row["protected"] is False
    assert row["unreviewed"] is None
    assert "unreviewed" in row["detail"]


def test_rules_the_command_did_not_answer_are_unknown_rather_than_assumed(db, box):
    box.guardrail(_without("RULES"))

    box.run(db, [issue(645)])

    row = one(db, "guardrail.observed")
    assert row["protected"] is False
    assert row["rules"] is None


# --- When it cannot be read at all ------------------------------------------


def test_a_guardrail_that_cannot_be_read_is_journaled_as_such(db, box):
    box.guardrail_command(
        'printf "gh: API rate limit exceeded\\n" >&2\nexit 1\n'
    )

    result = box.run(db, [issue(645)])

    assert result.returncode == 0, result.stderr
    assert events(db, "guardrail.observed") == []
    assert "rate limit" in one(db, "guardrail.unreadable")["error"]


def test_a_guardrail_that_cannot_be_read_does_not_stop_the_cycle(db, box):
    """It is a status read, like the box card. The dispatch behind it is what
    the cycle is for, and a Selector that refused to work because GitHub would
    not answer a question about its own rules would be a queue stopped by a
    dashboard."""
    box.guardrail_command('printf "no\\n" >&2\nexit 1\n')

    box.run(db, [issue(645)])

    assert len(events(db, "run.dispatched")) == 1
    assert last(db, "cycle.finished")["picked"] == 645
