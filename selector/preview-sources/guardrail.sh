#!/usr/bin/env bash
#
# The guardrail read, for an Attended Preview (ADR 0016).
#
# The real `guardrail-sources/protection.sh` runs `gh api` against the
# instance's repository and walks the deployed tree. Both are outward reaches
# on the say-so of unreviewed code, which is exactly what preview-cycle.sh
# exists to prevent - so a preview reads this instead and gets a protected
# answer that nothing had to ask GitHub for.
#
# Canned, and deliberately the PROTECTED state: a preview is for looking at
# the page, and a chip that was red on every preview would train a reader to
# ignore the one place it matters.
set -euo pipefail

printf 'SELECTOR_GUARDRAIL_REF=%s\n' "preview"
printf 'SELECTOR_GUARDRAIL_REF_HEAD=%s\n' "0000preview00"
printf 'SELECTOR_GUARDRAIL_RULES=%s\n' "deletion,non_fast_forward,pull_request"
printf 'SELECTOR_GUARDRAIL_PATHS=%s\n' "loop,selector"
printf 'SELECTOR_GUARDRAIL_UNREVIEWED=\n'
