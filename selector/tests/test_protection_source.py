"""`guardrail-sources/protection.sh`, driven directly (#165).

`test_guardrail.py` covers what the Selector does with the answer; this covers
the answer itself, which is the half that reads GitHub and the deployed tree.
Both edges are scripted: `gh` is a fake on PATH, and the tree is a throwaway
repository with a real `origin/master` in it, so what the script reports it can
only have got from the two things it is supposed to read.

No network, and no reliance on the state of the checkout the suite runs in -
which matters more here than usual, because the tree this script reads in
production IS that checkout.
"""
import os
import subprocess
from pathlib import Path

import pytest

SELECTOR = Path(__file__).resolve().parents[1]
PROTECTION = SELECTOR / "guardrail-sources" / "protection.sh"

# Two paths, so "it reported the one that changed" is distinguishable from "it
# reported everything it was given".
PATHS = ["loop", "selector"]

RULES_JSON = """[
  {"type": "deletion"},
  {"type": "non_fast_forward"},
  {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}
]
"""


def _answering(json_text: str) -> str:
    """A fake `gh` that answers `json_text` and honours `--jq`.

    Honouring it matters: the filter the script passes is the half that turns
    GitHub's rule objects into the names the verdict is made of, and a fake
    that ignored it would leave that expression checked by nothing.
    """
    return (
        f"payload=$(cat <<'JSON'\n{json_text}JSON\n)\n"
        'while (($# > 0)); do\n'
        '    case "$1" in\n'
        '        --jq) filter="$2"; shift 2 ;;\n'
        '        *) shift ;;\n'
        '    esac\n'
        'done\n'
        'printf %s "${payload}" | jq -r "${filter:-.}"\n'
    )


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def tree(tmp_path):
    """A repository with the executed paths in it, and an `origin/master`.

    The remote ref is written with `update-ref` rather than by cloning: what
    the script compares against is `refs/remotes/<remote>/<ref>` as the tree
    already holds it, and building it directly is both faster and closer to
    what a stale checkout looks like.
    """
    work = tmp_path / "tree"
    work.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(work), *args],
            check=True, capture_output=True, text=True,
        ).stdout

    git("init", "--quiet", "-b", "master")
    git("config", "user.email", "operator@example.invalid")
    git("config", "user.name", "The Operator")
    git("config", "commit.gpgsign", "false")
    for path in PATHS:
        (work / path).mkdir(parents=True)
        (work / path / "run.sh").write_text("the reviewed copy\n")
    (work / "reports").mkdir()
    (work / "reports" / "page.html").write_text("not executed by anything\n")
    git("add", "-A")
    git("commit", "--quiet", "-m", "The reviewed state")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    return work


@pytest.fixture
def run(tmp_path, tree):
    """The script, with `gh` faked and the tree and path list pointed at."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text(
        "# a comment, and a blank line, both ignored\n\n"
        + "".join(f"{path}\n" for path in PATHS)
    )

    def go(gh_body=_answering(RULES_JSON)):
        _script(bin_dir / "gh", gh_body)
        env = dict(os.environ)
        env.update(
            PATH=f"{bin_dir}:{env['PATH']}",
            SELECTOR_PROTECTED_REPO="acme/widgets",
            SELECTOR_PROTECTED_REF="master",
            SELECTOR_PROTECTED_TREE=str(tree),
            SELECTOR_PROTECTED_PATHS=str(paths_file),
        )
        return subprocess.run(
            [str(PROTECTION)], capture_output=True, text=True, env=env
        )

    return go


def read(done):
    """The `KEY=value` lines, as `cycle.py` parses them - absent stays absent."""
    return dict(
        line.split("=", 1) for line in done.stdout.splitlines() if "=" in line
    )


def test_it_reports_the_rules_github_holds_over_the_ref(run):
    done = run()
    assert done.returncode == 0, done.stderr
    facts = read(done)
    assert facts["SELECTOR_GUARDRAIL_REF"] == "master"
    assert facts["SELECTOR_GUARDRAIL_RULES"] == (
        "deletion,non_fast_forward,pull_request"
    )


def test_it_reports_the_paths_it_was_told_to_watch(run):
    facts = read(run())
    assert facts["SELECTOR_GUARDRAIL_PATHS"] == ",".join(PATHS)


def test_a_tree_that_matches_the_ref_has_nothing_unreviewed(run):
    facts = read(run())
    assert facts["SELECTOR_GUARDRAIL_UNREVIEWED"] == ""
    # The commit it compared against, so the answer names its own baseline.
    assert facts["SELECTOR_GUARDRAIL_REF_HEAD"]


def test_an_edited_executed_path_is_reported_unreviewed(run, tree):
    (tree / PATHS[1] / "run.sh").write_text("edited in place, reviewed by nobody\n")
    facts = read(run())
    assert facts["SELECTOR_GUARDRAIL_UNREVIEWED"] == f"{PATHS[1]}/run.sh"


def test_a_new_file_under_an_executed_path_is_reported_unreviewed(run, tree):
    """The case `git diff` alone misses. An untracked file in the deployed
    tree is code that will run, and a comparison that only looked at tracked
    files would call the tree clean while it held it."""
    (tree / PATHS[0] / "extra.sh").write_text("dropped in by hand\n")
    facts = read(run())
    assert facts["SELECTOR_GUARDRAIL_UNREVIEWED"] == f"{PATHS[0]}/extra.sh"


def test_a_change_outside_the_executed_paths_is_not_reported(run, tree):
    """The declaration is the fence. This repository is a working area - most
    of what changes in it is not executed unattended, and a guardrail that
    went red for a report page would be one nobody could keep green."""
    (tree / "reports" / "page.html").write_text("an edit to a published page\n")
    assert read(run())["SELECTOR_GUARDRAIL_UNREVIEWED"] == ""


def test_a_committed_but_unmerged_change_to_an_executed_path_counts(run, tree):
    """Committed is not reviewed. The tree runs whatever branch it is checked
    out to, and a commit that has not reached the protected ref has been
    through no review at all."""
    (tree / PATHS[1] / "run.sh").write_text("committed on a branch\n")
    subprocess.run(["git", "-C", str(tree), "commit", "--quiet", "-am", "wip"],
                   check=True, capture_output=True)
    assert read(run())["SELECTOR_GUARDRAIL_UNREVIEWED"] == f"{PATHS[1]}/run.sh"


def test_a_tree_behind_the_protected_ref_has_nothing_unreviewed(run, tree):
    """Stale is not unreviewed, and the difference is what makes the chip
    usable. This checkout is a shared working area: it sits on whatever branch
    somebody left it on and its `origin/master` moves whenever anybody fetches,
    so it is behind the protected ref most of the time. A comparison that read
    "master has moved on" as "the deployed tree holds unreviewed code" would be
    red permanently, and a chip that is always red is one nobody reads.

    So the comparison is three-dot: what the tree has that the protected ref's
    history does not, never what the ref has that the tree has not caught up
    with.
    """
    # A commit that CHANGES an executed path, because that is the only kind
    # this can get wrong: an empty one leaves nothing for a two-dot diff to
    # report and would pass whichever comparison the script used.
    (tree / PATHS[0] / "run.sh").write_text("the reviewed copy, one version on\n")
    subprocess.run(
        ["git", "-C", str(tree), "commit", "--quiet", "-am",
         "A reviewed commit the checkout has not pulled"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tree), "update-ref", "refs/remotes/origin/master", "HEAD"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(tree), "reset", "--quiet", "--hard", "HEAD~1"],
                   check=True, capture_output=True)

    assert read(run())["SELECTOR_GUARDRAIL_UNREVIEWED"] == ""


def test_an_uncommitted_edit_counts_even_on_a_tree_at_the_protected_ref(run, tree):
    """The other end of the same distinction. What runs is what is on disk,
    and this tree is group-writable by three accounts."""
    (tree / PATHS[0] / "run.sh").write_text("edited on a tree that is at master\n")
    assert read(run())["SELECTOR_GUARDRAIL_UNREVIEWED"] == f"{PATHS[0]}/run.sh"


def test_a_forge_that_will_not_answer_fails_rather_than_reports_green(run):
    """The failure mode that matters. Silence about the rules must reach the
    Journal as `guardrail.unreadable`, not as an answer with a missing half
    that something downstream might read as fine."""
    done = run("printf 'gh: API rate limit exceeded\\n' >&2\nexit 1\n")
    assert done.returncode != 0
    assert "rate limit" in done.stderr
    assert "SELECTOR_GUARDRAIL_RULES" not in done.stdout


def test_a_ref_with_no_rules_at_all_answers_empty_rather_than_failing(run):
    """An unprotected ref is a fact, not an outage: `gh` answers `[]` and the
    verdict is the Selector's to make. Failing here would report the one state
    the guardrail exists to catch as a broken command."""
    done = run(_answering("[]\n"))
    assert done.returncode == 0, done.stderr
    facts = read(done)
    assert facts["SELECTOR_GUARDRAIL_RULES"] == ""
    assert "SELECTOR_GUARDRAIL_REF" in facts
