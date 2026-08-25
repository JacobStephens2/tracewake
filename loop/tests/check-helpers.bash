# Shared setup for the completeness check's suite.
#
# Every test builds a small checkout - a handful of files holding a handful of
# occurrences - and an inventory alongside it. Nothing here reaches a model, a
# network, or the real Tourbot checkout: the check is a pure function of a
# checkout path and an inventory path, and the suite exercises it as one.
#
# shellcheck shell=bash

setup_check_fixture() {
    LOOP_SRC="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd)"
    export LOOP_SRC
    CHECK="${LOOP_SRC}/check-inventory.sh"
    export CHECK

    CHECKOUT="${BATS_TEST_TMPDIR}/checkout"
    export CHECKOUT
    INVENTORY="${BATS_TEST_TMPDIR}/inventory.md"
    export INVENTORY

    mkdir -p "${CHECKOUT}"
    git -C "${CHECKOUT}" init --quiet --initial-branch=main
    git -C "${CHECKOUT}" config user.email "loop@example.invalid"
    git -C "${CHECKOUT}" config user.name "The Loop"
    git -C "${CHECKOUT}" config commit.gpgsign false
}

# Write a file into the checkout and track it. Untracked files are deliberately
# not occurrences, so every fixture file goes through here.
checkout_file() {
    local path="$1"
    mkdir -p -- "$(dirname -- "${CHECKOUT}/${path}")"
    cat >"${CHECKOUT}/${path}"
    git -C "${CHECKOUT}" add -- "${path}"
    git -C "${CHECKOUT}" commit --quiet --message "add ${path}"
}

write_inventory() {
    cat >"${INVENTORY}"
}

run_the_check() {
    run "${CHECK}" --checkout "${CHECKOUT}" --inventory "${INVENTORY}" "$@"
}

# The three-occurrence checkout most tests start from: one read that should
# include notes, one read that is about email by design, one write.
seed_three_occurrences() {
    checkout_file "mtourbot/reports/contact_history.php" <<'PHP'
<?php
$sql = "SELECT * FROM tblEmailMessage WHERE ResID = ?";
render($sql);
PHP
    checkout_file "mtourbot/alerts/bounce_report.php" <<'PHP'
<?php
$sql = "SELECT Bounced FROM tblEmailMessage WHERE Bounced = 1";
PHP
    checkout_file "mtourbot/classes/Mailer.php" <<'PHP'
<?php
$db->insert("tblEmailMessage", $row);
PHP
}

# The inventory that accounts for all three.
seed_complete_inventory() {
    write_inventory <<'MD'
# tblEmailMessage inventory

## Dashboards and reports

- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab shows an empty timeline for reservations whose only contact was a note.
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design - a bounce is a
  property of a sent email; notes cannot bounce.

## Export/import and remaining surfaces

- `mtourbot/classes/Mailer.php:2` - write
MD
}
