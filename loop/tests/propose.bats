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

@test "the proposal points a reviewer at history rather than branch-tip files" {
    # The cleanup commit removed the Plan and the Progress Log from the tip,
    # so the body must never send a reviewer to open them there. The narrative
    # lives in history and in the Run's comment.
    run_propose --ended-by consecutive-noops --removal-commit abc1234 --comment-follows
    grep -q 'Loop: Run ended (consecutive-noops)' "${FAKE_PR_STATE}.body"
    grep -q 'abc1234' "${FAKE_PR_STATE}.body"
    grep -q 'history' "${FAKE_PR_STATE}.body"
    grep -q "The Run's comment on this proposal carries the same record" "${FAKE_PR_STATE}.body"
    ! grep -qF 'Read `PROGRESS.md` first' "${FAKE_PR_STATE}.body"
    ! grep -qE 'Read `[^`]*\.md`' "${FAKE_PR_STATE}.body"
}

@test "the proposal names the removal commit that took the scaffolding off the tip" {
    run_propose --removal-commit deadbee1234
    grep -q 'Run scaffolding removed in: deadbee1234' "${FAKE_PR_STATE}.body"
}

@test "a proposal without a removal commit still says the tip carries no scaffolding" {
    run_propose
    grep -q 'carries no Run scaffolding' "${FAKE_PR_STATE}.body"
    ! grep -q 'removed in:' "${FAKE_PR_STATE}.body"
}

@test "the Run's comment is pointed at only when one follows" {
    run_propose
    ! grep -q "The Run's comment on this proposal" "${FAKE_PR_STATE}.body"
}

@test "an owning area given on the command line wins over the Plan's" {
    run_propose --area "tour planning"
    grep -q 'Owning area this Run was scoped to: tour planning' "${FAKE_PR_STATE}.body"
    [[ "$(pr_field PR_TITLE)" == *"tour planning"* ]]
    [[ "$(pr_field PR_TITLE)" != *"dashboards and reports"* ]]
}

@test "a Run whose Plan is gone still names the task, the area and the bound" {
    # The merge-clean cleanup removes the Plan before propose.sh runs, so a
    # Run hands over what the body must name. This drives that exact shape: no
    # Plan on disk, everything as flags.
    rm -f "${REPO}/PLAN.md"
    run_propose --task-ref "Educational-Travel-Adventures/tourbot#648" \
        --task-title "Audit every tblEmailMessage read and classify it" \
        --area "dashboards and reports" \
        --ended-by iteration-cap --exit 0 --removal-commit deadbee1234 \
        --comment-follows
    [ "$status" -eq 0 ]
    grep -q 'Educational-Travel-Adventures/tourbot#648' "${FAKE_PR_STATE}.body"
    grep -q 'Owning area this Run was scoped to: dashboards and reports' "${FAKE_PR_STATE}.body"
    grep -q 'Ended by: iteration-cap' "${FAKE_PR_STATE}.body"
    grep -q 'Run exit code: 0' "${FAKE_PR_STATE}.body"
    grep -q 'Run scaffolding removed in: deadbee1234' "${FAKE_PR_STATE}.body"
    grep -q 'Loop: Run ended (iteration-cap)' "${FAKE_PR_STATE}.body"
    [ "$(pr_field PR_TITLE)" = "Loop: Audit every tblEmailMessage read and classify it (dashboards and reports)" ]
}

@test "the proposal closes the task when it is merged" {
    run_propose
    grep -q '^Closes #648$' "${FAKE_PR_STATE}.body"
}

@test "the Closes line does not break the metadata list in two" {
    # A keyword that works and renders as a stray paragraph between two lists
    # is a body a reviewer reads as broken. It goes last, after the prose.
    run_propose
    closes="$(grep -n '^Closes #648$' "${FAKE_PR_STATE}.body" | cut -d: -f1)"
    last_bullet="$(grep -n '^- ' "${FAKE_PR_STATE}.body" | tail -1 | cut -d: -f1)"
    [ "${closes}" -gt "${last_bullet}" ]
}

@test "a proposal against a branch that is not the default gets no Closes line" {
    # GitHub's keyword fires on a merge into the DEFAULT branch and nowhere
    # else, so a proposal aimed at a release branch would carry a line saying
    # the task is retired and retire nothing. Same reason as the
    # cross-repository case below.
    git -C "${REPO}" push --quiet origin master:release
    run_propose --base release
    [ "$status" -eq 0 ]
    ! grep -q '^Closes ' "${FAKE_PR_STATE}.body"
}

@test "--base naming the default branch still closes the task" {
    # The guard is about where the proposal lands, not about whether --base was
    # typed: passing the default branch explicitly is the same merge.
    run_propose --base master
    [ "$status" -eq 0 ]
    grep -q '^Closes #648$' "${FAKE_PR_STATE}.body"
}

@test "a task in another repository gets no Closes line" {
    # GitHub's keyword closes a cross-repository reference only for an actor
    # with write access on the OTHER repository, and a line that silently does
    # nothing is worse than none.
    run_propose --task-ref "someone/else#1"
    ! grep -q '^Closes ' "${FAKE_PR_STATE}.body"
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

@test "a push whose diff contains the token's pattern is refused" {
    printf 'sk-ant-oat01-leak-secret\n' >"${REPO}/token.txt"
    git -C "${REPO}" add "${REPO}/token.txt"
    git -C "${REPO}" commit --quiet --message "accidental token commit"
    run_propose
    [ "$status" -eq 2 ]
    [ "$(field LOOP_PROPOSE_RESULT)" = "push-failed" ]
    [[ "$output" == *"sk-ant-oat01-"* ]]
    ! remote_has_branch "loop/run-648"
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
