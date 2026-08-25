#!/usr/bin/env bats
#
# Proposal-Only Output's offline suite (#83).
#
# Every test drives the real propose.sh and asserts only what it externally
# produces: its exit code, its machine-readable result line, what ended up on
# the remote, and what the pull-request command was asked for. None of them
# names an internal function.
#
# The three exit codes are tested separately and deliberately. "The proposal
# failed" is not one outcome: a push that never happened and a push that landed
# without a pull request leave the world in different states, and the operator's
# next command is different in each.

load propose-helpers

setup() { setup_propose_fixture; }

# --- The proposal ------------------------------------------------------------

@test "a Run's branch is pushed and a pull request is opened" {
    run_propose
    [ "$status" -eq 0 ]
    [ "$(field LOOP_PROPOSE_RESULT)" = "proposed" ]
    [ "$(field LOOP_PROPOSE_BRANCH)" = "loop/run-648" ]
    [ "$(field LOOP_PROPOSE_BASE)" = "master" ]
    remote_has_branch "loop/run-648"
}

@test "the pull request's URL is reported" {
    run_propose
    [[ "$(field LOOP_PROPOSE_URL)" == https://github.com/*/pull/999 ]]
}

@test "the proposal is made against the remote's default branch, not a guess" {
    run_propose
    [ "$(pr_field PR_BASE)" = "master" ]
    [ "$(pr_field PR_HEAD)" = "loop/run-648" ]
}

@test "--base overrides the remote's default branch" {
    git -C "${REPO}" push --quiet origin master:release
    run_propose --base release
    [ "$status" -eq 0 ]
    [ "$(pr_field PR_BASE)" = "release" ]
}

@test "the repository is derived from the remote's URL" {
    run_propose
    [ "$(pr_field PR_REPO)" = "Educational-Travel-Adventures/tourbot" ]
}

# --- What the proposal says --------------------------------------------------
#
# Acceptance criterion: the draft pull request references the task it came from.

@test "the proposal references the task it came from" {
    run_propose
    grep -q 'Educational-Travel-Adventures/tourbot#648' "${FAKE_PR_STATE}.body"
}

@test "the proposal names the owning area the Run was scoped to" {
    run_propose
    grep -q 'dashboards and reports' "${FAKE_PR_STATE}.body"
}

@test "the title carries the task's own title and the owning area" {
    run_propose
    [[ "$(pr_field PR_TITLE)" == *"Audit every tblEmailMessage read"* ]]
    [[ "$(pr_field PR_TITLE)" == *"dashboards and reports"* ]]
}

@test "the proposal records which bound ended the Run and what it exited" {
    run_propose --ended-by consecutive-noops --exit 3
    grep -q 'consecutive-noops' "${FAKE_PR_STATE}.body"
    grep -q 'Run exit code: 3' "${FAKE_PR_STATE}.body"
}

@test "the proposal points a reviewer at the Progress Log" {
    run_propose
    grep -q 'PROGRESS.md' "${FAKE_PR_STATE}.body"
}

@test "a task reference given on the command line wins over the Plan's" {
    run_propose --task-ref "someone/else#1"
    grep -q 'someone/else#1' "${FAKE_PR_STATE}.body"
}

@test "a Run whose Plan cannot be read still proposes" {
    rm -f "${REPO}/PLAN.md"
    run_propose
    [ "$status" -eq 0 ]
    [ -n "$(pr_field PR_TITLE)" ]
}

# --- Refusals: nothing was pushed --------------------------------------------

@test "a Run on the base branch is refused before anything is pushed" {
    git -C "${REPO}" checkout --quiet master
    run_propose
    [ "$status" -eq 1 ]
    [[ "$output" == *"base branch"* ]]
    [ ! -f "${FAKE_PR_STATE}" ]
}

@test "a repository with no such remote is refused" {
    run "${PROPOSE}" --repo "${REPO}" --remote nowhere
    [ "$status" -eq 1 ]
}

@test "a detached head is refused - a Run works on a branch" {
    git -C "${REPO}" checkout --quiet --detach
    run_propose
    [ "$status" -eq 1 ]
    [ ! -f "${FAKE_PR_STATE}" ]
}

@test "a remote with no recorded default branch is refused rather than guessed" {
    git -C "${REPO}" remote set-head origin --delete
    run_propose
    [ "$status" -eq 1 ]
    [[ "$output" == *"--base"* ]]
    [ ! -f "${FAKE_PR_STATE}" ]
}

@test "a pull-request command that is not executable is refused" {
    LOOP_PR_COMMAND="${BATS_TEST_TMPDIR}/absent" run_propose
    [ "$status" -eq 1 ]
}

@test "an unknown argument is a could-not-run" {
    run "${PROPOSE}" --repo "${REPO}" --nonsense
    [ "$status" -eq 1 ]
}

@test "--help explains the script and exits 0" {
    run "${PROPOSE}" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"propose.sh"* ]]
}

# --- The two ways a proposal fails -------------------------------------------

@test "a push that fails is exit 2, and no pull request is attempted" {
    break_the_push
    run_propose
    [ "$status" -eq 2 ]
    [ "$(field LOOP_PROPOSE_RESULT)" = "push-failed" ]
    [ ! -f "${FAKE_PR_STATE}" ]
}

@test "a pull request that is refused is exit 3, and the branch is still pushed" {
    FAKE_PR_BEHAVIOUR=fail run_propose
    [ "$status" -eq 3 ]
    [ "$(field LOOP_PROPOSE_RESULT)" = "pull-request-failed" ]
    [ "$(field LOOP_PROPOSE_BRANCH)" = "loop/run-648" ]
    remote_has_branch "loop/run-648"
}

@test "a push is never forced" {
    # A remote whose branch has moved on is a conflict, and resolving one
    # unattended is not something a Run gets to do. The push fails; it does not
    # overwrite.
    git -C "${REPO}" commit --quiet --allow-empty --message "diverge"
    other="$(git -C "${REPO}" rev-parse HEAD)"
    git -C "${REPO}" push --quiet origin "HEAD:loop/run-648"
    git -C "${REPO}" reset --quiet --hard HEAD~1
    git -C "${REPO}" commit --quiet --allow-empty --message "a different commit"
    run_propose
    [ "$status" -eq 2 ]
    [ "$(git -C "${REMOTE}" rev-parse refs/heads/loop/run-648)" = "${other}" ]
}

# --- Re-running it -----------------------------------------------------------

@test "a proposal already open is reported rather than opened again" {
    run_propose
    [ "$status" -eq 0 ]
    FAKE_PR_BEHAVIOUR=existing run_propose
    [ "$status" -eq 0 ]
    [ "$(field LOOP_PROPOSE_RESULT)" = "proposed" ]
    [[ "$(field LOOP_PROPOSE_URL)" == */pull/17 ]]
}

@test "proposing twice from an unchanged branch is not an error" {
    run_propose
    [ "$status" -eq 0 ]
    run_propose
    [ "$status" -eq 0 ]
}
