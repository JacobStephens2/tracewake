# Attendedness is a fourth trust axis, and it does not collapse

`notes/lessons.md` decomposes safety into three axes - blast radius,
reversibility, trust model - and argues that all three collapse at single-user
scale, which is what licenses deleting the isolation stack outright ("Put the API
key in the environment and run the agent directly"). Designing the unattended
Loop showed that the blast-radius collapse rests on an unstated premise: *"I
notice and fix"*. An unattended Run is defined by nobody noticing for thirty to
forty-five minutes, so the premise fails and the collapse does not follow. We
therefore treat **Attendedness** as a fourth axis, and it is the only one that
does not collapse for a single operator.

## Consequences

This re-earns exactly one subsystem - the Execution Boundary - and nothing else.
The ETA factory's actual long pole stays deleted: no WIF-federated bearers, no
30-300 second credential TTLs, no tmpfs capability drives, no result-grepping, no
`BOUNDARY_TAINTED` marker, no content-trust floor. `lessons.md` remains correct
on every other point; this is a correction to one premise, not a reversal.

The axis also explains why the boundary is worth paying for even though the
operator authors his own inputs. It is not defending against hostile input. It is
defending against an unsupervised agent, which is a different thing and was
previously conflated with it.
