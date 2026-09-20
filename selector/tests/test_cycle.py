"""The Selector's entry point at its boundary: real cycle.py, one runner.

Cycle decisions live in-process (`test_drain.py`, `test_drain_eligibility.py`,
`test_drain_halts.py`, `test_drain_slots.py`). What remains here still forks
because it is the entry point, not a Cycle decision: tracker-command failure
shapes, `--target`, Config from the targets file, the unenrolled-Target
search, extra preflight names.

Two of the forked Cycle wiring tests live here: the `--dry-run` tripwire
(`test_dry_run_touches_nothing_but_the_journal`) and the preflight refusal
(`test_a_missing_required_value_stops_the_cycle_before_the_tracker`).
"""
import events as event_vocab
import journal
from conftest import issue


def events(dsn, kind=None):
    """Journal rows oldest first - the order a cycle wrote them."""
    with journal.connect(dsn) as conn:
        rows = list(reversed(journal.events(conn)))
    return [r for r in rows if kind is None or r["kind"] == kind]


def skips(dsn):
    """Skipped issue number -> the reason journaled for it."""
    return {e["payload"]["number"]: e["payload"]["reason"]
            for e in events(dsn, "issue.skipped")}


def picked(dsn):
    got = events(dsn, "cycle.picked")
    return got[0]["payload"] if got else None


def finished(dsn):
    got = events(dsn, "cycle.finished")
    assert got, "every cycle journals how it finished"
    return got[0]["payload"]


# --- The cycle as a whole ---------------------------------------------------


def test_dry_run_touches_nothing_but_the_journal(db, fakes):
    """Forked Cycle wiring: `--dry-run` reaches the tracker and nothing else.

    The tripwire PATH (`gh`, `git`, `ssh`, `seed-run.sh` shimmed to log and
    fail) is how that is a checked property rather than a claim.
    """
    result = fakes.run(db, [issue(645), issue(646, blockedBy=1)], dry_run=True)
    assert result.returncode == 0
    assert fakes.tripped() == "", "dry-run reached outside the Journal"
    assert events(db), "the Journal is the one thing it does write"


def test_the_tracker_is_asked_for_the_configured_repo_and_label(db, fakes):
    """Still forks: the entry point's tracker argv, not a Cycle decision."""
    fakes.run(db, [issue(645)], dry_run=True)
    assert fakes.args_file.read_text().strip() == "acme/widgets ready-for-agent"


def test_every_event_of_one_cycle_shares_its_cycle_id(db, fakes):
    """Still forks: the entry point's Journal rows for one firing."""
    fakes.run(db, [issue(645), issue(646, blockedBy=1)], dry_run=True)
    rows = events(db)
    start = [r for r in rows if r["kind"] == "cycle.started"]
    assert len(start) == 1
    cycle_id = start[0]["id"]
    assert {r["payload"].get("cycle") for r in rows[1:]} == {cycle_id}


def test_a_dead_tracker_fails_the_cycle_loudly(db, fakes, tmp_path):
    """Still forks: a tracker-command failure shape at the entry point."""
    broken = tmp_path / "broken.sh"
    broken.write_text("#!/usr/bin/env bash\necho 'tracker exploded' >&2\nexit 4\n")
    broken.chmod(0o755)
    result = fakes.run(db, [issue(645)], tracker_command=broken, dry_run=True)
    assert result.returncode != 0, "a dead Selector must not look like a quiet queue"
    assert "tracker" in result.stderr.lower()
    assert events(db, "cycle.failed"), "the failure is journaled, not only printed"


def test_a_tracker_that_exits_nonzero_after_printing_still_fails(db, fakes, tmp_path):
    """Still forks: a tracker-command failure shape at the entry point.

    The dangerous shape: a paginated read that fetched one page and then
    failed prints well-formed JSON on the way out. Trusting the exit code is
    the only thing between that and a short queue read as the whole queue -
    an issue skipped for not being there at all."""
    truncated = tmp_path / "truncated.sh"
    truncated.write_text(
        "#!/usr/bin/env bash\n"
        "echo '{\"issues\": []}'\n"
        "echo 'gh: API rate limit exceeded' >&2\n"
        "exit 3\n"
    )
    truncated.chmod(0o755)
    result = fakes.run(db, [issue(645)], tracker_command=truncated, dry_run=True)
    assert result.returncode != 0
    assert events(db, "cycle.failed")
    assert not events(db, "cycle.finished"), "a failed read is not a finished cycle"


def test_a_queue_record_with_no_number_fails_the_cycle(db, fakes):
    """Still forks: a tracker-command failure shape at the entry point.

    Malformed on the inside, not just at the envelope: the ordering step
    is the first thing to touch a record, and it must fail the same journaled
    way as an unparseable response."""
    result = fakes.run(db, [{"title": "no number here"}], dry_run=True)
    assert result.returncode != 0
    assert events(db, "cycle.failed")


def test_a_tracker_that_returns_nonsense_fails_the_cycle(db, fakes, tmp_path):
    """Still forks: a tracker-command failure shape at the entry point."""
    junk = tmp_path / "junk.sh"
    junk.write_text("#!/usr/bin/env bash\necho 'not json'\n")
    junk.chmod(0o755)
    result = fakes.run(db, [], tracker_command=junk, dry_run=True)
    assert result.returncode != 0
    assert events(db, "cycle.failed")


def test_dry_run_is_the_cycle_that_would_have_happened(db, fakes):
    """Still forks: `--dry-run` through the entry point journals the pick
    it would have dispatched. The tripwire holds "stops there"."""
    result = fakes.run(db, [issue(645), issue(646, blockedBy=1)], dry_run=True)
    assert result.returncode == 0
    assert picked(db)["number"] == 645
    assert finished(db)["dry_run"] is True
    assert not events(db, "run.dispatched"), "a dry run starts no Run"
    assert not events(db, "issue.returned"), "a dry run hands nothing back"


# --- The instance is configured, never coded (issue #3) ---------------------
#
# The target is a stanza in a file now, and everything about it - which
# repository, which four labels, whose labelling counts as a Handover, how
# many Proposals may sit in review, what a finished Run does with its work -
# comes from there and from nowhere else. What is checked here is that a
# dry-run cycle honours the stanza and journals it, and that an instance that
# is short of a required value stops before it has read anything.


def test_a_dry_run_works_the_target_the_file_declares(db, fakes):
    """Still forks: Config from the targets file, not a Cycle decision.

    The whole of a target's declaration, journaled with the cycle that ran
    under it. A Journal holding the reasoning but not the settings the
    reasoning ran under leaves "why that repository, under whose Handover?"
    answerable only from a file that has since changed."""
    result = fakes.run(db, [issue(645)], targets=[{
        "repo": "acme/gadgets",
        "labeler_allowlist": ["an-operator", "a-second-operator"],
        "review_cap": 7,
        "landing": "propose",
    }], dry_run=True)

    assert result.returncode == 0, result.stderr
    started = events(db, "cycle.started")[0]["payload"]
    assert started["repo"] == "acme/gadgets"
    assert started["label"] == "ready-for-agent"
    assert started["allowlist"] == ["an-operator", "a-second-operator"]
    assert started["review_cap"] == 7
    assert started["landing"] == "propose"
    assert fakes.args_file.read_text().strip() == "acme/gadgets ready-for-agent"


def test_the_handover_label_is_the_targets_own(db, fakes):
    """Still forks: Config from the targets file, not a Cycle decision.

    A target that calls its Handover something else gets a cycle that asks
    the tracker for that label. There is no product-wide `ready-for-agent`
    the code could fall back on."""
    fakes.run(db, [issue(645)], targets=[{"labels": {"ready": "hand-over"}}], dry_run=True)
    assert fakes.args_file.read_text().strip() == "acme/widgets hand-over"


def test_the_allowlist_is_the_targets_own(db, fakes):
    """Still forks: Config from the targets file, not a Cycle decision.

    The Handover IS the label, so who may apply it is the whole of who is
    trusted - and it is declared per target, because one operator's tracker
    accounts are not another's."""
    fakes.run(db, [issue(645)], targets=[{"labeler_allowlist": ["somebody-else"]}], dry_run=True)
    assert skips(db) == {645: "labeler-not-allowlisted"}


def test_every_declared_target_is_worked_by_one_cycle_run(db, fakes):
    """Still forks: the entry point's per-Target fan-out, not a Cycle decision.

    A second target is a stanza, not a second controller: one invocation,
    one Journal, one cycle per target."""
    result = fakes.run(db, [issue(645)], targets=[
        {"repo": "acme/widgets"}, {"repo": "acme/gadgets"}], dry_run=True)

    assert result.returncode == 0, result.stderr
    worked = [e["payload"]["repo"] for e in events(db, "cycle.started")]
    assert worked == ["acme/widgets", "acme/gadgets"]
    assert len(events(db, "cycle.finished")) == 2


def test_one_target_can_be_worked_on_its_own(db, fakes):
    """Still forks: `--target` is the entry point, not a Cycle decision."""
    result = fakes.run(
        db, [issue(645)],
        targets=[{"repo": "acme/widgets"}, {"repo": "acme/gadgets"}],
        select="acme/gadgets", dry_run=True)

    assert result.returncode == 0, result.stderr
    assert [e["payload"]["repo"] for e in events(db, "cycle.started")] == [
        "acme/gadgets"]


def test_a_missing_required_value_stops_the_cycle_before_the_tracker(db, fakes):
    """Forked Cycle wiring: preflight refusal, naming the value.

    Before the tracker is read and long before anything is dispatched: an
    instance that is half configured must not do half a cycle, and the half
    it would do is the half that comments on somebody's issue."""
    result = fakes.run(db, [issue(645)], targets=[{"box_repo": ""}], dry_run=True)

    assert result.returncode == 1
    assert "box_repo" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]
    assert "box_repo" in events(db, "cycle.failed")[0]["payload"]["error"]


def test_an_unconfigured_instance_stops_by_naming_the_variable(db, fakes):
    """Still forks: extra preflight name, not a Cycle decision."""
    result = fakes.run(db, [issue(645)], TRACEWAKE_TARGETS_FILE="", dry_run=True)

    assert result.returncode == 1
    assert "TRACEWAKE_TARGETS_FILE" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"


def test_a_missing_instance_value_also_stops_before_the_tracker(db, fakes):
    """Still forks: extra preflight name, not a Cycle decision.

    An instance value that is absent is not caught by the script that reads
    it until the cycle has already read the queue, seeded a branch and
    pushed it - `observe_box` treats a box it cannot read as a status
    failure and carries on, deliberately - so nothing but the preflight
    makes "before any tracker read" true of it."""
    result = fakes.run(db, [issue(645)], SELECTOR_BOX_HOST="", dry_run=True)

    assert result.returncode == 1
    assert "SELECTOR_BOX_HOST" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]


def test_a_missing_search_owner_stops_before_any_tracker_read(db, fakes):
    """Still forks: extra preflight name, not a Cycle decision.

    The searched owner is instance configuration (issue #39). A default
    that named an account would be the thing issue #3 forbids."""
    result = fakes.run(db, [issue(645)], SELECTOR_SEARCH_OWNER="", dry_run=True)

    assert result.returncode == 1
    assert "SELECTOR_SEARCH_OWNER" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"
    assert not fakes.search_args.exists(), "the owner-wide search ran anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]
    assert "SELECTOR_SEARCH_OWNER" in events(db, "cycle.failed")[0]["payload"]["error"]


# --- Unenrolled-Target warning (#39) ----------------------------------------
#
# Still forks: observe_unenrolled is the entry point, not a Cycle decision.


def labeled_elsewhere(repo="acme/other", number=7, **over):
    """A Handover-labeled issue as the owner-wide search reports it."""
    record = {
        "repo": repo,
        "number": number,
        "title": f"Issue {number} on {repo}",
        "url": f"https://github.invalid/{repo}/issues/{number}",
    }
    record.update(over)
    return record


def latest_unenrolled(dsn):
    rows = events(dsn, "target.unenrolled")
    assert rows, "expected a target.unenrolled row"
    return rows[-1]["payload"]


def seed_warning(dsn, **over):
    """A previous live warning, so a later cycle can see a standing gap."""
    payload = {
        "owner": "acme",
        "label": "ready-for-agent",
        "repos": [{"repo": "acme/other", "issues": [{"number": 7}]}],
        "new": ["acme/other"],
        "dry_run": False,
    }
    payload.update(over)
    with journal.connect(dsn) as conn:
        journal.append(conn, *event_vocab.target_unenrolled(**payload))


def test_a_handover_on_an_unenrolled_repo_is_journaled_as_new(db, fakes):
    result = fakes.run(db, [issue(645)], owner_issues=[labeled_elsewhere()], dry_run=True)

    assert result.returncode == 0, result.stderr
    payload = latest_unenrolled(db)
    assert payload["owner"] == "acme"
    assert payload["label"] == "ready-for-agent"
    assert [r["repo"] for r in payload["repos"]] == ["acme/other"]
    assert payload["repos"][0]["issues"][0]["number"] == 7
    assert payload["new"] == ["acme/other"]
    assert fakes.search_args.read_text().strip() == "acme ready-for-agent"


def test_a_standing_gap_journals_without_being_new(db, fakes):
    seed_warning(db)
    result = fakes.run(db, [issue(645)], owner_issues=[labeled_elsewhere()], dry_run=True)

    assert result.returncode == 0, result.stderr
    rows = events(db, "target.unenrolled")
    assert len(rows) == 2
    assert latest_unenrolled(db)["new"] == []
    assert [r["repo"] for r in latest_unenrolled(db)["repos"]] == ["acme/other"]


def test_a_newly_appearing_unenrolled_repo_is_new(db, fakes):
    seed_warning(db)
    result = fakes.run(
        db, [issue(645)],
        owner_issues=[
            labeled_elsewhere(),
            labeled_elsewhere(repo="acme/stray", number=3, title="Stray work"),
        ], dry_run=True)

    assert result.returncode == 0, result.stderr
    payload = latest_unenrolled(db)
    assert payload["new"] == ["acme/stray"]
    assert {r["repo"] for r in payload["repos"]} == {"acme/other", "acme/stray"}


def test_labeled_issues_on_declared_targets_are_never_flagged(db, fakes):
    result = fakes.run(
        db, [issue(645)],
        owner_issues=[
            labeled_elsewhere(repo="acme/widgets", number=645),
            labeled_elsewhere(repo="acme/other", number=7),
        ], dry_run=True)

    assert result.returncode == 0, result.stderr
    payload = latest_unenrolled(db)
    assert [r["repo"] for r in payload["repos"]] == ["acme/other"]
    assert payload["new"] == ["acme/other"]


def test_an_empty_gap_is_not_journaled_when_none_was_open(db, fakes):
    result = fakes.run(db, [issue(645)], owner_issues=[
        labeled_elsewhere(repo="acme/widgets", number=645),
    ], dry_run=True)

    assert result.returncode == 0, result.stderr
    assert events(db, "target.unenrolled") == []


def test_a_cleared_gap_is_journaled_empty(db, fakes):
    """A standing gap that goes away must leave a row, or a later
    reappearance would match the last non-empty set and stay silent."""
    seed_warning(db)
    result = fakes.run(db, [issue(645)], owner_issues=[
        labeled_elsewhere(repo="acme/widgets", number=645),
    ], dry_run=True)

    assert result.returncode == 0, result.stderr
    payload = latest_unenrolled(db)
    assert payload["repos"] == []
    assert payload["new"] == []


def test_a_gap_that_returns_after_clearing_is_new_again(db, fakes):
    seed_warning(db)
    seed_warning(db, repos=[], new=[])
    result = fakes.run(db, [issue(645)], owner_issues=[labeled_elsewhere()], dry_run=True)

    assert result.returncode == 0, result.stderr
    payload = latest_unenrolled(db)
    assert payload["new"] == ["acme/other"]
    assert [r["repo"] for r in payload["repos"]] == ["acme/other"]


def test_the_owner_search_runs_once_per_cycle_not_per_target(db, fakes):
    result = fakes.run(
        db, [issue(645)],
        targets=[{"repo": "acme/widgets"}, {"repo": "acme/gadgets"}],
        owner_issues=[labeled_elsewhere()], dry_run=True)

    assert result.returncode == 0, result.stderr
    log = fakes.search_args.parent.joinpath("search.log").read_text().splitlines()
    assert log == ["acme ready-for-agent"]
    assert len(events(db, "target.unenrolled")) == 1


def test_a_declared_target_worked_via_select_is_still_enrolled(db, fakes):
    """`--target` narrows the drain, not the enrollment set. A labeled issue
    on a declared Target the cycle did not work this time is not a gap."""
    result = fakes.run(
        db, [issue(645)],
        targets=[{"repo": "acme/widgets"}, {"repo": "acme/gadgets"}],
        select="acme/gadgets",
        owner_issues=[labeled_elsewhere(repo="acme/widgets", number=645)], dry_run=True)

    assert result.returncode == 0, result.stderr
    assert events(db, "target.unenrolled") == []


def test_a_failed_owner_search_fails_the_cycle_before_the_queue(db, fakes):
    """Still forks: the entry point's unenrolled search, not a Cycle decision."""
    failing = fakes.search_args.parent / "failing-search.sh"
    failing.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'search: GitHub refused' >&2\n"
        "exit 1\n"
    )
    failing.chmod(0o755)
    result = fakes.run(
        db, [issue(645)],
        owner_issues=[labeled_elsewhere()],
        search_command=failing, dry_run=True)

    assert result.returncode == 1
    assert not fakes.args_file.exists(), "the per-target tracker ran anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]
    assert "GitHub refused" in events(db, "cycle.failed")[0]["payload"]["error"]


def test_a_zero_drain_concurrency_stops_before_the_tracker(db, fakes):
    """Still forks: extra preflight name, not a Cycle decision.

    K of zero aborts at preflight naming the key (issue #37)."""
    result = fakes.run(db, [issue(645)], SELECTOR_DRAIN_CONCURRENCY="0", dry_run=True)

    assert result.returncode == 1
    assert "SELECTOR_DRAIN_CONCURRENCY" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]
    assert "SELECTOR_DRAIN_CONCURRENCY" in events(db, "cycle.failed")[0]["payload"]["error"]


def test_a_negative_drain_concurrency_stops_before_the_tracker(db, fakes):
    """Still forks: extra preflight name, not a Cycle decision."""
    result = fakes.run(db, [issue(645)], SELECTOR_DRAIN_CONCURRENCY="-2", dry_run=True)

    assert result.returncode == 1
    assert "SELECTOR_DRAIN_CONCURRENCY" in result.stderr
    assert not fakes.args_file.exists(), "the tracker was read anyway"
    assert [e["kind"] for e in events(db)] == ["cycle.failed"]
    assert "SELECTOR_DRAIN_CONCURRENCY" in events(db, "cycle.failed")[0]["payload"]["error"]
