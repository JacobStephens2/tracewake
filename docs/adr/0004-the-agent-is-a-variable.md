# The agent is a variable, not a decision

We evaluated running the Loop on Claude Code (natively supported by `sbx`, whose
host proxy injects credentials so the key never enters the microVM) against Grok
Build (the preferred agent for unattended work, already authenticated on the
orchestration VM, but absent from `sbx`'s supported list - Claude Code, Codex,
Copilot, Cursor, Docker Agent, Droid, Gemini, Kiro, OpenCode, Shell). Rather than
pick, we made the agent invocation one substitutable command, because Ralph's
architecture is a shell loop around a single headless call and both agents accept
the same shape: a single-turn prompt flag, `--permission-mode acceptEdits`, and a
native turn bound.

## Consequences

The first Run uses Claude Code, keeping `sbx` credential injection intact while
the loop mechanics are unproven. Grok Build is the second experiment and costs an
egress allowlist (`x.ai`, `auth.x.ai`, the cli-chat-proxy host - `sbx` is
deny-by-default and ships no xAI rules) plus a pinned one-line install. Running
the same Run under both agents tells us something about the technique rather than
about one vendor, which is the point of the spike.

We do **not** get to claim "credentials never enter the VM" as a property of the
Loop. It holds for Claude Code under `sbx` and not for Grok, whose subscription
credential is an auto-refreshing OAuth token in `~/.grok/auth.json` that must be
copied inside.

Hazard worth recording: Grok's `XAI_API_KEY` takes precedence over browser
credentials, and `TOURBOT_PREVIEW_XAI_API_KEY` already exists in both
`/srv/orchestration/env.tpl` and the vaulted-agent manifest. A bare `XAI_API_KEY`
reaching that box silently bypasses the subscription and moves billing to a
metered key with no error - the same failure shape as the `ANTHROPIC_API_KEY`
collision that broke Remote Control.
