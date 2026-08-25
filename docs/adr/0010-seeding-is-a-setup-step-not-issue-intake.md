# Seeding a Run is a setup step, and the box cannot read issues

A Run is seeded by fetching **one task, by number, chosen by the operator** and
writing it into the Plan. `loop/seed-run.sh` is that step, and it can do nothing
else: two arguments, a repository and a number, no search, no list, no filter,
no "next open issue". Spec issue #73 puts real issue intake out of scope; this
records why the boundary sits exactly there, because "we did not build it yet"
and "building it would invalidate something" are different statements and only
the second survives someone deciding intake looks easy.

ADR 0003 says the Execution Boundary is defending against an **unsupervised
agent**, not against hostile input, and that this is why `lessons.md`'s deleted
content-trust floor stays deleted. That argument has a premise: the operator
authored everything the Loop reads. It holds for a task he wrote and picked, and
it stops holding the moment the Loop reads issues by search - because the Plan is
prompt, the prompt goes to a process that can commit and open a pull request,
and nobody is watching for thirty to forty-five minutes. Intake would therefore
not be one more feature on top of this design. It would reopen the subsystem
ADR 0003 closed, and it needs its own decision rather than an afternoon.

**The property is enforced by what the token can reach, not by convention.** The
box's fine-grained token holds Contents: Read and write and Pull requests: Read
and write, and no Issues permission at all. So a Run cannot fetch a task even if
something inside it decided to: the request does not return the issue.
`wizards/loop-github-credentials.sh` sets that permission in stage 3 and probes
it in stage 4, asking GitHub whether the box's token can list the target
repository's issues and refusing to continue quietly if it can - the same shape
as the existing probe for a second repository, and for the same reason: a scope
is a claim until something asks. `seed-run.sh` runs as the operator, with
the operator's own GitHub identity, off the box - and the Plan reaches the box
the way everything else does, as a commit. That is the same shape as
Proposal-Only Output: a property of what the credential opens, not a rule the
agent is honouring.

## Consequences

**Do not add Issues to the box's token.** It is a one-line change in a browser
and it is the thing that turns this from a property into a convention. If a
later ticket needs a Run to read an issue, that ticket owns re-arguing ADR 0003,
not this one.

**The handover is reviewable twice.** The task's text is committed to the
repository before the Run starts, so the operator sees exactly what the agent
will read in a diff, and a reviewer sees it again in the pull request. That is
worth more than it looks: it is the only point at which a human reads the input
to an unattended process, and it costs nothing because the Plan had to be
committed anyway.

**Authorship is a social property, not a cryptographic one**, and it is worth
saying so rather than implying more. Fetching #648 does not prove Jacob wrote
#648; it proves he chose it. What makes the collapse valid is a single-operator
repository plus an operator who read the task he handed over - and the commit
above is what makes the second part checkable rather than assumed. On a
multi-operator repository this reasoning does not transfer, which is already why
spec #73 puts multi-operator use out of scope.

**One owning area, not the whole task.** `--area` is required. A Run is a handful
of Iterations and most tasks are larger than that, so how much of a task a Run is
for is a decision, and it is the operator's rather than an Iteration's. The Plan
says which area the Run is scoped to and that the rest is not remaining work, so
an Iteration that wanders into the rest of the task has left the Plan rather than
found more of it.

**Re-seeding is reproducible and refuses to be quiet.** The Plan and the Progress
Log are a pure function of the task, the area and the check command, and neither
carries a timestamp - so seeding the same task twice produces the same two files
and commits nothing the second time. Because both files are written whole rather
than appended to, a Progress Log that already records a Run would be destroyed
by a re-seed; that exits 2 and names `--reseed` instead. The Run's record is the
only account of what an unattended agent did, and losing it costs more than
typing the command again.
