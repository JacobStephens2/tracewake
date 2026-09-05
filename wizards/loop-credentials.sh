#!/usr/bin/env bash
#
# A wizard — walks a human through minting the Loop box's three yearly credentials.
# Generated following the /wizard skill.
#
# Everything above the "STAGES" marker is the wizard library: do not hand-edit
# it. Author the per-step stages below the marker.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Wizard library — delightful, consistent UX. Identical across every wizard.
# ──────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

TOTAL_STAGES=5

_STAGE_INDEX=0
ENV_FILE="${ENV_FILE:-.env}"
WRITTEN_ENV=()
WRITTEN_SECRET=()
SKIPPED=()

_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages%s\n\n' "$DIM" "$TOTAL_STAGES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later — it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  printf '\n%s%s▸ Stage %s/%s · %s%s\n' \
    "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET"
}

say()  { printf '  %s\n' "$1"; }
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if   command -v wslview     >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open    >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open        >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser — visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser — visit it manually: $url"
}

pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

_existing() {
  [[ -f "$ENV_FILE" ]] || return 1
  local line; line=$(grep -E "^${1}=" "$ENV_FILE" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

ask() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -r input || true
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

ask_secret() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -rs input || true
  printf '\n'
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  if [[ -n "${SHARED_DATE:-}" ]]; then
    printf '  %sAll three credentials share expiry: %s%s%s (%s)\n\n' \
      "$BOLD" "$GREEN" "${SHARED_DATE}" "$RESET" "${SHARED_EXPIRY}"
  fi
  if (( ${#SKIPPED[@]} )); then
    printf '\n'; warn "still to do by hand:"
    for s in "${SKIPPED[@]}"; do note "  - $s"; done
  fi
  printf '\n'
}

# ──────────────────────────────────────────────────────────────────────────
# STAGES
#
# Mint all three yearly credentials for the Loop box (ADR 0020, closes #7):
#   1. Docker PAT (Docker Personal Access Token) for the execution boundary (sbx)
#   2. GitHub PAT (fine-grained PAT) scoped to the target repository
#   3. Model setup-token (Claude Code subscription setup-token)
#
# All three share a single expiration date (one year out), aligned so the box
# has one yearly maintenance event rather than multiple rolling expiries.
# ──────────────────────────────────────────────────────────────────────────

LOOP_HOST="${LOOP_HOST:-}"
[[ -n "${LOOP_HOST}" ]] || {
    printf 'LOOP_HOST is not set, and there is no default for it: where the box of this instance is, is a fact about this instance, not about Tracewake.\n' >&2
    exit 1
}

# The shared expiration date: exactly one year from today.
SHARED_DATE="$(date -u -d "+1 year" +%Y-%m-%d)"
SHARED_EXPIRY="${SHARED_DATE}T00:00:00Z"
SHARED_EPOCH="$(date -u -d "${SHARED_EXPIRY}" +%s)"
SHARED_EPOCH_MS="${SHARED_EPOCH}000"

TARGET_REPO="${LOOP_TARGET_REPOSITORY:-}"

ENV_FILE=/dev/null

on_loop() {
  ssh -n -o BatchMode=yes -o ConnectTimeout=10 "root@${LOOP_HOST}" \
    "su - loop -s /bin/bash -c $(printf '%q' "$1")"
}

on_loop_stdin() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "root@${LOOP_HOST}" \
    "su - loop -s /bin/bash -c $(printf '%q' "$1")"
}

banner "The Loop's yearly credentials - Docker PAT, GitHub PAT & Model setup-token"

# ── 1 ─────────────────────────────────────────────────────────────────────
stage "Preflight - connectivity and shared alignment"
say "Checking the box over SSH and confirming base tooling before minting."
printf '\n'

if ! on_loop 'true' >/dev/null 2>&1; then
  warn "cannot reach loop as root@${LOOP_HOST} over SSH."
  note "Run this as conductor: sudo -u conductor -i"
  exit 1
fi
printf '  %s✓%s SSH to root@%s\n' "$GREEN" "$RESET" "$LOOP_HOST"

if ! on_loop 'command -v sbx >/dev/null 2>&1'; then
  warn "sbx is not installed on the box."
  exit 1
fi
printf '  %s✓%s sbx installed\n' "$GREEN" "$RESET"

if on_loop 'test -n "${ANTHROPIC_API_KEY:-}"' 2>/dev/null; then
  warn "ANTHROPIC_API_KEY is set in loop environment; metered keys are forbidden."
  exit 1
fi
printf '  %s✓%s no metered model keys\n' "$GREEN" "$RESET"

printf '\n'
say "All three credentials will be aligned to share this expiry date:"
printf '  %s%sShared Expiry: %s (%s)%s\n\n' "$BOLD" "$GREEN" "$SHARED_DATE" "$SHARED_EXPIRY" "$RESET"
pause "Press Enter to proceed to Docker PAT."

# ── 2 ─────────────────────────────────────────────────────────────────────
stage "Docker - create a personal access token (sbx)"
say "Open Docker settings to create a personal access token for sbx."
printf '\n'
open_url "https://app.docker.com/settings/personal-access-tokens"
step "Select 'Generate new token'."
step "Description: ${LOOP_HOST} - sbx"
step "Expiration date: Select Custom date -> ${SHARED_DATE} (one year from today)."
step "Access permissions: 'Read-only'."
step "Generate and copy the token."
printf '\n'
ask DOCKER_USERNAME "Your Docker username (not your email):"
ask_secret DOCKER_PAT "Paste the Docker PAT:"

if [[ -z "$DOCKER_USERNAME" || -z "$DOCKER_PAT" ]]; then
  warn "Both username and token are required."
  exit 1
fi

if printf '%s' "${DOCKER_PAT}" | on_loop_stdin "sbx login --username $(printf '%q' "${DOCKER_USERNAME}") --password-stdin"; then
  printf '  %s✓%s sbx signed in as %s\n' "$GREEN" "$RESET" "$DOCKER_USERNAME"
else
  warn "sbx login failed."
  exit 1
fi
unset DOCKER_PAT

on_loop "umask 077 && mkdir -p /home/loop/.config/loop && printf '%s\n' '${SHARED_EXPIRY}' > /home/loop/.config/loop/docker.expiry"
printf '  %s✓%s Docker expiry recorded as %s\n\n' "$GREEN" "$RESET" "$SHARED_EXPIRY"
pause "Press Enter to proceed to GitHub PAT."

# ── 3 ─────────────────────────────────────────────────────────────────────
stage "GitHub - create fine-grained PAT"
say "Create a fine-grained personal access token scoped to the target repository."
printf '\n'
if [[ -z "$TARGET_REPO" ]]; then
  ask TARGET_REPO "Target repository (e.g. owner/repo):"
fi
open_url "https://github.com/settings/personal-access-tokens/new"
step "Token name: ${LOOP_HOST}"
step "Resource owner: ${TARGET_REPO%%/*}"
step "Expiration: Custom date -> ${SHARED_DATE} (one year from today)."
step "Repository access: 'Only select repositories' -> ${TARGET_REPO}"
step "Permissions -> Repository permissions:"
step "  Contents: Read and write"
step "  Pull requests: Read and write"
step "  Metadata: Read-only"
step "Generate and copy the token."
printf '\n'
ask_secret GITHUB_TOKEN "Paste the fine-grained GitHub PAT:"

if [[ -z "$GITHUB_TOKEN" ]]; then
  warn "GitHub token is required."
  exit 1
fi
if [[ "$GITHUB_TOKEN" != github_pat_* ]]; then
  warn "Token must be fine-grained (starts with github_pat_)."
  confirm "Continue anyway?" || exit 1
fi

TOKEN_FILE="/home/loop/.config/loop/github-token"
printf '%s\n' "${GITHUB_TOKEN}" | on_loop_stdin "umask 077 && mkdir -p /home/loop/.config/loop && cat > ${TOKEN_FILE}.new && mv ${TOKEN_FILE}.new ${TOKEN_FILE}"
unset GITHUB_TOKEN

on_loop "umask 077 && printf '%s\n' '${SHARED_EXPIRY}' > /home/loop/.config/loop/github-token.expiry"
printf '  %s✓%s GitHub token and expiry written to %s\n\n' "$GREEN" "$RESET" "$TOKEN_FILE"
pause "Press Enter to proceed to Model setup-token."

# ── 4 ─────────────────────────────────────────────────────────────────────
stage "Model - mint setup-token (Claude Code yearly token)"
say "Mint a yearly setup-token for the Claude Code subscription (ADR 0020)."
say "This token lasts for one year and is carried via CLAUDE_CODE_OAUTH_TOKEN."
printf '\n'
step "Run on your machine or on the box:  claude setup-token"
step "Log in with your subscription account and authorize."
step "Copy the generated setup-token (starts with sk-ant-oat01-)."
printf '\n'
ask_secret MODEL_TOKEN "Paste the setup token (sk-ant-oat01-...):"

if [[ -z "$MODEL_TOKEN" ]]; then
  warn "Setup token is required."
  exit 1
fi
if [[ "$MODEL_TOKEN" != sk-ant-oat01-* ]]; then
  warn "Token does not begin with sk-ant-oat01-."
  confirm "Continue anyway?" || exit 1
fi

MODEL_FILE="/home/loop/.config/loop/model-token"
EXPIRY_FILE="/home/loop/.config/loop/credential-expiry"
printf '%s\n' "${MODEL_TOKEN}" | on_loop_stdin "umask 077 && mkdir -p /home/loop/.config/loop && cat > ${MODEL_FILE}.new && mv ${MODEL_FILE}.new ${MODEL_FILE}"
printf '%s\n' "${SHARED_EXPIRY}" | on_loop_stdin "umask 077 && cat > ${EXPIRY_FILE}.new && mv ${EXPIRY_FILE}.new ${EXPIRY_FILE}"

# Write .credentials.json so offline readers and assertion scripts remain compatible
CRED_JSON="/home/loop/.claude/.credentials.json"
printf '{"claudeAiOauth":{"accessToken":"%s","expiresAt":%s}}\n' "${MODEL_TOKEN}" "${SHARED_EPOCH_MS}" | \
  on_loop_stdin "umask 077 && mkdir -p /home/loop/.claude && cat > ${CRED_JSON}.new && mv ${CRED_JSON}.new ${CRED_JSON}"

unset MODEL_TOKEN
printf '  %s✓%s Model setup-token written to %s and %s\n' "$GREEN" "$RESET" "$MODEL_FILE" "$CRED_JSON"
printf '  %s✓%s Credential expiry written to %s\n\n' "$GREEN" "$RESET" "$EXPIRY_FILE"
pause "Press Enter to verify credentials inventory on the box."

# ── 5 ─────────────────────────────────────────────────────────────────────
stage "Proof & Inventory"
say "Running credential assertions on the box to confirm all credentials are held."
printf '\n'

if on_loop '/home/loop/loop/assert-credentials.sh' 2>&1 | sed 's/^/    /'; then
  printf '\n  %s✓%s All credentials verified and clean on the box\n' "$GREEN" "$RESET"
else
  warn "assert-credentials.sh reported issues; check the output above."
  SKIPPED+=("verify assert-credentials on the box")
fi

printf '\n'
say "Credential Expiry Summary:"
printf '  %sDocker PAT:%s      %s (%s)\n' "$BOLD" "$RESET" "$SHARED_DATE" "$SHARED_EXPIRY"
printf '  %sGitHub PAT:%s      %s (%s)\n' "$BOLD" "$RESET" "$SHARED_DATE" "$SHARED_EXPIRY"
printf '  %sModel Token:%s     %s (%s)\n' "$BOLD" "$RESET" "$SHARED_DATE" "$SHARED_EXPIRY"
printf '\n'
printf '  %sAll three credentials share expiry date: %s%s%s (%s)\n' \
  "$BOLD" "$GREEN" "$SHARED_DATE" "$RESET" "$SHARED_EXPIRY"

finish
