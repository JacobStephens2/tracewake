"""`issue-sources/github.sh`, driven directly.

`test_outcomes.py` covers what the Selector does with a Proposal's checks;
this covers the script that reads them, which the rest of the suite replaces
with a scripted fake. That substitution is the point of ADR 0004 and it has
one cost: the real script's own argument handling is exercised by nothing.

It cost a Run. On 2026-08-31 the first unattended dispatch (tourbot #712)
worked - exit 0, five Iterations, a draft Proposal - and then the cycle died
on the bookkeeping:

    cycle.py: could not read the checks on .../pull/713:
    issue-sources/github.sh: issue must be a number, got .../pull/713

The script's own usage says `checks <proposal-url-or-number>` and `cycle.py`
passes the URL, so both halves agreed and the guard between them did not.
These tests drive the real script with `gh` faked on PATH.

The same Run then hit the second half of that failure: the operator's
fine-grained PAT cannot read check runs at all - not a missing grant, a
permission GitHub's fine-grained tokens do not offer (#273) - so `checks`
reads workflow runs from the Actions API instead, and these tests fake the
two calls that takes: `gh pr view` for the Proposal's head commit, then
`gh api .../actions/runs?head_sha=`.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SELECTOR = Path(__file__).resolve().parents[1]
GITHUB = SELECTOR / "issue-sources" / "github.sh"

REPO = "acme/widgets"
PROPOSAL_URL = "https://github.com/acme/widgets/pull/713"
HEAD_SHA = "40349e3f26bc2bb0a85d92e010e241033ff62bcd"


def page(*runs):
    """One page of the Actions runs API, in GitHub's shape.

    Each run is a (name, status, conclusion) triple. The shape is GitHub's,
    not the Selector's: translating between them is what the script is for.
    """
    return json.dumps({
        "total_count": len(runs),
        "workflow_runs": [
            {"name": name, "status": status, "conclusion": conclusion}
            for name, status, conclusion in runs
        ],
    })


GREEN_PAGE = page(("Tourbot CI", "completed", "success"))


@pytest.fixture
def run(tmp_path):
    """The script, with a fake `gh` that records the arguments it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv"

    def go(*args, sha=HEAD_SHA, runs=GREEN_PAGE,
           pr_rc=0, api_rc=0, gh_err="", stdin=None):
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {argv_log}\n"
            f"printf '%s' {json.dumps(gh_err)} >&2\n"
            # Heredocs, not printf: a page boundary is a real newline, which
            # is how `gh api --paginate` concatenates its pages.
            "if [[ ${1-} == pr ]]; then\n"
            f"  cat <<'SHA'\n{sha}\nSHA\n"
            f"  exit {pr_rc}\n"
            "elif [[ ${1-} == api ]]; then\n"
            f"  cat <<'JSON'\n{runs}\nJSON\n"
            f"  exit {api_rc}\n"
            "fi\n"
        )
        gh.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        done = subprocess.run(
            [str(GITHUB), REPO, *args],
            capture_output=True, text=True, env=env, input=stdin,
        )
        done.gh_argv = argv_log.read_text() if argv_log.exists() else ""
        return done

    return go


def test_checks_accepts_a_proposal_url(run):
    """The documented contract: `checks <proposal-url-or-number>`.

    This is the regression. `cycle.py` has only the Proposal's URL to hand -
    the number is never parsed out of it anywhere - so a guard that demands a
    number here breaks the one path that runs after every successful Run.
    """
    done = run("checks", PROPOSAL_URL)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "green", "failing": []}
    assert PROPOSAL_URL in done.gh_argv


def test_checks_still_accepts_a_bare_number(run):
    """The other half of the same documented contract."""
    done = run("checks", "713")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "green", "failing": []}


def test_checks_refuses_something_that_is_neither(run):
    """A guard that accepts a URL must not become a guard that accepts anything.

    `gh pr view` would take a branch name, and taking one here would mean the
    Selector could read the checks of a Proposal other than the one the Run
    produced - a green light sourced from the wrong place.
    """
    done = run("checks", "loop/712-the-spelling")

    assert done.returncode == 1
    assert "proposal" in done.stderr.lower()
    assert not done.gh_argv


def test_a_url_for_another_repository_is_refused(run):
    """The Selector reads the checks of ITS repository's Proposals.

    `gh pr view <url>` resolves the repository from the URL and ignores
    `--repo`, so a URL from somewhere else is not a mismatch gh would catch:
    it is simply a different Proposal, answered as though it were this one.
    """
    done = run("checks", "https://github.com/someone/else/pull/1")

    assert done.returncode == 1
    assert "acme/widgets" in done.stderr
    assert not done.gh_argv


@pytest.mark.parametrize("action", ["comment", "relabel"])
def test_the_writing_actions_still_demand_a_number(run, action):
    """Only `checks` takes a URL.

    `gh issue comment` and `gh issue edit` are writes, and the number is the
    whole of what says which issue is written to. Loosening the guard for the
    read must not loosen it for those.
    """
    done = run(action, PROPOSAL_URL, "awaiting-review", "ready-for-agent",
               stdin="a comment")

    assert done.returncode == 1
    assert "must be a number" in done.stderr
    assert not done.gh_argv


# --- the Actions API, which is what `checks` actually reads (#273) ---------


def test_checks_asks_the_actions_api_for_the_proposals_head_commit(run):
    """Two calls, in that order: the head commit, then that commit's runs.

    The runs are filtered by `head_sha` rather than by branch, so a Proposal
    whose branch has moved on since the Run pushed cannot be graded by a
    later commit's CI.
    """
    done = run("checks", PROPOSAL_URL)

    assert done.returncode == 0, done.stderr
    lines = done.gh_argv.splitlines()
    assert lines[0].startswith("pr view")
    assert f"head_sha={HEAD_SHA}" in lines[1]
    # The Checks API is what a fine-grained PAT cannot read; never ask for it.
    assert "check-runs" not in done.gh_argv


def test_a_head_commit_that_is_not_a_sha_is_refused(run):
    """The sha is interpolated into an API path, so it is checked first.

    A gh that answered `null` - an empty `--jq` result reads as one - would
    otherwise send `head_sha=null` and get back a query matching nothing,
    which arrives as `none` and reads as a Proposal whose CI never ran.
    """
    done = run("checks", PROPOSAL_URL, sha="null")

    assert done.returncode == 1
    assert "head commit" in done.stderr
    assert not [line for line in done.gh_argv.splitlines()
                if line.startswith("api ")]


def test_every_completed_run_green_is_green(run):
    """`neutral` and `skipped` are passes, the same as `success`."""
    done = run("checks", PROPOSAL_URL, runs=page(
        ("Tourbot CI", "completed", "success"),
        ("ADR numbering", "completed", "skipped"),
        ("lint", "completed", "neutral"),
    ))

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "green", "failing": []}


def test_a_failed_run_is_red_and_named(run):
    """Red names the workflows: the comment cycle.py writes quotes them."""
    done = run("checks", PROPOSAL_URL, runs=page(
        ("Tourbot CI", "completed", "failure"),
        ("ADR numbering", "completed", "success"),
    ))

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "state": "red", "failing": ["Tourbot CI"],
    }


def test_an_unfinished_run_is_pending(run):
    done = run("checks", PROPOSAL_URL, runs=page(
        ("Tourbot CI", "in_progress", None),
        ("ADR numbering", "completed", "success"),
    ))

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "pending", "failing": []}


def test_red_outranks_pending(run):
    """A Proposal with one failure is red now, not pending until the rest land.

    Waiting for the stragglers would spend the whole checks timeout to reach
    an answer already known, and the answer is not green either way.
    """
    done = run("checks", PROPOSAL_URL, runs=page(
        ("Tourbot CI", "completed", "failure"),
        ("ADR numbering", "queued", None),
    ))

    assert json.loads(done.stdout) == {
        "state": "red", "failing": ["Tourbot CI"],
    }


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "stale",
                                        "action_required", "startup_failure",
                                        "vendor_invented_this_one"])
def test_an_unrecognised_conclusion_is_red(run, conclusion):
    """Red is the fallthrough. An unknown conclusion must never read as a pass.

    `stale` is in the list on purpose: GitHub marks a run stale when the
    commit it vouched for is no longer what the branch points at, which is
    precisely a verdict that no longer applies to this Proposal.
    """
    done = run("checks", PROPOSAL_URL,
               runs=page(("Tourbot CI", "completed", conclusion)))

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "state": "red", "failing": ["Tourbot CI"],
    }


def test_completed_with_no_conclusion_is_pending_not_red(run):
    """A run with no conclusion has not been decided, whatever its status says.

    The API reports `completed` with a null conclusion briefly, and red here
    would be a FALSE red rather than a safe one: red is terminal - `cycle.py`
    comments and swaps to `ready-for-human` - while only `pending` is polled
    again, so the transient would resolve itself on the next read.
    """
    done = run("checks", PROPOSAL_URL,
               runs=page(("Tourbot CI", "completed", None)))

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "pending", "failing": []}


def test_no_runs_at_all_is_none_not_green(run):
    """"No check ran" and "every check passed" are opposite facts.

    On a repository that has CI - which tourbot does - no runs for the head
    commit means something went wrong upstream, so it gets its own state and
    `cycle.py` hands it to the operator.
    """
    done = run("checks", PROPOSAL_URL, runs=page())

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"state": "none", "failing": []}


def test_pages_are_read_whole(run):
    """`--paginate` concatenates one JSON object per page; all of them count.

    A repository with more than a page of runs on one commit would otherwise
    be graded on its first hundred, and a failure on page two would ship as
    green.
    """
    two_pages = "\n".join([
        page(("Tourbot CI", "completed", "success")),
        page(("ADR numbering", "completed", "failure")),
    ])
    done = run("checks", PROPOSAL_URL, runs=two_pages)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "state": "red", "failing": ["ADR numbering"],
    }


def test_a_refused_runs_read_is_loud(run):
    """Fail closed. An API failure is not "no checks", and not green.

    This is the whole reason the script does its own translation: an
    `|| true` here would route unverified work to `awaiting-review` the
    moment a token loses a permission.
    """
    done = run("checks", PROPOSAL_URL, api_rc=1, runs="",
               gh_err="Resource not accessible by personal access token")

    assert done.returncode == 1
    assert "not accessible" in done.stderr
    assert "green" not in done.stdout


def test_a_refused_proposal_read_is_loud(run):
    """The same, one call earlier: no head commit, no verdict."""
    done = run("checks", PROPOSAL_URL, pr_rc=1, sha="",
               gh_err="Could not resolve to a PullRequest")

    assert done.returncode == 1
    assert "could not resolve" in done.stderr.lower()
    assert not done.stdout


# --- update-branch ----------------------------------------------------------

def test_update_branch_accepts_a_proposal_url(run):
    done = run("update-branch", PROPOSAL_URL)
    assert done.returncode == 0, done.stderr
    assert f"pr update-branch {PROPOSAL_URL} --repo {REPO}" in done.gh_argv


def test_update_branch_accepts_a_bare_number(run):
    done = run("update-branch", "713")
    assert done.returncode == 0, done.stderr
    assert f"pr update-branch 713 --repo {REPO}" in done.gh_argv


def test_update_branch_refuses_invalid_proposal_format(run):
    done = run("update-branch", "loop/712-the-spelling")
    assert done.returncode == 1
    assert "proposal" in done.stderr.lower()
    assert not done.gh_argv


def test_update_branch_refuses_foreign_repo_url(run):
    done = run("update-branch", "https://github.com/someone/else/pull/1")
    assert done.returncode == 1
    assert not done.gh_argv


def test_update_branch_fails_when_gh_refuses(run):
    done = run("update-branch", "713", pr_rc=1, gh_err="GitHub refused update-branch")
    assert done.returncode == 1
    assert "refused" in done.stderr.lower()

