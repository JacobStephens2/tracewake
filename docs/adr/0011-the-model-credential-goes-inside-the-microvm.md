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

## Amendment, 2026-08-30 (#259): the injection property holds after all, and it arrives unasked

"Host-proxy credential injection holds for neither configuration as the Loop is
built" is wrong. It holds for this one, and the Loop did not choose it.
`sandboxd`'s own log, during an Iteration:

```
proxy: sandbox sent OAuth refresh but host has no refresh token (OAuth state mismatch)  service=anthropic
proxy: intercepted OAuth token response  service=anthropic  url=https://platform.claude.com:443/v1/oauth/token
oauth: token manager updated tokens  expires_at=2026-08-31T03:21:38Z  scopes="user:file_upload user:inference user:mcp_servers user:profile user:sessions:claude_code"
proxy: oauth engine activated OAuth mode  service=anthropic
proxy: masked OAuth tokens in response with sentinels  service=anthropic
```

What the reasoning above missed is that `sbx secret set` is not the only way a
service secret arrives, and that a service secret is not only an API key. The
proxy terminates TLS for the vendor's OAuth endpoint. When the agent inside the
boundary refreshes the session `loop/agents/claude.sh` copied in, the proxy
intercepts the token response, takes custody of the tokens **on the host**, and
returns sentinels to the guest in their place. Every later sandbox sends the
sentinel back and the proxy substitutes the real refresh token -
`proxy: replaced sentinel refresh token in OAuth request`, observed on a bare
`sbx create`, twice.

So the accurate statement is not "the credential is copied in". It is: the
credential is copied in, **and** the host proxy takes custody of it the first
time an Iteration refreshes. There was something for the injection path to
inject; it just had to be captured rather than set.

### The residual, accepted rather than prevented

State crosses the boundary outward. After any Run the host's `sbx` store holds a
global `service anthropic` secret that nothing on the host created. Accepted,
for three reasons:

- **It cannot be prevented without giving up authentication.** The capture is a
  property of every OAuth exchange the guest performs, and the guest has to
  perform one.
- **What is captured is the operator's own subscription session** - scopes
  `user:inference` and `user:sessions:claude_code`, minted from his own refresh
  token. An injected request authenticates as the subscription and bills against
  it, not per token. User story 32's collision is a metered key superseding the
  subscription, and this is the subscription itself.
- **Removing it per Iteration would fight the tool and race a live proxy**, and
  `sbx secret rm` prints `Cancelled` and exits 0 with no terminal - so the
  removal that appears to have worked has not.

What is **not** accepted, and stays a violation: an API-key-backed `service`
secret for any model vendor. `sbx secret ls` separates the two forms by printing
`(oauth configured)` in the SECRET column, and `assert-credentials.sh` reads that
column rather than the service name.

**The scope is global, and that is the part to watch.** The entry is not scoped
to the Loop's sandboxes, so any sandbox created on that box - a `shell` sandbox,
anything an operator makes by hand - is handed the operator's subscription by
the proxy. Narrowing it to a per-sandbox scope is not attempted here.

**It gives #260 a second copy of the credential to reason about.** The proxy's
token manager held a session refreshed at 19:27 on 2026-08-30, expiring
03:27 the next day, while `/home/loop/.claude/.credentials.json` had not been
written since 17:13 on 2026-08-29. A refresh performed inside an Iteration
therefore does **not** die with the sandbox, contrary to the reasoning in that
ticket and in the comments of `agents/claude.sh`: it survives in the `sbx`
store, where nothing the Loop runs reads it and where it cannot age out the
host's own stale copy.
