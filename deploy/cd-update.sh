#!/usr/bin/env bash
# Continuous deployment for the Tracewake Host (Single-Host, ADR 0029).
#
# Runs ON the Host as root, invoked over SSH by .github/workflows/deploy.yml
# on every push to the default branch:
#
#   ssh "root@${TRACEWAKE_SSH_HOST}" 'bash /srv/tracewake/deploy/cd-update.sh'
#
# TRACEWAKE_SSH_HOST is the repository variable the workflow reads; this
# script itself takes no address, it runs on the Host.
#
# What it does, in order:
#   1. Moves the product checkout to the tip of the deployed branch.
#   2. Re-installs the Selector and window dependencies (pip is a no-op
#      when the requirements are unchanged).
#   3. Re-applies selector/schema.sql, which is idempotent by contract
#      (see its header) and is what deploy/ansible's tracewake_controller
#      role already runs on every apply.
#   4. Writes the window's timer sudoers drop-in (visudo-validated) and
#      clears NoNewPrivileges on the installed window unit, so Start/Stop
#      can sudo. Ansible does this on provision; CD does it too because a
#      Host stood up before the rule existed would otherwise keep a dead
#      toggle after every merge.
#   5. Reloads systemd and restarts the long-lived units (the window and
#      the notifier). The cycle unit is a oneshot behind a timer, so the
#      next trigger picks the new tree up on its own.
#
# What it deliberately does NOT do: full provisioning stays in
# deploy/ansible/host.yml (packages, accounts, Caddy, guest templates).
# This script rolls the deployed revision forward; ansible builds the Host.
#
# Self-update: the workflow invokes the copy in the CURRENT checkout, which
# may be behind. After moving the checkout, the script re-execs the copy in
# the NEW tree (guarded by TRACEWAKE_CD_REEXEC) so the deployed revision's
# own logic always finishes the deploy.
#
# Safety: aborts when the checkout carries local modifications to tracked
# files (an operator hotfix must be committed, not reset away) and fails
# loudly when a step fails (set -euo pipefail) so the Actions run goes red.
set -euo pipefail

TRACEWAKE_DIR="${TRACEWAKE_DIR:-/srv/tracewake}"
TRACEWAKE_USER="${TRACEWAKE_USER:-conductor}"
TRACEWAKE_JOURNAL_DB="${TRACEWAKE_JOURNAL_DB:-selector}"
TRACEWAKE_BRANCH="${TRACEWAKE_BRANCH:-main}"

as_conductor() {
  sudo -u "$TRACEWAKE_USER" "$@"
}

if [ "${TRACEWAKE_CD_REEXEC:-0}" != "1" ]; then
  if [ -n "$(as_conductor git -C "$TRACEWAKE_DIR" status --porcelain --untracked-files=no)" ]; then
    echo "cd-update: refusing to deploy over local modifications in $TRACEWAKE_DIR." >&2
    echo "cd-update: commit them or revert them, then re-run the workflow." >&2
    exit 1
  fi
  as_conductor git -C "$TRACEWAKE_DIR" fetch --prune origin "$TRACEWAKE_BRANCH"
  as_conductor git -C "$TRACEWAKE_DIR" checkout "$TRACEWAKE_BRANCH"
  as_conductor git -C "$TRACEWAKE_DIR" reset --hard "origin/$TRACEWAKE_BRANCH"
  export TRACEWAKE_CD_REEXEC=1
  exec bash "$TRACEWAKE_DIR/deploy/cd-update.sh"
fi

as_conductor "$TRACEWAKE_DIR/selector/.venv/bin/pip" install -r "$TRACEWAKE_DIR/selector/requirements.txt"
as_conductor "$TRACEWAKE_DIR/web/.venv/bin/pip" install -r "$TRACEWAKE_DIR/web/requirements.txt"

as_conductor psql -d "$TRACEWAKE_JOURNAL_DB" -v ON_ERROR_STOP=1 \
  -f "$TRACEWAKE_DIR/selector/schema.sql"

# Two commands on the one unit and nothing else - the same rule ansible
# templates into /etc/sudoers.d/tracewake-timer.
TIMER_SUDOERS=/etc/sudoers.d/tracewake-timer
TIMER_SUDOERS_TMP=$(mktemp)
cat > "$TIMER_SUDOERS_TMP" <<EOF
# Let the window's admin toggle drive the Selector's timer without a shell.
#
# The window runs as the instance's unprivileged user while the timer is a
# system unit, so the Start/Stop buttons on the status strip reach it through
# exactly these two commands on the one unit - and nothing else.
$TRACEWAKE_USER ALL=(root) NOPASSWD: /usr/bin/systemctl enable --now tracewake-selector-cycle.timer, /usr/bin/systemctl disable --now tracewake-selector-cycle.timer
EOF
visudo -cf "$TIMER_SUDOERS_TMP" || { rm -f "$TIMER_SUDOERS_TMP"; exit 1; }
install -m 0440 -o root -g root "$TIMER_SUDOERS_TMP" "$TIMER_SUDOERS"
rm -f "$TIMER_SUDOERS_TMP"

# The shipped window unit no longer sets NoNewPrivileges (sudo cannot
# become root through that flag). Hosts that already have the old unit
# keep it until ansible re-copies; drop the line here so the restart
# below actually lets the toggle work.
WEB_UNIT=/etc/systemd/system/tracewake-web.service
if [ -f "$WEB_UNIT" ] && grep -q '^NoNewPrivileges=true' "$WEB_UNIT"; then
  sed -i '/^NoNewPrivileges=true$/d' "$WEB_UNIT"
fi

systemctl daemon-reload
systemctl try-restart tracewake-web.service
systemctl try-restart tracewake-selector-notifier.service

# A deploy that leaves the window down is a failed deploy.
systemctl is-active --quiet tracewake-web.service

echo "cd-update: deployed $(as_conductor git -C "$TRACEWAKE_DIR" rev-parse --short HEAD) on branch $TRACEWAKE_BRANCH."
