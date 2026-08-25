#!/usr/bin/env bats
#
# The Termination Contract, verified offline.
#
# Every test here drives the REAL entry point - run.sh, unmodified - against a
# throwaway repository, with the agent replaced by a scripted fake at the
# substitution point ADR 0004 already created. No model, no network, no spend.
#
# The assertions are deliberately restricted to what a Run externally produces:
# its exit code, the bound it names, what the Progress Log says, and what the
# git history contains. Nothing here names an internal function, reads an
# intermediate variable, or depends on the order of steps inside the loop - a
# test that broke when run.sh was reorganised without a behaviour change would
# be the wrong test.

load helpers

setup() {
    setup_loop_fixture
}

# --- The Run's planned end -------------------------------------------------

@test "a clean Run exits zero, names the cap, and leaves one commit per Iteration" {
    export FAKE_AGENT_BEHAVIOURS="commit commit commit"

    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
    [[ "$output" == *"LOOP_RUN_FAULTS=none"* ]]

    [ "$(agent_invocations)" -eq 3 ]
    [ "$(git_log | grep -c '^Agent: task')" -eq 3 ]
}

@test "each Iteration is a separate agent process" {
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    # The fake counts its own invocations; three Iterations means three
    # processes, which is the whole mechanism for discarding context.
    [ "$(agent_invocations)" -eq 3 ]

    # Each was handed a prompt as a file rather than inheriting a conversation,
    # and each got its own Iteration number - which is only true if the process
    # boundary is real.
    [ "$(grep -c '^40$' "${FAKE_AGENT_STATE}.turns")" -eq 3 ]
    [ "$(git_log | grep -c '^Loop: Iteration')" -eq 3 ]
}

@test "the iteration cap ends a Run that would otherwise keep going" {
    export LOOP_MAX_ITERATIONS=2
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
    [ "$(agent_invocations)" -eq 2 ]
    [ "$(git_log | grep -c '^Agent: task')" -eq 2 ]
}

# --- The bounds ------------------------------------------------------------

@test "the Run's wall clock ends the Run before the iteration cap does" {
    export LOOP_MAX_ITERATIONS=10
    export LOOP_RUN_TIMEOUT_SECONDS=3
    export LOOP_ITERATION_TIMEOUT_SECONDS=30
    export FAKE_AGENT_BEHAVIOURS="slow:1"

    run_the_loop
    [ "$status" -eq 2 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=run-clock"* ]]

    # Fewer Iterations than the cap allowed: the Run clock, not the cap.
    [ "$(agent_invocations)" -lt 10 ]
    [[ "$(progress_log)" == *"Ended by: run-clock"* ]]
}

@test "an Iteration exceeding its wall clock is killed and the Run continues" {
    export LOOP_ITERATION_TIMEOUT_SECONDS=2
    export FAKE_AGENT_BEHAVIOURS="hang commit commit"

    run_the_loop
    # Reached the cap, so the Run was not stalled by the hung Iteration - but a
    # kill is a fault, and the Run is honest about it.
    [ "$status" -eq 5 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
    [[ "$output" == *"LOOP_RUN_FAULTS="*"iteration-timeout"* ]]

    [ "$(agent_invocations)" -eq 3 ]
    [[ "$(progress_log)" == *"killed at its 2s wall clock"* ]]
    [ "$(git_log | grep -c '^Agent: task')" -eq 2 ]
}

@test "the turn bound reaches the agent" {
    export LOOP_MAX_TURNS=7
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    # The agent records what it was handed; three Iterations, all bounded.
    [ "$(grep -c '^7$' "${FAKE_AGENT_STATE}.turns")" -eq 3 ]
    [[ "$(progress_log)" == *"Turn bound: 7"* ]]
}

@test "consecutive No-op Iterations abort the Run" {
    export LOOP_MAX_ITERATIONS=6
    export FAKE_AGENT_BEHAVIOURS="commit noop noop commit"

    run_the_loop
    [ "$status" -eq 3 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=consecutive-noops"* ]]

    # Aborted at the second consecutive No-op, not at the cap.
    [ "$(agent_invocations)" -eq 3 ]
    [[ "$(progress_log)" == *"No-op Iteration: head unchanged"* ]]
}

@test "a single No-op Iteration does not abort the Run" {
    export LOOP_MAX_ITERATIONS=4
    export FAKE_AGENT_BEHAVIOURS="commit noop commit commit"

    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
    [ "$(agent_invocations)" -eq 4 ]
}

@test "an agent exiting non-zero ends the Run and is named as the cause" {
    export FAKE_AGENT_BEHAVIOURS="commit fail commit"

    run_the_loop
    [ "$status" -eq 4 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=agent-failed"* ]]
    [ "$(agent_invocations)" -eq 2 ]
    [[ "$(progress_log)" == *"Agent exit: 3"* ]]

    # A Run nobody watched is diagnosed from the log it leaves behind.
    [[ "$(progress_log)" == *"pretending the invocation is broken"* ]]
}

# --- The Completion Promise ------------------------------------------------

@test "a Completion Promise is recorded and does not end the Run" {
    export FAKE_AGENT_BEHAVIOURS="promise commit commit"

    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]

    # The Run went the full distance despite the claim in Iteration 1.
    [ "$(agent_invocations)" -eq 3 ]
    [[ "$(progress_log)" == *"Completion Promise: recorded (advisory - the Run continues)"* ]]
    [[ "$(progress_log)" == *"Completion Promises recorded: 1"* ]]
}

@test "a Promise from an earlier Iteration is not re-counted in later ones" {
    export FAKE_AGENT_BEHAVIOURS="promise commit commit"

    run_the_loop
    [ "$status" -eq 0 ]

    # The Promise line stays in the Progress Log forever. Counting the log
    # rather than what each Iteration produced would report three.
    [[ "$(progress_log)" == *"Completion Promises recorded: 1"* ]]
}

@test "a Promise with no commit behind it is still only a No-op Iteration" {
    export LOOP_MAX_ITERATIONS=6
    export FAKE_AGENT_BEHAVIOURS="commit promise-noop promise-noop"

    run_the_loop
    [ "$status" -eq 3 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=consecutive-noops"* ]]
    [[ "$(progress_log)" == *"Completion Promises recorded: 2"* ]]
}

# --- The Contract and the Progress Log -------------------------------------

@test "the Contract's values are written into the Progress Log at Run start" {
    export LOOP_MAX_ITERATIONS=2
    export LOOP_ITERATION_TIMEOUT_SECONDS=11
    export LOOP_MAX_TURNS=13
    export LOOP_RUN_TIMEOUT_SECONDS=17
    export LOOP_MAX_CONSECUTIVE_NOOPS=2
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    log="$(progress_log)"
    [[ "$log" == *"Iterations per Run: 2"* ]]
    [[ "$log" == *"Iteration wall clock: 11s"* ]]
    [[ "$log" == *"Turns per Iteration: 13"* ]]
    [[ "$log" == *"Run wall clock: 17s"* ]]
    [[ "$log" == *"Consecutive No-op Iterations that abort: 2"* ]]
    [[ "$log" == *"Completion Promise: recorded, never terminal"* ]]
}

@test "the Run records the task it was started against" {
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop --task-ref "tourbot#648"
    [ "$status" -eq 0 ]
    [[ "$(progress_log)" == *"Task: tourbot#648"* ]]
}

@test "every Iteration is asked for decisions and blockers, not only completed tasks" {
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    # Asserted on the prompt the Loop hands the agent, not on what the fake
    # writes. The Progress Log's narrative comes from the agent, so a test that
    # only read the log would be asserting a property of the fake - it would
    # still pass with the instruction deleted from run.sh.
    prompt="$(cat "${FAKE_AGENT_STATE}.prompt")"
    [[ "$prompt" == *"DECIDED"* ]]
    [[ "$prompt" == *"BLOCKED"* ]]
    [[ "$prompt" == *"PROGRESS.md"* ]]

    # And the log does end up carrying them.
    log="$(progress_log)"
    [[ "$log" == *"Decided:"* ]]
    [[ "$log" == *"Blocked:"* ]]
}

@test "every Iteration is told the Plan is where the task is, and to do one thing" {
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    prompt="$(cat "${FAKE_AGENT_STATE}.prompt")"
    [[ "$prompt" == *"PLAN.md"* ]]
    [[ "$prompt" == *"ONE task"* ]]
    # The Promise is asked for as evidence, and explicitly not as an exit.
    [[ "$prompt" == *"does not end the Run"* ]]
}

@test "the Plan and the Progress Log are committed" {
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 0 ]

    [ -z "$(git -C "${REPO}" status --porcelain)" ]
    git -C "${REPO}" log --format='%H' -- PROGRESS.md | grep -q .
    [[ "$(git_log)" == *"Loop: Run started"* ]]
    [[ "$(git_log)" == *"Loop: Run ended (iteration-cap)"* ]]
}

@test "the Loop does not sweep an agent's uncommitted work into its own commit" {
    export LOOP_MAX_ITERATIONS=2
    export FAKE_AGENT_BEHAVIOURS="dirty dirty"

    run_the_loop
    # Two No-op Iterations in a row: the agent committed nothing.
    [ "$status" -eq 3 ]
    [[ "$(progress_log)" == *"Uncommitted changes left in the working tree"* ]]

    # The dirt is still dirt, visible to whoever reviews the Run.
    [[ "$(git -C "${REPO}" status --porcelain)" == *"work.txt"* ]]
}

# --- Preflight -------------------------------------------------------------

@test "a Run with no Plan does not start" {
    rm -f "${REPO}/PLAN.md"
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 1 ]
    [[ "$output" == *"no Plan at PLAN.md"* ]]
    [ "$(agent_invocations)" -eq 0 ]
}

@test "a Run against a non-repository does not start" {
    mkdir -p "${BATS_TEST_TMPDIR}/not-a-repo"
    export FAKE_AGENT_BEHAVIOURS="commit"

    run "${LOOP_SRC}/run.sh" --repo "${BATS_TEST_TMPDIR}/not-a-repo"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not a git repository"* ]]
}

@test "a bound that is not a positive integer stops the Run before it starts" {
    export LOOP_MAX_ITERATIONS=0
    export FAKE_AGENT_BEHAVIOURS="commit"

    run_the_loop
    [ "$status" -eq 1 ]
    [[ "$output" == *"LOOP_MAX_ITERATIONS must be a positive integer"* ]]
    [ "$(agent_invocations)" -eq 0 ]
}

# --- The state the Run reads and writes ------------------------------------

@test "the Plan and Progress Log paths are the Contract's, not hardcoded" {
    export LOOP_PLAN_PATH="docs/plan.md"
    export LOOP_PROGRESS_LOG_PATH="docs/progress.md"
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="noop"

    mkdir -p "${REPO}/docs"
    git -C "${REPO}" mv PLAN.md docs/plan.md
    git -C "${REPO}" mv PROGRESS.md docs/progress.md
    git -C "${REPO}" commit --quiet --message "Move the Run's state"

    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$(cat "${REPO}/docs/progress.md")" == *"Ended by: iteration-cap"* ]]
    [ ! -e "${REPO}/PROGRESS.md" ]
}

# --- The agent adapter -----------------------------------------------------

@test "a metered API key in the environment fails the agent instead of switching billing" {
    # The guard runs before the agent is exec'd, so this needs no agent
    # installed. Claude Code prefers ANTHROPIC_API_KEY over the subscription
    # login, and there is no per-Run spend ceiling behind it to catch the
    # switch - the Termination Contract is the whole cost control.
    run env ANTHROPIC_API_KEY=sk-not-a-real-key \
        "${LOOP_SRC}/agents/claude.sh" "${BATS_TEST_TMPDIR}/prompt" 40
    [ "$status" -eq 1 ]
    [[ "$output" == *"ANTHROPIC_API_KEY is set"* ]]
    [[ "$output" == *"metered"* ]]
}

# --- Proposal-Only Output ---------------------------------------------------
#
# The proposal is a Run's only external effect, and these assert what a Run does
# about it - not what propose.sh does, which has its own suite. The seam is the
# same one the agent uses: LOOP_PROPOSE_COMMAND, replaced by a scripted fake.

@test "a Run without --propose has no external effect and says so" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_PROPOSAL=skipped"* ]]
    [ -z "$(proposed_with)" ]
    [[ "$(progress_log)" == *"Proposal: none"* ]]
}

@test "a Run with --propose pushes and opens a draft pull request at its end" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop --propose
    [ "$status" -eq 0 ]
    [[ "$output" == *"LOOP_RUN_PROPOSAL=proposed"* ]]
    [[ "$output" == *"LOOP_PROPOSE_URL=https://github.com/owner/name/pull/999"* ]]
}

@test "the ending bound and the exit code reach the proposal" {
    export LOOP_MAX_ITERATIONS=4
    export FAKE_AGENT_BEHAVIOURS="commit noop noop"
    run_the_loop --propose
    [ "$status" -eq 3 ]
    [[ "$(proposed_with)" == *"consecutive-noops"* ]]
    [[ "$(proposed_with)" == *"--exit"* ]]
}

@test "the task reference reaches the proposal, so the pull request can name it" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop --propose --task-ref "owner/name#648"
    [[ "$(proposed_with)" == *"owner/name#648"* ]]
}

@test "a Run that went wrong still proposes - a failed Run is a result" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="fail"
    run_the_loop --propose
    [ "$status" -eq 4 ]
    [[ "$output" == *"LOOP_RUN_PROPOSAL=proposed"* ]]
}

@test "a clean Run whose proposal failed does not exit 0" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    export FAKE_PROPOSE_BEHAVIOUR=fail
    run_the_loop --propose
    [ "$status" -eq 6 ]
    [[ "$output" == *"LOOP_RUN_PROPOSAL=failed"* ]]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=iteration-cap"* ]]
}

@test "a failed proposal does not overwrite the bound that ended the Run" {
    export LOOP_MAX_ITERATIONS=4
    export FAKE_AGENT_BEHAVIOURS="commit noop noop"
    export FAKE_PROPOSE_BEHAVIOUR=fail
    run_the_loop --propose
    [ "$status" -eq 3 ]
    [[ "$output" == *"LOOP_RUN_ENDED_BY=consecutive-noops"* ]]
    [[ "$output" == *"LOOP_RUN_PROPOSAL=failed"* ]]
}

@test "the ending bound is still the first line when a proposal was made" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop --propose
    [[ "${lines[0]}" == "LOOP_RUN_ENDED_BY=iteration-cap" ]]
}

@test "the Progress Log records that a proposal was made, and is committed first" {
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop --propose
    [[ "$(progress_log)" == *"Proposal: pushing this branch"* ]]
    # Committed before the push, so what lands on the remote holds the record of
    # the Run that produced it.
    [ -z "$(git -C "${REPO}" status --porcelain -- PROGRESS.md)" ]
}

@test "a proposal command that is not executable stops the Run before it spends anything" {
    export LOOP_PROPOSE_COMMAND="${BATS_TEST_TMPDIR}/absent"
    run_the_loop --propose
    [ "$status" -eq 1 ]
    [ "$(agent_invocations)" -eq 0 ]
}

@test "a Run on origin's default branch is refused before it commits anything" {
    give_the_repo_a_remote
    head_before="$(git -C "${REPO}" rev-parse HEAD)"
    run_the_loop --propose
    [ "$status" -eq 1 ]
    [[ "$output" == *"default branch"* ]]
    [ "$(agent_invocations)" -eq 0 ]
    [ "$(git -C "${REPO}" rev-parse HEAD)" = "${head_before}" ]
}

@test "a Run on its own branch is not refused" {
    give_the_repo_a_remote
    git -C "${REPO}" checkout --quiet -b loop/run-1
    export LOOP_MAX_ITERATIONS=1
    export FAKE_AGENT_BEHAVIOURS="commit"
    run_the_loop --propose
    [ "$status" -eq 0 ]
}

@test "the branch refusal applies to a Run that was not going to propose either" {
    give_the_repo_a_remote
    run_the_loop
    [ "$status" -eq 1 ]
    [ "$(agent_invocations)" -eq 0 ]
}
