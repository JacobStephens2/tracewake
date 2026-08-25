# The model credential goes inside the microVM

Spec issue #73 says the Claude Code configuration "retains host-proxy credential
injection", where `sbx`'s proxy authenticates the agent's API requests and the
secret is never exposed inside the guest. The first Run does not have that
property, and the reason is the credential the same spec chose.

`sbx secret set` injects a **service secret** - an API key. The Loop bills
against a subscription, deliberately: user story 32 asks for a metered key to be
impossible or loudly detected, and the Termination Contract is the entire cost
control precisely because a subscription cannot be capped per Run. A
subscription login is an OAuth session in `~/.claude/.credentials.json`, not a
key the proxy can present, so there is nothing for the injection path to inject.

So the credential is copied into each Iteration's microVM by
`loop/agents/claude.sh`, and the agent inside can read it.

## Consequences

**The two properties trade against each other, and the spec assumed both.** A
metered key would give credential isolation and give up the cost control; the
subscription gives the cost control and gives up credential isolation. #78's
evidence note already found the spec too strong on this axis for Grok; it is too
strong for Claude Code as well, for a different reason. What is true is narrower
than what was written: the microVM, the separate kernel and the egress proxy
hold for both agents, and host-proxy credential injection holds for neither
configuration as the Loop is built.

**What bounds the exposure is the boundary and the credential, not the proxy.**
Egress is `deny-all` plus `github.com` and `api.github.com` (#100) plus the six
Anthropic hosts `sbx`'s own kit attaches, so a leaked session has nowhere
obvious to go; the sandbox is destroyed after every Iteration, so a copy outlives
one Iteration only; and the session is the operator's own, revocable from his
account without touching the other three credentials on the box.

**The signing key is inside on the same terms, and for a better reason.** An
Iteration commits, and a commit that is not signed is not Verified - which is
the acceptance criterion, and it is not recoverable after the fact. The GitHub
token is the one credential deliberately left outside: the push and the draft
pull request happen on the host after every agent process is gone, which is what
makes Proposal-Only Output a property of what is inside the boundary rather than
of what the prompt asked for.

**#84 inherits a question rather than an answer.** `xai` *is* a supported `sbx`
secret service, so a Grok configuration authenticating with an API key could
have the injection property that this one cannot. That would make the two
experiments differ on a second axis besides the model, which is worth knowing
before the comparison is drawn.
