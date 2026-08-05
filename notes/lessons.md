# Lessons for a single-user factory

*What building ETA's factory taught me about building a lightweight one. Drawn
from the construction of `eta-factory` — the heavy, company-scale version —
and written to say what to keep, what to drop, and why.*

---

## The reframe: velocity and safety are not a dial

The tempting model is a slider — buy safety with velocity, or the reverse. That
model is wrong, and believing it makes you keep apparatus you don't need out of
unease.

Most of what *looked* like a safety-for-velocity trade in the ETA build wasn't
one. The expensive subsystems — the isolation substrate, the provider-credential
lifecycle, the content-trust floor — were **calibrated to a specific threat
model**: an unattended, multi-user worker touching a production ERP, fed by a
hostile intake surface. Change the threat model and they don't make you *less
safe* — they stop guarding a danger that isn't present. That's not a point on a
tradeoff curve; it's the curve not applying.

The useful decomposition is that **"safety" is three independent axes**, and
each one alone tells you which subsystem to keep:

| Axis | Question | Drives | ETA | Single-user |
| --- | --- | --- | --- | --- |
| **Blast radius** | what happens when it goes wrong | prevention apparatus | prod ERP for a company | I notice and fix |
| **Reversibility** | can I undo a bad outcome | rollback / backup machinery | hard to undo prod damage | `git revert`, or re-run |
| **Trust model** | who feeds it input, can the executor be trusted | isolation / credential apparatus | hostile intake, untrusted worker | I feed it my own work |

For the single-user factory all three collapse toward zero, and each collapse
independently deletes a subsystem. That's the whole cost saving — not accepting
more risk, but building for a smaller, honest threat model.

---

## The root fact: content-trust is authorship, not authentication

The single-user property is the *cause* of the lightness, so name and design
around it.

The heavy factory's content-trust floor exists because **trusted people relay
untrusted content**. At a company, even a five-person allowlist forwards
customer emails, ERP screenshots, third-party source — material the submitter
didn't author. The floor defends against injection carried *through* a trusted
submitter, not against a hostile submitter. Narrowing *who* submits barely moves
it.

For a single-user factory the floor nearly vanishes — **not because there are
fewer submitters, but because I author what I submit.** No forwarded hostile
content, no adversarial OCR. Content-trust is a function of authorship, and I am
the author. That is a categorically better reason to drop the floor than "fewer
users," and it's the reason that generalizes.

*(Correction worth recording: even at ETA the worker never touched the
production database. Verification runs read-only against a redacted preview
sandbox over HTTP; the evidence job holds no DB credential at all. The in-run
blast radius was already small by design — the isolation architecture did the
containing, not the content floor. So the content floor was always more
negotiable than it looked, at any scale.)*

---

## What actually cost time at ETA, ranked

The ranking should drive what you refuse to build.

1. **The trust-model apparatus — by far the largest.** Isolation substrate,
   the six-requirement provider-credential lifecycle, the content-trust floor.
   Entire sessions, and the current long pole. Almost all stakes-driven.
2. **First-execution defects from horizontal slicing.** Eleven of fourteen
   defects in the cutover session were in code that had never run end to end,
   because the plan built by layer and nothing integrated until the end.
3. **Live-only + silent-success defects.** Code that reads correctly and
   behaves wrong — found only by running against real state, at the most
   expensive point in the pipeline. "Reports success without acting" recurred
   four separate times before it became a rule.
4. **Reproducibility infrastructure.** GitOps-everything: OpenTofu, Ansible
   desired-state, encrypted state, the migration ledger. This is what made the
   cutover both *possible* and *expensive*.
5. **Governance ceremony.** 114 rulings, each a PR with propagation and a
   metrics-regeneration gate.

Items 1, 4, and 5 — three of the largest — are stakes-driven, not
capability-driven. They don't make the factory better at resolving requests.
They make it safe to run unattended, against production, at a company.

---

## Keep regardless of scale — these are velocity properties, and free

Three lessons cost nothing in safety and pay velocity at every scale. They are
not on the tradeoff axis. Keep them.

- **Vertical slices, run for real from day one.** One request, resolved end to
  end — intake → triage → implement → verify → output — however crude, before
  deepening anything. This was pure loss in the ETA build: it deferred first
  execution of nearly everything to one session and produced most of the
  defects. A drift that costs five minutes to catch early costs a full
  provision-restore cycle to catch late.
- **An honest failure signal.** Exit nonzero when anything failed — including
  teardown and cleanup — and say what. This gets *more* important as you strip
  everything else away: with no adversarial reviewer, the run itself is the only
  thing that can tell you it didn't work. It is both a velocity property (you
  find breakage now) and a safety property (nothing proceeds silently) — which
  is the tell that "velocity vs safety" was never a clean opposition.
- **Mutation-test your own checks.** A test that passes when you break the thing
  it guards is worse than no test — it reports coverage that doesn't exist. The
  highest-value review technique that emerged in the ETA build was mutating the
  source and confirming the check fails. It's ~zero ceremony as a personal
  habit.

---

## Drop or defer at single-user scale

Each of these is deleted or shrunk by one of the three collapsing axes.

- **Collapse the trust model → delete the isolation stack.** No
  provider-credential lifecycle, no capability drives, no secret-free ephemeral
  worker. Put the API key in the environment and run the agent directly. This
  deletes the ETA factory's current long pole outright.
- **Defer reproducibility.** "It runs on my machine / one box I set up by hand"
  is fine until it isn't. Add Ansible the day you rebuild a *second* time — not
  before. You lose rebuild-from-declaration and gain enormous velocity.
- **Lighter governance.** Keep a `decisions.md` you append to — the *value* (why
  you chose things) without the *ceremony* (PR + propagate + regenerate +
  review). Append-only ledgers with stable-citation discipline are worth it when
  a decision log is multi-author and cross-referenced across a repo. When you're
  the only one citing it, `git`-plus-edit-in-place is fine.
- **Python over Rust.** The ETA choice of Rust was justified by shipping to
  production, security surface, and correctness under adversarial conditions.
  Every one of those justifications weakens at single-user scale. It isn't just
  "Python has a richer AI-automation ecosystem" — it's that the *reasons for
  Rust evaporate* when the worst case is "my script did something dumb and I
  noticed." Iteration speed compounds against exactly the build-cycle time that
  is the real bottleneck.

---

## The one over-correction to avoid

The failure mode of "minimal factory from a maximal one" is deleting a guardrail
*and* the incident-detection that would tell you that you needed it back.

Strip the apparatus — but keep one cheap thing: the honest, loud failure signal
above. At single-user scale you have no adversarial reviewer to catch a silent
failure, so the run itself has to be honest about whether it worked. That is the
one piece of the safety story that gets *more* important as everything else is
stripped away, because you become the only reviewer.

---

## The through-line

Name it by the constraint that makes everything true: **single-user.** Because I
author my inputs, content-trust collapses. Because the blast radius is me, the
prevention apparatus collapses. Because I rarely rebuild, the reproducibility
apparatus collapses. Build for that threat model honestly, keep the three free
velocity wins, and don't mistake the resulting lightness for recklessness — it's
correct sizing for a danger that mostly isn't there.
