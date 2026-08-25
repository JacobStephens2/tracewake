# A property belongs to an agent until two agents have shown it

The first Run produced a list of things that held: an Iteration ran inside a
microVM, commits were attributed and Verified, the GitHub token stayed outside
the boundary, the turn bound was recognised, the egress allowlist was two hosts
long. Every one of those was written down as a property of **the Loop**, and
every one of them was in fact observed of **the Loop running Claude Code** -
because that is the only thing that had ever run.

ADR 0004 made the agent a variable so this could be found out rather than
argued about. #84 ran the same task under Grok Build, and the answer is that the
list splits three ways rather than transferring whole.

## Decision

A property observed under one agent is recorded against **that agent** until a
second agent has shown it. Three tiers, and the tier is stated wherever the
property is:

**Structural** - true of the Execution Boundary and the Loop's shape, so true
for any agent. The microVM and its separate kernel; the workspace bind mount;
one boundary created and destroyed per Iteration; the fresh process per
Iteration; the five bounds of the Termination Contract; the GitHub token being
outside the boundary, and Proposal-Only Output following from that; the
`deny-all` egress posture and the two common hosts.

**Per-agent** - true of what the vendor does, so it must be re-established for
each. The egress hosts the agent needs and where they are declared; the wording
and exit status on reaching the turn bound; the permission mode that lets an
Iteration commit; the environment variable names that supersede the
subscription; whether the agent is provided by the boundary or installed inside
it, and at whose pin.

**Neither, and inherited wrongly** - claims that read as the Loop's and are the
first agent's alone. `sbx` credential injection is the one this project has
already got wrong twice: spec #73 assigned it to the Claude configuration, ADR
0011 found it holds for neither, and it would have been the natural thing to
carry into #84's writeup.

## Consequences

**The evidence notes carry the tier.** `loop-grok-run-evidence.md` states which
properties were re-established under the second agent and which were only
re-checked, and `loop-execution-boundary-evidence.md`'s isolation section
already separates what was gathered in an agent-less `shell` sandbox - which is
why those claims transferred untouched.

**Two suites, not one parameterised suite.** `tests/boundary.bats` and
`tests/boundary-grok.bats` assert the shared properties twice on purpose. A
shared suite would have to be written in the vocabulary the two agents have in
common, and that vocabulary is exactly where the differences hide - the turn
bound is one event with two spellings, and a suite that abstracted over the
spelling would have asserted nothing about either.

**The cost of a swap is now a number rather than a feeling.** Swapping agents
is one line in the Loop (`LOOP_AGENT_COMMAND`) and one in the play
(`loop_agent_name`); what it costs is an egress host the other agent does not
need, a per-Iteration install, and a credential the boundary cannot inject.
That is a trade an operator can weigh, which is what ADR 0004 was for.

**A third agent is a day's work, not a redesign.** Write a sibling adapter,
declare its hosts under `loop_execution_boundary_egress_by_agent`, add a row to
`tests/mutation-check.sh`, and re-run the same task. Nothing in `run.sh`,
`contract.sh`, `propose.sh` or the boundary moves - which is the claim ADR 0004
made and this ticket is the second data point for.
