# examples

What a configured instance looks like. These are Educational Travel
Adventures' own values, which is the point: the product's code carries no
default that names a company, a host, a person or a repository, so the only
honest way to show what a filled-in instance looks like is to show a real one.

**Nothing here is a secret.** These are addresses, paths and account names.
The GitHub token lives on the box in the file a target's `token_file` names,
the model credential is minted on the box by a wizard in `wizards/`, and the
mail relay's credential belongs to whatever `SELECTOR_NOTIFY_COMMAND` and
`WINDOW_MAIL_COMMAND` run.
`selector/tests/test_configuration.py` reads this directory to grade the
shipping tree against it, so a value that appears here must not appear as a
default in code.

| file | what it is |
| --- | --- |
| `tracewake.env` | the instance: the Journal, the box, the window, the mail surface, the guardrail |
| `targets.toml` | the targets: one stanza per repository worked |
| `inventory.yml` | the box play's variables - who the Run's commits are by, and which repository its token is for |
| `droplet.tf` | the box as a cloud resource, with account identifiers left as placeholders |

The wizards in `wizards/` read two more from the environment rather than from
a file, because they are run by hand: `LOOP_HOST`, the box they walk you
through logging in to, and `LOOP_TOKEN_NEGATIVE_PROBE_REPO`, a repository the
box's token must NOT be able to see. The second is required rather than
optional: it is the probe that proves the blast radius stops at one
repository (ADR 0009), and a probe that quietly did not run would leave the
walkthrough printing a tick beside the positive one.

Copy them, change every value, and read `selector/README.md` for what each
one does.
