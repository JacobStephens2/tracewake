#!/usr/bin/env bats
#
# The completeness check's offline suite (issue #80).
#
# The check is the Run's only honest failure signal on a task with no test
# suite, so it is seamed and tested on its own rather than only through the Loop
# (spec issue #73, Seam B). Every test here asserts what the check externally
# produces - its exit code and its report - and never how it is written inside.

load check-helpers

setup() {
    setup_check_fixture
}

# --- The denominator is derived from the checkout ---------------------------

@test "a complete inventory exits zero and reports the derived count" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == "CHECK_RESULT=complete"* ]]
    [[ ${output} == *"CHECK_OCCURRENCES=3"* ]]
    [[ ${output} == *"CHECK_ACCOUNTED=3"* ]]
}

@test "an occurrence added after the inventory was written fails the check" {
    seed_three_occurrences
    seed_complete_inventory
    checkout_file "mtourbot/tools/resend.php" <<'PHP'
<?php
$rows = query("SELECT * FROM tblEmailMessage WHERE ResID = ?");
PHP

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_OCCURRENCES=4"* ]]
    [[ ${output} == *"mtourbot/tools/resend.php:2"* ]]
}

@test "a count written in the inventory does not become the denominator" {
    seed_three_occurrences
    write_inventory <<'MD'
# tblEmailMessage inventory

The task says there are 276 occurrences across 78 files. All accounted for.

- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab under-counts when the only contact was a note.
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_OCCURRENCES=3"* ]]
    [[ ${output} == *"CHECK_MISSING=2"* ]]
}

@test "a checkout the symbol does not appear in is an error, not a pass" {
    checkout_file "mtourbot/reports/nothing.php" <<'PHP'
<?php
$sql = "SELECT 1";
PHP
    write_inventory <<'MD'
# Nothing to see
MD

    run_the_check

    [ "${status}" -eq 1 ]
    [[ ${output} == *"denominator of zero"* ]]
}

@test "only tracked files count towards the denominator" {
    seed_three_occurrences
    seed_complete_inventory
    printf '<?php $x = "tblEmailMessage";\n' >"${CHECKOUT}/scratch.php"

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_OCCURRENCES=3"* ]]
}

@test "the symbol matches case-insensitively and once per line" {
    seed_three_occurrences
    seed_complete_inventory
    checkout_file "mtourbot/reports/join.php" <<'PHP'
<?php
$sql = "SELECT * FROM tblemailmessage e JOIN TBLEMAILMESSAGE f ON e.ID = f.ID";
PHP
    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_OCCURRENCES=4"* ]]
    [[ ${output} == *"mtourbot/reports/join.php:2"* ]]
}

@test "--symbol changes what is being inventoried" {
    checkout_file "mtourbot/reports/notes.php" <<'PHP'
<?php
$sql = "SELECT * FROM tblResNote";
PHP
    write_inventory <<'MD'
- `mtourbot/reports/notes.php:2` - write
MD

    run_the_check --symbol tblResNote

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_SYMBOL=tblResNote"* ]]
}

# --- Exclusions and scope, both declared in the report ----------------------

@test "non-application paths are excluded and the exclusion is reported" {
    seed_three_occurrences
    seed_complete_inventory
    checkout_file "mysql_files/2026_08_01_notes.sql" <<'SQL'
ALTER TABLE tblEmailMessage ADD COLUMN Foo INT;
SQL
    checkout_file "specifications/emails.md" <<'MD'
The tblEmailMessage table holds sent email.
MD
    checkout_file "vendor/acme/mailer/src/Mailer.php" <<'PHP'
<?php $t = "tblEmailMessage";
PHP

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_OCCURRENCES=3"* ]]
    [[ ${output} == *"CHECK_EXCLUDED=3"* ]]
    [[ ${output} == *"mysql_files/"* ]]
}

@test "--exclude removes further paths and says how many it removed" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check --exclude 'mtourbot/alerts/*'

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_OCCURRENCES=2"* ]]
    [[ ${output} == *"CHECK_EXCLUDED=1"* ]]
    [[ ${output} == *"CHECK_OUT_OF_SCOPE=1"* ]]
    [[ ${output} == *"mtourbot/alerts/*"* ]]
}

@test "--scope narrows the denominator to one owning area" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab shows nothing for reservations whose only contact was a note.
MD

    run_the_check --scope 'mtourbot/reports/*'

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_OCCURRENCES=1"* ]]
    [[ ${output} == *"CHECK_SCOPE=mtourbot/reports/*"* ]]
}

@test "an entry for a real occurrence outside the scope is not a stale entry" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check --scope 'mtourbot/reports/*'

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_OUT_OF_SCOPE=2"* ]]
}

# --- What the inventory has to account for ----------------------------------

@test "a missing occurrence is named, with the line that produced it" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab under-counts when the only contact was a note.
- `mtourbot/classes/Mailer.php:2` - write
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_MISSING=1"* ]]
    [[ ${output} == *"mtourbot/alerts/bounce_report.php:2"* ]]
    [[ ${output} == *"SELECT Bounced FROM tblEmailMessage"* ]]
}

@test "an empty inventory lists every occurrence rather than only counting them" {
    seed_three_occurrences
    write_inventory <<'MD'
# tblEmailMessage inventory
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_MISSING=3"* ]]
    [[ ${output} == *"mtourbot/reports/contact_history.php:2"* ]]
    [[ ${output} == *"mtourbot/alerts/bounce_report.php:2"* ]]
    [[ ${output} == *"mtourbot/classes/Mailer.php:2"* ]]
}

@test "an entry naming a line that no longer holds the symbol is stale" {
    seed_three_occurrences
    seed_complete_inventory
    checkout_file "mtourbot/classes/Mailer.php" <<'PHP'
<?php
// the insert moved to the repository class
PHP

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_STALE=1"* ]]
    [[ ${output} == *"mtourbot/classes/Mailer.php:2"* ]]
}

@test "an entry naming a file that does not exist is stale" {
    seed_three_occurrences
    seed_complete_inventory
    printf -- '- `mtourbot/reports/deleted.php:12` - write\n' >>"${INVENTORY}"

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_STALE=1"* ]]
    [[ ${output} == *"mtourbot/reports/deleted.php:12"* ]]
}

@test "the same occurrence classified twice is a duplicate" {
    seed_three_occurrences
    seed_complete_inventory
    printf -- '- `mtourbot/classes/Mailer.php:2` - write\n' >>"${INVENTORY}"

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_DUPLICATE=1"* ]]
    [[ ${output} == *"mtourbot/classes/Mailer.php:2"* ]]
}

@test "an entry with no classification is not an accounted occurrence" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - looks fine to me
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design - bounces are a
  property of a sent email.
- `mtourbot/classes/Mailer.php:2` - write
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_UNCLASSIFIED=1"* ]]
    [[ ${output} == *"mtourbot/reports/contact_history.php:2"* ]]
    # Reported once, as what is wrong with it - not again as an occurrence
    # nobody wrote down.
    [[ ${output} == *"CHECK_MISSING=0"* ]]
}

@test "two classifications on one entry is ambiguous" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab under-counts when the only contact was a note.
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design - bounces are a
  property of a sent email; notes cannot bounce.
- `mtourbot/classes/Mailer.php:2` - write, or possibly should-include-notes
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_AMBIGUOUS=1"* ]]
    [[ ${output} == *"CHECK_MISSING=0"* ]]
}

@test "a rationale may use the word write without reading as two classifications" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes - the timeline
  is empty for reservations whose only contact was a note, because the write
  that produced it went to the note table.
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design - bounces are a
  property of a sent email; notes cannot bounce.
- `mtourbot/classes/Mailer.php:2` - write
MD

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_AMBIGUOUS=0"* ]]
    [[ ${output} == *"should-include-notes: 1"* ]]
}

@test "a should-include-notes entry without a symptom is unjustified" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design - bounces are a
  property of a sent email; notes cannot bounce.
- `mtourbot/classes/Mailer.php:2` - write
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_UNJUSTIFIED=1"* ]]
    [[ ${output} == *"mtourbot/reports/contact_history.php:2"* ]]
}

@test "an emails-only-by-design entry without a reason is unjustified" {
    seed_three_occurrences
    write_inventory <<'MD'
- `mtourbot/reports/contact_history.php:2` - should-include-notes - the contact
  history tab under-counts when the only contact was a note.
- `mtourbot/alerts/bounce_report.php:2` - emails-only-by-design
- `mtourbot/classes/Mailer.php:2` - write
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [[ ${output} == *"CHECK_UNJUSTIFIED=1"* ]]
    [[ ${output} == *"mtourbot/alerts/bounce_report.php:2"* ]]
}

@test "a write entry needs no rationale" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == *"CHECK_UNJUSTIFIED=0"* ]]
}

@test "the report breaks the inventory down by classification" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check

    [ "${status}" -eq 0 ]
    [[ ${output} == *"should-include-notes: 1"* ]]
    [[ ${output} == *"emails-only-by-design: 1"* ]]
    [[ ${output} == *"write: 1"* ]]
}

# --- Usable as backpressure and as acceptance -------------------------------

@test "the check leaves the checkout untouched" {
    seed_three_occurrences
    seed_complete_inventory

    run_the_check

    [ "${status}" -eq 0 ]
    run git -C "${CHECKOUT}" status --porcelain
    [ -z "${output}" ]
}

@test "the first line is the machine-readable result on every outcome" {
    seed_three_occurrences
    write_inventory <<'MD'
# empty
MD

    run_the_check

    [ "${status}" -eq 2 ]
    [ "${lines[0]}" = "CHECK_RESULT=incomplete" ]
}

# --- Refusing to run rather than passing wrongly ----------------------------

@test "a missing --checkout is a usage error" {
    run "${CHECK}" --inventory "${INVENTORY}"

    [ "${status}" -eq 1 ]
    [[ ${output} == *"--checkout is required"* ]]
}

@test "a checkout that is not a git repository is an error" {
    seed_three_occurrences
    seed_complete_inventory
    rm -rf "${CHECKOUT}/.git"

    run_the_check

    [ "${status}" -eq 1 ]
    [[ ${output} == *"not a git repository"* ]]
}

@test "an inventory that does not exist is an error" {
    seed_three_occurrences

    run "${CHECK}" --checkout "${CHECKOUT}" --inventory "${BATS_TEST_TMPDIR}/absent.md"

    [ "${status}" -eq 1 ]
    [[ ${output} == *"no such file"* ]]
}
