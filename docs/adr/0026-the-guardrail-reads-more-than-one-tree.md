# The Guardrail reads more than one tree

What runs unattended now lives in two places - the product itself, and the
instance's own configuration - and a guardrail that watched only one would be
green over an unreviewed change to the other.

## Context and Problem

The Guardrail stands over the code that executes unattended: it reads the forge's
branch protection rules over the ref the executed paths are deployed from, and
verifies that the deployed working tree matches that ref with no unreviewed
modifications.

Previously, the Guardrail was configured for a single repository and ref
(`SELECTOR_PROTECTED_REPO` and `SELECTOR_PROTECTED_REF`). For an instance like
ETA's, what runs unattended spans both Tracewake and the instance's orchestration
repository. If the Guardrail only inspects Tracewake, an unreviewed commit or
modified script in the orchestration tree would execute unattended with the status
chip showing reassuring green.

Furthermore, ADR 0021 moved the Guardrail reading to execute before each dispatch
during a Cycle drain. When the queue is idle - no issues queued or eligible - no
dispatch occurs, and no Guardrail event is journaled. As a result, an idle Selector
running every thirty minutes would allow its Guardrail reading to exceed the 90-minute
freshness threshold, causing the status chip on `/loop` to report stale silence even
when the system is healthy.

## The Decision

1. **Multi-tree declaration (`SELECTOR_GUARDRAIL_TREES`).**
   The instance declares every tree running unattended via `SELECTOR_GUARDRAIL_TREES`
   (as a JSON array of `{repo, ref, tree, paths}` objects or delimiter-separated
   tuples). When unset, it falls back to the existing single-tree configuration
   (`SELECTOR_PROTECTED_REPO`, `SELECTOR_PROTECTED_REF`, `SELECTOR_PROTECTED_TREE`,
   and `SELECTOR_PROTECTED_PATHS`) so existing instances remain fully compatible.

2. **All trees must be green for the chip to be green.**
   Every declared tree is evaluated in each non-dry-run Cycle. Each tree runs the
   guardrail probe with its specific `SELECTOR_PROTECTED_*` overlay. The overall
   reading is `protected: True` only if every declared tree passes all required
   rules (`deletion`, `non_fast_forward`, `pull_request`) and has zero unreviewed
   paths.

3. **Unknown is never green.**
   If the guardrail command fails or cannot answer for any tree, that failure counts
   against the verdict. The unreadable tree is recorded with `protected: False` and
   its error detailed; it never defaults or passes through as green.

4. **The chip names the failing tree and rule.**
   When protection fails or is unreadable, the event detail and the `/loop` chip
   prefix the fault with the repository name (e.g. `acme/config: master is missing pull_request`).
   When green with multiple trees, the chip displays each tree's repository, ref,
   and commit head.

5. **Journaled once per Cycle.**
   The Guardrail is read and journaled once at the beginning of each non-dry-run
   Cycle, before the dispatch loop. Because every Cycle (idle or active) records
   a reading, the chip reflects current repository and tree state, and properly
   reports silence only if the Cycle itself stops running.

## Consequences

- An unreviewed edit or missing branch protection on any declared tree turns the
  `/loop` status chip red immediately on the next Cycle.
- Idle cycles keep the Guardrail status fresh, eliminating false-positive stale
  alarms when no issues are dispatched.
- Multi-tree configuration is optional; single-tree instances require no migration.
