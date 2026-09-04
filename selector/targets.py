"""What an instance is configured with: the env file, and `targets.toml`.

The Loop and the Selector were built inside one company's repository, and
every fact about that company was a default in code - the task repository,
the operator whose label counts as a Handover, the box's hostname, the
checkout a Run works in. A second operator could not run any of it without
editing it, and a second target needed a second controller.

So the facts move out. **The instance is an env file** (`tracewake.env`:
where the box is, where the Journal is, how mail is sent) and **the targets
are a TOML file** - one stanza per repository the instance works, carrying
everything that differs between them:

    [[target]]
    repo           = "acme/widgets"      # owner/name on the tracker
    work_repo      = "/var/lib/tracewake/work/widgets"
    box_repo       = "/home/loop/widgets"
    token_file     = "/home/loop/.config/loop/widgets-token"
    guest_template = "widgets-php:1"
    labeler_allowlist = ["some-operator"]
    review_cap     = 20                  # optional
    landing        = "propose"           # optional

    [target.labels]                      # optional; these are the defaults
    ready      = "ready-for-agent"
    needs_info = "needs-info"
    review     = "awaiting-review"
    human      = "ready-for-human"

Two rules hold this together, and both are acceptance criteria of issue #3:

**No default names a company, a host, a person or a repository.** A value
like that has exactly one right answer per instance and no right answer in
the product, so the code refuses rather than guesses - `missing()` raises,
naming the variable and saying what it is. `tests/test_configuration.py`
enforces the rule mechanically against every default in the shipping tree,
so the next default of that shape fails the suite rather than shipping.

**A required value that is absent stops the cycle at preflight.** Before the
tracker is read, before the box is reached: an instance that is half
configured must not do half a cycle. `Target.load` and `Instance.from_env`
raise `NotConfigured`, and `cycle.py` turns that into a journaled
`cycle.failed` and a non-zero exit.

`examples/` carries ETA's own values for both files, with no secrets in
them - a reader who wants to see what a filled-in instance looks like reads
those rather than reading defaults out of the code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the older runtime
    import tomli as tomllib


# Where the targets file is. The one variable that cannot itself live in a
# file, so it is the instance's single entry point.
TARGETS_FILE_VAR = "TRACEWAKE_TARGETS_FILE"

# The four labels a target's lifecycle runs on. Defaulted, unlike everything
# else here, because a label name is a fact about the *workflow* rather than
# about a company: an instance that renames them says so, and an instance
# that does not gets the vocabulary `docs/agents/triage-labels.md` describes.
DEFAULT_LABELS = {
    "ready": "ready-for-agent",
    "needs_info": "needs-info",
    "review": "awaiting-review",
    "human": "ready-for-human",
}

# How many of a target's Proposals may sit in the review column before the
# Selector stops dispatching. The spend cap it replaces was four Runs a day;
# this one follows the operator's review capacity instead, which is the
# constraint that actually binds. Enforcement is issue #8's; what is settled
# here is that the number is per target and configured, not global and coded.
DEFAULT_REVIEW_CAP = 20

# What a finished Run does with its work. `propose` is the only mode that
# exists: a Run opens a Proposal and a human merges it. `land` is reserved
# and deliberately refused here rather than silently accepted, so that an
# instance cannot declare a mode that nothing implements.
LANDING_MODES = ("propose",)

_REQUIRED_TARGET_KEYS = (
    "repo",
    "work_repo",
    "box_repo",
    "token_file",
    "guest_template",
    "labeler_allowlist",
)

_KNOWN_TARGET_KEYS = _REQUIRED_TARGET_KEYS + (
    "labels", "review_cap", "landing",
)


# The instance values that name a host or a repository and therefore have no
# default anywhere in the code. Each maps to what it is, because the refusal
# says so: "SELECTOR_BOX_HOST is not set" tells an operator which line to add
# and not what the line is for.
#
# The scripts that read them refuse too, and that is not redundancy: this is
# the PREFLIGHT, which stops the cycle before it has read the tracker, and
# theirs is the backstop for a script an operator runs by hand. Without the
# preflight the cycle reads the queue, seeds a branch and pushes it before the
# box command finally refuses - which is exactly the half a cycle this ticket
# exists to prevent.
#
# Deliberately not here: SELECTOR_NOTIFY_COMMAND and SELECTOR_LOOP_URL. They
# are the notifier's, it is a separate process with its own start, and it
# refuses on them by the same rule (notifier.py). A cycle that would not run
# because mail is unconfigured would be a queue stopped by a mailer.
REQUIRED_INSTANCE_VARS = {
    "SELECTOR_BOX_HOST": "where this instance's box is",
    "SELECTOR_PROTECTED_REPO": "the repository holding the guardrail's rules",
    "SELECTOR_PROTECTED_REF": "the ref the executed paths are deployed from",
}


class NotConfigured(Exception):
    """A required instance value is absent, or one that is present is not
    usable. Raised at preflight - before the tracker is read - and always
    naming the value, because "the cycle failed" without the name is a
    message that costs an operator the same half hour every time."""


def missing(name: str, what: str) -> NoReturn:
    """Stop, naming the value.

    Called from the right-hand side of an `or`, where a value would go;
    `NoReturn` is what tells a reader no value ever comes back from it.
    """
    raise NotConfigured(
        f"{name} is not set, and there is no default for it: {what} is a fact"
        " about this instance, not about Tracewake."
    )


@dataclass(frozen=True)
class Labels:
    """The four labels one target's lifecycle runs on."""

    ready: str
    needs_info: str
    review: str
    human: str


@dataclass(frozen=True)
class Target:
    """One repository this instance works, and everything that is per-target.

    A second target is this stanza a second time - not a second controller,
    a second timer or a second checkout of the product.
    """

    repo: str
    labels: Labels
    labeler_allowlist: tuple[str, ...]
    work_repo: Path
    box_repo: str
    token_file: str
    guest_template: str
    landing: str
    review_cap: int

    @classmethod
    def load(cls, stanza: dict[str, Any], *, where: str, index: int) -> "Target":
        """One `[[target]]` stanza, validated.

        `where` and `index` are in every message this raises: a file with
        four stanzas in it needs to say which one is short of a value, and
        "the third target in /etc/tracewake/targets.toml" is the only form
        of that an operator can act on without counting.
        """
        name = f"target {index} in {where}"
        unknown = sorted(set(stanza) - set(_KNOWN_TARGET_KEYS))
        if unknown:
            # Loudly, because the failure a typo produces otherwise is a
            # setting that reads as configured and is not: `review-cap`
            # spelled with a hyphen would leave the default in force and
            # nothing would ever say so.
            raise NotConfigured(
                f"{name} declares {', '.join(unknown)}, which Tracewake does"
                f" not read. Known keys: {', '.join(_KNOWN_TARGET_KEYS)}."
            )
        for key in _REQUIRED_TARGET_KEYS:
            if not stanza.get(key):
                raise NotConfigured(
                    f"{name} has no `{key}`, and there is no default for it:"
                    " it is a fact about this target, not about Tracewake."
                )

        allowlist = stanza["labeler_allowlist"]
        # A list of non-empty strings, and nothing else. A bare string is the
        # spelling somebody reaches for first and is the dangerous one: TOML
        # would accept it, and iterating it would put every CHARACTER of the
        # account name in the allowlist.
        if not isinstance(allowlist, (list, tuple)) or not all(
            isinstance(entry, str) and entry.strip() for entry in allowlist
        ):
            raise NotConfigured(
                f"{name} has a `labeler_allowlist` that is not a list of"
                " account names. The Handover is the label, so who may apply"
                " it is the whole of who is trusted."
            )

        labels = dict(DEFAULT_LABELS)
        declared = stanza.get("labels") or {}
        unknown_labels = sorted(set(declared) - set(DEFAULT_LABELS))
        if unknown_labels:
            raise NotConfigured(
                f"{name} declares label(s) {', '.join(unknown_labels)}, which"
                f" Tracewake does not read. Known: {', '.join(DEFAULT_LABELS)}."
            )
        for key, value in declared.items():
            if not (isinstance(value, str) and value.strip()):
                raise NotConfigured(
                    f"{name} has an empty `labels.{key}`. A label the tracker"
                    " cannot carry is not a rename, it is a queue with no name."
                )
            labels[key] = value.strip()

        landing = str(stanza.get("landing") or LANDING_MODES[0])
        if landing not in LANDING_MODES:
            raise NotConfigured(
                f"{name} declares landing = {landing!r}. Tracewake proposes;"
                f" the modes it has are {', '.join(LANDING_MODES)}."
            )

        try:
            review_cap = int(stanza.get("review_cap", DEFAULT_REVIEW_CAP))
        except (TypeError, ValueError):
            review_cap = -1
        if review_cap < 1:
            raise NotConfigured(
                f"{name} has a `review_cap` that is not a positive whole"
                " number. A cap of zero is a paused instance, and pausing has"
                " its own control."
            )

        return cls(
            repo=str(stanza["repo"]),
            labels=Labels(**labels),
            labeler_allowlist=tuple(entry.strip() for entry in allowlist),
            work_repo=Path(str(stanza["work_repo"])),
            box_repo=str(stanza["box_repo"]),
            token_file=str(stanza["token_file"]),
            guest_template=str(stanza["guest_template"]),
            landing=landing,
            review_cap=review_cap,
        )

    def environ(self) -> dict[str, str]:
        """The per-target values the substitutable commands read.

        The commands are scripts (ADR 0004) and the environment is how a
        Python caller reaches them, so a target's box checkout, repository
        token and guest image arrive the same way the instance's own values
        do. Overlaid on the process environment at the call site rather than
        exported into it, so that two targets in one drain cannot leak each
        other's checkout.
        """
        return {
            "SELECTOR_TASK_REPO": self.repo,
            "SELECTOR_BOX_REPO": self.box_repo,
            # Read on the box by the git credential helper and by the agent
            # adapter. Named with the Loop's prefix because the box is what
            # consumes them, and box-sources/ssh.sh carries them across.
            "LOOP_GITHUB_TOKEN_FILE": self.token_file,
            "LOOP_GUEST_TEMPLATE": self.guest_template,
        }


@dataclass(frozen=True)
class Instance:
    """The instance's own values: what is the same for every target.

    Held as a type rather than checked in passing, because "is this instance
    configured?" is a question the cycle asks at preflight and the window asks
    on every page, and two spellings of it would drift.
    """

    box_host: str
    protected_repo: str
    protected_ref: str

    @classmethod
    def from_env(cls) -> "Instance":
        """Read the instance, or refuse naming the first value that is absent.

        In declaration order, so an instance missing three values is told
        about them in the order it would fill them in rather than in whatever
        order a dict happened to iterate.
        """
        for name, what in REQUIRED_INSTANCE_VARS.items():
            if not os.environ.get(name):
                missing(name, what)
        return cls(
            box_host=os.environ["SELECTOR_BOX_HOST"],
            protected_repo=os.environ["SELECTOR_PROTECTED_REPO"],
            protected_ref=os.environ["SELECTOR_PROTECTED_REF"],
        )


def overlaid(overlay: dict[str, str] | None) -> dict[str, str] | None:
    """This process's environment with a target's values laid over it.

    The one spelling of the rule, because every outward command needs it and
    three hand-written copies of `{**os.environ, **overlay}` are three chances
    for one of them to pass the overlay alone - which would run the command
    with no PATH and no credentials, and look like the command being broken.

    `None` in, `None` out: `subprocess.run(env=None)` means "inherit", which
    is what a call site with no target to overlay wants.
    """
    return None if overlay is None else {**os.environ, **overlay}


def targets_file() -> Path:
    """Where the targets file is, or a refusal naming the variable."""
    return Path(
        os.environ.get(TARGETS_FILE_VAR)
        or missing(TARGETS_FILE_VAR, "the file declaring the targets to work")
    )


def load(path: Path | None = None) -> tuple[Target, ...]:
    """Every declared target, in the order the file declares them.

    Order is the file's, not sorted, because it is the order a drain works
    them in: which repository gets the box first when both have work is the
    operator's call, and a sort would take it away from them and hide that it
    had.
    """
    where = path or targets_file()
    try:
        raw = where.read_bytes()
    except OSError as exc:
        raise NotConfigured(f"{TARGETS_FILE_VAR} names {where}, which could not be read: {exc}") from exc
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise NotConfigured(f"{where} is not readable TOML: {exc}") from exc

    stanzas = document.get("target")
    if not stanzas:
        raise NotConfigured(
            f"{where} declares no `[[target]]`. An instance with no target is"
            " a controller with nothing to control."
        )
    if isinstance(stanzas, dict):
        # `[target]` rather than `[[target]]`: one stanza, written as a table.
        # Accepted rather than refused, because a single-target instance is
        # the common case and the two spellings mean the same thing to a
        # reader.
        stanzas = [stanzas]

    targets = tuple(
        Target.load(stanza, where=str(where), index=index)
        for index, stanza in enumerate(stanzas, start=1)
    )
    repos = [target.repo for target in targets]
    duplicated = sorted({repo for repo in repos if repos.count(repo) > 1})
    if duplicated:
        raise NotConfigured(
            f"{where} declares {', '.join(duplicated)} more than once. Two"
            " stanzas for one repository would be two review caps and two"
            " work checkouts for one queue."
        )
    return targets


def select(targets: tuple[Target, ...], repo: str | None) -> tuple[Target, ...]:
    """The targets a cycle is to work: all of them, or the one named.

    `--target` is how an operator works one repository by hand without
    stopping the timer for the other, and how the suite drives one target
    through a file that could hold several.
    """
    if repo is None:
        return targets
    for target in targets:
        if target.repo == repo:
            return (target,)
    raise NotConfigured(
        f"no target declares repo = {repo!r}. Declared: "
        + ", ".join(target.repo for target in targets)
    )
