#!/usr/bin/env bash
#
# Owner-wide Handover search, faked for an Attended Preview (ADR 0016).
#
#   search.sh <owner> <label>
#
# Same contract as search-sources/github.sh - {"issues": [...]} - and the
# issues are none. A preview may run a whole cycle; what it may not do is
# search the operator's real account, because the next thing a cycle does
# with an unenrolled hit is journal a warning and (live) mail it.
set -euo pipefail

: "${1:?usage: search.sh <owner> <label>}"
: "${2:?usage: search.sh <owner> <label>}"

printf '{"issues": []}\n'
