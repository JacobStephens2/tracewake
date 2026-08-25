#!/usr/bin/env bash
#
# The agent, as one substitutable command (ADR 0004), executing inside the
# Execution Boundary.
#
#   claude.sh <prompt-file> <max-turns>
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
if [[ -n ${ANTHROPIC_API_KEY:-} ]]; then
    printf 'claude.sh: ANTHROPIC_API_KEY is set; it would supersede the subscription and move billing to a metered key. Unset it.\n' >&2
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

"${sbx}" create --quiet --name "${sandbox}" claude "${workspace}" >&2 ||
    die "could not create the Execution Boundary for this Iteration"

# Everything the guest needs, placed after creation rather than mounted. A mount
# would leave the host's copy writable from inside the boundary; a copy dies
# with the sandbox.
#
# `sbx cp` will not create a parent directory it has not been given, so each
# destination directory is made first.
guest_home=/home/agent
put() {
    local src="$1" dest="$2" mode="$3"
    [[ -f ${src} ]] || return 0
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

# --permission-mode acceptEdits, not a bypass: the Iteration edits and commits
# without prompting, because nobody is there to answer a prompt, and the
# Execution Boundary rather than the permission mode is what it cannot cross.
# The turn bound is the agent's own - the Loop passes the Contract's value in
# rather than reimplementing it.
#
# No `exec`: the trap above has to run, and the sandbox has to be removed even
# when the agent exits non-zero.
agent_rc=0
"${sbx}" exec --workdir "${workspace}" "${sandbox}" \
    claude \
    --print \
    --permission-mode acceptEdits \
    --max-turns "${max_turns}" \
    "$(cat -- "${prompt_file}")" || agent_rc=$?

exit "${agent_rc}"
