#!/usr/bin/env bash
#
# The box's own facts, faked for an Attended Preview (ADR 0016).
#
#   facts.sh
#
# Same contract as box-sources/facts.sh: print `LOOP_BOX_*=value` lines and
# exit 0 when the box answered.
#
# It exists for the same reason box.sh does, one step milder. The real one
# only reads - a hash, a template, a version - so it would not start anything;
# but it is still an SSH session opened from a preview instance to the box
# that runs Runs, on the say-so of unreviewed code, every cycle. "Nothing
# leaves the VM" is a property of the preview rather than a description of it,
# and a read is a thing that leaves.
#
# The facts below are deliberately not the live box's. A preview that showed
# the real hash would let a reviewer read the card as evidence about the box,
# when what it is evidence about is the branch's rendering of a card.
set -euo pipefail

cat <<'FACTS'
LOOP_BOX_SCRIPTS_HASH=preview0fixture
LOOP_BOX_AGENT=claude
LOOP_BOX_AGENT_VERSION=2.1.221 (Claude Code, preview fixture)
FACTS
