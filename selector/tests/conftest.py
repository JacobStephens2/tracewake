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

# The end-of-run report on throwaway databases this run failed to drop
# (#178). Imported rather than restated: one definition of what a leak is.
from testdb import pytest_terminal_summary  # noqa: F401


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

from fixtures import ALLOWLISTED_OPERATOR, BODY, TARGET_REPO  # noqa: E402,F401
from fixtures import hours_ago_iso, issue, write_targets  # noqa: E402,F401

# A configured instance, for the harnesses that run the real cycle.py.
# Preflight refuses these by name before the tracker is read (issue #3), so a
# suite that left them unset would be testing the refusal in every test. The
# values are unreachable on purpose: every command that would use them is
# scripted, and a real one must fail rather than reach a real machine.
INSTANCE_ENV = {
    "SELECTOR_BOX_HOST": "root@box.invalid",
    "SELECTOR_PROTECTED_REPO": "acme/tracewake",
    "SELECTOR_PROTECTED_REF": "main",
    "SELECTOR_SEARCH_OWNER": "acme",
}


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
        "set -euo pipefail\n"
        f'if [[ ! -f "{tmp_path}/tracker.args" ]]; then\n'
        f'    printf "%s %s\\n" "$1" "$2" > "{tmp_path}/tracker.args"\n'
        f'fi\n'
        f'printf "%s %s\\n" "$1" "$2" >> "{tmp_path}/tracker.log"\n'
        f'queue="{tmp_path}/queue-${{2}}.json"\n'
        f'if [[ -f "${{queue}}" ]]; then\n'
        f'    exec cat "${{queue}}"\n'
        f'fi\n'
        f'if [[ "${{2}}" != "awaiting-review" ]]; then\n'
        f'    exec cat "{queue_file}"\n'
        f'fi\n'
        f'printf \'{{"issues": []}}\\n\'\n'
    )
    tracker.chmod(0o755)

    search_file = tmp_path / "search.json"
    search_file.write_text('{"issues": []}\n')
    search = tmp_path / "search.sh"
    search.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s %s\\n" "$1" "$2" >> "{tmp_path}/search.log"\n'
        f'if [[ ! -f "{tmp_path}/search.args" ]]; then\n'
        f'    printf "%s %s\\n" "$1" "$2" > "{tmp_path}/search.args"\n'
        f'fi\n'
        f'exec cat "{search_file}"\n'
    )
    search.chmod(0o755)

    class Runner:
        args_file = tmp_path / "tracker.args"
        search_args = tmp_path / "search.args"

        targets_file = tmp_path / "targets.toml"

        def run(self, dsn, issues=(), *, review_issues=(), owner_issues=(),
                tracker_command=None, search_command=None,
                dry_run=True, targets=None, select=None, archived=False,
                **env):
            """One cycle against a canned queue.

            `targets` is a list of stanza overrides when a test cares what
            the target declares; without it one default target is written,
            because there is no longer any way to name a repository except
            through the targets file (issue #3).

            `archived` is the tracker payload's repository flag. The canned
            command does not empty the issue list when it is set: leaking
            the records is how the cycle suite checks that the Cycle itself
            refuses to operate, rather than only the GitHub adapter.
            """
            payload = {"issues": list(issues)}
            if archived:
                payload["archived"] = True
            queue_file.write_text(json.dumps(payload))
            search_file.write_text(json.dumps({"issues": list(owner_issues)}))
            stanzas = targets or ()
            review_label = "awaiting-review"
            if stanzas and isinstance(stanzas, (list, tuple)) and len(stanzas) > 0:
                review_label = stanzas[0].get("labels", {}).get("review", "awaiting-review")
            (tmp_path / f"queue-{review_label}.json").write_text(
                json.dumps({"issues": list(review_issues)})
            )
            write_targets(self.targets_file, *stanzas)
            environ = dict(os.environ)
            environ.update(
                {
                    "PATH": f"{bin_dir}:{environ['PATH']}",
                    "SELECTOR_JOURNAL_DSN": dsn,
                    "SELECTOR_TRACKER_COMMAND": str(tracker_command or tracker),
                    "SELECTOR_SEARCH_COMMAND": str(search_command or search),
                    "TRACEWAKE_TARGETS_FILE": str(self.targets_file),
                    **INSTANCE_ENV,
                }
            )
            environ.update({k: str(v) for k, v in env.items()})
            argv = ["--dry-run"] if dry_run else []
            if select:
                argv += ["--target", select]
            return subprocess.run(
                [sys.executable, str(CYCLE), *argv],
                capture_output=True,
                text=True,
                env=environ,
            )

        def queue(self, label, issues):
            (tmp_path / f"queue-{label}.json").write_text(
                json.dumps({"issues": list(issues)})
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

# The first Iteration's rendered briefing, as run.sh emits it on stdout
# (#80): prose between two markers, last on the stream so the LOOP_RUN_*
# block stays first. Every scripted box answers with it, so the dispatch
# suite journals the row the way a real Run does. Short like the summaries,
# but shaped like the real thing: file paths, the checklist naming the
# discipline skills, and the completion promise - and no credentials.
RUN_BRIEFING_TEXT = """You are Iteration 1 of at most 5 in an unattended Run.
You have no memory of earlier Iterations. Everything you know is on disk.

1. Read PLAN.md for the task and what remains of it.
2. Read PROGRESS.md for what earlier Iterations already did.

Work it in the discipline the repository's own skills define, invoking them
by name for yourself: /tdd for code work, /diagnosing-bugs for something broken or slow, /code-review before every commit.

LOOP: WORK COMPLETE"""
RUN_BRIEFING_BLOCK = (
    "LOOP_BRIEFING_BEGIN\n" + RUN_BRIEFING_TEXT + "\nLOOP_BRIEFING_END\n"
)

CLEAN_RUN = """LOOP_RUN_ENDED_BY=iteration-cap
LOOP_RUN_EXIT=0
LOOP_RUN_ITERATIONS=5
LOOP_RUN_FAULTS=none
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/12

The Run ended at its iteration cap.
""" + RUN_BRIEFING_BLOCK

FAILED_RUN = """LOOP_RUN_ENDED_BY=agent-failed
LOOP_RUN_EXIT=4
LOOP_RUN_ITERATIONS=1
LOOP_RUN_FAULTS=agent-failed
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/13
""" + RUN_BRIEFING_BLOCK

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
SELECTOR_GUARDRAIL_PATHS=loop,selector
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
""" + RUN_BRIEFING_BLOCK

FAILED_RUN = """LOOP_RUN_ENDED_BY=agent-failed
LOOP_RUN_EXIT=4
LOOP_RUN_ITERATIONS=1
LOOP_RUN_FAULTS=agent-failed
LOOP_RUN_PROPOSAL=proposed
LOOP_RUN_NOTIFIED=sent
LOOP_PROPOSE_URL=https://github.invalid/acme/widgets/pull/13
""" + RUN_BRIEFING_BLOCK


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
    # What the box's reconcile verb answers with. Separate from the Run
    # summary above because one drain can hold both: a reconcile Run reports
    # LOOP_RECONCILE_BRANCH, an ordinary Run reports LOOP_RUN_ENDED_BY, and
    # one file cannot be both answers at once.
    reconcile_summary_file = tmp_path / "reconcile-summary.txt"
    reconcile_summary_file.write_text("LOOP_RECONCILE_BRANCH=\n")
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
    search_file = tmp_path / "search.json"
    search_file.write_text('{"issues": []}\n')
    queue_file = tmp_path / "queue.json"

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
            .replace("@QUEUE@", str(queue_file))
            .replace("@GUEST@", str(tmp_path / "guest"))
            .replace("@SEARCH@", str(search_file))
            .replace("@RSUMMARY@", str(reconcile_summary_file))
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
        # The reconcile verb answers from its own file and exits on its own
        # variable, so a drain holding both a reconcile and an ordinary Run
        # scripts each answer separately. Logged the same way, including the
        # per-target environment the real box command carries across.
        if [[ "${1:-}" == reconcile ]]; then
            printf 'box %s\n' "$*" >> "@LOG@"
            printf 'box-env repo=%s box_repo=%s token=%s guest=%s\n' \
                "${SELECTOR_TASK_REPO:-}" "${SELECTOR_BOX_REPO:-}" \
                "${LOOP_GITHUB_TOKEN_FILE:-}" "${LOOP_GUEST_TEMPLATE:-}" >> "@LOG@"
            cat "@RSUMMARY@"
            exit "${RECONCILE_EXIT:-0}"
        fi
        printf 'box %s\n' "$*" >> "@LOG@"
        # The per-target values, as the box command actually receives them
        # (issue #3). Logged rather than assumed: the box checkout, the
        # repository token and the guest image are read by scripts, so the
        # environment is the seam, and a drain that worked two targets could
        # otherwise hand the second one the first's token with nothing saying
        # so.
        printf 'box-env repo=%s box_repo=%s token=%s guest=%s\n' \
            "${SELECTOR_TASK_REPO:-}" "${SELECTOR_BOX_REPO:-}" \
            "${LOOP_GITHUB_TOKEN_FILE:-}" "${LOOP_GUEST_TEMPLATE:-}" >> "@LOG@"
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
        #
        # Guest dir is per-branch so two concurrent Dispatches (issue #37)
        # cannot share one working tree. BOX_SLEEP holds the process the way
        # a real Run does, so overlap is observable rather than a race that
        # finishes before the other thread starts.
        printf 'box-begin %s\n' "$1" >> "@LOG@"
        if [[ -n ${BOX_SLEEP:-} && ${BOX_SLEEP} != 0 ]]; then
            sleep "${BOX_SLEEP}"
        fi
        if grep -q '^LOOP_RUN_ENDED_BY=' "@SUMMARY@"; then
            guest="$(dirname "@GUEST@")/guest-$(printf '%s' "$1" | tr '/' '_')"
            rm -rf "${guest}"
            git clone --quiet --branch "$1" "@BARE@" "${guest}"
            {
                printf '\n## Run started 2026-08-31 00:00:00\n\nTask: %s\n\n' "$2"
                printf '### Iteration 1\n\nWhat the first attempt tried.\n\n'
            } >> "${guest}/PROGRESS.md"
            git -C "${guest}" -c user.email=box@example.invalid -c user.name="The Box" \
                commit --quiet -a -m "Loop: Run started"
            git -C "${guest}" push --quiet origin "$1"
        fi
        printf 'box-end %s\n' "$1" >> "@LOG@"
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
        if [[ ${2:-} == relabel ]]; then
            num="${3:-}"
            add="${4:-}"
            rem="${5:-}"
            python3 -c "import json, sys, os
q_path = sys.argv[1]
num = int(sys.argv[2])
add_label = sys.argv[3] if len(sys.argv) > 3 else ''
rem_label = sys.argv[4] if len(sys.argv) > 4 else ''
q_dir = os.path.dirname(q_path)
repo = os.environ.get('SELECTOR_TASK_REPO', '').replace('/', '_')

def load(path):
    return json.load(open(path)) if os.path.exists(path) else {'issues': []}

def save(path, data):
    json.dump(data, open(path, 'w'))

def take(data, n):
    found = [i for i in data.get('issues', []) if int(i.get('number', 0)) == n]
    data['issues'] = [i for i in data.get('issues', []) if int(i.get('number', 0)) != n]
    return found

removed = []
if os.path.exists(q_path):
    data = load(q_path)
    removed = take(data, num) or removed
    save(q_path, data)

def strip(path):
    global removed
    if not os.path.exists(path):
        return False
    data = load(path)
    found = take(data, num)
    if found:
        removed = found
    save(path, data)
    return True

if rem_label:
    strip(f'{q_dir}/queue-{rem_label}.json')
    if repo:
        strip(f'{q_dir}/queue-{repo}-{rem_label}.json')

if add_label:
    item = removed[0] if removed else {'number': num}
    dests = [f'{q_dir}/queue-{add_label}.json']
    if repo:
        dest_repo = f'{q_dir}/queue-{repo}-{add_label}.json'
        if os.path.exists(dest_repo) or os.path.exists(f'{q_dir}/queue-{repo}-{rem_label}.json'):
            dests.append(dest_repo)
    for dest in dests:
        data = load(dest)
        data.setdefault('issues', []).append(item)
        save(dest, data)
" "@QUEUE@" "$num" "$add" "$rem"
        fi
        if [[ ${2:-} == checks ]]; then
            head -n 1 "@CHECKS@"
            if (($(wc -l < "@CHECKS@") > 1)); then
                tail -n +2 "@CHECKS@" > "@CHECKS@.rest"
                mv "@CHECKS@.rest" "@CHECKS@"
            fi
            exit "${CHECKS_EXIT:-0}"
        fi
        if [[ ${2:-} == update-branch ]]; then
            exit "${UPDATE_BRANCH_EXIT:-0}"
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
        repo_safe="$(printf '%s' "${SELECTOR_PROTECTED_REPO:-}" | tr '/' '_')"
        repo_file="$(dirname "@GUARDRAIL@")/guardrail-${repo_safe}.txt"
        if [[ -n "${repo_safe}" && -f "${repo_file}" ]]; then
            exec cat "${repo_file}"
        fi
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
        # The target's checkout, as the watcher's command actually receives
        # it. `SELECTOR_BOX_REPO` has no default and lives only in a stanza
        # (issue #3), so a watch built from the bare environment would run
        # the real progress.sh with nothing telling it which checkout to
        # read - every poll of every real Run journaling `run.watch-failed`
        # while the dispatch beside it worked. Found by review, 2026-09-04.
        printf 'progress-env box_repo=%s\n' "${SELECTOR_BOX_REPO:-}" >> "@LOG@"
        exec cat "@PROGRESS@"
    '''))

    tracker = _script(tmp_path / "tracker.sh", fill('''
        set -euo pipefail
        queue_dir="$(dirname "@QUEUE@")"
        repo_safe="$(printf '%s' "${1:-}" | tr '/' '_')"
        labeled_repo="${queue_dir}/queue-${repo_safe}-${2}.json"
        if [[ -n "${repo_safe}" && -f "${labeled_repo}" ]]; then
            exec cat "${labeled_repo}"
        fi
        labeled="${queue_dir}/queue-${2}.json"
        if [[ -f "${labeled}" ]]; then
            exec cat "${labeled}"
        fi
        if [[ "${2}" != "awaiting-review" ]]; then
            exec cat "@QUEUE@"
        fi
        printf '{"issues": []}\n'
    '''))

    search_command = _script(tmp_path / "search.sh", fill('''
        printf 'search %s\n' "$*" >> "@LOG@"
        exec cat "@SEARCH@"
    '''))

    class Runner:
        # Assigned after the class body: `bare = bare` inside it would read
        # the class-local name, not the fixture's.
        targets_file = tmp_path / "targets.toml"
        # The controller-side checkout every stanza this fixture writes points
        # at, exposed so a test can assert Seeding happened in it.
        work_repo = work

        def run(self, dsn, issues=(), *, review_issues=(), owner_issues=(),
                dry_run=False, targets=None, archived=False, **env):
            payload = {"issues": list(issues)}
            if archived:
                payload["archived"] = True
            queue_file.write_text(json.dumps(payload))
            search_file.write_text(json.dumps({"issues": list(owner_issues)}))
            stanzas = targets or ({},)
            review_label = stanzas[0].get("labels", {}).get("review", "awaiting-review")
            (tmp_path / f"queue-{review_label}.json").write_text(
                json.dumps({"issues": list(review_issues)})
            )
            write_targets(
                self.targets_file,
                *({"work_repo": str(work), **over} for over in stanzas),
            )
            environ = dict(os.environ)
            environ.update(
                {
                    "SELECTOR_JOURNAL_DSN": dsn,
                    "SELECTOR_TRACKER_COMMAND": str(tracker),
                    "SELECTOR_SEARCH_COMMAND": str(search_command),
                    "TRACEWAKE_TARGETS_FILE": str(self.targets_file),
                    **INSTANCE_ENV,
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

        def queue(self, label, issues):
            (tmp_path / f"queue-{label}.json").write_text(
                json.dumps({"issues": list(issues)})
            )

        def queue_for(self, repo, label, issues):
            """A labeled queue for one target, so two Targets in one drain
            can hold different Eligible issues (issue #37)."""
            repo_safe = str(repo).replace("/", "_")
            (tmp_path / f"queue-{repo_safe}-{label}.json").write_text(
                json.dumps({"issues": list(issues)})
            )

        def extra_work(self, name):
            """A second controller-side checkout of the same remote.

            Concurrent Dispatches cannot share one working tree: git checkout
            of two branches in one directory is a race, and each Target
            already has its own `work_repo` in production."""
            dest = tmp_path / name
            subprocess.run(
                ["git", "clone", "--quiet", str(bare), str(dest)], check=True,
            )
            git("config", "user.email", "operator@example.invalid", cwd=dest)
            git("config", "user.name", "The Operator", cwd=dest)
            git("config", "commit.gpgsign", "false", cwd=dest)
            return dest

        def commands(self):
            return log.read_text() if log.exists() else ""

        def run_summary(self, text):
            summary.write_text(text)

        def reconcile_summary(self, text):
            reconcile_summary_file.write_text(text)

        def checks(self, text):
            checks_file.write_text(text)

        def facts(self, text):
            facts_file.write_text(text)

        def guardrail(self, text, repo=None):
            if repo:
                repo_safe = repo.replace("/", "_")
                (tmp_path / f"guardrail-{repo_safe}.txt").write_text(text)
            else:
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
            return _script(tmp_path / "guardrail.sh", fill(body))

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


# --- In-process Cycle harness ------------------------------------------------
#
# A canned tracker, a recording doing, and a Config built directly. Later
# tickets (#139–#141) reuse these; they know the ABC, not how a doing is built.

import dispatch as dispatch_mod  # noqa: E402
import doing as doing_mod  # noqa: E402
import drain  # noqa: E402
from targets import Config, Labels, Target  # noqa: E402


def a_target() -> Target:
    return Target(
        repo=TARGET_REPO,
        labels=Labels(
            ready="ready-for-agent",
            needs_info="needs-info",
            review="awaiting-review",
            human="ready-for-human",
        ),
        labeler_allowlist=(ALLOWLISTED_OPERATOR,),
        work_repo=Path("/nonexistent/work"),
        box_repo="/nonexistent/box",
        token_file="/nonexistent/token",
        guest_template="widgets-guest:1",
        landing="propose",
        review_cap=20,
    )


def a_config() -> Config:
    """Per-Target configuration, constructed directly - no env, no file."""
    return Config(
        target=a_target(),
        tracker_command="/nonexistent/tracker",
        box_facts_command="/nonexistent/facts",
        box_facts_timeout_seconds=60,
        guardrail_command="/nonexistent/guardrail",
        guardrail_timeout_seconds=30,
        board_timeout_seconds=10,
        guardrail_trees=(),
        drain_concurrency=1,
    )


def a_dispatch_config() -> dispatch_mod.DispatchConfig:
    """Dummy paths. A recording doing must not invoke them."""
    return dispatch_mod.DispatchConfig(
        work_repo=Path("/nonexistent/work"),
        command_env={},
        remote="origin",
        branch_prefix="loop/",
        seed_command="/nonexistent/seed",
        progress_log_path="PROGRESS.md",
        run_heading="## Run started",
        box_command="/nonexistent/box",
        issue_command="/nonexistent/issue",
        command_timeout_seconds=1,
        run_timeout_seconds=1,
        checks_timeout_seconds=1,
        checks_poll_seconds=1,
    )


class CannedTracker(drain.Tracker):
    """Answers from memory: no subprocess, no env."""

    def __init__(self, handover, review=None, *, archived=False, error=None):
        self._handover = list(handover)
        self._review = list(review or [])
        self._archived = archived
        self._error = error

    def read(self, config):
        if self._error is not None:
            raise drain.CycleFailed(self._error)
        handover = list(self._handover)
        if not self._archived:
            # Same contract as fetch_tracker: the Cycle picks eligible[0]
            # as lowest-first, so the queue arrives already ordered.
            handover = sorted(handover, key=lambda record: int(record["number"]))
        return drain.TrackerReads(
            handover=handover,
            review=list(self._review),
            archived=self._archived,
        )


class RecordingDoing(doing_mod.Doing):
    """Records calls. work_pick succeeds without GitHub or the box."""

    def __init__(self, *, error=None):
        self.calls = []
        self.picks = []
        self._error = error

    def observe_guardrail(self, conn, cycle_id, config):
        self.calls.append("observe_guardrail")

    def keep_proposals_current(
        self, conn, cycle_id, config, dispatch_config, handover, review,
    ):
        self.calls.append("keep_proposals_current")
        return doing_mod.ProposalUpkeep(
            updated=set(), failed=set(), reconciled=set(), reconcile_failed=set(),
        )

    def return_to_operator(
        self, conn, cycle_id, config, dispatch_config, record, detail,
    ):
        self.calls.append(("return_to_operator", record.get("number")))
        return None

    def work_pick(self, conn, cycle_id, config, dispatch_config, pick, attempt):
        self.picks.append(pick["number"])
        self.calls.append(("work_pick", pick["number"]))
        if self._error is not None:
            raise drain.CycleFailed(self._error)
        return doing_mod.PickResult(
            dispatched=True,
            outcome={"ended_by": "iteration-cap", "issue": pick["number"]},
            route="awaiting-review",
        )
