"""`search-sources/github.sh`, driven directly.

The rest of the suite replaces this script with a canned list, which is what
lets the owner-wide search's own argument handling go unexercised. These
tests drive the real script with `gh` faked on PATH, and they are also the
pin that the warning path is a read: a `gh` invocation that wrote would
fail them.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SELECTOR = Path(__file__).resolve().parents[1]
GITHUB = SELECTOR / "search-sources" / "github.sh"

HITS = json.dumps([
    {
        "repository": {"nameWithOwner": "acme/other"},
        "number": 7,
        "title": "Do the thing",
        "url": "https://github.com/acme/other/issues/7",
    }
])


@pytest.fixture
def run(tmp_path):
    """The script, with a fake `gh` that records the arguments it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv"

    def go(*args, hits=HITS, gh_rc=0, gh_err=""):
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {argv_log}\n"
            f"printf '%s' {json.dumps(gh_err)} >&2\n"
            f"cat <<'JSON'\n{hits}\nJSON\n"
            f"exit {gh_rc}\n"
        )
        gh.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        done = subprocess.run(
            [str(GITHUB), *args],
            capture_output=True, text=True, env=env,
        )
        done.gh_argv = argv_log.read_text() if argv_log.exists() else ""
        return done

    return go


def test_the_search_lists_labeled_issues_across_the_owner(run):
    done = run("acme", "ready-for-agent")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "issues": [{
            "repo": "acme/other",
            "number": 7,
            "title": "Do the thing",
            "url": "https://github.com/acme/other/issues/7",
        }]
    }
    argv = done.gh_argv
    assert argv.startswith("search issues")
    assert "--owner acme" in argv
    assert "--label ready-for-agent" in argv
    assert "--state open" in argv


def test_zero_hits_is_an_empty_list_not_a_failure(run):
    done = run("acme", "ready-for-agent", hits="[]")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"issues": []}


def test_a_github_refusal_fails_closed(run):
    done = run("acme", "ready-for-agent", gh_rc=1, gh_err="API rate limit")

    assert done.returncode == 1
    assert "refused" in done.stderr.lower() or "rate limit" in done.stderr.lower()


def test_a_repository_slug_is_refused_as_an_owner(run):
    done = run("acme/widgets", "ready-for-agent")

    assert done.returncode == 1
    assert "owner" in done.stderr.lower()
    assert not done.gh_argv


def test_the_search_never_writes(run):
    """The warning path is a list. A comment or a label swap here would be
    the Selector mutating a repository it has not been enrolled to work."""
    done = run("acme", "ready-for-agent")

    assert done.returncode == 0, done.stderr
    argv = done.gh_argv
    assert "issue comment" not in argv
    assert "issue edit" not in argv
    assert "pr " not in argv
