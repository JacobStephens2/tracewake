#!/usr/bin/env bash
#
# The agent, as one substitutable command (ADR 0004), executing inside the
# Execution Boundary.
#
#   claude.sh <prompt-file> <max-turns>
#   claude.sh --guest-template     the `sbx` template the boundary is built from
#   claude.sh --metered-env-names  the names that would supersede the subscription
#   claude.sh --credential-expiry  when the box's model credential stops working
#   claude.sh --refresh-credential renew it if it is close to stopping
#
# Invoked by run.sh with the repository as the working directory, once per
# Iteration, as a fresh process. Exits with the agent's exit status. Swapping
# vendor is writing a sibling of this file and pointing LOOP_AGENT_COMMAND at
# it - which is exactly what the offline suite does with its scripted fake, and
# what #84 will do with Grok Build.
#
# Vendor-specific concerns belong HERE rather than in the loop, and two of them
# earn that distinction: the credential guard below defends against a collision
# that is a property of this agent, and `sbx create ... claude` names the kit
# that exists for this agent and not for the next one.
#
# ## One microVM per Iteration
#
# The boundary is created here and destroyed here, so an Iteration cannot leave
# anything behind for the next one to find. That is the same forgetting the
# fresh process gives the agent's context, applied to its filesystem: state that
# survives an Iteration has to be on disk in the repository, where a reviewer
# sees it, rather than in a sandbox nobody reads.
#
# The repository is bind-mounted at its own path, so a commit made inside the
# guest is a commit in the checkout on the host. That is the whole integration:
# run.sh compares the head before and after and knows nothing about any of this.
#
# ## What goes in, and what deliberately does not
#
# In: the model credential, the signing key, and the git identity that uses it.
# An Iteration that could not sign would produce commits that are not Verified,
# and the first place anyone would find out is the pull request.
#
# NOT in: the GitHub token. The push and the draft pull request happen on the
# host after every agent process is gone (propose.sh). An agent inside the
# boundary therefore cannot push, cannot open a pull request and cannot reach
# anything with the token, and Proposal-Only Output is a property of what is
# inside the boundary rather than of what the prompt asked for.
#
# The signing key IS inside, and that is a stated cost rather than an oversight:
# an agent in the guest can read it. What it can do with it is bounded by the
# egress allowlist - github.com and api.github.com, both of which need the token
# it does not have - and by the key being dedicated to the Loop and revocable on
# its own (ADR 0005). Recorded in the first Run's evidence note.

set -euo pipefail

# The Contract, for one value: the exit status that means the turn bound fired.
# Sourced rather than restated, because run.sh reads the same declaration and two
# files agreeing on a number by both spelling it out is a seam that breaks
# silently - the Loop would simply stop recognising the bound.
agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=../contract.sh
source "${agent_dir}/../contract.sh"

# Every environment variable name that would supersede the subscription login.
# Declared here because which names a vendor honours is vendor knowledge, which
# ADR 0004 puts in the adapter; `assert-credentials.sh` reads this list rather
# than restating it.
metered_env_names=(
    ANTHROPIC_API_KEY
    ANTHROPIC_AUTH_TOKEN
)

# The `sbx` template every Iteration's microVM is built from. Declared here,
# once, and used by the create below rather than the create naming it a second
# time: the template IS the Execution Boundary the Run was bounded by (ADR
# 0003), and it is a thing that may change.
#
# It has now changed. This was `claude`, the vendor's stock image, and story 33
# of spec #151 asked for a PHP-capable guest so `/tdd` is real
# red-green-refactor rather than an Iteration asserting that a test would have
# failed. `loop-php:1` is that image: the vendor's, plus PHP and Composer, built
# and snapshotted ON the box by `ansible/roles/loop_guest_template`. The
# fallback the story pre-agreed - stay on the stock image - was not taken; the
# evidence note records what it cost instead.
#
# It is now an IMAGE rather than an agent name, and the create below reflects
# that: `sbx create -t <image> claude`, where `claude` is still the agent whose
# kit is attached. Those are two different arguments and it matters that they
# are: the kit is what carries the six Anthropic egress rules at `sandbox:`
# scope, and a custom image does not replace it.
#
# The box has to be holding it. Nothing here falls back to the stock image if it
# is missing, deliberately - a Run that quietly ran without a test runner would
# produce Iterations whose Progress Log says the suite could not be found, and
# the first place anyone would read that is the Proposal. `sbx create` fails
# instead, and the die below names the play that puts the image there.
#
# Readable from outside for the same reason `--metered-env-names` is: the
# Selector's box card reports which boundary the box would build (#156), and a
# status card that read it out of this file by pattern would be a second place
# to be wrong the day the create moved.
# Per target, and defaulted to the image this adapter was calibrated against
# (issue #3): a PHP target and a Python target run their own suites inside the
# boundary, so the image is a fact about the target rather than about the
# adapter. The Selector sets it from the target's stanza and box-sources/ssh.sh
# carries it across the hop; the default is what a box configured for one
# target has always used.
guest_template="${LOOP_GUEST_TEMPLATE:-loop-php:1}"

die() {
    printf 'claude.sh: %s\n' "$*" >&2
    exit 1
}

# Spec issue #73 asks for this to fail loudly rather than switch billing
# quietly. Claude Code prefers ANTHROPIC_API_KEY over the subscription login, so
# a stray metered key in the environment moves every Iteration onto per-token
# billing with no error and no output difference - the failure shape that broke
# Remote Control on the orchestration VM, recorded in that box's CLAUDE.md.
# There is no per-Run spend ceiling to catch it afterwards: the Termination
# Contract is the whole cost control.
#
# A list rather than a name, and readable from outside: `assert-credentials.sh`
# grades the whole box against the union of what every adapter declares, so this
# is the one place these names are written down. Two files agreeing on a set of
# names by both spelling them out is a seam that breaks silently, and the thing
# that would break is the check that the collision cannot happen (#84).
#
# A function since #260, because there are now TWO things on this box that run
# the vendor's client and both have to be behind it: an Iteration, and the
# renewal below. A renewal that ran with a metered key set would bill per token
# and then fail with "did not renew it", which is the wrong diagnosis for the
# fault - and it would bill on a path nobody is watching.
#
# It deliberately does not guard `--metered-env-names`: that option exists to
# be asked what the names ARE, and refusing to answer when one of them is set
# would make `assert-credentials.sh` unable to grade the box it most needs to.
refuse_metered_keys() {
    local name
    local -a metered_found=()
    for name in "${metered_env_names[@]}"; do
        [[ -n ${!name:-} ]] && metered_found+=("${name}")
    done
    ((${#metered_found[@]} > 0)) || return 0
    printf 'claude.sh: %s is set; it would supersede the subscription and move billing to a metered key. Unset it.\n' \
        "$(
            IFS=', '
            printf '%s' "${metered_found[*]}"
        )" >&2
    exit 1
}

# --- The model credential's clock -------------------------------------------
#
# The subscription login is an OAuth session, and its access token stops
# working eight hours after it is minted. Nothing inside the boundary can
# renew the host's copy: the credential is copied INTO each Iteration's
# microVM and the copy dies with it (ADR 0011), so a refresh performed in a
# guest is written to a filesystem that is destroyed seconds later. The host's
# copy therefore ages from the moment a human last logged in, and a Run
# dispatched more than eight hours after that fails at its first API call.
# That is Run 645 on 2026-08-29, and the only place it appeared was that Run's
# own Progress Log (#260).
#
# Both answers live HERE rather than in the Selector or in
# assert-credentials.sh, for the reason `--guest-template` does: which file
# holds the credential, what shape it is in, and what renews it are all vendor
# facts, and ADR 0004 puts vendor facts in the adapter. Three files agreeing
# on the layout of somebody else's JSON is a seam that breaks silently, and
# what would break is the check that a Run can still authenticate.
#
#   claude.sh --credential-expiry    when the host's copy stops working
#   claude.sh --refresh-credential   renew it if it is close to stopping
#
# How much life the credential must have left for a dispatch to use it as it
# stands. The Termination Contract bounds a Run at ninety minutes
# (loop/contract.sh, LOOP_RUN_TIMEOUT_SECONDS), so a credential with less than
# that could expire DURING an Iteration - which fails a Run halfway through
# and leaves a half-built branch, and is worse than not starting it. Two hours
# is that bound with a margin on top.
credential_renewal_margin_seconds=7200

# The credential the box holds. Named here rather than at its use below,
# because the two options above are answered before an Iteration is set up and
# both of them need it.
credentials_dir="${LOOP_CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
credentials_file="${credentials_dir}/.credentials.json"

# The vendor incantation that renews the session, substitutable so the offline
# suite can drive this with no network and so a box can be pointed at a
# different one without editing a reviewed path.
#
# It is the vendor's own client rather than a hand-rolled call to its token
# endpoint. Reimplementing someone else's OAuth is a copy of an undocumented
# contract, and it breaks the day they change it by minting nothing while
# reporting success. Running the client makes an API request, and an API
# request is what obliges it to present a live access token - so the renewal
# is a side effect of the vendor's own resolution order, which is the part
# that will still be true next quarter. One turn and a prompt that asks for
# nothing: the request is the point, not the answer.
# Split on whitespace, so an argument that needs to contain a space cannot be
# expressed here. That is a real limit and it is the right one: the override
# exists to point at a different COMMAND - the offline suite's scripted fake,
# or a wrapper - and a command line that needs quoting belongs in a wrapper
# script rather than in an environment variable.
read -r -a refresh_command <<<"${LOOP_CLAUDE_REFRESH_COMMAND:-claude -p ok --max-turns 1}"

# The access token's expiry, as epoch seconds. Prints nothing and fails when
# there is no credential or no expiry inside it, which every caller reads as
# "this could not be answered" rather than as "it has expired". An
# unanswerable question and a bad answer are different, and reporting the
# second for the first would page for a box that is fine.
#
# Read with `grep` rather than a JSON parser on purpose: this is asked of the
# box over an SSH read that a Selector cycle is holding open, and a dependency
# the box might not have would make the fact quietly absent from the card. The
# field is a bare integer in a flat object, and `"expiresAt"` cannot match
# `"refreshTokenExpiresAt"` - the leading quote is what separates them.
credential_expiry_epoch() {
    local milliseconds
    [[ -f ${credentials_file} ]] || return 1
    milliseconds="$(grep -o '"expiresAt"[[:space:]]*:[[:space:]]*[0-9]\+' \
        -- "${credentials_file}" 2>/dev/null | head -n1 | grep -o '[0-9]\+$')"
    [[ -n ${milliseconds} ]] || return 1
    printf '%s\n' "$((milliseconds / 1000))"
}

as_instant() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# Renew the host's copy if it is close to expiring, and print when it now
# expires. Cheap and idempotent on the common path: a credential with more
# than the margin left is left alone, so this costs nothing on most dispatches
# and runs the vendor's client only in the hours before a Run would have
# failed anyway.
#
# What is NOT trusted is that the renewal worked. The expiry is re-read
# afterwards and has to have moved past the margin; a renewal command that
# silently did nothing fails here, before a Run is dispatched, rather than at
# that Run's first Iteration where only its Progress Log would say so.
refresh_credential() {
    local before after now
    # Before anything is read and long before the vendor's client is run: a
    # renewal on a metered key bills per token, on a path nobody watches.
    refuse_metered_keys
    before="$(credential_expiry_epoch)" ||
        die "no model credential at ${credentials_file} - run wizards/loop-claude-login.sh"
    now="$(date +%s)"
    if ((before - now > credential_renewal_margin_seconds)); then
        as_instant "${before}"
        return 0
    fi
    # A renewal that exits non-zero is not itself the failure and is not
    # reported as one: what decides this is whether the expiry moved, which is
    # read below. A client that printed a warning and renewed anyway must not
    # stop a dispatch that can now go out.
    "${refresh_command[@]}" >/dev/null 2>&1 || true
    after="$(credential_expiry_epoch)" ||
        die "the credential at ${credentials_file} could not be read after a renewal attempt"
    now="$(date +%s)"
    if ((after - now <= credential_renewal_margin_seconds)); then
        die "the model credential expires $(as_instant "${after}") and '${refresh_command[*]}' did not renew it past the ${credential_renewal_margin_seconds}s a Run needs - run wizards/loop-claude-login.sh"
    fi
    as_instant "${after}"
}

case "${1:-}" in
    --metered-env-names)
        printf '%s\n' "${metered_env_names[@]}"
        exit 0
        ;;
    --guest-template)
        printf '%s\n' "${guest_template}"
        exit 0
        ;;
    --credential-expiry)
        expiry="$(credential_expiry_epoch)" || exit 1
        as_instant "${expiry}"
        exit 0
        ;;
    --refresh-credential)
        refresh_credential
        exit 0
        ;;
esac

prompt_file="${1:?usage: claude.sh <prompt-file> <max-turns>}"
max_turns="${2:?usage: claude.sh <prompt-file> <max-turns>}"

refuse_metered_keys

sbx="${LOOP_SBX_COMMAND:-sbx}"
command -v "${sbx}" >/dev/null 2>&1 ||
    die "${sbx} is not on PATH - an Iteration runs inside the Execution Boundary, not beside it"

workspace="$(pwd)"

# The model credential. The box holds the operator's subscription login; each
# Iteration gets a copy inside its own microVM, which dies with it.
#
# What stops a Run starting without one is the line below, not
# `assert-credentials.sh` - that script grades a box for an operator and
# nothing in a Run calls it.
#
# Renewed before the copy goes in, and renewed at EVERY Iteration rather than
# once when the Run was dispatched (#260). Run 645 is why: it authenticated
# for Iteration 1 and died on Iteration 2, so a credential checked once at
# dispatch would have passed and the Run would have failed exactly as it did.
# A Run is bounded at ninety minutes and the session at eight hours, so the
# window a Run can cross is real.
#
# This is the host side of the boundary - `sbx create` has not run yet - which
# is the only place the renewal can happen at all: the copy that goes in dies
# with the microVM (ADR 0011), so a refresh performed inside is written to a
# filesystem destroyed seconds later. It is also why the renewal is here and
# not in the Selector, which reaches this box over SSH and would need a second
# hop to do it.
#
# On the common path this costs nothing: a credential with more than the
# margin left is returned unchanged without the vendor's client being run.
refresh_credential >/dev/null

signing_key="${LOOP_SIGNING_KEY:-${HOME}/.ssh/loop_signing_ed25519}"
gitconfig="${LOOP_GITCONFIG:-${HOME}/.gitconfig}"
allowed_signers="${LOOP_ALLOWED_SIGNERS:-${HOME}/.config/loop/allowed_signers}"

# A name unique to this Iteration, because the boundary is per Iteration and two
# Runs on one box must not collide. `sbx` accepts letters, numbers, hyphens and
# periods and wants a letter or number first.
sandbox="loop-$$-$(date +%s)"

# Where this Iteration builds the copy of the git identity that goes inside.
# Host-side scratch, removed with the sandbox.
staging="$(mktemp -d)"

# The sandbox outlives this script only if this script is killed between create
# and remove, and run.sh kills it by design when an Iteration reaches its wall
# clock. So the removal is a trap, not a line at the end.
# shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
cleanup() {
    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true
    rm -rf -- "${staging}"
}
trap cleanup EXIT INT TERM

# The Loop's own scripts, mounted read-only alongside the repository. The Plan
# names a completeness check for an Iteration to grade itself against, and the
# first Run found it unreachable: a sandbox mounts the workspace and nothing
# else, so a check that lived beside the Loop's scripts was outside the
# session's allowed directories and every Iteration recorded it as blocked.
# Backpressure an Iteration cannot reach is not backpressure.
#
# Read-only. The check is what says the work did not land, and an agent that
# could edit it could make it say otherwise - which is the one thing a Run's own
# grade must not be able to do.
loop_dir="$(cd -- "${agent_dir}/.." && pwd)"

"${sbx}" create --quiet --name "${sandbox}" -t "${guest_template}" claude "${workspace}" "${loop_dir}:ro" >&2 ||
    die "could not create the Execution Boundary for this Iteration from ${guest_template}. If the box is not holding that image, apply ansible/loop.yml - role loop_guest_template builds it."

# Everything the guest needs, placed after creation rather than mounted. A mount
# would leave the host's copy writable from inside the boundary; a copy dies
# with the sandbox.
#
# `sbx cp` will not create a parent directory it has not been given, so each
# destination directory is made first.
#
# A missing source is fatal, not skipped. Every one of these is placed by
# `ansible/loop.yml`, so its absence means a box that was never configured - and
# skipping it would produce an Iteration that runs, commits, and lands commits
# that are unsigned or attributed to nobody. That is not recoverable after the
# fact, and the first place anyone would find out is the pull request.
guest_home=/home/agent
# `dest` is a path in the GUEST, and it has to be one the guest account can
# write without privilege - which in practice means under ${guest_home}. A host
# path is not that: `sbx` owns the synthesised parents of the workspace mount by
# depth, so whether one happens to be writable is a property of the workspace
# argument rather than of anything visible from here (#268).
put() {
    local src="$1" dest="$2" mode="$3"
    [[ -f ${src} ]] ||
        die "${src} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
    "${sbx}" exec "${sandbox}" mkdir -p "$(dirname -- "${dest}")" ||
        die "could not prepare ${dest} in the boundary: the guest account cannot create $(dirname -- "${dest}"). Everything an Iteration is given goes under ${guest_home}."
    "${sbx}" cp "${src}" "${sandbox}:${dest}" >/dev/null ||
        die "could not place ${dest} in the boundary"
    "${sbx}" exec "${sandbox}" chmod "${mode}" "${dest}" ||
        die "could not set the mode of ${dest} in the boundary"
}

# Everything lands under the guest account's own home, and that is the whole of
# #268. The signing key and the allowed-signers file used to go in at their HOST
# paths - `/home/loop/.ssh/...` - which is a path the guest has no reason to be
# able to write, and could write only by accident: `sbx` synthesises the parent
# directories of a bind mount and owns them by depth, so `/home/loop` came out
# `agent:agent` when the workspace was one level under it and `root:root` when it
# was two. Measured on the box against `loop-php:1` on 2026-08-30.
#
# `loop_scripts_workspace: /home/loop/tourbot` is one level, so every real Run
# worked and the coupling was invisible until a repro harness used a deeper
# workspace. What it produced then was an Iteration that died before the agent
# started, saying it could not prepare the signing key - a message that names the
# key for a fault whose cause is the workspace argument, in a place only that
# Run's Progress Log would record.
#
# `/home/agent` is the guest account's home under every `sbx` template, at every
# mount depth, with no privilege needed to write it. Nothing below now depends on
# a host path being writable inside the guest.
guest_signing_key="${guest_home}/.ssh/$(basename -- "${signing_key}")"
guest_allowed_signers="${guest_home}/.config/loop/allowed_signers"

# The git identity, rewritten to name the two files where they now are. The
# host's copy names them at host paths - `ansible/roles/loop_credentials` writes
# it that way and the box itself needs it that way - and a config naming a
# signing key that is not there is worse than one naming none: the failure is a
# commit that is not Verified, and the first place anyone reads that is the pull
# request.
#
# Rewritten on the host, into a copy, rather than edited inside the guest: the
# host's own `.gitconfig` is what every Run and every operator on the box uses,
# and an Iteration must not be able to change it.
guest_gitconfig="${staging}/gitconfig"
[[ -f ${gitconfig} ]] ||
    die "${gitconfig} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
cp -- "${gitconfig}" "${guest_gitconfig}"
git config --file "${guest_gitconfig}" user.signingkey "${guest_signing_key}.pub"
git config --file "${guest_gitconfig}" gpg.ssh.allowedSignersFile "${guest_allowed_signers}"

put "${credentials_dir}/.credentials.json" "${guest_home}/.claude/.credentials.json" 0600
put "${guest_gitconfig}" "${guest_home}/.gitconfig" 0644
put "${signing_key}" "${guest_signing_key}" 0600
put "${signing_key}.pub" "${guest_signing_key}.pub" 0644
put "${allowed_signers}" "${guest_allowed_signers}" 0644

# --permission-mode bypassPermissions. This was `acceptEdits`, and the first Run
# proved that choice incoherent with the technique: `acceptEdits` auto-approves
# FILE EDITS and still gates Bash, so every Iteration did its work, wrote its
# Progress Log entry, and was refused at `git add` with "This command requires
# approval". An Iteration that cannot commit is a No-op by the Loop's own
# definition - the head does not move - so every Run aborted on consecutive
# No-ops with the whole Run's work sitting uncommitted in the working tree.
#
# The reason to bypass is ADR 0003 rather than convenience. A permission prompt
# is a control that spends a human, and there is no human: the boundary is what
# Attendedness re-earns, and inside it the agent has a microVM of its own, two
# allowed hosts, and no GitHub token. There is nothing here for a prompt to
# protect that the boundary is not already protecting, and `sbx`'s own image
# ships `defaultMode: bypassPermissions` for exactly that reason.
#
# The turn bound is the agent's own - the Loop passes the Contract's value in
# rather than reimplementing it.
#
# No `exec`: the trap above has to run, and the sandbox has to be removed even
# when the agent exits non-zero.
# Streamed AND captured. Streamed because run.sh quotes the tail of a faulting
# Iteration's output into the Progress Log and an Iteration killed at its wall
# clock must not take its own diagnosis with it; captured because the turn bound
# has to be told apart from a broken invocation, and Claude Code says which by
# what it prints rather than by what it exits.
transcript="$(mktemp)"
trap 'cleanup; rm -f -- "${transcript}"' EXIT INT TERM

# `pipefail` off for this one pipeline, and it is load-bearing rather than
# stylistic: with it on, an agent exiting non-zero fails the pipeline, `set -e`
# ends this script on that line, and everything below - including telling the
# turn bound apart from a failure - never runs. Off, the pipeline carries tee's
# status and the agent's own is in PIPESTATUS, which is what this needs.
set +o pipefail
"${sbx}" exec --workdir "${workspace}" "${sandbox}" \
    claude \
    --print \
    --permission-mode bypassPermissions \
    --max-turns "${max_turns}" \
    "$(cat -- "${prompt_file}")" 2>&1 | tee -- "${transcript}"
agent_rc="${PIPESTATUS[0]}"
set -o pipefail

# The turn bound firing is not the agent failing. Claude Code exits 1 for both,
# so the message is the only thing that tells them apart - and the first Run
# proved the difference is the whole Run: read as a failure, a bound of the
# Termination Contract ended everything at Iteration 1.
#
# If the vendor rewords this, the behaviour degrades to what it was before -
# reported as agent-failed, with the output quoted into the Progress Log, where
# a human reads the words "Reached max turns" and finds this comment. That is a
# visible degradation rather than a silent one, which is the reason to prefer a
# message match here over `--output-format json`: the JSON carries a structured
# `error_max_turns` and would be more robust, at the cost of making every
# faulting Iteration's Progress Log excerpt a blob nobody reads. Worth
# revisiting if the log excerpt stops being the way a Run is diagnosed.
if ((agent_rc != 0)) && grep -qF 'Reached max turns' -- "${transcript}"; then
    exit "${LOOP_AGENT_TURN_BOUND_EXIT}"
fi

exit "${agent_rc}"
