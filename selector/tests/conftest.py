import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import pytest
from psycopg.types.json import Jsonb

import journal
import testdb


@pytest.fixture
def db():
    """DSN of a throwaway schema-loaded database on the local Postgres."""
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")
    with testdb.throwaway_db() as dsn:
        yield dsn


@pytest.fixture
def dispatch():
    """Append a dispatch (and optionally its outcome) to a test Journal.

    Backdating needs an explicit `at`, which an INSERT may set and no UPDATE
    ever could - the table is append-only, so a test that wants history writes
    history rather than editing it.
    """
    def append(dsn, number, *, outcome=None, hours_ago=0):
        with psycopg.connect(dsn, autocommit=True) as conn:
            for kind, payload in (
                ("run.dispatched", {"issue": number}),
                ("run.outcome", {"issue": number, "outcome": outcome}),
            ):
                if kind == "run.outcome" and outcome is None:
                    continue
                conn.execute(
                    "INSERT INTO journal.events (at, kind, payload)"
                    " VALUES (now() - make_interval(hours => %s), %s, %s)",
                    (hours_ago, kind, Jsonb(payload)),
                )
    return append


# --- Canned tracker records -------------------------------------------------
#
# Shared by the cycle suite and the dispatch suite, which drive the same
# `cycle.py` through the same tracker seam and differ only in what they let it
# reach afterwards. One definition of "an eligible issue" so that a change to
# what Eligibility needs breaks both suites rather than one.

BODY = """## Problem

Something is wrong.

## Acceptance criteria

- [ ] It is right

## Owning area

The nightly sync script
"""


def hours_ago_iso(hours):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(number, **over):
    """One tracker record, eligible unless a field is overridden."""
    record = {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.invalid/{number}",
        "state": "OPEN",
        "body": BODY,
        "labeledBy": "JacobStephens2",
        "labeledAt": None,
        "blockedBy": 0,
        "openSubIssues": 0,
        "proposals": [],
    }
    record.update(over)
    return record


# --- The dispatch harness ---------------------------------------------------
#
# Moved here from the dispatch suite when a third suite (outcome routing)
# needed the same box. One definition of "a scripted box, a scripted Seeding
# step, a scripted issue command and a REAL git remote", so that a change to
# what a dispatch reaches breaks every suite that drives one rather than the
# one that happens to own the fixture.

CLEAN_RUN = """LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/12

The Run ended at its iteration cap.
"""

FAILED_RUN = """LOOP_RUN_ENDED_BY=agent-failed
LOOP_RUN_EXIT=4
LOOP_RUN_ITERATIONS=1
LOOP_RUN_FAULTS=agent-failed
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/13
"""

# What the issue command's `checks` action answers with. The shape is the
# Selector's, not GitHub's: the real github.sh translates `gh pr checks` into
# it, which is what keeps the translation in the substitutable script rather
# than in cycle.py.
GREEN_CHECKS = '{"state": "green", "failing": []}\n'
RED_CHECKS = '{"state": "red", "failing": ["phpunit", "lint"]}\n'
PENDING_CHECKS = '{"state": "pending", "failing": []}\n'
# No check ran against the Proposal at all - its own answer, deliberately not
# a flavour of green.
NO_CHECKS = '{"state": "none", "failing": []}\n'

SELECTOR = Path(__file__).resolve().parents[1]
CYCLE = SELECTOR / "cycle.py"

CLEAN_RUN = """LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/12

The Run ended at its iteration cap.
"""

FAILED_RUN = """LOOP_RUN_ENDED_BY=agent-failed
LOOP_RUN_EXIT=4
LOOP_RUN_ITERATIONS=1
LOOP_RUN_FAULTS=agent-failed
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/13
"""


def _script(path, body):
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path):
    """A work checkout with a real bare remote, plus the three scripted
    commands a dispatch reaches. Returns a runner."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    log = tmp_path / "commands.log"
    summary = tmp_path / "run-summary.txt"
    summary.write_text(CLEAN_RUN)
    checks_file = tmp_path / "checks.json"
    checks_file.write_text(GREEN_CHECKS)

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd or work), check=True,
            capture_output=True, text=True,
        ).stdout

    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "master", str(bare)],
                   check=True)
    subprocess.run(["git", "clone", "--quiet", str(bare), str(work)], check=True)
    git("config", "user.email", "operator@example.invalid")
    git("config", "user.name", "The Operator")
    git("config", "commit.gpgsign", "false")
    (work / "README.md").write_text("the work repository\n")
    git("add", "README.md")
    git("commit", "--quiet", "-m", "First commit")
    git("push", "--quiet", "-u", "origin", "master")
    git("remote", "set-head", "origin", "--auto")

    def fill(template):
        """@LOG@-style placeholders rather than %-formatting: these scripts are
        full of printf format strings, and %s meaning two things in one file is
        a fixture that breaks in a way nothing in it explains."""
        return (
            template.replace("@LOG@", str(log))
            .replace("@BARE@", str(bare))
            .replace("@SUMMARY@", str(summary))
            .replace("@CHECKS@", str(checks_file))
        )

    seed = _script(tmp_path / "seed.sh", fill('''
        printf 'seed %s\n' "$*" >> "@LOG@"
        repo=""
        while (($# > 0)); do
            case "$1" in
                --repo) repo="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        printf 'a Plan the Selector seeded\n' > "${repo}/PLAN.md"
        git -C "${repo}" add PLAN.md
        git -C "${repo}" commit --quiet -m "Loop: Seed the Run" || true
        printf 'LOOP_SEED_RESULT=seeded\nLOOP_SEED_CRITERIA=3\n'
        exit "${SEED_EXIT:-0}"
    '''))

    box_command = _script(tmp_path / "box.sh", fill('''
        printf 'box %s\n' "$*" >> "@LOG@"
        # What the box would find on the remote when it fetched: the Plan has
        # to be there BEFORE the Run starts, not pushed afterwards.
        printf 'remote-tree %s\n' \
            "$(git --git-dir=@BARE@ ls-tree -r --name-only "$1" 2>&1 | tr '\n' ' ')" \
            >> "@LOG@"
        # And the in-flight lock has to be held while the Run is running.
        printf 'dispatched-rows %s\n' \
            "$(psql "${SELECTOR_JOURNAL_DSN}" -tAc \
                "select count(*) from journal.events where kind = 'run.dispatched'" \
                2>&1)" >> "@LOG@"
        cat "@SUMMARY@"
        exit "${BOX_EXIT:-0}"
    '''))

    issue_command = _script(tmp_path / "issue.sh", fill('''
        {
            printf 'issue %s\n' "$*"
            if [[ ${2:-} == comment ]]; then
                printf -- '--- body ---\n'
                cat
                printf -- '\n--- end ---\n'
            fi
        } >> "@LOG@"
        # `checks` answers from a file the test writes, so a Proposal's CI
        # state is scripted the same way the Run's summary is: the Selector
        # can only have learned it from what this printed.
        #
        # One line per call, consumed as it is read, so a test can script CI
        # CHANGING - pending, then green - and the Selector's polling is
        # driven rather than assumed. The last line stays put, so a file with
        # one line in it is simply a constant answer.
        if [[ ${2:-} == checks ]]; then
            head -n 1 "@CHECKS@"
            if (($(wc -l < "@CHECKS@") > 1)); then
                tail -n +2 "@CHECKS@" > "@CHECKS@.rest"
                mv "@CHECKS@.rest" "@CHECKS@"
            fi
            exit "${CHECKS_EXIT:-0}"
        fi
        exit "${ISSUE_EXIT:-0}"
    '''))

    queue_file = tmp_path / "queue.json"
    tracker = _script(tmp_path / "tracker.sh", f'exec cat "{queue_file}"\n')

    class Runner:
        # Assigned after the class body: `bare = bare` inside it would read
        # the class-local name, not the fixture's.
        def run(self, dsn, issues=(), *, dry_run=False, **env):
            queue_file.write_text(json.dumps({"issues": list(issues)}))
            environ = dict(os.environ)
            environ.update(
                {
                    "SELECTOR_JOURNAL_DSN": dsn,
                    "SELECTOR_TRACKER_COMMAND": str(tracker),
                    "SELECTOR_TASK_REPO": "acme/widgets",
                    "SELECTOR_LABELER_ALLOWLIST": "JacobStephens2",
                    "SELECTOR_WORK_REPO": str(work),
                    "SELECTOR_SEED_COMMAND": str(seed),
                    "SELECTOR_BOX_COMMAND": str(box_command),
                    "SELECTOR_ISSUE_COMMAND": str(issue_command),
                }
            )
            environ.update({k: str(v) for k, v in env.items()})
            return subprocess.run(
                [sys.executable, str(CYCLE), *(["--dry-run"] if dry_run else [])],
                capture_output=True, text=True, env=environ,
            )

        def commands(self):
            return log.read_text() if log.exists() else ""

        def run_summary(self, text):
            summary.write_text(text)

        def checks(self, text):
            checks_file.write_text(text)

        def git(self, *args, cwd=None):
            return git(*args, cwd=cwd)

        def remote_branches(self):
            out = subprocess.run(
                ["git", "--git-dir", str(bare), "for-each-ref",
                 "--format=%(refname:short)", "refs/heads/"],
                capture_output=True, text=True, check=True,
            ).stdout
            return out.split()

    runner = Runner()
    runner.bare = bare
    runner.work = work
    return runner


def events(dsn, kind=None):
    """Journal rows oldest first - the order the cycle wrote them."""
    with journal.connect(dsn) as conn:
        rows = list(reversed(journal.events(conn)))
    return [r for r in rows if kind is None or r["kind"] == kind]


def one(dsn, kind):
    rows = events(dsn, kind)
    assert rows, f"no {kind} row in the Journal"
    return rows[0]["payload"]


def last(dsn, kind):
    """The newest row of a kind - for the tests that seed history first."""
    rows = events(dsn, kind)
    assert rows, f"no {kind} row in the Journal"
    return rows[-1]["payload"]


