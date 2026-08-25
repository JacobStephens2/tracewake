#!/usr/bin/env bash
#
# The Loop's credential inventory, asserted rather than described.
#
#   assert-credentials.sh [--home <dir>] [--system-root <prefix>] [--sbx <cmd>]
#
# Spec issue #73 makes a claim about this box - that it holds a model
# credential, a repository-scoped GitHub token and a dedicated signing key, and
# that it holds no vault token, no database credential, no fleet SSH key and no
# DigitalOcean token. That claim is what every isolation argument in the spec
# rests on, and until this script existed it was a sentence in a document.
#
# It reports BOTH directions, because either one alone is a half-truth: what the
# box is allowed to hold and does, and what it may not hold and does not.
#
# It grades the box for a RUN, not for a ticket. That is why an Execution
# Boundary whose session has lapsed is a violation rather than a note: the box
# would still satisfy every acceptance criterion of #81 and a Run started on it
# would have no boundary.
#
# **`run.sh` does not call this**, and that is a decision rather than an
# omission. A Run's preflight checks what a Run needs and can check offline, so
# that the whole Contract is exercisable by a suite with no box under it; this
# grades a specific box, needs `sbx`, and takes seconds. What a Run does enforce
# is the one credential it cannot start without - `agents/claude.sh` refuses an
# Iteration with no model credential, and refuses one with a metered key in the
# environment. This script is the operator's and the walkthrough's: it is stage
# 5 of `wizards/loop-claude-login.sh`, and it is what to run before leaving a
# box alone for a while rather than before every Run.
#
#   0  clean - every gating credential is held, and nothing forbidden is here.
#   1  the check could not run.
#   2  at least one violation. Every one is named; there is no first-failure
#      exit, because an operator fixing one and re-running to find the next is
#      how a five-minute check becomes an afternoon.
#
# ## The fourth credential
#
# The spec says three. It is four, and this script says four. `sbx` will not
# create a sandbox without a Docker identity, so the Execution Boundary itself
# requires one (#78). What is on the box is a read-only Docker personal access
# token in `gnome-keyring` under ~loop: it can read Docker Hub, it cannot push
# an image or reach any ETA system, it revokes on its own, and revoking it stops
# the boundary rather than degrading it. It is held here as a line of the
# inventory rather than as an exception to it, because a count that has to be
# remembered as "three, plus one nobody mentions" is not an inventory.
#
# ## Why an environment variable is a credential on this box
#
# The collision spec issue #73 story 32 wants made impossible has three doors,
# and only the first is the agent's own environment:
#
#   1. `ANTHROPIC_API_KEY` in the Run's environment. Claude Code prefers it over
#      the subscription login, so billing moves with no error and no output
#      difference - the shape that broke Remote Control on the orchestration VM.
#   2. The same name exported from a shell profile or /etc/environment, which is
#      not in this process's environment and is in every future login's.
#   3. `sbx secret import`, which imports secrets DETECTED IN HOST ENVIRONMENT
#      VARIABLES, and `sbx secret set`, which stores one durably for the proxy
#      to inject. A key that reached the boundary's secret store is not visible
#      in any environment at all afterwards (#78).
#
# All three are checked. The third is why this script asks `sbx` rather than
# only reading files.
#
# No network. Reads only; it never fixes anything it finds, because what to do
# about a fleet key on this box is not a decision a script should take
# unattended.

set -euo pipefail

die() {
    printf 'assert-credentials.sh: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
assert-credentials.sh [--home <dir>] [--system-root <prefix>] [--sbx <cmd>]

Asserts the Loop's box holds the four credentials it is allowed to hold and
none of the four families it is not. Exits 0 when clean, 2 naming every
violation, 1 when the check could not run.

  --home         the Run account's home directory (default /home/loop)
  --system-root  prefix for absolute system paths, for testing (default none)
  --sbx          the Execution Boundary CLI (default sbx)
  --list-env-names  print every forbidden environment variable name and exit

Run it on the box, as the account a Run executes as:

  ssh root@loop.etadventures.com 'su - loop -c "…/assert-credentials.sh"'
USAGE
}

# --- What the box is allowed to hold ----------------------------------------
#
# Four lines, and all four now gate the exit code. The model credential's row
# was the one that did not, because until #83 no agent was installed and
# requiring it would have made this script red on a box that was exactly as the
# spec intended. #83 put the operator's subscription login on the box, so the
# row gates like the rest of them - which was the whole plan, and the only
# change it took.
#
#   name|gating|what it is
allowed=(
    "github-token|yes|fine-grained, repository-scoped, contents + pull requests"
    "signing-key|yes|dedicated SSH signing key, registered to the operator"
    "docker-identity|yes|read-only Docker PAT the Execution Boundary requires"
    "model-credential|yes|the operator's Claude Code subscription login"
)

# --- What the box may not hold ----------------------------------------------
#
# One array pair per family. Names are the ones that actually appear on the
# orchestration VM's manifests and in ETA's own runbooks, because the realistic
# way any of these arrives here is somebody copying a working command over.

# One row per family: the family name, then every environment variable name
# that would carry it. A flat list rather than one array per family - the check
# below is the same loop for all of them, and five arrays reached by name would
# be five things to keep in step with a list of five names.
forbidden_env=(
    "vault-token OP_SERVICE_ACCOUNT_TOKEN OP_CONNECT_TOKEN OP_API_TOKEN VAULT_TOKEN"
    "database-credential MYSQL_PWD DATABASE_URL DB_PASSWORD PROD_DB_SERVER_HOST PROD_DB_SERVER_MYSQL_USER PROD_DB_SERVER_MYSQL_PASS"
    "digitalocean-token DIGITALOCEAN_TOKEN DIGITALOCEAN_ACCESS_TOKEN DO_TOKEN DO_API_TOKEN TF_VAR_do_token SPACES_ACCESS_KEY_ID SPACES_SECRET_ACCESS_KEY"
    # Not "an Anthropic key": any metered model credential supersedes a
    # subscription, and #84 put a second vendor on this box. The names here are
    # the ones no adapter claims - OpenAI-compatible endpoints are configurable
    # in both agents, so a stray OPENAI_API_KEY is a metered key on this box
    # whether or not either vendor's own resolution order mentions it. Every
    # name an adapter DOES claim is added below rather than repeated here.
    "metered-model-key OPENAI_API_KEY"
    # SSH_AUTH_SOCK is a family member rather than a file: a forwarded agent is
    # fleet reach that leaves nothing on disk, and `ssh -A` to this box is the
    # easiest way to hand an unattended agent the operator's whole key ring.
    "fleet-ssh-key SSH_AUTH_SOCK"
)

# What each agent adapter says its vendor honours, folded into the family above.
#
# Read off the adapters rather than restated, because the adapter is where ADR
# 0004 puts vendor knowledge and because this script and the Run-start guard must
# not be able to disagree: the guard in `agents/grok.sh` refuses an Iteration
# whose environment holds one of these, and this script refuses a BOX that holds
# one anywhere. Two lists spelled out twice would drift the first time a vendor
# gained a name, and what would break is the check that the collision cannot
# happen.
#
# A silent empty answer is refused. An adapter that failed to run would leave
# this family holding one name and reading as complete, which is the one thing a
# check like this must never do.
adapters_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/agents"
adapter_metered_names=()
for adapter in "${adapters_dir}"/*.sh; do
    [[ -x ${adapter} ]] || continue
    adapter_names="$("${adapter}" --metered-env-names 2>/dev/null || true)"
    [[ -n ${adapter_names} ]] ||
        die "${adapter} named no metered key environment variables - this check would silently stop looking for them"
    while IFS= read -r adapter_name; do
        [[ -n ${adapter_name} ]] && adapter_metered_names+=("${adapter_name}")
    done <<<"${adapter_names}"
done
((${#adapter_metered_names[@]} > 0)) ||
    die "no agent adapters under ${adapters_dir} - the metered-model-key family would be incomplete"

for row_index in "${!forbidden_env[@]}"; do
    if [[ ${forbidden_env[row_index]} == metered-model-key\ * ]]; then
        forbidden_env[row_index]+=" $(printf '%s ' "${adapter_metered_names[@]}")"
        forbidden_env[row_index]="${forbidden_env[row_index]% }"
    fi
done

# The families, derived from the declaration above rather than written out a
# second time. The report at the bottom walks this list, so a family added to
# `forbidden_env` appears there without anyone remembering to add it.
forbidden_families=()
for row in "${forbidden_env[@]}"; do
    forbidden_families+=("${row%% *}")
done

# Services `sbx secret set` supports that would bill per token if the proxy
# injected one. `github` is not here: a GitHub secret in the boundary is a
# design question for #83, not a billing collision, and it is reported.
metered_secret_services=(anthropic openai xai google groq mistral nebius openrouter)

# --- Arguments ---------------------------------------------------------------

home="/home/loop"
system_root=""
sbx_cmd="sbx"

while (($# > 0)); do
    case "$1" in
        --home) home="${2:?--home needs a path}"; shift 2 ;;
        --system-root) system_root="${2:?--system-root needs a path}"; shift 2 ;;
        --sbx) sbx_cmd="${2:?--sbx needs a command}"; shift 2 ;;
        # Every forbidden environment variable name, one per line. The offline
        # suite unsets exactly these before each run - it has to be runnable in
        # a vaulted-agent session on the orchestration VM, where several of them
        # genuinely are in the environment. Reading them from here rather than
        # restating them is what stops the suite and the script drifting apart
        # the first time a family gains a name.
        --list-env-names)
            for row in "${forbidden_env[@]}"; do
                read -r _ names <<<"${row}"
                # Unquoted on purpose: the row holds several names and each is
                # wanted on its own line.
                # shellcheck disable=SC2086
                printf '%s\n' ${names}
            done
            exit 0
            ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -d ${home} ]] || die "no such home directory: ${home}"
home="$(cd -- "${home}" && pwd)"

signing_key="${home}/.ssh/loop_signing_ed25519"
token_file="${home}/.config/loop/github-token"

# Files whose job is to put a name into every future login's environment. A
# stray export here is invisible to `env` in this process and present in every
# Run afterwards, which is the difference between checking the environment and
# checking the box.
env_files=(
    "${home}/.bashrc" "${home}/.profile" "${home}/.bash_profile"
    "${home}/.bash_login" "${home}/.zshrc"
    "${system_root}/etc/environment"
)
while IFS= read -r extra; do
    [[ -n ${extra} ]] && env_files+=("${extra}")
done < <(
    find "${system_root}/etc/profile.d" "${home}/.config/environment.d" \
        -maxdepth 1 -type f 2>/dev/null || true
)

# --- Findings ----------------------------------------------------------------

violations=()
observations=()
indeterminate=()

violation() { violations+=("$1"); }
observe() { observations+=("$1"); }

# A probe that could not be evaluated. NOT a violation - it is the absence of
# evidence, and calling it a violation would make the script red forever on a
# box that is exactly right. It is emphatically not a pass either: the family it
# belongs to prints [partial] rather than [clear], because "we looked and found
# nothing" and "we could not look" are the same output only in a check nobody
# should trust. The documented invocation is `su - loop`, /root is mode 0700,
# and every probe under it lands here.
undetermined() { indeterminate+=("$1"); }

# Is NAME set and non-empty in this process's environment?
env_is_set() { [[ -n ${!1:-} ]]; }

# Which of the env_files assign NAME? Prints one path per line. Matches
# `NAME=`, `export NAME=` and `declare -x NAME=`, at a word boundary so
# MY_ANTHROPIC_API_KEY is not mistaken for the real thing.
env_files_assigning() {
    local name="$1" file
    for file in "${env_files[@]}"; do
        [[ -f ${file} ]] || continue
        if grep -qE "(^|[[:space:]])(export[[:space:]]+|declare[[:space:]]+-x[[:space:]]+)?${name}=" \
            "${file}" 2>/dev/null; then
            printf '%s\n' "${file}"
        fi
    done
}

# Printed with a leading zero so a mode in a violation line reads the way an
# operator would type it into chmod.
mode_of() { printf '0%s' "$(stat -c '%a' -- "$1" 2>/dev/null || printf '???')"; }

# --- The forbidden families --------------------------------------------------

# Every forbidden name, in this process's environment and in every file that
# would put it into a future one. Both doors, one loop.
check_forbidden_env() {
    local row family names name file
    for row in "${forbidden_env[@]}"; do
        read -r family names <<<"${row}"
        for name in ${names}; do
            if env_is_set "${name}"; then
                violation "${family}: ${name} is set in the environment"
            fi
            while IFS= read -r file; do
                [[ -n ${file} ]] || continue
                violation "${family}: ${name} is assigned in ${file}"
            done < <(env_files_assigning "${name}")
        done
    done
}

check_forbidden_files() {
    local family="$1" path parent
    shift
    for path in "$@"; do
        if [[ -e ${path} ]]; then
            violation "${family}: ${path} is on the box"
            continue
        fi
        # `[[ -e ]]` is false both for "not there" and for "cannot look", and
        # those are different answers. If the containing directory cannot be
        # searched, this probe found nothing because it was not allowed to.
        parent="$(dirname -- "${path}")"
        if [[ -d ${parent} && ! -x ${parent} ]]; then
            undetermined "${family}: ${path} could not be checked - ${parent} is not searchable by $(id -un)"
        fi
    done
}

check_forbidden_env
check_forbidden_files vault-token \
    "${system_root}/etc/orchestration/op.env" \
    "${home}/.config/op" "${home}/.op"

check_forbidden_files database-credential \
    "${home}/.my.cnf" "${home}/.mylogin.cnf" "${system_root}/root/.my.cnf"

check_forbidden_files digitalocean-token \
    "${home}/.config/doctl" "${system_root}/root/.config/doctl" \
    "${home}/.config/openstack"


# Fleet SSH keys. A private key is identified by its header rather than by its
# filename, because the filename is the one part of a key an operator renames.
repository_keys=()

# Whether a private key is upstream content of a checkout rather than something
# put on this box. True only when the file exists on the remote's recorded
# default branch - see the caller for why "tracked" is not enough.
key_came_with_the_clone() {
    local candidate="$1" dir root base relative
    dir="$(dirname -- "${candidate}")"
    root="$(git -C "${dir}" rev-parse --show-toplevel 2>/dev/null)" || return 1
    base="$(git -C "${root}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)" ||
        return 1
    relative="${candidate#"${root}/"}"
    git -C "${root}" cat-file -e "${base}:${relative}" 2>/dev/null
}
#
# root's home is the sweep's blind spot when this runs as the Run account, which
# is the documented way to run it. Named rather than passed over: an empty sweep
# of a directory nobody could open is not an empty directory.
for unreadable in "${system_root}/root" "${system_root}/root/.ssh"; do
    if [[ -d ${unreadable} && ! -x ${unreadable} ]]; then
        undetermined "fleet-ssh-key: ${unreadable} could not be swept - it is not searchable by $(id -un)"
    fi
done

while IFS= read -r candidate; do
    [[ -n ${candidate} ]] || continue
    [[ ${candidate} == "${signing_key}" ]] && continue
    # The box's own SSH host keys. Every Linux box has them, they are the box's
    # identity rather than reach into anything, and flagging them would make
    # this family red on a correctly-built box - which is how a check stops
    # being read. A key parked in /etc/ssh under any other name is still caught.
    [[ $(basename -- "${candidate}") == ssh_host_* ]] && continue
    if head -n1 -- "${candidate}" 2>/dev/null | grep -q -- '-----BEGIN .*PRIVATE KEY-----'; then
        # A key that CAME WITH THE CLONE is the repository's, not the box's. The
        # Loop's box holds a checkout of the repository a Run works in, and
        # `tourbot` carries three vendor sample keys in phpdocx's examples - so
        # without this the assertion is red on a correctly-built box, which is
        # precisely how a check stops being read (ADR 0009).
        #
        # The line is drawn at "on the remote's default branch", not at
        # "tracked", and the difference is the whole point: a Run commits into
        # this same checkout, so `git ls-files` would exempt a key an Iteration
        # committed - an unattended agent could put a fleet key on this box and
        # take it out of the sweep in the same move. A file on `origin/HEAD`
        # arrived by clone from upstream, which is what the exemption claims.
        #
        # No `origin/HEAD` means no exemption, which is the safe direction: a
        # checkout with no recorded default branch is one this cannot reason
        # about.
        if key_came_with_the_clone "${candidate}"; then
            repository_keys+=("${candidate}")
            continue
        fi
        violation "fleet-ssh-key: ${candidate} is a private key that is not the Loop's signing key"
    fi
done < <(
    # The whole home, not only ~/.ssh: a key put anywhere is a key. The
    # Execution Boundary's own state directory is excluded because it holds
    # guest images and sandbox state by the thousand file, and none of it is
    # somewhere an operator drops a key. A private key is under 32k, which is
    # what keeps this from reading the box.
    find "${home}" -type f -size -32k \
        -not -path "${home}/.local/state/sandboxes/*" \
        -not -path "${home}/.cache/*" 2>/dev/null || true
    find "${system_root}/root/.ssh" "${system_root}/etc/ssh" -maxdepth 1 -type f 2>/dev/null || true
)

# Reported rather than silent. An exclusion nobody can see is an exclusion
# nobody can audit, which is the same reason check-inventory.sh prints its own
# with the count each removed.
if ((${#repository_keys[@]} > 0)); then
    observe "$(printf '%d private key(s) skipped as upstream repository content: %s' \
        "${#repository_keys[@]}" "$(printf '%s ' "${repository_keys[@]}")")"
fi

# The third door: a secret the boundary holds, which is in no environment and no
# file this script can read.
sbx_secrets=""
# `timeout` on both boundary calls. As root, or on a box whose daemon is not
# running, `sbx` can sit waiting for a keyring or a daemon that will not arrive -
# and this script is what #83's preflight runs before an unattended Run. A
# preflight that blocks forever is worse than one that fails: the failure is
# reported, and the block is a Run that never starts and never says so.
if sbx_secrets="$(timeout 30 "${sbx_cmd}" secret ls 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g')"; then
    if grep -qi 'No secrets found' <<<"${sbx_secrets}"; then
        observe "the Execution Boundary stores no secrets"
    else
        # The table's header row is not a stored secret; listing it as one
        # makes the observation read as one more secret than there is.
        observe "the Execution Boundary stores: $(grep -viE '^(SCOPE|NAME)[[:space:]]' <<<"${sbx_secrets}" | tr '\n' ' ' | tr -s ' ')"
        for service in "${metered_secret_services[@]}"; do
            if grep -qiE "(^|[[:space:]])${service}([[:space:]]|$)" <<<"${sbx_secrets}"; then
                violation "metered-model-key: the Execution Boundary stores a '${service}' secret, which the proxy would inject"
            fi
        done
    fi
else
    # Not a violation of a credential family: it is the boundary being
    # unreachable, which the docker-identity row below reports as its own
    # failure. Saying it twice would inflate the count.
    observe "could not list the Execution Boundary's stored secrets"
fi

# --- The allowed inventory ---------------------------------------------------
#
# Each of these sets `state_<name>` to held/absent and appends its own
# violations. Absent is not automatically a violation: the model credential row
# is not gating today.

declare -A state=()
declare -A detail=()

# github-token
if [[ ! -f ${token_file} ]]; then
    state[github-token]="absent"
    detail[github-token]="expected at ${token_file}"
elif [[ ! -s ${token_file} ]]; then
    state[github-token]="absent"
    detail[github-token]="${token_file} is empty"
else
    state[github-token]="held"
    detail[github-token]="${token_file}"
    mode="$(mode_of "${token_file}")"
    if [[ ${mode} != "0600" && ${mode} != "0400" ]]; then
        violation "github-token: ${token_file} is mode ${mode}; it must be readable only by its owner"
    fi
    # A classic token cannot be scoped to one repository - its scopes are
    # account-wide - so its presence contradicts the acceptance criterion even
    # though the file is in the right place with the right mode.
    if ! grep -q '^github_pat_' -- "${token_file}"; then
        violation "github-token: ${token_file} does not hold a fine-grained token (github_pat_...); a classic token cannot be repository-scoped"
    fi
fi

# signing-key
if [[ ! -f ${signing_key} ]]; then
    state[signing-key]="absent"
    detail[signing-key]="expected at ${signing_key}"
else
    state[signing-key]="held"
    detail[signing-key]="${signing_key}"
    mode="$(mode_of "${signing_key}")"
    if [[ ${mode} != "0600" && ${mode} != "0400" ]]; then
        violation "signing-key: ${signing_key} is mode ${mode}; a private key must be readable only by its owner"
    fi
    [[ -f "${signing_key}.pub" ]] ||
        violation "signing-key: ${signing_key}.pub is missing; the public half is what git names as the signing key and what GitHub was given"
fi

# The key is only a credential if git actually signs with it, so the four
# settings that decide that are part of this row rather than a separate one.
gitconfig="${home}/.gitconfig"
git_setting() { git config --file "${gitconfig}" --get "$1" 2>/dev/null || true; }
if [[ -f ${gitconfig} ]]; then
    [[ "$(git_setting gpg.format)" == "ssh" ]] ||
        violation "signing-key: git gpg.format is not ssh, so the key would not be used"
    [[ "$(git_setting commit.gpgsign)" == "true" ]] ||
        violation "signing-key: git commit.gpgsign is not true, so an Iteration's commits would be unsigned"
    [[ -n "$(git_setting user.email)" ]] ||
        violation "signing-key: git user.email is unset, so a commit would not attribute to the operator's GitHub account"
    configured_key="$(git_setting user.signingkey)"
    case "${configured_key}" in
        "${signing_key}" | "${signing_key}.pub") ;;
        key::*)
            if ! { [[ -f "${signing_key}.pub" ]] &&
                grep -qF -- "${configured_key#key::}" "${signing_key}.pub"; }; then
                violation "signing-key: git user.signingkey holds a literal key that is not the Loop's"
            fi
            ;;
        *)
            violation "signing-key: git user.signingkey is '${configured_key}', not the Loop's key at ${signing_key}.pub"
            ;;
    esac
else
    violation "signing-key: no git config at ${gitconfig}, so nothing signs or attributes"
fi

# docker-identity
if diagnose="$(timeout 30 "${sbx_cmd}" diagnose 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g')"; then
    auth_line="$(grep -i 'Authentication' <<<"${diagnose}" | head -n1)"
    if [[ ${auth_line} == *authenticated* && ${auth_line} != *"not authenticated"* ]]; then
        state[docker-identity]="held"
        detail[docker-identity]="${sbx_cmd} reports authenticated"
    else
        state[docker-identity]="absent"
        detail[docker-identity]="${sbx_cmd} does not report authenticated - run wizards/loop-sbx-login.sh"
    fi
else
    # An assertion that could not be evaluated is not an assertion that passed.
    state[docker-identity]="unknown"
    detail[docker-identity]="${sbx_cmd} could not be asked (not installed, or its daemon is down)"
fi

# model-credential
# `.credentials.json` and nothing else. `~/.claude.json` is Claude Code's
# configuration and exists the moment the binary is installed, so accepting it
# would report a credential on a box where the login was never completed - and
# the Run would find out at its first Iteration.
if [[ -f "${home}/.claude/.credentials.json" ]]; then
    state[model-credential]="held"
    detail[model-credential]="${home}/.claude"
else
    state[model-credential]="absent"
    detail[model-credential]="no agent login on the box - see wizards/loop-claude-login.sh"
fi

held=0
for row in "${allowed[@]}"; do
    IFS='|' read -r name gating _ <<<"${row}"
    [[ ${state["${name}"]} == "held" ]] && held=$((held + 1))
    if [[ ${gating} == "yes" && ${state["${name}"]} != "held" ]]; then
        violation "${name}: ${state["${name}"]} - ${detail["${name}"]}"
    fi
done

# --- The report --------------------------------------------------------------

result="clean"
((${#violations[@]} == 0)) || result="violations"

printf 'CREDENTIALS_RESULT=%s\n' "${result}"
printf 'CREDENTIALS_HELD=%d\n' "${held}"
printf 'CREDENTIALS_VIOLATIONS=%d\n' "${#violations[@]}"
printf 'CREDENTIALS_INDETERMINATE=%d\n' "${#indeterminate[@]}"
printf 'CREDENTIALS_HOME=%s\n' "${home}"

printf '\nAllowed - the box holds these and nothing else:\n'
for row in "${allowed[@]}"; do
    IFS='|' read -r name gating what <<<"${row}"
    marker="${state["${name}"]}"
    [[ ${gating} == "no" ]] && marker="${marker}*"
    printf '  %-9s %-17s %s\n' "[${marker}]" "${name}" "${what}"
    printf '  %-9s %-17s %s\n' "" "" "${detail["${name}"]}"
done
# Printed only when there is a starred row to explain. Every row gates today -
# the model credential's stopped being the exception when #83 put the agent's
# login on the box - and a footnote about a marker nobody can see is noise in
# the one report that has to stay worth reading.
if printf '%s\n' "${allowed[@]}" | grep -q '|no|'; then
    printf '  %s\n' "* not gating: absent is the expected state for this row."
fi

printf '\nForbidden - none of these may be on the box:\n'
for family in "${forbidden_families[@]}"; do
    if printf '%s\n' "${violations[@]:-}" | grep -q "^${family}:"; then
        printf '  [found]   %s\n' "${family}"
    elif printf '%s\n' "${indeterminate[@]:-}" | grep -q "^${family}:"; then
        printf '  [partial] %s\n' "${family}"
    else
        printf '  [clear]   %s\n' "${family}"
    fi
done

if ((${#indeterminate[@]} > 0)); then
    printf '\nCould not be checked - these are not passes:\n'
    printf '  - %s\n' "${indeterminate[@]}"
    printf '  Run it again as root for the paths root owns. Run it as the Run\n'
    printf '  account for the environment a Run actually gets; neither run sees\n'
    printf '  what the other does.\n'
fi

if ((${#observations[@]} > 0)); then
    printf '\nObserved:\n'
    printf '  - %s\n' "${observations[@]}"
fi

if ((${#violations[@]} > 0)); then
    printf '\nViolations:\n'
    printf '  - %s\n' "${violations[@]}"
    printf '\nNothing here has been changed. What to do about a credential that reached\n'
    printf 'this box is not a decision to take unattended.\n'
    exit 2
fi

exit 0
