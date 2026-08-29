#!/usr/bin/env bash
#
# The provisioning smoke assertion for the PHP-capable guest template.
#
#   smoke-guest-template.sh --tag TAG --workspace DIR \
#                           [--agent NAME] [--marker PATH]
#
# Spec #151's testing decisions say this in one line: "The PHP guest gets a
# provisioning smoke assertion, not offline tests." There is nothing here a
# scripted fake could stand in for - the thing being asserted is that a real
# microVM, built from a real image, can run a real test suite - so this is a
# post-apply proof against the box rather than a bats file.
#
# ## Against a FRESH guest, and that is the whole point
#
# The build proves php runs in the sandbox it was installed in. That is not the
# same claim: a snapshot can lose things, and an image that works only in the
# sandbox it was cut from is exactly the failure this exists to catch. So this
# creates a new sandbox from the tag, the way an Iteration will, and destroys it
# afterwards, the way an Iteration does.
#
# ## What it proves, in the order it proves it
#
#   php           present, and version 8 or later - tourbot's composer.json
#                 requires >=8.0 and would refuse to install under anything less
#   composer      present and runnable under that php
#   the suite     `composer install` completes against tourbot's own lock file,
#                 and the runner it installs can load `phpunit.xml` and
#                 enumerate the suites in it
#
# ## Why it stops at enumerating the suites rather than running them
#
# A red test suite on tourbot's master is a fact about tourbot, not about this
# box's provisioning, and an apply that fails because somebody merged a broken
# test is an apply whose failure says the wrong thing. `--list-suites` is the
# strongest assertion that stays inside the question this script is asking: it
# only answers after PHPUnit has started under this PHP, parsed `phpunit.xml`
# and bootstrapped `vendor/autoload.php`, which is every provisioning failure
# mode there is.
#
# What a Run's Iteration records are for is the other half - the suite actually
# executing, with results - and that is observed there rather than asserted
# here.
#
# ## Its side effect, which is deliberate
#
# `composer install` runs in the checkout a Run works in, so it leaves `vendor/`
# populated there. That directory is gitignored in tourbot and the checkout
# persists between Runs, so the first Iteration to run the suite finds it
# already installed instead of spending its wall clock on the download. An
# apply is how that gets refreshed after a `composer.lock` change.

set -euo pipefail

die() {
    printf 'smoke-guest-template.sh: %s\n' "$*" >&2
    exit 1
}

tag=""
workspace=""
agent=claude
marker=""

while (($#)); do
    case "$1" in
        --tag) tag="${2:?--tag needs a value}"; shift 2 ;;
        --workspace) workspace="${2:?--workspace needs a value}"; shift 2 ;;
        --agent) agent="${2:?--agent needs a value}"; shift 2 ;;
        --marker) marker="${2:?--marker needs a value}"; shift 2 ;;
        *) die "unrecognised argument: $1" ;;
    esac
done

[[ -n ${tag} ]] || die "usage: smoke-guest-template.sh --tag TAG --workspace DIR"
[[ -n ${workspace} ]] || die "usage: smoke-guest-template.sh --tag TAG --workspace DIR"
[[ -d ${workspace} ]] || die "${workspace} is not a directory - the checkout a Run works in should be there"
[[ -f "${workspace}/composer.json" ]] ||
    die "${workspace} has no composer.json - this asserts against the tourbot checkout, not an empty directory"
# No default, for build-guest-template.sh's reason: the marker path belongs to
# the role's defaults, and a copy here is a second place to be wrong.
[[ -n ${marker} ]] || die "usage: smoke-guest-template.sh --marker PATH"

sbx="${LOOP_SBX_COMMAND:-sbx}"
command -v "${sbx}" >/dev/null 2>&1 || die "${sbx} is not on PATH"

# This assertion WRITES to the checkout a Run works in - `composer install`
# leaves `vendor/` there, which is the side effect the header calls deliberate -
# so it must not run while an Iteration is using it. Two Composers in one
# `vendor/` is a Run whose test runner disappears mid-Iteration, and the Run
# would report that as its own failure with nothing naming the apply that caused
# it.
#
# **`run.sh` is the signal, not a sandbox.** The obvious check is a running
# `loop-` sandbox - the boundary the adapter builds, named `loop-$$-<epoch>` -
# and it is the wrong one, which was found by writing it that way first. The
# boundary is per ITERATION: it is created and destroyed around each agent
# process, so between Iterations, while the Run is committing its Progress Log
# and deciding whether to go again, there is no sandbox at all. A check on the
# sandbox has a hole in it once per Iteration, and a Run that got hit in one of
# those holes would look exactly like a Run that failed by itself.
#
# `run.sh` exists for the whole Run, holes included. Both are checked - the
# sandbox catches a Run whose `run.sh` died and left a boundary up, which is the
# one case the process check misses.
#
# This assertion WRITES to the checkout a Run works in - `composer install`
# leaves `vendor/` there, which is the side effect the header calls deliberate -
# so this is the same hazard `loop_execution_boundary` already states in prose,
# "do not run an apply against a box mid-Run", made into a refusal rather than a
# sentence somebody has to have read.
#
# Deliberately not a wait. A Run is ninety minutes at its bound, and an apply
# that silently blocks for an hour and a half is worse than one that stops and
# says why.
run_in_flight=""
if pgrep -f '/run\.sh --repo' >/dev/null 2>&1; then
    run_in_flight="run.sh is executing"
elif "${sbx}" ls 2>/dev/null | grep -qE '^loop-'; then
    run_in_flight="an Iteration's boundary is up with no run.sh behind it"
fi
if [[ -n ${run_in_flight} ]]; then
    die "a Run is in flight on this box - ${run_in_flight}.
This assertion installs into ${workspace}, which that Run is working in, so it
would pull the test runner out from under a live Iteration. Wait for the Run to
end, then apply again."
fi

sandbox="tmpl-smoke-$$-$(date +%s)"
# shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
cleanup() { "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

"${sbx}" create --quiet --name "${sandbox}" -t "${tag}" "${agent}" "${workspace}" >&2 ||
    die "no sandbox could be created from ${tag}"

# No per-sandbox egress rules here, and their absence is an assertion rather
# than an omission. `composer install` reaches `repo.packagist.org` and
# `codeload.github.com`, which `loop_execution_boundary` allows GLOBALLY because
# an Iteration needs them too. Adding them at `sandbox:` scope here would make
# this pass on a box whose global posture is missing them - and then every real
# Run would fail at the first `composer install` with nothing having warned
# anybody.

in_guest() { "${sbx}" exec --workdir "${workspace}" "${sandbox}" bash -lc "$1"; }

# --- What the guest says it was built from -----------------------------------
#
# Reported rather than asserted, and the distinction is worth a line. The build
# script has just compared this marker against the declared recipe and rebuilt
# if they differed, so asserting it again here would be the same comparison
# twice, failing in exactly the same cases. What it is FOR is the apply's
# output: the recipe is the only thing that says which packages the boundary a
# Run is about to be bounded by actually holds, and a tag alone does not.
recipe="$(in_guest "cat ${marker}" 2>/dev/null || true)"

# --- php ---------------------------------------------------------------------

php_version="$(in_guest 'php -r "echo PHP_VERSION;"')" ||
    die "php did not run inside a fresh guest from ${tag}"
php_major="${php_version%%.*}"
((php_major >= 8)) ||
    die "the guest has php ${php_version}; tourbot's composer.json requires >=8.0 and would refuse to install"

# --- composer ----------------------------------------------------------------

composer_version="$(in_guest 'composer --version --no-interaction 2>/dev/null | head -n1')" ||
    die "composer did not run inside a fresh guest from ${tag}"
[[ -n ${composer_version} ]] || die "composer answered nothing when asked its version"

# --- the suite runner --------------------------------------------------------
#
# `--no-interaction` because there is no terminal, and `--no-progress` because
# the output is read by whoever is looking at an apply rather than by a person
# watching a bar. Not `--no-dev`: PHPUnit is a dev requirement, so the runner
# this whole script is about would not be installed.
in_guest 'composer install --no-progress --prefer-dist --no-interaction' >&2 ||
    die "composer install failed in the guest. If it could not reach
repo.packagist.org or codeload.github.com, the box's global egress allowlist is
missing them - see ansible/roles/loop_execution_boundary/defaults/main.yml."

runner_version="$(in_guest './vendor/bin/phpunit --version | head -n1')" ||
    die "the suite runner composer installed did not run under the guest's php"

suites="$(in_guest './vendor/bin/phpunit --list-suites')" ||
    die "the suite runner could not load phpunit.xml and enumerate the suites in it"

printf 'A fresh guest from %s runs the suite.\n' "${tag}"
printf '  recipe:   %s\n' "${recipe:-<no marker>}"
printf '  php:      %s\n' "${php_version}"
printf '  composer: %s\n' "${composer_version}"
printf '  runner:   %s\n' "${runner_version}"
printf '%s\n' "${suites}" | sed 's/^/  /'
