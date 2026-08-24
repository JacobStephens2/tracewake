# A Verified commit asserts the operator caused it, not that he wrote it

The Loop needs commits attributed to JacobStephens2 *and* carrying GitHub's
Verified badge. Signing server-side through the `createCommitOnBranch` GraphQL
mutation would keep every key out of the microVM, but it replaces the agent's own
`git commit` with a replay of file contents and fights the technique, whose unit
of work is one commit per Iteration. We instead generate a dedicated SSH signing
key on the Loop's host, register it to the operator's account as a signing key,
and let the agent commit normally. The operator's laptop key never leaves the
laptop, and this key revokes independently.

## Consequences

Verified changes meaning here, deliberately. Conventionally the badge is read as
"this person wrote this"; on Loop commits it means "this person caused this to
happen" - the operator chose the task, set the Termination Contract, and owns the
result. This follows the working practice of high-volume agent operators and is
the meaning the operator has adopted.

The externality is that other readers of `tourbot`'s history - Victoria, Michael,
any future engineer - will apply the conventional meaning unless told, so the
reinterpretation has to be published rather than held privately. The signing key
carries a distinct title, making the signing key itself the discriminator: which
key signed a commit is visible, so Loop commits stay distinguishable from
hand-authored ones without annotating commit messages. That matters because ETA
policy forbids AI-attribution trailers in commits and PR bodies, so a trailer is
not available as the marker.

The residual risk is accepted knowingly: a key that signs as the operator, on an
unattended box, means a compromise of that box produces *verified* commits in his
name. `required_pull_request_reviews: 1` on `tourbot`'s `master` is the
compensating control - nothing the Loop signs merges without a human approval.
