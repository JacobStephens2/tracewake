#!/usr/bin/env bash
#
# Seeding, faked for an Attended Preview (ADR 0016).
#
# The real seed-run.sh writes a Plan and an initialized Progress Log into a
# checkout of the task repository. A preview has a throwaway work repo and no
# task, so this writes a Plan that says exactly that and returns.
set -euo pipefail
echo "preview: seeded a throwaway Plan for $* (no task was fetched)" >&2
exit 0
