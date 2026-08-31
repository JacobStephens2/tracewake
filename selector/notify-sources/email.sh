#!/usr/bin/env bash
#
# The Loop's notification surface: one email to the operator.
#
#   email.sh <subject> [link]            # body on stdin
#
# The default SELECTOR_NOTIFY_COMMAND (#280). ADR 0018 put this surface on the
# Selector's side rather than on the Loop's box: the box's whole external reach
# stays "the repository" - no mail host on its egress allowlist and no fifth
# credential in its inventory (ADR 0009) - while this VM already holds mail
# infrastructure and its credentials for the status dashboard.
#
# So this sends nothing itself. It hands the notice to that same mailer, in the
# dashboard's own virtualenv, which is what "the same mechanism the status
# dashboard uses" means concretely: the same Mandrill relay, the same
# credentials out of the same place, the same DASHBOARD_ALERT_TO target, and one
# definition of how this box sends mail rather than two.
#
# Email only, never SMS. Settled with the operator on 2026-08-31 and recorded in
# ADR 0018: nothing in the Loop is urgent the way a down production host is. The
# `--channel email` below is where that decision is enforced.
#
# The body arrives on stdin rather than as an argument. It is multi-line text
# and an argument would put the whole of it in this VM's process listing - the
# same reason issue-sources/github.sh takes a comment body that way.
#
# Substitutable (ADR 0004) like every other outward reach here, and it is the
# seam the notifier's suite drives: a scripted fake in its place is what lets
# those tests assert which notices were sent, with no relay and no network.
#
# Exit codes: 0 sent, 1 could not run or the relay refused. A non-zero exit
# leaves the notifier's cursor where it was, so the notice is sent again on the
# next start rather than lost.

set -euo pipefail

subject="${1:?usage: email.sh <subject> [link]   # body on stdin}"
link="${2:-}"

# Resolved from this script rather than hardcoded to /srv/orchestration, so a
# worktree sends through ITS OWN copy of the mailer. The deployed tree resolves
# to the same path, and a checkout whose notifier and mailer disagree - which is
# every branch that touches both - would otherwise be untestable without
# merging it first.
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD="$(cd -- "${here}/../../../../status-dashboard" && pwd)" || {
    printf 'notify-sources/email.sh: no status-dashboard beside %s\n' "${here}" >&2
    exit 1
}

# The interpreter, though, may come from the deployed tree: `.venv/` is
# gitignored, so a worktree has none, and the mailer that runs is the one
# beside the script above whichever interpreter loads it (a script's own
# directory is what leads sys.path). Requirements are the dashboard's either
# way - this needs `requests`, which the Selector's venv does not have.
PYTHON="${DASHBOARD}/.venv/bin/python"
[[ -x ${PYTHON} ]] || PYTHON=/srv/orchestration/status-dashboard/.venv/bin/python
[[ -x ${PYTHON} ]] || {
    printf 'notify-sources/email.sh: no dashboard venv at %s\n' "${PYTHON}" >&2
    exit 1
}

# Read whole, then passed as an argument to the dashboard's own CLI, which
# takes --detail that way. It is the notifier's own text and holds no secret;
# what it must not do is arrive empty, which would send a subject with no body.
detail="$(cat)"
[[ -n ${detail} ]] || {
    printf 'notify-sources/email.sh: the notice body was empty\n' >&2
    exit 1
}

exec "${PYTHON}" "${DASHBOARD}/checkpoint_notify.py" \
    --kind loop \
    --channel email \
    --subject "${subject}" \
    --detail "${detail}" \
    --link "${link}"
