"""`tracker-sources/github.sh`, driven directly.

The rest of the suite replaces this script with a canned queue, which is
what lets the real script's GraphQL pagination contract go unexercised.
These tests drive the real script with `gh` faked on PATH.

The contract under test is the cursor variable's NAME: `gh api graphql
--paginate` advances pagination by injecting a variable called exactly
`$endCursor` between pages. A query that names it anything else (`$cursor`)
is silently never advanced, so every page request fetches page one again -
an unbounded identical-page loop that ends when GitHub 504s it or jq runs
out of memory. A 72-issue queue once streamed hundreds of megabytes this
way and killed a cycle.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SELECTOR = Path(__file__).resolve().parents[1]
GITHUB = SELECTOR / "tracker-sources" / "github.sh"

PAGE = {
    "data": {
        "repository": {
            "isArchived": False,
            "issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": "cursor-1"},
                "nodes": [
                    {
                        "number": 7,
                        "title": "Do the thing",
                        "url": "https://github.com/acme/widgets/issues/7",
                        "state": "OPEN",
                        "body": "The body",
                        "timelineItems": {
                            "nodes": [
                                {
                                    "createdAt": "2026-08-26T12:00:00Z",
                                    "label": {"name": "ready-for-agent"},
                                    "actor": {"login": "an-operator"},
                                }
                            ]
                        },
                        "issueDependenciesSummary": {"blockedBy": 0},
                        "blockedBy": {"nodes": []},
                        "subIssues": {"nodes": []},
                        "closedByPullRequestsReferences": {"nodes": []},
                    }
                ],
            }
        }
    }
}


@pytest.fixture
def run(tmp_path):
    """The script, with a fake `gh` that records the arguments it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv"

    def go(*args, page=PAGE, gh_rc=0, gh_err=""):
        hits = json.dumps(page)
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
            capture_output=True, text=True, env=env, timeout=120,
        )
        done.gh_argv = argv_log.read_text() if argv_log.exists() else ""
        return done

    return go


def test_the_query_advances_through_ghs_paginate_cursor(run):
    """The cursor variable must be `$endCursor`: that is the one name
    `gh api graphql --paginate` injects between pages."""
    done = run("acme/widgets", "ready-for-agent")

    assert done.returncode == 0, done.stderr
    argv = done.gh_argv
    assert "api graphql" in argv
    assert "--paginate" in argv
    assert "$endCursor: String" in argv
    assert "after: $endCursor" in argv
    assert "$cursor" not in argv.replace("$endCursor", "")
    assert "isArchived" in argv


def test_one_page_of_hits_normalizes_to_the_queue_shape(run):
    done = run("acme/widgets", "ready-for-agent")

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "archived": False,
        "issues": [{
            "number": 7,
            "title": "Do the thing",
            "url": "https://github.com/acme/widgets/issues/7",
            "state": "OPEN",
            "body": "The body",
            "labeledBy": "an-operator",
            "labeledAt": "2026-08-26T12:00:00Z",
            "blockedBy": 0,
            "blockers": [],
            "openSubIssues": 0,
            "proposals": [],
        }]
    }


def test_an_archived_repository_yields_an_empty_queue(run):
    """GitHub makes an archived repository read-only. Listing its labeled
    issues would hand the Selector work it cannot comment on, relabel, or
    open a Proposal for."""
    archived = {
        "data": {
            "repository": {
                "isArchived": True,
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": "cursor-1"},
                    "nodes": PAGE["data"]["repository"]["issues"]["nodes"],
                },
            }
        }
    }
    done = run("acme/widgets", "ready-for-agent", page=archived)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"archived": True, "issues": []}


def test_a_github_refusal_fails_closed(run):
    # gh_rc=4 is what `gh api` itself exits with on an HTTP refusal, as the
    # production 504 showed. The script propagates the failure rather than
    # shipping jq's output as a queue.
    done = run("acme/widgets", "ready-for-agent", gh_rc=4, gh_err="gh: HTTP 504")

    assert done.returncode != 0
    assert "504" in done.stderr


def test_a_malformed_slug_is_refused(run):
    done = run("not-a-slug", "ready-for-agent")

    assert done.returncode == 1
    assert "owner/name" in done.stderr
    assert not done.gh_argv
