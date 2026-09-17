#!/usr/bin/env bats
#
# The seed step's offline suite (#82).
#
# Seeding a Run is the one place a task the operator chose becomes something an
# unattended agent reads, so what is asserted here is what the Plan ends up
# saying, whether the Progress Log is ready for an Iteration, and what happens
# when the same command is run twice. Nothing asserts on an internal function of
# seed-run.sh or on the order of its steps.

load seed-helpers

setup() {
    setup_seed_fixture
}

# --- Fetching one chosen task ----------------------------------------------

@test "the seed fetches exactly the one task it was given" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ "$(fetches)" = "Educational-Travel-Adventures/tourbot 648" ]
}

@test "the Plan names the task by number, title and URL" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    plan | grep -qF "Educational-Travel-Adventures/tourbot#648"
    plan | grep -qF "Audit every tblEmailMessage read and classify it"
    plan | grep -qF "https://github.com/Educational-Travel-Adventures/tourbot/issues/648"
}

@test "the task repository defaults to the work repository's origin" {
    git -C "${REPO}" remote add origin git@github.com:Educational-Travel-Adventures/tourbot.git
    write_classification_task
    run_the_seed --task 648 --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ "$(fetches)" = "Educational-Travel-Adventures/tourbot 648" ]
}

@test "a fetch that fails leaves no Plan behind" {
    FAKE_TASK_RC=1 run_the_seed --task 648 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
    [ ! -f "${REPO}/PROGRESS.md" ]
    ! git_log | grep -qF "Loop:"
}

@test "a task source that emits something that is not a task does not seed" {
    write_task_json <<<'not json at all'
    run_the_seed --task 648 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

# --- The acceptance criteria -----------------------------------------------

@test "the Plan carries the task's acceptance criteria" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    plan | grep -qF "Every occurrence in application code is accounted for and classified"
    plan | grep -qF "The inventory is grouped by owning area"
    plan | grep -qF "Each should-include-notes entry names the user-visible symptom"
}

@test "the seed reports how many acceptance criteria it carried over" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_SEED_CRITERIA=3"* ]]
}

@test "sub-bullets under a criterion are not counted as criteria of their own" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task whose criteria have detail under them",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## Acceptance criteria\n\n- [ ] the first thing is true\n  - including this detail\n  - and this one\n- [ ] the second thing is true\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_SEED_CRITERIA=2"* ]]
    plan | grep -qF "including this detail"
}

@test "a task whose body is JSON null is refused, not seeded with the word null" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task nobody wrote a body for",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": null
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "a task whose title is JSON null does not reach the Plan as the word null" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": null,
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## Acceptance criteria\n\n- [ ] it works\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "a task with no acceptance criteria is refused rather than seeded blind" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task nobody wrote criteria for",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## What to build\n\nSomething, somehow.\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [[ ${output} == *"acceptance criteria"* ]]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "an acceptance criteria heading with nothing under it is refused" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A heading and nothing under it",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## What to build\n\nSomething.\n\n## Acceptance criteria\n\n## Blocked by\n\nNone.\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "an acceptance criteria section of prose with no criteria in it is refused" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A section that gestures at criteria without listing any",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## What to build\n\nSomething.\n\n## Acceptance criteria\n\nIt should be good, and everyone should be happy with it.\n\n## Blocked by\n\nNone.\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [[ ${output} == *"acceptance criteria"* ]]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "a task source that answers with a different task does not seed" {
    write_task_json <<'JSON'
{
  "number": 649,
  "title": "The task next door",
  "url": "https://github.com/owner/repo/issues/649",
  "state": "OPEN",
  "body": "## Acceptance criteria\n\n- [ ] it works\n"
}
JSON
    run_the_seed --task 648 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "the rest of the task's body reaches the Plan too" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    plan | grep -qF "Every occurrence gets marked as one of three things."
    plan | grep -qF "What to build"
}

@test "the Plan says a count written in the task is not the denominator" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    plan | grep -qF "not as fact about the codebase today"
}

@test "the task's own headings are demoted so they nest inside the Plan" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    # "## What to build" was the task's; a "## " of that name in the Plan would
    # sit at the same level as the Plan's own sections and read as one of them.
    ! plan | grep -q '^## What to build'
    plan | grep -q '^### What to build'
}

@test "a hash inside a fenced block in the task is not treated as a heading" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task with a code block",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## What to build\n\n```\n# not a heading, a shell comment\ngit status\n```\n\n## Acceptance criteria\n\n- [ ] it works\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 0 ]
    plan | grep -qF '# not a heading, a shell comment'
    ! plan | grep -qF '## not a heading, a shell comment'
}

@test "an acceptance criteria heading inside a fenced block does not count" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task quoting another ticket",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "OPEN",
  "body": "## What to build\n\n```\n## Acceptance criteria\n- [ ] quoted from somewhere else\n```\n\nAnd nothing of its own.\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ ! -f "${REPO}/PLAN.md" ]
}

# --- One owning area, not the whole task ------------------------------------

@test "the Plan names the one owning area the Run is scoped to" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    plan | grep -qF "dashboards and reports"
    [[ ${output} == *"LOOP_SEED_AREA=dashboards and reports"* ]]
}

@test "the Plan says the rest of the task is not this Run's remaining work" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    plan | grep -qiE "out of scope|not this Run"
}

@test "a Run is not seeded without an owning area" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot
    [ "$status" -eq 1 ]
    [[ ${output} == *"--area"* ]]
    [ ! -f "${REPO}/PLAN.md" ]
}

# --- The mechanical check ---------------------------------------------------

@test "the Plan carries the completeness check command it was given" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports" \
        --check "./grade.sh --checkout . --inventory inventory.md --scope 'reports/*'"
    [ "$status" -eq 0 ]
    plan | grep -qF "./grade.sh --checkout . --inventory inventory.md --scope 'reports/*'"
}

@test "a Plan with no check says so rather than saying nothing" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    plan | grep -qi "no mechanical"
}

# --- The Progress Log -------------------------------------------------------

@test "the Progress Log is initialized ready for the first Iteration" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ -f "${REPO}/PROGRESS.md" ]
    progress_log | grep -qF "Educational-Travel-Adventures/tourbot#648"
    progress_log | grep -qi "no Iteration has run"
}

@test "the seeded Progress Log holds no Run and no Iteration" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    ! progress_log | grep -q '^## Run started'
    ! progress_log | grep -q '^### Iteration'
}

# --- What it commits --------------------------------------------------------

@test "the Plan and the Progress Log are committed" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ -z "$(git -C "${REPO}" status --porcelain -- PLAN.md PROGRESS.md)" ]
    git_log | grep -qF "648"
}

@test "the seed does not sweep unrelated work into its own commit" {
    printf 'someone was in the middle of something\n' >"${REPO}/scratch.txt"
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    git -C "${REPO}" status --porcelain | grep -qF "scratch.txt"
    ! git -C "${REPO}" show --name-only --format= HEAD | grep -qF "scratch.txt"
}

# --- Re-running it ----------------------------------------------------------

@test "seeding the same task twice produces the same Plan and no second commit" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    first_plan="$(plan)"
    commits_before="$(git_log | wc -l)"

    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ "$(plan)" = "${first_plan}" ]
    [ "$(git_log | wc -l)" -eq "${commits_before}" ]
    [[ ${output} == *"LOOP_SEED_RESULT=unchanged"* ]]
}

@test "seeding a different area re-writes the Plan rather than appending to it" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "alerts and background jobs"
    [ "$status" -eq 0 ]
    plan | grep -qF "alerts and background jobs"
    ! plan | grep -qF "dashboards and reports"
    [ "$(grep -c '^## Acceptance criteria' "${REPO}/PLAN.md")" -eq 1 ]
}

@test "re-seeding refuses to discard a Run that has already happened" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    printf '\n## Run started 2026-08-25T10:00:00Z\n\n### Iteration 1\n\nwork\n' \
        >>"${REPO}/PROGRESS.md"
    git -C "${REPO}" commit --quiet -a --message "a Run happened"

    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "alerts and background jobs"
    [ "$status" -eq 2 ]
    [[ ${output} == *"--reseed"* ]]
    progress_log | grep -qF "## Run started 2026-08-25T10:00:00Z"
    ! plan | grep -qF "alerts and background jobs"
}

@test "a finished Run's record survives a re-seed, in history rather than in the way" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]

    # Driven through the real run.sh rather than by appending a heading by hand.
    export FAKE_AGENT_STATE="${BATS_TEST_TMPDIR}/fake-agent-state"
    run env LOOP_AGENT_COMMAND="${LOOP_SRC}/tests/fake-agent.sh" \
        LOOP_MAX_ITERATIONS=1 LOOP_ITERATION_TIMEOUT_SECONDS=30 \
        LOOP_RUN_TIMEOUT_SECONDS=120 FAKE_AGENT_BEHAVIOURS="commit" \
        timeout 120 "${LOOP_SRC}/run.sh" --repo "${REPO}"
    [ "$status" -eq 0 ]

    # The Run ended merge-clean, so there is no live log left to discard:
    # re-seeding writes a fresh one rather than refusing, and the finished
    # Run's record stays readable in history. The refusal next door keeps its
    # force for a live log that still records a Run - a Run killed mid-flight,
    # not one that ended.
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "alerts and background jobs"
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_SEED_RESULT=seeded"* ]]
    plan | grep -qF "alerts and background jobs"
    progress_log | grep -qF "No Iteration has run yet"
    ! progress_log | grep -q '^## Run started'

    ended="$(git -C "${REPO}" log --format='%H' --grep='Loop: Run ended' | head -n 1)"
    [ -n "${ended}" ]
    [[ "$(git -C "${REPO}" show "${ended}:PROGRESS.md")" == *"Ended by: iteration-cap"* ]]
}

@test "--reseed discards the Run's history and says how much it discarded" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    printf '\n## Run started 2026-08-25T10:00:00Z\n\n### Iteration 1\n\nwork\n\n### Iteration 2\n\nmore\n' \
        >>"${REPO}/PROGRESS.md"
    git -C "${REPO}" commit --quiet -a --message "a Run happened"

    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "alerts and background jobs" --reseed
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_SEED_RESULT=reseeded"* ]]
    ! progress_log | grep -q '^## Run started'
    ! progress_log | grep -q '^### Iteration'
    plan | grep -qF "alerts and background jobs"
}

@test "--reseed on a repository that never ran is the same as seeding it" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports" --reseed
    [ "$status" -eq 0 ]
    plan | grep -qF "dashboards and reports"
}

# --- Preflight --------------------------------------------------------------

@test "the seed refuses a directory that is not a git repository" {
    mkdir -p "${BATS_TEST_TMPDIR}/plain"
    write_classification_task
    run "${SEED}" --repo "${BATS_TEST_TMPDIR}/plain" --task 648 \
        --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
}

@test "a task number that is not a number does not reach the task source" {
    write_classification_task
    run_the_seed --task "648; rm -rf /" --task-repo owner/repo --area "one area"
    [ "$status" -eq 1 ]
    [ -z "$(fetches)" ]
}

@test "a task repository that is not owner/name does not reach the task source" {
    write_classification_task
    run_the_seed --task 648 --task-repo "not a repository" --area "one area"
    [ "$status" -eq 1 ]
    [ -z "$(fetches)" ]
}

@test "the Plan is written where the Contract says, not at a hardcoded path" {
    write_classification_task
    LOOP_PLAN_PATH="docs/PLAN.md" LOOP_PROGRESS_LOG_PATH="docs/PROGRESS.md" \
        run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]
    [ -f "${REPO}/docs/PLAN.md" ]
    [ -f "${REPO}/docs/PROGRESS.md" ]
    [ ! -f "${REPO}/PLAN.md" ]
}

@test "a closed task is seeded but reported as closed" {
    write_task_json <<'JSON'
{
  "number": 5,
  "title": "A task somebody already closed",
  "url": "https://github.com/owner/repo/issues/5",
  "state": "CLOSED",
  "body": "## Acceptance criteria\n\n- [ ] it works\n"
}
JSON
    run_the_seed --task 5 --task-repo owner/repo --area "one area"
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_SEED_TASK_STATE=CLOSED"* ]]
}

# --- The seam to the Run ----------------------------------------------------

@test "a Run starts against a repository the seed prepared" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"
    [ "$status" -eq 0 ]

    export FAKE_AGENT_STATE="${BATS_TEST_TMPDIR}/fake-agent-state"
    run env LOOP_AGENT_COMMAND="${LOOP_SRC}/tests/fake-agent.sh" \
        LOOP_MAX_ITERATIONS=2 LOOP_ITERATION_TIMEOUT_SECONDS=5 \
        LOOP_RUN_TIMEOUT_SECONDS=60 LOOP_MAX_CONSECUTIVE_NOOPS=2 \
        FAKE_AGENT_BEHAVIOURS="commit commit" \
        timeout 60 "${LOOP_SRC}/run.sh" --repo "${REPO}" --task-ref "tourbot#648"
    [ "$status" -eq 0 ]
    [[ ${output} == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
    # The Run ends merge-clean, so its record is read from history: the
    # Run-ended commit's tree is the last one that carries the scaffolding.
    ended="$(git -C "${REPO}" log --format='%H' --grep='Loop: Run ended' | head -n 1)"
    history_log="$(git -C "${REPO}" show "${ended}:PROGRESS.md")"
    printf '%s\n' "${history_log}" | grep -qiF "no Iteration has run"
    printf '%s\n' "${history_log}" | grep -q '^### Iteration 1'
}

@test "the prompt an Iteration gets points at the Plan the seed wrote" {
    write_classification_task
    run_the_seed --task 648 --task-repo Educational-Travel-Adventures/tourbot \
        --area "dashboards and reports"

    export FAKE_AGENT_STATE="${BATS_TEST_TMPDIR}/fake-agent-state"
    run env LOOP_AGENT_COMMAND="${LOOP_SRC}/tests/fake-agent.sh" \
        LOOP_MAX_ITERATIONS=1 LOOP_ITERATION_TIMEOUT_SECONDS=5 \
        LOOP_RUN_TIMEOUT_SECONDS=60 FAKE_AGENT_BEHAVIOURS="commit" \
        timeout 60 "${LOOP_SRC}/run.sh" --repo "${REPO}"
    [ "$status" -eq 0 ]
    grep -qF "PLAN.md" "${FAKE_AGENT_STATE}.prompt"
}
