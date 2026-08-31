import json
import os
import subprocess
import sys
import textwrap
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

    The writing itself is `testdb.append_run`, shared with the dashboard
    suite: the page's budget cell and the Selector's cap are supposed to be
    reading the same rows, so there is one definition of what those rows are.
    """
    return testdb.append_run


# --- Canned tracker records -------------------------------------------------
#
# Shared with the dashboard suite behind the queue board, which drives the
# same Eligibility predicate over the same records: `../fixtures.py`. Re-
# exported here so the suites that already read them keep reading them by
# name.

from fixtures import BODY, hours_ago_iso, issue  # noqa: E402,F401


# --- The dry-run harness ----------------------------------------------------
#
# A canned tracker and a tripwire PATH: `gh`, `git`, `ssh` and `seed-run.sh`
# shimmed to log and fail. Shared, because "a dry run reaches the tracker and
# nothing else" is a property every suite that drives one should be able to
# assert rather than one that happens to own the fixture.

@pytest.fixture
def fakes(tmp_path):
    """A fake tracker command plus a tripwire PATH; returns a runner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tripped = tmp_path / "tripped.log"

    for name in ("gh", "git", "ssh", "seed-run.sh"):
        shim = bin_dir / name
        shim.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s %s\\n" "{name}" "$*" >> "{tripped}"\n'
            "exit 1\n"
        )
        shim.chmod(0o755)

    queue_file = tmp_path / "queue.json"
    tracker = tmp_path / "tracker.sh"
    tracker.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s %s\\n" "$1" "$2" > "{tmp_path}/tracker.args"\n'
        f'exec cat "{queue_file}"\n'
    )
    tracker.chmod(0o755)

    class Runner:
        args_file = tmp_path / "tracker.args"

        def run(self, dsn, issues=(), *, tracker_command=None,
                dry_run=True, **env):
            queue_file.write_text(json.dumps({"issues": list(issues)}))
            environ = dict(os.environ)
            environ.update(
                {
                    "PATH": f"{bin_dir}:{environ['PATH']}",
                    "SELECTOR_JOURNAL_DSN": dsn,
                    "SELECTOR_TRACKER_COMMAND": str(tracker_command or tracker),
                    "SELECTOR_TASK_REPO": "acme/widgets",
                    "SELECTOR_LABELER_ALLOWLIST": "JacobStephens2",
                }
            )
            environ.update({k: str(v) for k, v in env.items()})
            argv = ["--dry-run"] if dry_run else []
            return subprocess.run(
                [sys.executable, str(CYCLE), *argv],
                capture_output=True,
                text=True,
                env=environ,
            )

        def tripped(self):
            return tripped.read_text() if tripped.exists() else ""

    return Runner()



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

# What the box-facts command answers with: the four things the box card on
# /loop is built from (#156, #260). key=value lines, because that is the shape
# the box already answers a Run in (LOOP_RUN_*) and a second parser earns
# nothing.
#
# The template and the agent are deliberately DIFFERENT strings. They were both
# `claude` until #164 made the guest an image, and that made the fixture unable
# to fail: a cycle that journalled the agent into the template's field - or the
# other way round - passed every assertion here. Keep them distinguishable.
#
# The credential expiry is a fixed instant far in the past, and that is not an
# oversight. Every cycle in this suite journals it, so a value computed from
# `now` would make these fixtures depend on when they ran; the tests that care
# whether an expired credential renders differently construct their own
# instant, and the tests that do not care want a value that never moves.
BOX_FACTS = """LOOP_BOX_SCRIPTS_HASH=8c1f3a90d2
LOOP_BOX_GUEST_TEMPLATE=loop-php:1
LOOP_BOX_AGENT=claude
LOOP_BOX_AGENT_VERSION=2.1.221 (Claude Code)
LOOP_BOX_CREDENTIAL_EXPIRES_AT=2026-08-30T01:13:44Z
"""

# What the guardrail command answers with: the write protection standing over
# the paths the Loop and the Selector execute (#165). Same key=value shape as
# the box facts, for the same reason - the Selector already parses one, and a
# second parser would earn nothing.
#
# The default is the protected state, because every dispatching cycle reads
# this: a fixture that answered "unprotected" would put the whole suite's
# cycles into a state only one file is about.
GUARDRAIL = """SELECTOR_GUARDRAIL_REF=master
SELECTOR_GUARDRAIL_REF_HEAD=44a596d0a1b2
SELECTOR_GUARDRAIL_RULES=deletion,non_fast_forward,pull_request
SELECTOR_GUARDRAIL_PATHS=lab/single-user-factory/loop,lab/single-user-factory/selector
SELECTOR_GUARDRAIL_UNREVIEWED=
"""

# What the issue command's `checks` action answers with. The shape is the
# Selector's, not GitHub's: the real github.sh translates GitHub's workflow
# runs into it, which is what keeps the translation in the substitutable
# script rather than in cycle.py.
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
    """A work checkout with a real bare remote, plus the scripted commands a
    dispatch reaches - Seeding, the box, the issue writes, the box facts and
    the Progress Log the watcher reads. Returns a runner."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    log = tmp_path / "commands.log"
    summary = tmp_path / "run-summary.txt"
    summary.write_text(CLEAN_RUN)
    checks_file = tmp_path / "checks.json"
    checks_file.write_text(GREEN_CHECKS)
    facts_file = tmp_path / "box-facts.txt"
    facts_file.write_text(BOX_FACTS)
    guardrail_file = tmp_path / "guardrail.txt"
    guardrail_file.write_text(GUARDRAIL)
    progress_file = tmp_path / "PROGRESS.md"
    progress_file.write_text(
        "# Progress Log\n\nSeeded by seed-run.sh. No Iteration has run yet.\n"
    )

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
            .replace("@FACTS@", str(facts_file))
            .replace("@GUARDRAIL@", str(guardrail_file))
            .replace("@PROGRESS@", str(progress_file))
            .replace("@GUEST@", str(tmp_path / "guest"))
        )

    # Seeding writes BOTH files, and refuses to overwrite a Progress Log that
    # already records a Run, because the real seed-run.sh does (exit 2). A fake
    # that wrote only the Plan is what let #301 through: in production every
    # second dispatch of an issue whose first attempt ran meets that refusal,
    # and nothing here could see it.
    seed = _script(tmp_path / "seed.sh", fill('''
        printf 'seed %s\n' "$*" >> "@LOG@"
        repo=""
        reseed=false
        while (($# > 0)); do
            case "$1" in
                --repo) repo="$2"; shift 2 ;;
                --reseed) reseed=true; shift ;;
                *) shift ;;
            esac
        done
        if ! ${reseed} && [[ -f "${repo}/PROGRESS.md" ]] &&
            grep -q '^## Run started' "${repo}/PROGRESS.md"; then
            printf 'seed-run.sh: PROGRESS.md already records 1 Run(s) and 1 Iteration(s).\n' >&2
            printf 'Seeding rewrites it. Pass --reseed to discard that record, or seed a fresh checkout.\n' >&2
            exit 2
        fi
        printf 'a Plan the Selector seeded\n' > "${repo}/PLAN.md"
        printf '# Progress Log\n\nSeeded by seed-run.sh. No Iteration has run yet.\n' \
            > "${repo}/PROGRESS.md"
        git -C "${repo}" add PLAN.md PROGRESS.md
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
        # A hook for the concurrency tests: whatever NESTED_CYCLE names is run
        # WHILE this Run is in flight, which is the only moment a second cycle
        # can meet the first. Nothing sets it unless a test does.
        if [[ -n ${NESTED_CYCLE:-} ]]; then
            {
                printf 'nested-begin\n'
                "${NESTED_CYCLE}"
                printf 'nested-exit %s\n' "$?"
            } >> "@LOG@" 2>&1
        fi
        # The Run's own bookkeeping, committed and pushed to the branch the way
        # run.sh does it - `commit_bookkeeping "Loop: Run started"`, and then
        # the proposal's push. A box that left the branch exactly as it found
        # it is the fake that hid #301: the second dispatch of an issue then
        # met a Progress Log no Run had ever written to, which is a state a
        # real retry never meets.
        #
        # Only when a Run was actually reported: a box that never connected
        # starts nothing, so it writes nothing.
        if grep -q '^LOOP_RUN_ENDED_BY=' "@SUMMARY@"; then
            rm -rf "@GUEST@"
            git clone --quiet --branch "$1" "@BARE@" "@GUEST@"
            {
                printf '\n## Run started 2026-08-31 00:00:00\n\nTask: %s\n\n' "$2"
                printf '### Iteration 1\n\nWhat the first attempt tried.\n\n'
            } >> "@GUEST@/PROGRESS.md"
            git -C "@GUEST@" -c user.email=box@example.invalid -c user.name="The Box" \
                commit --quiet -a -m "Loop: Run started"
            git -C "@GUEST@" push --quiet origin "$1"
        fi
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

    # Scripted here rather than in the one suite that asserts on it, because
    # every dispatch reads it: a fixture that left it unset would send the
    # whole suite at the real box over real SSH.
    facts_command = _script(tmp_path / "facts.sh", fill('''
        printf 'facts %s\n' "$*" >> "@LOG@"
        exec cat "@FACTS@"
    '''))

    # Scripted here for the reason the facts command is: every dispatching
    # cycle reads the guardrail, so a fixture that left it unset would send the
    # whole suite at the real `gh` and at this repository's own checkout.
    guardrail_command = _script(tmp_path / "guardrail.sh", fill('''
        printf 'guardrail %s\n' "$*" >> "@LOG@"
        exec cat "@GUARDRAIL@"
    '''))

    # The Progress Log the Iteration watcher reads while a Run is in flight
    # (#157). Scripted here for the same reason as the facts command: every
    # dispatch starts a watcher, so a fixture that left this unset would point
    # the whole suite at the real box over real SSH. The default snapshot is a
    # seeded log with no Iteration in it, so a suite that is not about the
    # watcher sees exactly the rows it saw before.
    progress_command = _script(tmp_path / "progress.sh", fill('''
        printf 'progress %s\n' "$*" >> "@LOG@"
        exec cat "@PROGRESS@"
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
                    "SELECTOR_BOX_FACTS_COMMAND": str(facts_command),
                    "SELECTOR_BOX_PROGRESS_COMMAND": str(progress_command),
                    "SELECTOR_GUARDRAIL_COMMAND": str(guardrail_command),
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

        def facts(self, text):
            facts_file.write_text(text)

        def guardrail(self, text):
            guardrail_file.write_text(text)

        def progress(self, text):
            progress_file.write_text(text)

        def facts_command(self, body):
            """Replace the whole facts script - for the box that cannot be
            read at all, which is a failing command rather than odd output."""
            return _script(tmp_path / "facts.sh", body)

        def guardrail_command(self, body):
            """Replace the whole guardrail script - for the protection that
            cannot be read at all, which is a failing command rather than odd
            output."""
            return _script(tmp_path / "guardrail.sh", body)

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
    runner.progress_file = progress_file
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


