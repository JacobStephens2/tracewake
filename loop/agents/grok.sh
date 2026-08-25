#!/usr/bin/env bash
#
# The second agent, as one substitutable command (ADR 0004), executing inside
# the Execution Boundary.
#
#   grok.sh <prompt-file> <max-turns>
#   grok.sh --pinned-version      the version this adapter installs in the guest
#   grok.sh --metered-env-names   the names that would supersede the subscription
#
# Same contract as agents/claude.sh - two positional arguments, the repository
# as the working directory, the agent's exit status - because that contract is
# the whole of what run.sh knows about an agent. Swapping to this one is a
# single line, `LOOP_AGENT_COMMAND`, and nothing in run.sh or contract.sh moves.
#
# Everything below is what makes the two DIFFERENT, and #84 exists to find out
# which of the first Run's properties were the technique's and which were Claude
# Code's. Four things differ, and none of them is a preference:
#
# ## 1. The boundary is agent-less, and the agent is installed inside it
#
# `sbx create` offers claude, codex, copilot, cursor, docker-agent, droid,
# gemini, kiro, opencode and shell. Grok Build is not on that list, so this
# adapter creates a `shell` sandbox and installs the agent within it at a pinned
# version, which is what spec issue #73 assumes for the second Run.
#
# The install is a per-Iteration network download - about six seconds, from one
# host - rather than a copy of the host's binary. That is deliberate: a copy
# would make the guest's agent whatever the host happens to be holding, and the
# reason to pin at all is that an agent whose version changes between Iterations
# is an agent whose behaviour nobody has observed. It also means `x.ai:443` is on
# the boundary's egress allowlist for this agent and not for the other one, which
# is a cost ADR 0004 priced in and the notes record rather than a surprise.
#
# ## 2. Credential injection does not hold here, and must not be inherited
#
# `sbx`'s host proxy injects a stored `sbx secret` so the value never enters the
# microVM. That is a property of the proxy plus a stored service secret, and it
# is not what this adapter does: Grok's subscription credential is an
# auto-refreshing OAuth token in `~/.grok/auth.json`, and it is COPIED INSIDE,
# where the agent can read it. `xai` is a supported secret service, so a
# proxy-injected configuration may be possible - but it would be an API key,
# which is the one thing story 32 exists to prevent, so it is not the road.
#
# The token is the operator's subscription session, it refreshes against
# `auth.x.ai` during a Run, and it expires on its own. What bounds an agent that
# can read it is the egress allowlist and the token being revocable by logging
# out - the same shape as the signing key under ADR 0011, and the same stated
# cost. `../notes/loop-grok-run-evidence.md` says which properties hold for
# which agent so that no reader inherits one that only holds for the other.
#
# ## 3. The metered key has more than one door
#
# The vendor's own shipped README is explicit - "The API key takes precedence
# over browser credentials" - and its credential resolution order ends
# `... -> session token -> XAI_API_KEY`. `TOURBOT_PREVIEW_XAI_API_KEY` already
# exists in both of the orchestration VM's manifests, so the realistic way one
# arrives here is somebody copying a working command over. A bare key would move
# every Iteration onto per-token billing with no error and no output difference,
# which is the failure shape that broke Remote Control.
#
# So the guard is a list rather than a name, it fails the Run loudly at second
# zero rather than switching billing quietly, and `assert-credentials.sh` reads
# that same list off this file rather than restating it - two files agreeing on a
# set of names by both spelling them out is a seam that breaks silently, and the
# thing that would break is the check that the collision cannot happen.
#
# A file door is checked too: `~/.grok/config.toml` can carry a per-model
# `api_key` or `env_key`, which the resolution order puts AHEAD of the session
# token. That file is copied into the guest, so a config with one in it is a
# metered key inside the boundary with nothing in the environment to show for it.
#
# ## 4. The turn bound says something else
#
# Grok Build 1.0.5 prints `Error: max turns reached` and exits 1. Claude Code
# prints `Error: Reached max turns (N)` and exits 1. Same shape, different words,
# which is exactly why ADR 0004 keeps the message in the adapter and lets
# `contract.sh` declare only the exit status the two sides share.

set -euo pipefail

# The Contract, for one value: the exit status that means the turn bound fired.
agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source-path=SCRIPTDIR source=../contract.sh
source "${agent_dir}/../contract.sh"

# The pin, declared once. `ansible/roles/loop_agent` asks this file for it
# rather than carrying a second copy, so moving the pin is one line here.
#
# 1.0.5 is what the orchestration VM runs and what #84's Run was made under. It
# is installed INSIDE the boundary; the host's copy exists only for the login.
LOOP_GROK_VERSION="${LOOP_GROK_VERSION:-1.0.5}"

# Every environment variable name that would supersede the subscription. Read by
# assert-credentials.sh, so this is the one place any of them is written down.
#
#   XAI_API_KEY              the documented one, and the one already in ETA's
#                            manifests as TOURBOT_PREVIEW_XAI_API_KEY
#   GROK_CODE_XAI_API_KEY    the same key under the name the binary also honours
#   GROK_DEPLOYMENT_KEY      an enterprise install key; the installer accepts it
#                            and it authenticates without a browser at all
#   GROK_AUTH_PROVIDER_ACCESS_TOKEN
#   GROK_AUTH_PROVIDER_COMMAND
#                            the BYOK handback: a token, or a command that mints
#                            one, resolved ahead of the session token
metered_env_names=(
    XAI_API_KEY
    GROK_CODE_XAI_API_KEY
    GROK_DEPLOYMENT_KEY
    GROK_AUTH_PROVIDER_ACCESS_TOKEN
    GROK_AUTH_PROVIDER_COMMAND
)

die() {
    printf 'grok.sh: %s\n' "$*" >&2
    exit 1
}

case "${1:-}" in
    --pinned-version)
        printf '%s\n' "${LOOP_GROK_VERSION}"
        exit 0
        ;;
    --metered-env-names)
        printf '%s\n' "${metered_env_names[@]}"
        exit 0
        ;;
esac

prompt_file="${1:?usage: grok.sh <prompt-file> <max-turns>}"
max_turns="${2:?usage: grok.sh <prompt-file> <max-turns>}"

[[ -f ${prompt_file} ]] || die "no prompt file at ${prompt_file}"

# --- The metered key, before anything else ----------------------------------
#
# Before the boundary is built and before a token of model time is spent, so
# that a Run whose environment is wrong costs nothing and says why. Every name
# is reported rather than the first, because an operator clearing one and
# re-running to find the next is how a one-second check becomes a morning.
metered_found=()
for name in "${metered_env_names[@]}"; do
    [[ -n ${!name:-} ]] && metered_found+=("${name}")
done
if ((${#metered_found[@]} > 0)); then
    printf 'grok.sh: %s is set; it would supersede the subscription and move billing to a metered key. Unset it.\n' \
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
# Iteration gets a copy inside its own microVM, which dies with it.
grok_home="${LOOP_GROK_HOME:-${HOME}/.grok}"
[[ -f "${grok_home}/auth.json" ]] ||
    die "no model credential at ${grok_home}/auth.json - run wizards/loop-grok-login.sh"

# The file door. A per-model `api_key` or `env_key` in the config resolves ahead
# of the session token, and this file goes into the guest - so a config carrying
# one is the collision above with nothing in the environment to show for it.
grok_config="${grok_home}/config.toml"
if [[ -f ${grok_config} ]] &&
    grep -qE '^[[:space:]]*(api_key|env_key)[[:space:]]*=' "${grok_config}"; then
    die "${grok_config} sets a model api_key or env_key; it resolves ahead of the subscription session and would move billing. Remove it."
fi

signing_key="${LOOP_SIGNING_KEY:-${HOME}/.ssh/loop_signing_ed25519}"
gitconfig="${LOOP_GITCONFIG:-${HOME}/.gitconfig}"
allowed_signers="${LOOP_ALLOWED_SIGNERS:-${HOME}/.config/loop/allowed_signers}"

sandbox="loop-$$-$(date +%s)"

# shellcheck disable=SC2329  # invoked by the trap below, which shellcheck does not follow
cleanup() {
    "${sbx}" rm --force "${sandbox}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

loop_dir="$(cd -- "${agent_dir}/.." && pwd)"

# `shell`, not an agent template - there is no Grok template to ask for. This is
# the agent-less boundary spec #73 assumes for this Run, and the evidence for
# every isolation claim in ../notes/loop-execution-boundary-evidence.md was
# gathered in exactly this configuration, so those claims transfer unchanged.
#
# One thing does not transfer: the `shell` kit attaches `openrouter.ai` to every
# sandbox it makes, the way the `claude` kit attaches Anthropic's six hosts.
# Choosing a template chooses hosts, and this template's choice is recorded
# rather than assumed away.
"${sbx}" create --quiet --name "${sandbox}" shell "${workspace}" "${loop_dir}:ro" >&2 ||
    die "could not create the Execution Boundary for this Iteration"

# The guest's account, which is `agent` under every `sbx` template. Named
# alongside its home because the two are always used together, and because the
# directory preparation below has to give it ownership of a path it does not own.
guest_home=/home/agent
guest_user=agent

# `sudo`, which the other adapter's copy of this function does not need - and the
# difference is a template's rather than a preference.
#
# The signing key and the allowed-signers file go in at their HOST paths, because
# the git config that names them is the host's and a copy that landed somewhere
# else would produce commits that are not Verified. Under `sbx create claude`,
# `/home/loop` inside the guest is owned by `agent` and the plain `mkdir -p`
# works. Under `sbx create shell` it is `root:root` and mode 0755, so the same
# call fails with "Permission denied" - the guest user is in `sudo` without a
# password, so this reaches around it and hands the directory back.
#
# Found by running it. Nothing offline would have said so, and the failure it
# produces is late and unhelpful: a Run that builds a boundary, installs an
# agent, and dies placing a credential.
put() {
    local src="$1" dest="$2" mode="$3" dir
    [[ -f ${src} ]] ||
        die "${src} is not on this box - a Run needs it inside the boundary. Apply ansible/loop.yml."
    dir="$(dirname -- "${dest}")"
    "${sbx}" exec "${sandbox}" sudo mkdir -p "${dir}" ||
        die "could not prepare ${dest} in the boundary"
    "${sbx}" exec "${sandbox}" sudo chown "${guest_user}:${guest_user}" "${dir}" ||
        die "could not prepare ${dest} in the boundary"
    "${sbx}" cp "${src}" "${sandbox}:${dest}" >/dev/null ||
        die "could not place ${dest} in the boundary"
    "${sbx}" exec "${sandbox}" chmod "${mode}" "${dest}" ||
        die "could not set the mode of ${dest} in the boundary"
}

# The agent itself, installed inside the boundary at the pin. Before the
# credential rather than after it: the installer writes into the same directory,
# and a step that could clobber the credential must not run once it is there.
#
# The vendor's installer is the only supported way to place this binary, and it
# takes the version as its argument - so the pin is enforced by what is asked
# for AND re-read afterwards, because an installer that quietly fell back to a
# channel pointer would otherwise put an unobserved version inside an unattended
# Run.
"${sbx}" exec "${sandbox}" bash -lc \
    "set -o pipefail; curl -fsSL https://x.ai/cli/install.sh | bash -s ${LOOP_GROK_VERSION}" >&2 ||
    die "could not install the agent inside the boundary - is x.ai:443 on the egress allowlist? See ansible/roles/loop_execution_boundary/defaults/main.yml"

guest_agent="${guest_home}/.grok/bin/grok"
installed="$("${sbx}" exec "${sandbox}" "${guest_agent}" --version 2>&1 || true)"
[[ ${installed} == *"${LOOP_GROK_VERSION}"* ]] ||
    die "the boundary is holding '${installed}', not the pinned ${LOOP_GROK_VERSION}"

put "${grok_home}/auth.json" "${guest_home}/.grok/auth.json" 0600
put "${gitconfig}" "${guest_home}/.gitconfig" 0644
put "${signing_key}" "${signing_key}" 0600
put "${signing_key}.pub" "${signing_key}.pub" 0644
put "${allowed_signers}" "${allowed_signers}" 0644

# The prompt as a file rather than an argument. Claude Code's adapter passes it
# as one; this one cannot, because the invocation below already carries an `env`
# prefix and a whole Iteration prompt as a shell word inside that is a quoting
# hazard for no gain.
put "${prompt_file}" "${guest_home}/prompt" 0644

transcript="$(mktemp)"
trap 'cleanup; rm -f -- "${transcript}"' EXIT INT TERM

# Three environment settings, and each turns off a thing that reaches a host the
# boundary does not allow. None of them is a preference:
#
#   GROK_TELEMETRY_ENABLED=0   the binary posts to Mixpanel and a GCS bucket.
#                              Blocked by `deny-all`, so leaving it on buys
#                              nothing and spends the Iteration's wall clock on
#                              connections that will fail.
#   GROK_CHANGELOG_OFFLINE=1   a changelog fetch from `x.ai`. Cosmetic, and
#                              `x.ai` is allowed for the install - so this is
#                              the difference between a host allowed for one
#                              known reason and a host allowed for two.
#   GROK_DISABLE_AUTOUPDATER=1 the pin is the point. An agent that updated
#                              itself mid-Run would be a different agent from
#                              the one the version check above passed.
#
# --permission-mode bypassPermissions, for ADR 0003's reason rather than
# convenience: a permission prompt is a control that spends a human and there is
# no human. The first Run proved the weaker mode incoherent with the technique -
# `acceptEdits` gates Bash, so an Iteration cannot `git add`, and an Iteration
# that cannot commit is a No-op by the Loop's own definition.
#
# No `exec`: the trap has to run and the sandbox has to be removed even when the
# agent exits non-zero.
#
# Streamed AND captured, for the same two reasons as the other adapter: run.sh
# quotes the tail of a faulting Iteration's output into the Progress Log, and
# the turn bound has to be told apart from a broken invocation by what the agent
# printed rather than by what it exited.
#
# `pipefail` off for this one pipeline, and load-bearing rather than stylistic:
# with it on, an agent exiting non-zero fails the pipeline, `set -e` ends this
# script on that line, and the turn-bound detection below never runs.
set +o pipefail
"${sbx}" exec --workdir "${workspace}" "${sandbox}" \
    env GROK_TELEMETRY_ENABLED=0 GROK_CHANGELOG_OFFLINE=1 GROK_DISABLE_AUTOUPDATER=1 \
    "${guest_agent}" \
    --prompt-file "${guest_home}/prompt" \
    --permission-mode bypassPermissions \
    --max-turns "${max_turns}" \
    --output-format plain 2>&1 | tee -- "${transcript}"
agent_rc="${PIPESTATUS[0]}"
set -o pipefail

# The turn bound firing is not the agent failing. Grok Build exits 1 for both,
# so the message is the only thing that tells them apart - and the first Run
# proved, under the other vendor, that the difference is the whole Run: read as
# a failure, a bound of the Termination Contract ended everything at Iteration 1.
#
# `-i` because the binary prints the phrase twice in two casings - `Max turns
# reached` on one line and `Error: max turns reached` on the next - and matching
# the one that happens to carry `Error:` would turn on which of the two the
# vendor keeps.
#
# If the vendor rewords it, this degrades to what the other adapter degrades to:
# reported as agent-failed, with the words quoted into the Progress Log where a
# human reads them. Visible, not silent.
if ((agent_rc != 0)) && grep -qiF 'max turns reached' -- "${transcript}"; then
    exit "${LOOP_AGENT_TURN_BOUND_EXIT}"
fi

exit "${agent_rc}"
