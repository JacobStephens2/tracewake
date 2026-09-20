"""The targets file: what a stanza must carry, and how it refuses.

Issue #3 moves every instance fact out of the code and into two files, and the
whole value of that move is in the refusals. A loader that filled a missing
value in with something plausible would put the old problem back with an extra
file in front of it: the cycle would run, against a repository nobody
configured, and the first sign would be a comment on a stranger's issue.

So what is tested here is mostly what does NOT happen, and that the message
names the value when it does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import targets  # noqa: E402
from fixtures import targets_toml, write_targets  # noqa: E402


def load(tmp_path, *stanzas):
    path = tmp_path / "targets.toml"
    return targets.load(Path(write_targets(path, *stanzas)))


def refusal(tmp_path, text: str) -> str:
    path = tmp_path / "targets.toml"
    path.write_text(text)
    with pytest.raises(targets.NotConfigured) as raised:
        targets.load(path)
    return str(raised.value)


# --- What a stanza must carry ------------------------------------------------


def test_a_complete_stanza_loads(tmp_path):
    (target,) = load(tmp_path)
    assert target.repo == "acme/widgets"
    assert target.labeler_allowlist == ("an-operator",)
    assert target.work_repo == Path("/nonexistent/work")


@pytest.mark.parametrize(
    "key",
    ["repo", "work_repo", "box_repo", "token_file", "guest_template",
     "labeler_allowlist"],
)
def test_a_missing_required_value_is_refused_by_name(tmp_path, key):
    """By name, because "the cycle could not run" costs an operator the same
    half hour every time. The file and the stanza's position are in the
    message too: a file with four stanzas needs to say which one is short."""
    text = "\n".join(
        line for line in targets_toml().splitlines()
        if not line.startswith(f"{key} = ")
    )
    message = refusal(tmp_path, text)
    assert key in message
    assert "targets.toml" in message
    assert "target 1" in message


def test_an_empty_value_is_as_absent_as_a_missing_one(tmp_path):
    message = refusal(tmp_path, targets_toml(repo=""))
    assert "repo" in message


def test_a_misspelled_key_is_refused_rather_than_ignored(tmp_path):
    """The failure a typo produces otherwise is a setting that reads as
    configured and is not: `review-cap` with a hyphen would leave the default
    in force and nothing would ever say so."""
    message = refusal(tmp_path, targets_toml() + 'review-cap = 5\n')
    assert "review-cap" in message
    assert "review_cap" in message


def test_a_file_with_no_target_is_refused(tmp_path):
    message = refusal(tmp_path, "# nothing here\n")
    assert "[[target]]" in message


def test_the_same_repository_twice_is_refused(tmp_path):
    message = refusal(tmp_path, targets_toml() + "\n" + targets_toml())
    assert "acme/widgets" in message


def test_an_unreadable_file_names_the_variable_that_points_at_it(tmp_path):
    with pytest.raises(targets.NotConfigured) as raised:
        targets.load(tmp_path / "not-there.toml")
    assert targets.TARGETS_FILE_VAR in str(raised.value)


def test_no_targets_file_at_all_names_the_variable(monkeypatch):
    """And says there is no default for it, which is the load-bearing half:
    a conventional path stood in for here would let an instance that
    configured nothing work whatever targets happened to be sitting there."""
    monkeypatch.delenv(targets.TARGETS_FILE_VAR, raising=False)
    with pytest.raises(targets.NotConfigured) as raised:
        targets.load()
    assert targets.TARGETS_FILE_VAR in str(raised.value)
    assert "no default" in str(raised.value)


# --- The settings that do have defaults --------------------------------------


def test_the_four_labels_default_to_the_vocabulary(tmp_path):
    (target,) = load(tmp_path)
    assert (target.labels.ready, target.labels.needs_info) == (
        "ready-for-agent", "needs-info")
    assert (target.labels.review, target.labels.human) == (
        "awaiting-review", "ready-for-human")


def test_a_target_may_rename_one_label_without_naming_the_rest(tmp_path):
    (target,) = load(tmp_path, {"labels": {"review": "second-look"}})
    assert target.labels.review == "second-look"
    assert target.labels.ready == "ready-for-agent"


def test_a_label_the_product_does_not_read_is_refused(tmp_path):
    message = refusal(
        tmp_path, targets_toml() + "\n[target.labels]\nblocked = \"nope\"\n")
    assert "blocked" in message


def test_the_review_cap_defaults_and_may_be_set(tmp_path):
    (default,) = load(tmp_path)
    assert default.review_cap == targets.DEFAULT_REVIEW_CAP
    (set_low,) = load(tmp_path, {"review_cap": 3})
    assert set_low.review_cap == 3


@pytest.mark.parametrize("cap", [0, -1, "twenty"])
def test_a_cap_that_is_not_a_positive_number_is_refused(tmp_path, cap):
    """Zero is the interesting one: it reads as "never dispatch", which is
    pausing - and pausing has its own control, on the page, that a cycle
    journals a reason for."""
    message = refusal(tmp_path, targets_toml(review_cap=cap))
    assert "review_cap" in message


def test_landing_defaults_to_propose(tmp_path):
    (target,) = load(tmp_path)
    assert target.landing == "propose"


def test_a_landing_mode_nothing_implements_is_refused(tmp_path):
    """`land` is reserved and deliberately refused rather than accepted and
    ignored: an instance that declared it would believe its Runs were merging
    themselves."""
    message = refusal(tmp_path, targets_toml(landing="land"))
    assert "land" in message
    assert "propose" in message


# --- More than one target ----------------------------------------------------


def test_targets_keep_the_order_the_file_declares_them_in(tmp_path):
    loaded = load(tmp_path, {}, {"repo": "acme/gadgets"})
    assert [t.repo for t in loaded] == ["acme/widgets", "acme/gadgets"]


def test_one_target_may_be_selected_by_repository(tmp_path):
    loaded = load(tmp_path, {}, {"repo": "acme/gadgets"})
    (selected,) = targets.select(loaded, "acme/gadgets")
    assert selected.repo == "acme/gadgets"


def test_selecting_a_target_that_is_not_declared_lists_the_ones_that_are(
    tmp_path,
):
    loaded = load(tmp_path)
    with pytest.raises(targets.NotConfigured) as raised:
        targets.select(loaded, "acme/nothing")
    assert "acme/widgets" in str(raised.value)


# --- Removing a target -------------------------------------------------------
#
# The window's Remove control unenrolls a stanza. The file is the instance's
# list, so the public seam is here: drop the named repo, leave the rest in
# file order, and load() is what a Cycle reads next.


def test_removing_a_target_leaves_the_others_in_file_order(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(
        path,
        {"repo": "acme/alpha"},
        {"repo": "acme/beta"},
        {"repo": "acme/gamma"},
    )
    remaining = targets.remove("acme/beta", path)
    assert [t.repo for t in remaining] == ["acme/alpha", "acme/gamma"]
    assert [t.repo for t in targets.load(path)] == ["acme/alpha", "acme/gamma"]


def test_the_remaining_target_keeps_the_values_it_declared(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(
        path,
        {"repo": "acme/alpha"},
        {
            "repo": "acme/beta",
            "review_cap": 3,
            "labels": {"review": "second-look"},
            "labeler_allowlist": ["beta-operator"],
        },
    )
    targets.remove("acme/alpha", path)
    (beta,) = targets.load(path)
    assert beta.repo == "acme/beta"
    assert beta.review_cap == 3
    assert beta.labels.review == "second-look"
    assert beta.labeler_allowlist == ("beta-operator",)


def test_removing_the_last_target_leaves_a_file_with_no_stanza(tmp_path):
    """An empty instance is still a file the variable names.

    Deleting the file would make the next load say it could not be read,
    which is the wrong diagnosis: the operator just unenrolled the last
    Target, and a Cycle should refuse for having nothing to work.
    """
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"})
    remaining = targets.remove("acme/alpha", path)
    assert remaining == ()
    assert path.is_file()
    with pytest.raises(targets.NotConfigured) as raised:
        targets.load(path)
    assert "[[target]]" in str(raised.value)


def test_removing_a_repository_that_is_not_declared_is_refused(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"})
    with pytest.raises(targets.NotConfigured) as raised:
        targets.remove("acme/nothing", path)
    assert "acme/nothing" in str(raised.value)
    assert "acme/alpha" in str(raised.value)
    (still,) = targets.load(path)
    assert still.repo == "acme/alpha"


def test_removing_a_target_does_not_delete_its_host_files(tmp_path):
    """Unenroll, do not clean the machine.

    The stanza is what a Cycle reads. The checkout and token file are the
    Host's, and a Remove from the window must not take them with it.
    """
    work = tmp_path / "work"
    token = tmp_path / "token"
    work.mkdir()
    token.write_text("secret\n")
    path = tmp_path / "targets.toml"
    write_targets(
        path,
        {"repo": "acme/alpha"},
        {
            "repo": "acme/beta",
            "work_repo": str(work),
            "token_file": str(token),
        },
    )
    targets.remove("acme/beta", path)
    assert work.is_dir()
    assert token.read_text() == "secret\n"


# --- Adding a target ---------------------------------------------------------
#
# The window's Add form writes a stanza. Same seam as remove: the file is
# what a Cycle reads next, in file order, with every required value named
# by the operator and none invented.


def _stanza(**over):
    stanza = {
        "repo": "acme/gamma",
        "work_repo": "/nonexistent/work/gamma",
        "box_repo": "/nonexistent/box/gamma",
        "token_file": "/nonexistent/token/gamma",
        "guest_template": "gamma-guest:1",
        "labeler_allowlist": ["an-operator"],
    }
    stanza.update(over)
    return stanza


def test_adding_a_target_appends_it_in_file_order(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"}, {"repo": "acme/beta"})
    remaining = targets.add(_stanza(), path)
    assert [t.repo for t in remaining] == [
        "acme/alpha", "acme/beta", "acme/gamma",
    ]
    assert [t.repo for t in targets.load(path)] == [
        "acme/alpha", "acme/beta", "acme/gamma",
    ]


def test_adding_a_target_keeps_the_values_already_declared(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(
        path,
        {
            "repo": "acme/alpha",
            "review_cap": 3,
            "labels": {"review": "second-look"},
        },
    )
    targets.add(_stanza(), path)
    alpha, gamma = targets.load(path)
    assert alpha.review_cap == 3
    assert alpha.labels.review == "second-look"
    assert gamma.repo == "acme/gamma"
    assert gamma.labeler_allowlist == ("an-operator",)


def test_adding_the_first_target_to_an_empty_file_enrolls_it(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"})
    targets.remove("acme/alpha", path)
    remaining = targets.add(_stanza(), path)
    assert [t.repo for t in remaining] == ["acme/gamma"]
    (loaded,) = targets.load(path)
    assert loaded.repo == "acme/gamma"


def test_adding_the_first_target_creates_a_missing_file(tmp_path):
    path = tmp_path / "targets.toml"
    assert path.exists() is False
    remaining = targets.add(_stanza(), path)
    assert [t.repo for t in remaining] == ["acme/gamma"]
    (loaded,) = targets.load(path)
    assert loaded.repo == "acme/gamma"


def test_adding_a_repository_already_declared_is_refused(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"})
    with pytest.raises(targets.NotConfigured) as raised:
        targets.add(_stanza(repo="acme/alpha"), path)
    assert "acme/alpha" in str(raised.value)
    (still,) = targets.load(path)
    assert still.repo == "acme/alpha"


def test_adding_does_not_overwrite_a_malformed_file(tmp_path):
    path = tmp_path / "targets.toml"
    path.write_text("this is not toml {\n")
    with pytest.raises(targets.NotConfigured):
        targets.add(_stanza(), path)
    assert path.read_text() == "this is not toml {\n"


def test_adding_a_stanza_short_of_a_required_value_is_refused(tmp_path):
    path = tmp_path / "targets.toml"
    write_targets(path, {"repo": "acme/alpha"})
    with pytest.raises(targets.NotConfigured) as raised:
        targets.add(_stanza(token_file=""), path)
    assert "token_file" in str(raised.value)
    (still,) = targets.load(path)
    assert still.repo == "acme/alpha"


def test_a_single_stanza_may_be_written_as_a_table(tmp_path):
    """`[target]` and `[[target]]` mean the same thing to a reader, and a
    single-target instance is the common case."""
    path = tmp_path / "targets.toml"
    path.write_text(targets_toml().replace("[[target]]", "[target]", 1))
    (target,) = targets.load(path)
    assert target.repo == "acme/widgets"


# --- What reaches the commands -----------------------------------------------


def test_the_per_target_values_the_commands_read_are_the_targets_own(tmp_path):
    """The box checkout, the repository token and the guest image are read by
    scripts, and the environment is how a Python caller reaches a script. A
    second target worked in the same drain must not inherit the first's."""
    (widgets,) = load(tmp_path, {
        "repo": "acme/widgets", "box_repo": "/box/widgets",
        "token_file": "/tokens/widgets", "guest_template": "widgets:2",
    })
    assert widgets.environ() == {
        "SELECTOR_TASK_REPO": "acme/widgets",
        "SELECTOR_BOX_REPO": "/box/widgets",
        "LOOP_GITHUB_TOKEN_FILE": "/tokens/widgets",
        "LOOP_GUEST_TEMPLATE": "widgets:2",
    }


def test_an_allowlist_written_as_a_bare_string_is_refused(tmp_path):
    """The spelling somebody reaches for first, and the dangerous one: TOML
    accepts it, and iterating a string would put every character of the
    account name in the allowlist - which allowlists most of the alphabet."""
    message = refusal(
        tmp_path,
        targets_toml().replace(
            'labeler_allowlist = ["an-operator"]',
            'labeler_allowlist = "an-operator"',
        ),
    )
    assert "labeler_allowlist" in message


# --- The instance, not just the targets --------------------------------------


@pytest.mark.parametrize("name", sorted(targets.REQUIRED_INSTANCE_VARS))
def test_a_missing_instance_value_is_refused_by_name(monkeypatch, name):
    for present in targets.REQUIRED_INSTANCE_VARS:
        monkeypatch.setenv(present, "something")
    monkeypatch.delenv(name)
    with pytest.raises(targets.NotConfigured) as raised:
        targets.Instance.from_env()
    assert name in str(raised.value)
    assert "no default" in str(raised.value)


def test_a_configured_instance_reads_back(monkeypatch):
    monkeypatch.setenv("SELECTOR_BOX_HOST", "root@box.invalid")
    monkeypatch.setenv("SELECTOR_PROTECTED_REPO", "acme/tracewake")
    monkeypatch.setenv("SELECTOR_PROTECTED_REF", "main")
    monkeypatch.setenv("SELECTOR_SEARCH_OWNER", "acme")
    instance = targets.Instance.from_env()
    assert (
        instance.box_host,
        instance.protected_repo,
        instance.protected_ref,
        instance.search_owner,
    ) == (
        "root@box.invalid", "acme/tracewake", "main", "acme")


def test_install_declares_every_required_instance_value():
    """INSTALL.md is what an operator copies. One that was short of a
    required value would send them straight into the refusal the guide
    exists to save them from."""
    text = (Path(__file__).resolve().parents[2] / "INSTALL.md").read_text()
    for name in targets.REQUIRED_INSTANCE_VARS:
        assert name in text, (
            f"INSTALL.md does not name {name}, which preflight refuses "
            "with no default"
        )
    assert targets.TARGETS_FILE_VAR in text


# --- Multi-tree Guardrail configuration (#14) --------------------------------


def test_guardrail_trees_parsed_from_json_list_of_objects(monkeypatch):
    monkeypatch.setenv(
        "SELECTOR_GUARDRAIL_TREES",
        '[{"repo": "acme/tracewake", "ref": "main", "tree": "/srv/tracewake", "paths": "paths1.txt"}, '
        '{"repo": "acme/config", "ref": "master", "tree": "/srv/config", "paths": "paths2.txt"}]',
    )
    trees = targets.load_guardrail_trees()
    assert len(trees) == 2
    assert trees[0].repo == "acme/tracewake"
    assert trees[0].ref == "main"
    assert str(trees[0].tree) == "/srv/tracewake"
    assert str(trees[0].paths) == "paths1.txt"
    assert trees[1].repo == "acme/config"
    assert trees[1].ref == "master"
    assert str(trees[1].tree) == "/srv/config"
    assert str(trees[1].paths) == "paths2.txt"


def test_guardrail_trees_parsed_from_json_list_of_tuples(monkeypatch):
    monkeypatch.setenv(
        "SELECTOR_GUARDRAIL_TREES",
        '[["acme/tracewake", "main", "/srv/tracewake", "paths1.txt"], '
        '["acme/config", "master", "/srv/config", "paths2.txt"]]',
    )
    trees = targets.load_guardrail_trees()
    assert len(trees) == 2
    assert trees[0].repo == "acme/tracewake"
    assert trees[1].repo == "acme/config"


def test_guardrail_trees_parsed_from_delimited_string(monkeypatch):
    monkeypatch.setenv(
        "SELECTOR_GUARDRAIL_TREES",
        "acme/tracewake:main:/srv/tracewake:paths1.txt;acme/config:master:/srv/config:paths2.txt",
    )
    trees = targets.load_guardrail_trees()
    assert len(trees) == 2
    assert trees[0].repo == "acme/tracewake"
    assert trees[1].repo == "acme/config"


def test_guardrail_trees_fallback_to_protected_repo_and_ref(monkeypatch):
    monkeypatch.delenv("SELECTOR_GUARDRAIL_TREES", raising=False)
    monkeypatch.setenv("SELECTOR_PROTECTED_REPO", "acme/tracewake")
    monkeypatch.setenv("SELECTOR_PROTECTED_REF", "main")
    monkeypatch.setenv("SELECTOR_PROTECTED_TREE", "/srv/tracewake")
    monkeypatch.setenv("SELECTOR_PROTECTED_PATHS", "/srv/tracewake/paths.txt")
    trees = targets.load_guardrail_trees()
    assert len(trees) == 1
    assert trees[0].repo == "acme/tracewake"
    assert trees[0].ref == "main"
    assert str(trees[0].tree) == "/srv/tracewake"
    assert str(trees[0].paths) == "/srv/tracewake/paths.txt"


def test_malformed_guardrail_trees_refuses_by_name(monkeypatch):
    monkeypatch.setenv("SELECTOR_GUARDRAIL_TREES", "[invalid-json")
    with pytest.raises(targets.NotConfigured) as raised:
        targets.load_guardrail_trees()
    assert "SELECTOR_GUARDRAIL_TREES" in str(raised.value)


def test_guardrail_trees_satisfies_instance_configuration_without_legacy_vars(monkeypatch):
    monkeypatch.setenv("SELECTOR_BOX_HOST", "root@box.invalid")
    monkeypatch.setenv("SELECTOR_SEARCH_OWNER", "acme")
    monkeypatch.delenv("SELECTOR_PROTECTED_REPO", raising=False)
    monkeypatch.delenv("SELECTOR_PROTECTED_REF", raising=False)
    monkeypatch.setenv(
        "SELECTOR_GUARDRAIL_TREES",
        '[{"repo": "acme/tracewake", "ref": "main", "tree": "/srv/tracewake", "paths": "paths.txt"}]',
    )
    instance = targets.Instance.from_env()
    assert instance.box_host == "root@box.invalid"
    assert instance.protected_repo == "acme/tracewake"
    assert instance.protected_ref == "main"
    assert len(instance.guardrail_trees) == 1


