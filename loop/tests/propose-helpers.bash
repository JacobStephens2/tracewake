# Shared setup for the proposal's offline suite (#83).
#
# Every test builds a throwaway repository with a real git remote - a bare
# repository in a tmpdir - and drives the real propose.sh at it. The push is
# therefore a real `git push`, which is the half of this script most worth
# testing for real: a bare repository answers the same protocol GitHub does, and
# a push that would fail on GitHub fails here.
#
# The pull request is the other half, and it is the scripted fake, for the same
# reason the agent is: LOOP_PR_COMMAND is a substitution point (ADR 0004), so
# the suite reaches no network, needs no token, and leaves no draft pull request
# behind on a real repository every time it runs.
#
# shellcheck shell=bash

setup_propose_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC
    PROPOSE="${LOOP_SRC}/propose.sh"
    export PROPOSE

    # The remote. Bare, so it accepts a push to any branch, and given a HEAD so
    # propose.sh can read a default branch out of it the way it reads one out of
    # GitHub's.
    REMOTE="${BATS_TEST_TMPDIR}/remote.git"
    git init --quiet --bare --initial-branch=master "${REMOTE}"
    export REMOTE

    TARGET_URL="https://github.com/Educational-Travel-Adventures/tourbot.git"
    export TARGET_URL

    REPO="${BATS_TEST_TMPDIR}/repo"
    git init --quiet --initial-branch=master "${REPO}"
    git -C "${REPO}" config user.email "loop@example.invalid"
    git -C "${REPO}" config user.name "The Loop"
    git -C "${REPO}" config commit.gpgsign false
    # One remote, and it is the GitHub URL the box really has - so the
    # owner/name derivation is exercised rather than bypassed by a bare path.
    # `insteadOf` sends what git actually pushes to the bare repository above,
    # which is invisible to `git remote get-url` and therefore to propose.sh.
    # The alternative - a second remote pointing at a path - would test a code
    # path the box never takes and would leave the derivation untested.
    git -C "${REPO}" remote add origin "${TARGET_URL}"
    git -C "${REPO}" config "url.${REMOTE}.insteadOf" "${TARGET_URL}"

    write_plan
    printf '# Progress Log\n' >"${REPO}/PROGRESS.md"
    git -C "${REPO}" add -A
    git -C "${REPO}" commit --quiet --message "Seed the Run"
    git -C "${REPO}" push --quiet origin master
    git -C "${REPO}" remote set-head origin master
    git -C "${REPO}" checkout --quiet -b loop/run-648
    export REPO

    # The scripted fake pull-request command. It records what it was asked for
    # so a test can assert the head, the base and the title the Run proposed,
    # and FAKE_PR_BEHAVIOUR says what GitHub answers.
    FAKE_PR_STATE="${BATS_TEST_TMPDIR}/fake-pr"
    export FAKE_PR_STATE
    export FAKE_PR_BEHAVIOUR=ok
    export LOOP_PR_COMMAND="${LOOP_SRC}/tests/fake-pr-source.sh"
}

write_plan() {
    cat >"${REPO}/PLAN.md" <<'PLAN'
# Plan

## Task

**Educational-Travel-Adventures/tourbot#648 - Audit every tblEmailMessage read and classify it**

https://github.com/Educational-Travel-Adventures/tourbot/issues/648

State when this Run was seeded: OPEN

## The owning area this Run is scoped to

**dashboards and reports**

## Acceptance criteria

- [ ] something

## Remaining work

- [ ] nothing yet
PLAN
}

# Drive the real script, with every default it has on the box.
run_propose() {
    run "${PROPOSE}" --repo "${REPO}" "$@"
}

# Send the rewrite somewhere there is no repository. The remote's URL is
# untouched, so propose.sh still derives owner/name from it and still gets as
# far as the push - which is the failure this models.
break_the_push() {
    # Removed rather than shadowed: two rewrites claiming the same URL is an
    # ambiguity git resolves by keeping one and warning, and a test that turned
    # on which one would be a test of git.
    git -C "${REPO}" config --unset-all "url.${REMOTE}.insteadOf"
    git -C "${REPO}" config "url.${BATS_TEST_TMPDIR}/not-a-repository.insteadOf" "${TARGET_URL}"
}

pr_field() {
    grep -E "^${1}=" <"${FAKE_PR_STATE}" | head -n1 | cut -d= -f2-
}

# shellcheck disable=SC2154
field() {
    grep -E "^${1}=" <<<"${output}" | head -n1 | cut -d= -f2-
}

remote_has_branch() {
    git -C "${REMOTE}" rev-parse --verify --quiet "refs/heads/$1" >/dev/null
}
