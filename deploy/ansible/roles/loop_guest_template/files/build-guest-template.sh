#!/usr/bin/env bash
#
# Build the PHP-capable guest template an Iteration's microVM is created from.
#
#   build-guest-template.sh --tag TAG --packages "p1 p2 ..." \
#                           [--agent NAME] [--allow HOST:PORT,...] \
#                           [--marker PATH] [--force]
#
# Run on the Loop's box, as the `loop` account, by ansible's
# `loop_guest_template` role. Everything it needs is an argument rather than a
# variable read from the environment, so the role's defaults stay the one place
# the recipe is written down and this script can be driven by hand to reproduce
# a build.
#
# ## Why a script rather than a list of ansible tasks
#
# The build is a sequence with a cleanup obligation - create a sandbox, reach
# the network from inside it, install, snapshot, destroy - and the destroy has
# to happen on every path including the ones that fail. A `block`/`always` pair
# across nine `command:` tasks says that less clearly than a trap does, and a
# half-built sandbox left behind by a failed apply is the thing that makes the
# NEXT apply ambiguous.
#
# ## The decision it makes, and the thing that is NOT the decision
#
# It rebuilds when the image is absent, or when the recipe recorded inside the
# image differs from the one it was handed. It does NOT rebuild on the version
# in the tag alone: a tag is a name somebody types, and a package added to the
# role's list with the version left alone would otherwise be a change that
# silently never reached the box. The marker is what makes the recipe the
# trigger; the version is what makes the CHANGE visible on the box card.
#
# The consequence worth stating: moving the version WITHOUT changing the recipe
# does rebuild, because the tag is part of the marker and the new tag does not
# exist yet. That is the right way round - a version bump is somebody saying
# they want the boundary to be a new thing, and a rebuild is what makes that
# true.
#
# ## Egress
#
# The build reaches the Ubuntu archives, which the box's `deny-all` global
# posture does not allow and should not: #100 narrowed that to two hosts and a
# template build is not a reason to widen it. `sbx policy allow network
# --sandbox` writes the rule at `sandbox:` scope instead, so it applies to this
# build's sandbox alone and is gone when the sandbox is - which is why there is
# no matching removal below and why a failed build leaves no posture behind.
#
# Two facts about that scoping, both learned on the box rather than assumed:
# a rule added to a sandbox that is ALREADY running does not reach the proxy
# that sandbox is using, so every allow here happens before anything inside it
# touches the network; and the vendor's `claude` kit rule is attached at the
# same scope by `sbx create` even under a custom `-t` image, so the guest keeps
# the six Anthropic hosts without this script naming any of them.

set -euo pipefail

die() {
    printf 'build-guest-template.sh: %s\n' "$*" >&2
    exit 1
}

tag=""
packages=""
marker=""
agent=claude
force=false
allow=""
verify=""

while (($#)); do
    case "$1" in
        --tag) tag="${2:?--tag needs a value}"; shift 2 ;;
        --packages) packages="${2:?--packages needs a value}"; shift 2 ;;
        --marker) marker="${2:?--marker needs a value}"; shift 2 ;;
        --agent) agent="${2:?--agent needs a value}"; shift 2 ;;
        --allow) allow="${2:?--allow needs a value}"; shift 2 ;;
        --verify) verify="${2:?--verify needs a value}"; shift 2 ;;
        --force) force=true; shift ;;
        *) die "unrecognised argument: $1" ;;
    esac
done

[[ -n ${tag} ]] || die "usage: build-guest-template.sh --tag TAG --packages \"p1 p2 ...\""
[[ -n ${packages} ]] || die "usage: build-guest-template.sh --tag TAG --packages \"p1 p2 ...\""
# No default, deliberately. The marker path is what the rebuild decision turns
# on, and a default here would be a second declaration of it - this script and
# the role's defaults able to disagree about where the recipe is recorded, which
# reads as "the image was built from something else" and rebuilds on every apply.
[[ -n ${marker} ]] || die "usage: build-guest-template.sh --marker PATH"

sbx="${LOOP_SBX_COMMAND:-sbx}"
command -v "${sbx}" >/dev/null 2>&1 ||
    die "${sbx} is not on PATH - the template is built by the Execution Boundary, not beside it"

# The recipe, as one line, and the ONLY thing compared to decide a rebuild.
# Sorted, because the order two people write a package list in is not part of
# what the image is, and an unsorted comparison would rebuild on a reordering.
# shellcheck disable=SC2086  # the split is the point: --packages is one space-separated list
recipe="tag=${tag} packages=$(printf '%s\n' ${packages} | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"

# --- Is the box already holding this? ---------------------------------------
#
# Two questions, in the order that makes the cheap one answer first. `sbx
# template ls` is a read against the image store; reading the marker costs a
# guest boot, so it only happens once the tag is known to exist.

have_tag=false
if "${sbx}" template ls 2>/dev/null | awk '{print $1 ":" $2}' | grep -qE "(^|/)${tag//./\\.}$"; then
    have_tag=true
fi

if [[ ${force} == false && ${have_tag} == true ]]; then
    probe="tmpl-probe-$$-$(date +%s)"
    # A workspace is a required argument to `sbx create` and this boot only ever
    # reads one file, so it gets an empty directory of its own rather than the
    # checkout - a probe that mounted the repository could write to it.
    #
    # Made BEFORE the trap is armed and removed BY it. The other order looks
    # equivalent and is not: the `die` below leaves through the trap, so a
    # directory the trap does not know about is one empty tmpdir left behind per
    # apply that finds a template it cannot boot.
    probe_ws="$(mktemp -d)"
    # shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
    drop_probe() {
        "${sbx}" rm --force "${probe}" >/dev/null 2>&1 || true
        rmdir -- "${probe_ws}" 2>/dev/null || true
    }
    trap drop_probe EXIT INT TERM
    "${sbx}" create --quiet --name "${probe}" -t "${tag}" "${agent}" "${probe_ws}" >&2 ||
        die "the image store has ${tag} but a sandbox cannot be created from it"
    on_box="$("${sbx}" exec "${probe}" cat "${marker}" 2>/dev/null || true)"
    drop_probe
    trap - EXIT INT TERM

    if [[ ${on_box} == "${recipe}" ]]; then
        printf '%s is already built from this recipe.\n' "${tag}"
        exit 0
    fi
    printf 'Rebuilding %s: the image was built from a different recipe.\n  on the box: %s\n  declared:   %s\n' \
        "${tag}" "${on_box:-<no marker>}" "${recipe}"
fi

# --- Build -------------------------------------------------------------------

sandbox="tmpl-build-$$-$(date +%s)"
workspace="$(mktemp -d)"

# The sandbox is removed on every path out of here, including the ones that
# fail. A trap rather than a line at the end, for the same reason the adapter
# uses one: a build killed between create and save must not leave a sandbox
# behind for the next apply to find and be confused by.
# shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
cleanup() {
    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true
    rmdir -- "${workspace}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"${sbx}" create --quiet --name "${sandbox}" "${agent}" "${workspace}" >&2 ||
    die "could not create the sandbox the template is built in"

# One command rather than one per host, because `sbx policy allow network` takes
# the comma-separated list and writes a rule per resource either way. An empty
# list is a legitimate build - a template that installs nothing from the network
# needs no egress at all - and `sbx policy allow network ""` is not a command.
if [[ -n ${allow} ]]; then
    "${sbx}" policy allow network --sandbox "${sandbox}" "${allow}" >&2 ||
        die "could not allow ${allow} for the build sandbox"
fi

# Three deliberate things in one command, in the order they appear in it.
#
# **The wait.** Not defensive padding. A freshly created guest runs an `apt-get`
# of its own during boot, and this command lands in the middle of it:
#
#   E: Could not get lock /var/lib/apt/lists/lock. It is held by process 296 (apt-get)
#
# which is how the first apply of this role failed. It never showed up while the
# same steps were driven by hand, because typing the next command takes longer
# than the guest's own apt does - the kind of race that only appears once
# something automates it. `DPkg::Lock::Timeout` alone would cover it on apt 3
# and is passed as well; the bounded wait is what keeps the failure legible if
# the guest is ever doing something longer than an apt run.
#
# **The source exclusion**, rather than moving
# `/etc/apt/sources.list.d/docker.list` aside and putting it back. The stock
# image carries Docker's apt repository, which is not on the build's allowlist
# and does not need to be - nothing installed here comes from it - so an
# unrestricted `apt-get update` fails on a 403 from the egress proxy and takes
# the whole build with it. Overriding it for one command leaves the saved
# template's apt configuration the vendor's, untouched, so a guest from this
# image can still see Docker's repository if an Iteration ever needs it.
#
# **`--no-install-recommends`**, as `loop_execution_boundary` does and for the
# same reason: this image is the Execution Boundary, and the boundary holds as
# little as it can. `apt-get clean` after it because the package cache is a
# hundred megabytes of nothing in an image copied for every Iteration.
"${sbx}" exec "${sandbox}" bash -c "
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    for _ in \$(seq 1 60); do
        pgrep -x apt-get >/dev/null 2>&1 || break
        sleep 5
    done
    sudo apt-get update -qq -o DPkg::Lock::Timeout=300 \
        -o Dir::Etc::SourceParts=/dev/null \
        -o Dir::Etc::sourcelist=sources.list.d/ubuntu.sources
    sudo apt-get install -y -qq --no-install-recommends -o DPkg::Lock::Timeout=300 ${packages}
    sudo apt-get clean
    sudo rm -rf /var/lib/apt/lists/*
" >&2 || die "the package install failed inside the build sandbox"

# The recipe, recorded where a fresh guest can be asked for it. Written last, so
# an image carrying a marker is an image whose install completed - a marker
# written first would make a half-installed template look current to the next
# apply.
"${sbx}" exec "${sandbox}" bash -c "printf '%s' $(printf '%q' "${recipe}") | sudo tee ${marker} >/dev/null" >&2 ||
    die "could not record the recipe in the image"

# Proof before the snapshot rather than after it. This is not the smoke
# assertion - that runs against a FRESH guest, which is the only thing that
# proves the template rather than the sandbox it was made from - but a build
# that produced no working environment should fail here, where the message is about
# the build, rather than one step later where it is about the template.
if [[ -n ${verify} ]]; then
    "${sbx}" exec "${sandbox}" bash -lc "${verify}" >&2 ||
        die "verification command '${verify}' did not run in the sandbox"
elif [[ " ${packages} " == *" php "* || " ${packages} " == *" php-"* || " ${packages} " == *" composer "* ]]; then
    "${sbx}" exec "${sandbox}" bash -lc 'php --version >/dev/null && composer --version >/dev/null' >&2 ||
        die "php or composer did not run in the sandbox they were just installed in"
else
    "${sbx}" exec "${sandbox}" bash -lc "dpkg -s ${packages} >/dev/null" >&2 ||
        die "installed packages did not report installed via dpkg in the sandbox"
fi

# `sbx template save` refuses to snapshot a running sandbox, and says so.
"${sbx}" stop "${sandbox}" >&2 || die "could not stop the build sandbox"
"${sbx}" template save "${sandbox}" "${tag}" >&2 || die "could not save ${tag}"

printf 'Built %s.\n  recipe: %s\n' "${tag}" "${recipe}"
