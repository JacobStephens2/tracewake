#!/usr/bin/env bash
#
# The agent, as one substitutable command (ADR 0004).
#
#   claude.sh <prompt-file> <max-turns>
#
# Invoked by run.sh with the repository as the working directory, once per
# Iteration, as a fresh process. Exits with the agent's exit status. Swapping
# vendor is writing a sibling of this file and pointing LOOP_AGENT_COMMAND at
# it - which is exactly what the offline suite does with its scripted fake, and
# what #84 will do with Grok Build.
#
# Vendor-specific concerns belong HERE rather than in the loop, and the
# credential guard below is why that distinction earns its keep: the collision
# it defends against is a property of this agent, not of the Loop.

set -euo pipefail

prompt_file="${1:?usage: claude.sh <prompt-file> <max-turns>}"
max_turns="${2:?usage: claude.sh <prompt-file> <max-turns>}"

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

# --permission-mode acceptEdits, not a bypass: the Iteration edits and commits
# without prompting, because nobody is there to answer a prompt, and the
# Execution Boundary rather than the permission mode is what it cannot cross.
# The turn bound is the agent's own - the Loop passes the Contract's value in
# rather than reimplementing it.
#
# This calls `claude` directly. Wrapping the call in `sbx` so each Iteration
# gets its own microVM is #83's change, and it belongs in this file - the Loop
# does not know what a boundary is.
exec claude \
    --print \
    --permission-mode acceptEdits \
    --max-turns "${max_turns}" \
    "$(cat -- "${prompt_file}")"
