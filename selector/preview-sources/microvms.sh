#!/usr/bin/env bash
#
# The box's microVMs, faked for an Attended Preview (ADR 0016).
#
#   microvms.sh
#
# Same contract as box-sources/microvms.sh: print `sbx ls --json` and
# exit 0 when the box answered.
#
# It exists for the same reason facts.sh does. The real one only reads, but
# it is still a listing of the live box's Execution Boundary, on the say-so
# of unreviewed code, every page view. A preview that showed the real
# microVMs would let a reviewer read the widget as evidence about the box,
# when what it is evidence about is the branch's rendering of a widget.
set -euo pipefail

cat <<'LIST'
{"sandboxes":[{"name":"preview-fixture","agent":"claude","status":"running","workspace":"/preview/workspace"}]}
LIST
