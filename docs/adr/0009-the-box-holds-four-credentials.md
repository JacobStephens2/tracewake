# The Loop's box holds four credentials, not three

Spec issue #73 says the box holds exactly three: a model credential, a
repository-scoped GitHub token, and a dedicated signing key. It holds four.
`sbx` will not create a sandbox without a Docker identity, so the Execution
Boundary - the one subsystem Attendedness re-earns (ADR 0003) - itself requires
a credential. #78 installed one knowingly rather than quietly, and this ADR
folds it into the inventory rather than leaving it as an exception to a count.

The fourth is a **read-only Docker personal access token**, held under `~loop`.
It reads Docker Hub. It cannot push an image, reach any ETA system, or
authenticate to anything else. It revokes on its own from `app.docker.com`
without touching the other three, and revoking it stops the boundary rather than
degrading it - which is the right failure direction for a credential whose whole
job is to let a boundary start.

Alongside it, the inventory is asserted by a script rather than described in a
document: `loop/assert-credentials.sh`, run on the box, reports both directions -
the four the box is allowed to hold, and the four families it may not.

## Consequences

The number in spec #73 is wrong wherever it appears, and the fix is to say four
rather than to stop counting. A count that has to be remembered as "three, plus
one nobody mentions" is not an inventory, and the reason to have an inventory at
all is that every isolation argument in the spec rests on it.

The spec's fifth acceptance criterion for #81 - "the box holds no vault token, no
database credential, no fleet SSH key, and no DigitalOcean token" - is unaffected
and still true as written. It is the count above it that moved. That distinction
is worth keeping straight: the Docker token widens what the box holds by one line
that reads Docker Hub, and widens what the box can reach by nothing.

The assertion gates on the Docker identity, which is a step past merely
counting it, and deliberate: the script grades the box **for a Run**, not for a
ticket. A box whose `sbx` session has lapsed satisfies every acceptance
criterion of #81 and would start a Run with no Execution Boundary, and this
script is what #83's preflight asks. So "the box satisfies #81" and "the
assertion is clean" are different statements, and the second is the stricter one.

A fifth credential is a decision, not an accident, and the assertion script is
where it becomes visible: anything on the box that is not one of the four is
either a violation it names or a gap in the script, and both are things to fix
rather than to discover during a Run.

## Amendment: Three base credentials plus one token per target (Issue #5, September 2026)

When the box serves more than one target repository, holding a single GitHub token
would either require that token to have scopes across multiple repositories
(violating repository scoping and blast-radius bounds) or require a separate box
for every target.

The invariant is amended from "four credentials" to **three base credentials plus
one repository token per target**:

- Base credentials (fixed across all targets):
  1. Dedicated SSH signing key (held on the box, registered to the operator)
  2. Read-only Docker personal access token (`sbx` requirement)
  3. Model credential (the operator's agent subscription login)
- Per-target credentials:
  - Exactly one fine-grained, repository-scoped GitHub PAT per declared target,
    placed where only the Run account (`loop`) can read it (mode 0600 or 0400).

On a box serving one target, the count is four (3 base + 1 target token). On a box
serving N targets, the count is 3 + N.

The credential inventory (`loop/assert-credentials.sh`) enforces this amended
invariant: every declared target token is validated for fine-grained prefix
(`github_pat_`), owner-only permissions (0600/0400), and counted in `CREDENTIALS_HELD`.
Any classic token or extra credential remains a violation.

