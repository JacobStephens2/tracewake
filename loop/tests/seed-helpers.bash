# Shared setup for the seed step's offline suite.
#
# Every test builds a throwaway repository with no Plan in it, points the seed
# step at the scripted fake task source, and asserts on what the seed leaves
# behind - the Plan, the Progress Log, the git history and the exit code. The
# real GitHub is never reached: the task source is one substitutable command
# and this suite substitutes it.
#
# shellcheck shell=bash

setup_seed_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC
    SEED="${LOOP_SRC}/seed-run.sh"
    export SEED

    REPO="${BATS_TEST_TMPDIR}/repo"
    mkdir -p "${REPO}"
    git -C "${REPO}" init --quiet --initial-branch=main
    git -C "${REPO}" config user.email "loop@example.invalid"
    git -C "${REPO}" config user.name "The Loop"
    git -C "${REPO}" config commit.gpgsign false
    printf 'the work repository\n' >"${REPO}/README.md"
    git -C "${REPO}" add -A
    git -C "${REPO}" commit --quiet --message "Initial commit"
    export REPO

    FAKE_TASK_STATE="${BATS_TEST_TMPDIR}/fetches"
    export FAKE_TASK_STATE
    export LOOP_TASK_SOURCE_COMMAND="${LOOP_SRC}/tests/fake-task-source.sh"
}

# The task most tests seed from. Written to a file rather than inlined so a test
# that cares about one shape of task can override just that shape.
write_task_json() {
    FAKE_TASK_JSON="${BATS_TEST_TMPDIR}/task.json"
    export FAKE_TASK_JSON
    cat >"${FAKE_TASK_JSON}"
}

# Tourbot issue 648 in miniature: a body with sections either side of the
# acceptance criteria, so that extracting them can be seen to take the right
# lines and leave the rest.
write_classification_task() {
    write_task_json <<'JSON'
{
  "number": 648,
  "title": "Audit every tblEmailMessage read and classify it",
  "url": "https://github.com/Educational-Travel-Adventures/tourbot/issues/648",
  "state": "OPEN",
  "body": "## What to build\n\nEvery occurrence gets marked as one of three things.\n\n## Acceptance criteria\n\n- [ ] Every occurrence in application code is accounted for and classified\n- [ ] The inventory is grouped by owning area\n- [ ] Each should-include-notes entry names the user-visible symptom\n\n## Blocked by\n\nNone.\n"
}
JSON
}

run_the_seed() {
    run "${SEED}" --repo "${REPO}" "$@"
}

plan() {
    cat -- "${REPO}/PLAN.md"
}

progress_log() {
    cat -- "${REPO}/PROGRESS.md"
}

git_log() {
    git -C "${REPO}" log --format='%s'
}

# What the seed fetched, one line per invocation. A seed step that reached for
# more than the one task it was given shows up here.
fetches() {
    if [[ -f ${FAKE_TASK_STATE} ]]; then
        cat -- "${FAKE_TASK_STATE}"
    fi
}
