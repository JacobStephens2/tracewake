#!/usr/bin/env bash
#
# The agent, as one substitutable command (ADR 0004), executing inside the
# Execution Boundary.
#
#   claude.sh <prompt-file> <max-turns>
#   claude.sh --guest-template    the `sbx` template the boundary is built from
#   claude.sh --metered-env-names the names that would supersede the subscription
#
# Invoked by run.sh with the repository as the working directory, once per
# Iteration, as a fresh process. Exits with the agent's exit status. Swapping
# vendor is writing a sibling of this file and pointing LOOP_AGENT_COMMAND at
# it - which is exactly what the offline suite does with its scripted fake, and
# what #84 will do with Grok Build.
#
# Vendor-specific concerns belong HERE rather than in the loop, and two of them
# earn that distinction: the credential guard below defends against a collision
# that is a property of this agent, and `sbx create claude` names a template
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
# 0003), and it is a thing that may change - spec #151's story 33 wants a
# PHP-capable guest so `/tdd` is real red-green-refactor, falling back to this
# stock image if that work runs long.
#
# Readable from outside for the same reason `--metered-env-names` is: the
# Selector's box card reports which boundary the box would build (#156), and
# a status card that read it out of this file by pattern would be a second
# place to be wrong the day the create moved.
guest_template=claude

case "${1:-}" in
    --metered-env-names)
        printf '%s\n' "${metered_env_names[@]}"
        exit 0
        ;;
    --guest-template)
        printf '%s\n' "${guest_template}"
        exit 0
        ;;
esac

prompt_file="${1:?usage: claude.sh <prompt-file> <max-turns>}"
max_turns="${2:?usage: claude.sh <prompt-file> <max-turns>}"

die() {
    printf 'claude.sh: %s\n' "$*" >&2
    exit 1
}

# Spec issue #73 asks for this to fail the Run loudly rather than switch billing
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
metered_found=()
for name in "${metered_env_names[@]}"; do
    [[ -n ${!name:-} ]] && metered_found+=("${name}")
done
if ((${#metered_found[@]} > 0)); then
    printf 'claude.sh: %s is set; it would supersede the subscription and move billing to a metered key. Unset it.\n' \
        "$(
            IFS=', '
            printf '%s' "${metered_found[*]}"
        )" >&2
    exit 1
fi

sbx="${LOOP_SBX_COMMAND:-sbx}"
command -v "${sbx}" >/dev/null 2>&1 ||
    die "${sbx} is not on PATH - an Iteration runs inside the Execution Boundary, not beside it"

workspace="$(pwd)"

# The model credential. The box holds the operator's subscription login; each
# Iteration gets a copy inside its own microVM, which dies with it. This is the
# path assert-credentials.sh gates on, so a Run cannot start without it.
credentials_dir="${LOOP_CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
[[ -f "${credentials_dir}/.credentials.json" ]] ||
    die "no model credential at ${credentials_dir}/.credentials.json - run wizards/loop-claude-login.sh"

signing_key="${LOOP_SIGNING_KEY:-${HOME}/.ssh/loop_signing_ed25519}"
gitconfig="${LOOP_GITCONFIG:-${HOME}/.gitconfig}"
allowed_signers="${LOOP_ALLOWED_SIGNERS:-${HOME}/.config/loop/allowed_signers}"

# A name unique to this Iteration, because the boundary is per Iteration and two
# Runs on one box must not collide. `sbx` accepts letters, numbers, hyphens and
# periods and wants a letter or number first.
sandbox="loop-$$-$(date +%s)"

# The sandbox outlives this script only if this script is killed between create
# and remove, and run.sh kills it by design when an Iteration reaches its wall
# clock. So the removal is a trap, not a line at the end.
# shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
cleanup() {
    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# The Loop's own scripts, mounted read-only alongside the repository. The Plan
# names a completeness check for an Iteration to grade itself against, and the
# first Run found it unreachable: a sandbox mounts the workspace and nothing
# else, so `check-inventory.sh` was outside the session's allowed directories and
# every Iteration recorded it as blocked. Backpressure an Iteration cannot reach
# is not backpressure.
#
# Read-only. The check is what says the work did not land, and an agent that
# could edit it could make it say otherwise - which is the one thing a Run's own
# grade must not be able to do.
loop_dir="$(cd -- "${agent_dir}/.." && pwd)"

"${sbx}" create --quiet --name "${sandbox}" "${guest_template}" "${workspace}" "${loop_dir}:ro" >&2 ||
    die "could not create the Execution Boundary for this Iteration"

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
put() {
    local src="$1" dest="$2" mode="$3"
    [[ -f ${src} ]] ||
        die "${src} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
    "${sbx}" exec "${sandbox}" mkdir -p "$(dirname -- "${dest}")" ||
        die "could not prepare ${dest} in the boundary"
    "${sbx}" cp "${src}" "${sandbox}:${dest}" >/dev/null ||
        die "could not place ${dest} in the boundary"
    "${sbx}" exec "${sandbox}" chmod "${mode}" "${dest}" ||
        die "could not set the mode of ${dest} in the boundary"
}

put "${credentials_dir}/.credentials.json" "${guest_home}/.claude/.credentials.json" 0600
put "${gitconfig}" "${guest_home}/.gitconfig" 0644
put "${signing_key}" "${signing_key}" 0600
put "${signing_key}.pub" "${signing_key}.pub" 0644
put "${allowed_signers}" "${allowed_signers}" 0644

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
