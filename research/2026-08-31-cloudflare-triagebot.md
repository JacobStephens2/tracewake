# Cloudflare/Astro triagebot: architecture, evidence, forkability

Banked as an **eta-factory lead**, not a Loop adoption: the system's whole input surface is hostile third-party GitHub issue text plus reporter-supplied reproduction repos — exactly what eta-factory defends against and the Loop excludes by design (ADR 0010/0014).

The short version: "Cloudflare's triagebot" is really the **Astro team's** `withastro/triagebot-action` — a Node GitHub Action driving a label-encoded finite state machine (reproduce → diagnose → verify → fix) over each new issue, executed by LLM subagents on a plain `ubuntu-latest` GitHub Actions runner via the **Flue** agent framework, with a preview release published to `pkg.pr.new` and the *reporter* (not a maintainer) as the gate before a PR is opened. The blog post lives on blog.cloudflare.com because Astro's core maintainers work at Cloudflare; the models in the headline config are Cloudflare Workers AI Kimi models, but the action's *defaults* are Anthropic Claude models and Workers AI is just one of three pluggable providers over an OpenAI-compatible REST endpoint. The measured claim is a self-reported open-issue count on `withastro/astro`: "from over 200 to about 30" over "the past several months," with the 85% figure appearing only in the post's meta-description — and the count is confounded by Astro's separate 3-day auto-close stale-issue workflow. The security posture is thin by eta-factory standards: isolation is the ephemeral Actions runner itself (Flue's `local()` sandbox, full internet access), untrusted issue text is interpolated raw into prompts, and the phrase "prompt injection" appears zero times in the blog post or the repo README. The real, forkable engineering is the **token split** (the agent session only ever holds a read token; commit/push/comment/PR run outside the session with a separate bot write token) and the FSM/retry/report.md pipeline shape. One hard blocker: **`triagebot-action` has no license** — no LICENSE file, no `license` field in package.json, GitHub's license API 404s — so despite the blog's "The code is open. Fork it," a literal fork is legally all-rights-reserved code. Flue itself is Apache-2.0.

## A. Provenance and what "Cloudflare's" means

- Blog post: "How Astro used AI agents to resolve its GitHub issue backlog," blog.cloudflare.com/astro-issue-triage/, authored by **Matthew Phillips** (Astro core maintainer), published **2026-08-04T13:00:00Z** (page metadata via extraction; UNVERIFIED against a byline screenshot).
- The action repo is under the **withastro** org, not cloudflare: `github.com/withastro/triagebot-action`, created 2026-06-17, description "A GitHub Action for issue triage," actively pushed (last push 2026-08-28 at time of research).
- Flue, the agent framework underneath, is also withastro: `github.com/withastro/flue` ("The sandbox agent framework," created 2026-02-07), npm `@flue/runtime`, homepage flueframework.com. Blog: the automation "grew into Flue, an open framework for building this kind of agent automation." Flue's runtime is built on `@earendil-works/pi-agent-core` / `pi-ai` (the pi agent stack) per the pnpm lockfile.
- Doc bug worth knowing before trusting the README: triagebot-action's README links Flue as `https://github.com/anthropics/flue`, which does not resolve. The real repo is `withastro/flue`.

## B. Pipeline stages as the sources describe them

Blog (paraphrase of extraction) and repo README/SKILL.md agree on a four-phase pipeline run per issue:

1. **Reproduce** — clone/set up the reported reproduction and verify the bug occurs. The example `reproduce.md` skill instructs early exits: not-actionable (feature requests), missing-details (no repro or expected-behavior), unsupported-version, and host-specific/CI-untriageable ("skipped").
2. **Diagnose** — instrument the codebase, add logging, pinpoint root cause; self-rated confidence (high/medium/low); low confidence exits the pipeline.
3. **Verify** — check tests, comments, docs to decide bug vs intended behavior. Blog gives the rationale: "To prevent the frequent LLM bias toward forcing a solution when a bug might not actually exist, each phase is executed by an isolated subagent." Verdict `intended-behavior` exits.
4. **Fix** — convert the repro into failing unit tests, fix, verify.

Mechanics from `SKILL.md` and `src/handlers/triage.ts`:

- Each phase runs as a **subagent** "to isolate context" (SKILL.md, both the action's own skills and Astro's copy at `withastro/astro/.agents/skills/triage`). Handoff between phases is a **`report.md`** file in the triage working dir — "The orchestrator and downstream skills depend on this file... If you finish without writing it, the entire pipeline fails silently" (`examples/skills/triage/reproduce.md`). Blog: subagents "pass information forward sequentially by compiling their discoveries into a report.md file," "ensuring complete transparency so that anyone could easily audit the agent's sequential reasoning."
- The pipeline returns a structured `TriageResult` (`completedStage`, `reproducible`, `skipped`, `verdict`, `diagnosisConfidence`, `fixed`, `commitMessage`) validated with valibot.
- Before the pipeline, the handler creates branch `triagebot/fix-<issue-number>`. After it, if `git diff main --stat` shows changes, the action commits (outside the agent session) and pushes the branch with the write token.
- **Preview release**: `publishPreviewRelease` diffs changed files, extracts changed `packages/(integrations/)?*` dirs, and runs `pnpm dlx pkg-pr-new publish` on them; install URLs (`https://pkg.pr.new/astro@abc1234`) go into the issue comment. Blog: it "spins up a preview release with pkg.pr.new and posts everything back to the issue: a summary of what it found, the full logs, and instructions for installing the preview." This step is hardcoded to a pnpm `packages/` monorepo — it is Astro/JS-specific, and repos that can't publish previews are told to set `auto-pr-on-fix: true` instead.
- **State machine** (README, encoded in `src/router.ts` + `src/labels.ts`): every issue carries exactly one triage label. `triage: needs triage` → one of `not actionable` / `needs reproduction` / `skipped` / `unable to reproduce` / `unable to fix` / `failed` / `fix pending`. `fix pending` → `fix verified` (reporter confirms; PR created) or `fix rejected`. Six labels are **re-triageable**: a new comment triggers an LLM evaluation ("is there new actionable information?") and possibly a re-run. `failed` is retried at most **3** times (`MAX_TRIAGE_FAILURES = 3`, tracked via an HTML comment marker in failure comments). Terminal: `fix verified`, `not actionable`, `skipped`.
- Two models: `triage-model` (default `anthropic/claude-opus-4-6`) runs the pipeline; a cheaper `verification-model` (default `anthropic/claude-sonnet-4-6`) does re-triage evaluation and fix-confirmation classification.

## C. Sandbox, isolation, and untrusted-input posture

This is the section eta-factory cares about, and it is the weakest part of the system.

- **Execution substrate**: a stock GitHub Actions `ubuntu-latest` runner. The Flue agent is constructed with `sandbox: local({env: {...}})` (`src/handlers/triage.ts:395`, `verify-fix.ts:47`, `retriage.ts:30`) — the agent's shell commands run directly on the runner. The only isolation boundary is the runner VM's own ephemerality. Flue's `createDefaultEnv` in `src/flue.ts` builds its Bash tool with `network: { dangerouslyAllowFullInternetAccess: true }` — the flag name implies Flue *can* restrict egress, but triagebot doesn't.
- **The word "isolated" in the blog means context isolation, not security isolation.** The stated purpose of per-phase subagents is defeating LLM solution-forcing bias and making reasoning auditable. No blog or README sentence discusses containment of malicious repro code.
- **Untrusted code execution is inherent and unmitigated beyond the runner**: the reproduce skill clones and runs the *reporter's* reproduction repository on the runner, with full internet access and the read token in env.
- **Prompt injection: NOT STATED anywhere.** `grep -c -i injection` over the fetched blog HTML: 0. Nothing in the repo README either. Untrusted issue title, body, and *all comments* are interpolated raw into the verification and retriage prompts (`verify-fix.ts`, `retriage.ts` build prompts with `${issueDetails.body}` and every `c.body`). The triage pipeline receives `issueDetails` as skill args the same way.
- **What IS engineered is the token split**, and it's genuinely good:
  - The agent session env is an allowlist: `GH_TOKEN` = **read token** plus a handful of `GITHUB_*` context vars only (`triage.ts`); verify/retriage sessions get only `GH_TOKEN` = read token.
  - The **write token never enters any agent session**. `src/github.ts`: `gitCommit` "Runs outside the sandbox and passes the commit message as an argv argument (never a shell string), so backticks, parentheses, quotes, and newlines in an LLM-authored message can't be interpreted by the shell"; `gitPush` "Runs outside any sandbox so the write token is never exposed to the LLM agent." Comments, labels, and PR creation are direct `fetch` calls to the GitHub API from action code, not agent tools.
  - The recommended workflow is `permissions: {}` at the top, job-level `contents: read` + `issues: read` (+ `id-token: write` in the README example), `actions/checkout` with `persist-credentials: false`, per-issue `concurrency` without cancel-in-progress, and a 60-minute timeout (the dogfood workflow uses 30). Write authority comes solely from a separately provisioned bot secret (`BOT_GITHUB_TOKEN`; Astro's dogfood uses `FREDKBOT_GITHUB_TOKEN`).
- **Residual injection→write path**: agent-authored *content* still flows to write actions — the LLM's commit message, the triage comment generated from findings (`handlers/comment.ts`), and the PR title/body (`generatePRContent`) are all produced by a model that has read hostile text, then posted/pushed by trusted code. The argv-hardening stops shell interpretation, not social-engineering payloads in comment/PR bodies. NOT STATED that anyone reviewed this surface.

## D. The "~85% issue reduction" claim, precisely

- Verbatim from the post body: "It wasn't an instant success. But through a lot of iteration, we've used it to bring our open issues down **from over 200 to about 30**" and "we expect to hit zero sometime in the next month," which would be "the first time this repository has seen zero open issues in its 5+ year history."
- The **"85%"** figure appears in the page's *description/meta text*: "By replacing manual issue verification with isolated AI subagents running in GitHub Actions, the Astro maintainers reduced open issue count by 85%." (200→30 is 85%.)
- **What was measured**: open issue count on the `withastro/astro` monorepo (repo created 2021-03-15, so "5+ year history" checks out). **By whom**: the Astro maintainers themselves — self-reported, no third-party audit. **Period**: "For the past several months"; one extraction adds work started "at the start of the year" (that phrase is from a secondary WebFetch pass — UNVERIFIED verbatim). The org runs a public daily tracker, `withastro/astro-issues` → issues.astro.build, collecting per-day issue JSON since 2023; the underlying series is therefore independently checkable, though this research did not chart it.
- **Independent corroboration of the trajectory**: on 2026-08-31 the GitHub search API returned **8 open issues** of `type:issue` on withastro/astro. The trend the post claims is real.
- **Confounds the post does not address**: (1) Astro simultaneously runs `issue-state-issue.yml`, a cron that **auto-closes any issue lacking a minimal reproduction after 3 days of inactivity** ("closed... because it has been inactive for more than 3 days and it does not have a minimal reproduction"), plus `issue-needs-repro.yml` which threatens that closure the moment the `triage: needs reproduction` label lands — and that label is exactly what triagebot applies to repro-less issues. So the open-count reduction is triagebot + aggressive auto-close + human work, unseparated. (2) No denominator of *fixed* vs *closed-unfixed* issues is given anywhere. (3) No cost, run-time, or Actions-minutes figures at all (NOT STATED).

## E. License and what forking actually requires

- **`withastro/triagebot-action` has no license.** No LICENSE file in the tree, no `license` field in `package.json` (which still says `"version": "0.1.0"`), and `GET /repos/withastro/triagebot-action/license` returns 404. The blog says "The code is open. Fork it, strip it down, or just borrow the parts that fit your project" and the README calls it "a working reference you can read, learn from, and adapt" — a public invitation, but not a copyright license. Until a LICENSE lands, a fork for company use rests on that blog sentence. For ETA: treat it as a **design reference to reimplement**, not code to vendor.
- **Flue is properly licensed**: `withastro/flue` carries an Apache-2.0 LICENSE; npm `@flue/runtime` declares Apache-2.0. triagebot pins `@flue/runtime@^0.8.1`; npm latest is already 2.0.3, so the action trails its own framework by two majors.
- **Versioning trap**: both the blog and the README say `uses: withastro/triagebot-action@v1`. **No `v1` tag exists.** Actual tags: v0.1.0 through v0.4.0. A fork must pin v0.4.0 or a SHA; `@v1` fails at workflow resolution.
- **To run it on a repo you need**:
  - `read-token` — the default `GITHUB_TOKEN` suffices.
  - `write-token` — a GitHub App token or PAT with issues+contents write (the bot identity that comments, pushes `triagebot/fix-*` branches, creates PRs, swaps labels).
  - One model credential: `anthropic-api-key` (defaults `anthropic/claude-opus-4-6` / `claude-sonnet-4-6`), or `openai-api-key`, or `cloudflare-api-key` + `cloudflare-account-id` for Workers AI models (the blog's config: `cloudflare-workers-ai/@cf/moonshotai/kimi-k2.7-code` triage, `.../kimi-k2.6` verification). README: "Workers AI is called over its OpenAI-compatible REST endpoint, so the action still runs on the standard GitHub Actions runner — no Worker deployment is required." No self-hosted runners, no GPUs.
  - **Project-owned skill files** — the real adaptation cost: `SKILL.md` + `reproduce.md` / `diagnose.md` / `verify.md` / `fix.md` teaching the agent how to build, run, and debug *your* codebase (starter templates in `examples/skills/triage/` with `<!-- CUSTOMIZE -->` markers; production example at `withastro/astro/.agents/skills/triage`). Optional `pr-skill`, `build-command`, `bot-logins`, and full label-name customization.
  - Preview releases require the pnpm/`packages/` layout and pkg.pr.new; anything else (a PHP app like tourbot) sets `auto-pr-on-fix: true` and loses the reporter-confirmation stage.
- **Where Astro actually runs it is not publicly visible.** `withastro/astro` on its default branch has the skills directory but **no workflow referencing triagebot-action** (full workflow listing checked; code search for "triagebot" in the repo: zero hits; no commit history for a `.github/workflows/triage.yml`). The action dogfoods on its own repo (`triagebot-action/.github/workflows/triage.yml`, 30-min timeout, Anthropic key, FREDKBOT write token). Either Astro's production trigger lives in a private repo/external dispatcher or it was removed from main — NOT VERIFIED.

## F. How human review is gated

- **No maintainer gate anywhere in the automated path.** The bot labels, comments, pushes branches, and publishes installable preview packages with zero human approval.
- **The gate is the reporter**: after a fix, the issue sits at `triage: fix pending` with a comment asking the reporter to install the preview. Blog: "The original reporter can then try the patch against their own project, and if they confirm it works, the automation opens a pull request linked to the issue." The `verification-model` classifies the confirming comment; positive → `fix verified` + PR (non-draft — `createPullRequest` sends no `draft` flag).
- **Authorization gap in that gate**: `verify-fix.ts` takes the *latest non-bot comment* on the issue — it does not check the commenter is the original reporter or a maintainer. The prompt shows the model each commenter's `authorAssociation`, but nothing in code enforces it. Any GitHub account commenting "yes this fixes it" can advance the FSM to PR creation. (`auto-pr-on-fix: true` removes even the reporter gate.)
- The only remaining human control is ordinary PR review before merge, plus branch cleanup on issue close (`handlers/cleanup.ts`). Maintainers can also intervene by editing labels, since the FSM state *is* the labels.
- Blog framing of the payoff: maintainers didn't stop talking to users — "If anything, we talk to users more now, just in more useful places" (Discord, RFCs). Failed fixes are treated as signal: "When an agent fails to identify a correct solution, we interpret that failure as an indicator of an underlying architectural or documentation issue" — bucketed as opaque abstractions, missing documentation, insufficient testing (extraction; exact bucket wording UNVERIFIED verbatim).

## What the sources do not answer

1. **Prompt-injection defense: none stated, and code shows raw interpolation.** Whether Astro ever saw an injection attempt in ~8 months of hostile input is unrecorded.
2. **Cost and run time per issue** — no dollars, tokens, or Actions-minutes anywhere.
3. **The fixed-vs-auto-closed split behind 200→30.** How many of the ~170 issues were resolved by an agent fix versus the 3-day no-repro auto-closer versus humans.
4. **Where Astro's production triage workflow actually runs** (not on `withastro/astro` main).
5. **Why there is no license on triagebot-action** — oversight or intent; whether one is coming.
6. **False-positive rate**: how often `fix pending` fixes were rejected, or `not actionable`/`unable to reproduce` labels were wrong. `fix rejected` exists as a state; no counts published.
7. **Why `@v1` is documented when only v0.x tags exist** — presumably a floating tag was planned; nothing says so.
8. **Whether the reporter-confirmation classifier restricting to the actual reporter was considered** — the code accepts any non-bot commenter.
9. **Model quality dependence**: the blog config shows Kimi on Workers AI, defaults are Claude Opus/Sonnet; no comparison of triage success by model is published.
10. **"Start of the year" timeline** — the launch date and the shape of the reduction curve (steady vs cliff at auto-close adoption) is answerable from `withastro/astro-issues` daily JSON but not answered in any prose source.

## Fit against eta-factory

**Maps cleanly onto the existing design:**

- **Label-driven FSM intake.** Triagebot's one-label-per-issue state machine with re-triageable vs terminal states and a 3-strike failure cap is a more complete version of eta-factory's `ready-for-agent` intake. The specific state set (needs-repro / unable-to-reproduce / unable-to-fix / skipped / failed / fix-pending / fix-verified) is directly reusable vocabulary for tourbot repos, and the "new comment → LLM decides if there's new actionable info → re-run" loop is a cheap, bounded re-entry mechanism eta-factory lacks.
- **The token split is eta-factory's publisher pattern, independently reinvented.** Agent session holds read-only credentials; all writes (commit argv-safe, push, comment, PR) happen in trusted code outside the session. That matches eta-factory's secret-free worker + separate publisher (draft-PR path in `factory-supervisor`'s publisher) and validates the shape.
- **Staged pipeline with structured handoff.** reproduce→diagnose→verify→fix with a mandatory `report.md` per stage and a typed `TriageResult` at the end maps onto eta-factory's pipeline stages and evidence artifacts; the "verify before fix, to fight solution-forcing bias" stage is worth stealing outright.
- **Skills as project-owned config vs action-owned engine.** The engine/skill split (FSM+API in the action, per-repo `reproduce.md`/`diagnose.md`/etc. in the target repo) is a clean answer to "how does one factory serve many tourbot repos."
- **The reporter-confirmation outer loop** (fix → preview → reporter tests → PR) is a genuinely new idea for eta-factory: a verification signal from outside the factory before a PR exists. No pkg.pr.new equivalent exists for PHP tourbot, but "push branch + deploy to a preview slot + ask reporter/staff to confirm" is ETA-shaped (preview slots already exist).

**Conflicts — where triagebot's choices are inadmissible here:**

- **Isolation substrate.** Triagebot's boundary is a shared-nothing GitHub Actions VM with `dangerouslyAllowFullInternetAccess: true`, executing reporter-supplied repro code. eta-factory's Firecracker workers with the two-IP egress allowlist and probed containment contract are strictly stronger, and hostile intake is precisely why. Adopt the pipeline shape, keep the substrate.
- **No prompt-injection posture.** Raw interpolation of issue bodies and *all comments* into prompts, plus LLM-authored comment/PR bodies posted by the write token, is the exact threat model eta-factory exists to handle. Any port must treat issue text as data (delimited, provenance-tagged) and keep the publisher's output surface reviewed.
- **The confirmation gate is spoofable.** "Latest non-bot comment" advancing the FSM to PR creation means any account gates the factory's output. eta-factory's equivalent gate must check identity (reporter or allowlisted staff), not just classify sentiment.
- **Run budget.** Triagebot allows 30-60 minutes per issue; eta-factory's implement seat has a 300-second hard kill (the same tension already flagged against Ralph's 30-45 min loops in the 2026-08-24 research). A triage pipeline of this depth does not fit one eta-factory turn; it would need to be staged runs (one per pipeline phase, report.md as the inter-run artifact — which the phase structure conveniently already supports).
- **License.** No-license triagebot code cannot be vendored into ETA repos; Flue (Apache-2.0) can. Reimplement the ~1,500 lines of FSM/handler logic; the design is fully documented above and in the repo's README.
- **Non-goal drift**: `auto-pr-on-fix: true` (the mode a PHP repo would need) silently deletes the only human-adjacent gate in the system. If eta-factory adopts the FSM, the draft-PR output must remain the gate, not become the bypass.

## Sources

**Web, fetched 2026-08-31:**

- https://blog.cloudflare.com/astro-issue-triage/ — the owning source. Matthew Phillips, published 2026-08-04. Source for the four-phase pipeline description, "isolated subagent" rationale, report.md handoff, pkg.pr.new preview flow, "over 200 to about 30," "85%" (meta-description), "zero... next month," "5+ year history," "past several months," Flue/flueframework.com, "The code is open. Fork it," and the label lifecycle ("triage needed" → "fix verified"). Fetched twice via WebFetch (summary + verbatim-quote passes) and once raw via curl; raw HTML grep confirmed **zero** occurrences of "injection." Quotes from WebFetch extraction passes are marked; the page is client-rendered so verbatim confidence is extraction-level, not eyeball-level.
- https://registry.npmjs.org/@flue/runtime — license Apache-2.0, repo withastro/flue, homepage flueframework.com, latest 2.0.3, deps on `@earendil-works/pi-agent-core`/`pi-ai`.

**Repo, `withastro/triagebot-action` (cloned at main, 2026-08-31; tags v0.1.0–v0.4.0, no v1):**

- `README.md` — state machine diagram, label reference, setup workflow, token/provider matrix, inputs table, action-owned vs project-owned architecture, the dead `anthropics/flue` link.
- `action.yml` — full input schema, model defaults (`anthropic/claude-opus-4-6` / `claude-sonnet-4-6`), `node24` runtime.
- `src/flue.ts` — `InMemoryFs` + `Bash({network:{dangerouslyAllowFullInternetAccess:true}})` default env.
- `src/handlers/triage.ts` — `sandbox: local()` env allowlist (read token only), branch creation, pipeline invocation per-step, `publishPreviewRelease` (pnpm/`packages/` hardcoding), `MAX_TRIAGE_FAILURES = 3`, `TriageResult` shape.
- `src/handlers/verify-fix.ts`, `src/handlers/retriage.ts` — raw interpolation of issue body/comments into prompts; latest-non-bot-comment selection; no reporter identity enforcement.
- `src/github.ts` — `gitCommit` argv-hardening comment, `gitPush` "write token never exposed to the LLM agent," `createPullRequest` (no draft flag).
- `.agents/skills/triage/SKILL.md`, `examples/skills/triage/reproduce.md` — subagent-per-step, mandatory report.md, early-exit taxonomy, "bail out after 2 attempts" infrastructure rule.
- `.github/workflows/triage.yml` — the dogfood deployment: `permissions:{}`, contents:read+issues:read, 30-min timeout, `persist-credentials: false`, per-issue concurrency, `FREDKBOT_GITHUB_TOKEN` + `CI_ANTHROPIC_API_KEY`.
- `package.json` (no license field, version 0.1.0), `pnpm-lock.yaml` (`@flue/runtime@0.8.1` → pi-agent deps); `gh api repos/withastro/triagebot-action` (licenseInfo null) and `.../license` (404).

**GitHub API, 2026-08-31:**

- `repos/withastro/astro` — created 2021-03-15; workflow listing (no triage workflow; `issue-state-issue.yml` = 3-day no-repro auto-closer verbatim, `issue-needs-repro.yml` = closure-warning commenter); `.agents/skills/triage/SKILL.md` (production skills exist); `search/issues type:issue state:open` → **8**; code search for "triagebot" in-repo → 0; no commit history for `.github/workflows/triage.yml`.
- `repos/withastro/flue` — LICENSE file (Apache-2.0 full text), created 2026-02-07, "The sandbox agent framework."
- `repos/withastro/astro-issues` — the metric instrument: daily issue-count JSON since 2023-10, rendered at issues.astro.build.
- `search/code org:withastro triagebot-action` — hits only in `withastro/astro-issues` (skills + package refs), supporting the "production wiring not public" finding.

**Local:** `lab/single-user-factory/research/2026-08-24-ralph-loop-and-isolation.md` — eta-factory substrate facts reused in the fit section (Firecracker, two-IP egress allowlist, secret-free host, 300 s ceiling, draft-PR publisher).
