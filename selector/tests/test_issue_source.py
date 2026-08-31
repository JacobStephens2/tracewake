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

# One green row, so a call that reaches `gh` at all produces a state the
# caller can assert on. The shape is gh's, not the Selector's: translating
# between them is what the script is for.
GREEN_ROWS = json.dumps([{"name": "unit", "state": "SUCCESS"}])


@pytest.fixture
def run(tmp_path):
    """The script, with a fake `gh` that records the arguments it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv"

    def go(*args, gh_rows=GREEN_ROWS, stdin=None):
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {argv_log}\n"
            f"cat <<'JSON'\n{gh_rows}\nJSON\n"
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

    This is the regression. `gh pr checks` takes a URL perfectly well, and
    `cycle.py` has only the Proposal's URL to hand - the number is never
    parsed out of it anywhere - so a guard that demands a number here breaks
    the one path that runs after every successful Run.
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

    `gh pr checks` would take a branch name, and taking one here would mean
    the Selector could read the checks of a Proposal other than the one the
    Run produced - a green light sourced from the wrong place.
    """
    done = run("checks", "loop/712-the-spelling")

    assert done.returncode == 1
    assert "proposal" in done.stderr.lower()
    assert not done.gh_argv


def test_a_url_for_another_repository_is_refused(run):
    """The Selector reads the checks of ITS repository's Proposals.

    `gh pr checks <url>` resolves the repository from the URL and ignores
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
