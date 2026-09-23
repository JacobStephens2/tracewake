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

INSTALL.md shows the Single-Host shape of both files. The product ships no
filled-in Instance; a reader who wants values writes them on the Host.
"""
from __future__ import annotations

import json
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
    "SELECTOR_SEARCH_OWNER": (
        "the account whose repositories this instance searches for a "
        "Handover label on unenrolled Targets"
    ),
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
class GuardrailTree:
    """One tree the Guardrail watches: its repository, the ref deployed from it,
    the working copy on the controller, and the paths file or list of paths."""

    repo: str
    ref: str
    tree: Path
    paths: str | Path

    def environ(self) -> dict[str, str]:
        return {
            "SELECTOR_PROTECTED_REPO": self.repo,
            "SELECTOR_PROTECTED_REF": self.ref,
            "SELECTOR_PROTECTED_TREE": str(self.tree),
            "SELECTOR_PROTECTED_PATHS": str(self.paths),
        }


def load_guardrail_trees(environ: dict[str, str] | None = None) -> tuple[GuardrailTree, ...]:
    """Parse every declared guardrail tree from environment.

    Reads `SELECTOR_GUARDRAIL_TREES` if present (as JSON or semicolon/line-separated
    `repo:ref:tree:paths`), or falls back to `SELECTOR_PROTECTED_REPO` /
    `SELECTOR_PROTECTED_REF` / `SELECTOR_PROTECTED_TREE` / `SELECTOR_PROTECTED_PATHS`.
    """
    env = os.environ.get if environ is None else environ.get
    raw = env("SELECTOR_GUARDRAIL_TREES")
    if raw and raw.strip():
        raw = raw.strip()
        parsed: list[GuardrailTree] = []
        if raw.startswith("["):
            try:
                data = json.loads(raw)
            except Exception as exc:
                raise NotConfigured(
                    f"SELECTOR_GUARDRAIL_TREES is not valid JSON: {exc}"
                ) from exc
            if not isinstance(data, list):
                raise NotConfigured("SELECTOR_GUARDRAIL_TREES must be a list")
            for item in data:
                if isinstance(item, dict):
                    repo = item.get("repo") or item.get("repository")
                    ref = item.get("ref")
                    tree = item.get("tree")
                    paths = item.get("paths")
                elif isinstance(item, (list, tuple)) and len(item) >= 4:
                    repo, ref, tree, paths = item[0], item[1], item[2], item[3]
                else:
                    raise NotConfigured(
                        f"SELECTOR_GUARDRAIL_TREES entry is malformed: {item}"
                    )
                if not (repo and ref and tree and paths):
                    raise NotConfigured(
                        f"SELECTOR_GUARDRAIL_TREES entry missing required field: {item}"
                    )
                parsed.append(GuardrailTree(
                    repo=str(repo).strip(),
                    ref=str(ref).strip(),
                    tree=Path(str(tree).strip()),
                    paths=str(paths).strip(),
                ))
        else:
            entries = [e.strip() for e in raw.replace("\n", ";").split(";") if e.strip()]
            for entry in entries:
                if ":" in entry:
                    parts = [p.strip() for p in entry.split(":", 3)]
                else:
                    parts = [p.strip() for p in entry.split(",", 3)]
                if len(parts) < 4 or not all(parts):
                    raise NotConfigured(
                        f"SELECTOR_GUARDRAIL_TREES entry is malformed: {entry} (expected repo:ref:tree:paths)"
                    )
                parsed.append(GuardrailTree(
                    repo=parts[0],
                    ref=parts[1],
                    tree=Path(parts[2]),
                    paths=parts[3],
                ))
        if not parsed:
            raise NotConfigured("SELECTOR_GUARDRAIL_TREES declared no trees")
        return tuple(parsed)

    repo = env("SELECTOR_PROTECTED_REPO")
    ref = env("SELECTOR_PROTECTED_REF")
    if repo and ref:
        here = Path(__file__).resolve().parent
        tree = Path(env("SELECTOR_PROTECTED_TREE") or str(here.parent))
        paths = env("SELECTOR_PROTECTED_PATHS") or str(here / "guardrail-sources" / "paths.txt")
        return (GuardrailTree(
            repo=repo.strip(),
            ref=ref.strip(),
            tree=tree,
            paths=paths,
        ),)
    return ()


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
    search_owner: str
    guardrail_trees: tuple[GuardrailTree, ...]

    @classmethod
    def from_env(cls) -> "Instance":
        """Read the instance, or refuse naming the first value that is absent.

        In declaration order, so an instance missing three values is told
        about them in the order it would fill them in rather than in whatever
        order a dict happened to iterate.
        """
        if not os.environ.get("SELECTOR_BOX_HOST"):
            missing("SELECTOR_BOX_HOST", REQUIRED_INSTANCE_VARS["SELECTOR_BOX_HOST"])
        if not os.environ.get("SELECTOR_SEARCH_OWNER"):
            missing(
                "SELECTOR_SEARCH_OWNER",
                REQUIRED_INSTANCE_VARS["SELECTOR_SEARCH_OWNER"],
            )
        if not os.environ.get("SELECTOR_GUARDRAIL_TREES"):
            for name in ("SELECTOR_PROTECTED_REPO", "SELECTOR_PROTECTED_REF"):
                if not os.environ.get(name):
                    missing(name, REQUIRED_INSTANCE_VARS[name])
        trees = load_guardrail_trees()
        protected_repo = os.environ.get("SELECTOR_PROTECTED_REPO") or (trees[0].repo if trees else "")
        protected_ref = os.environ.get("SELECTOR_PROTECTED_REF") or (trees[0].ref if trees else "")
        return cls(
            box_host=os.environ["SELECTOR_BOX_HOST"],
            protected_repo=protected_repo,
            protected_ref=protected_ref,
            search_owner=os.environ["SELECTOR_SEARCH_OWNER"],
            guardrail_trees=trees,
        )


HERE = Path(__file__).resolve().parent

# How many Dispatches one Cycle may hold at once (issue #37). Serial is the
# established behavior, so the default is 1; zero or negative is not a drain
# and is refused at preflight. Review Cap, not this number, remains the
# throughput bound (ADR 0021 as amended).
DRAIN_CONCURRENCY_VAR = "SELECTOR_DRAIN_CONCURRENCY"
DEFAULT_DRAIN_CONCURRENCY = 1


@dataclass(frozen=True)
class Config:
    """One cycle's configuration: the target it works, and the instance it
    works it from.

    The two halves are deliberately one object. Everything below `target` is
    the instance - the same for every repository this controller works - and
    the target carries what differs: its labels, who may hand work over on
    it, its checkouts, its token, its guest image, its review cap and what a
    finished Run does with its work. A second target is a second `Config`
    around the same instance values, which is what makes it a stanza rather
    than a second controller (issue #3).

    The target's own fields are read through properties rather than copied,
    so there is one spelling of "the review label" and a Config cannot be
    built that disagrees with the stanza it came from.
    """

    target: Target
    tracker_command: str
    box_facts_command: str
    box_facts_timeout_seconds: int
    guardrail_command: str
    guardrail_timeout_seconds: int
    board_timeout_seconds: int
    guardrail_trees: tuple[GuardrailTree, ...]
    drain_concurrency: int

    @property
    def task_repo(self) -> str:
        return self.target.repo

    @property
    def label(self) -> str:
        return self.target.labels.ready

    @property
    def needs_info_label(self) -> str:
        return self.target.labels.needs_info

    @property
    def review_label(self) -> str:
        return self.target.labels.review

    @property
    def human_label(self) -> str:
        return self.target.labels.human

    @property
    def allowlist(self) -> tuple[str, ...]:
        return self.target.labeler_allowlist

    @property
    def review_cap(self) -> int:
        return self.target.review_cap

    @classmethod
    def for_target(cls, target: Target) -> "Config":
        env = os.environ.get
        return cls(
            target=target,
            tracker_command=env(
                "SELECTOR_TRACKER_COMMAND", str(HERE / "tracker-sources" / "github.sh")
            ),
            box_facts_command=env(
                "SELECTOR_BOX_FACTS_COMMAND",
                str(HERE / "box-sources" / "facts-local.sh"),
            ),
            # Short on purpose. This is a status read, and a status read that
            # can hold a cycle open is worse than one that goes missing: the
            # dispatch behind it is what the cycle is for.
            box_facts_timeout_seconds=int(
                env("SELECTOR_BOX_FACTS_TIMEOUT_SECONDS", "60")
            ),
            guardrail_command=env(
                "SELECTOR_GUARDRAIL_COMMAND",
                str(HERE / "guardrail-sources" / "protection.sh"),
            ),
            # One `gh api` call and one walk of the deployed tree, on a cycle
            # that has work to do: the same reasoning as the box read above.
            guardrail_timeout_seconds=int(
                env("SELECTOR_GUARDRAIL_TIMEOUT_SECONDS", "30")
            ),
            # Shorter still, and for a sharper version of the same reason: the
            # queue board's tracker reads happen inside a page request. Three
            # of them against a live GitHub queue took about three seconds when
            # this was measured, so ten seconds is a tracker that is broken
            # rather than slow - and a column saying so beats a page that
            # hangs.
            board_timeout_seconds=int(
                env("SELECTOR_BOARD_TIMEOUT_SECONDS", "10")
            ),
            guardrail_trees=load_guardrail_trees(),
            drain_concurrency=_drain_concurrency(),
        )


    @classmethod
    def load(cls, repo: str | None = None) -> tuple["Config", ...]:
        """Every target this cycle is to work, configured.

        The preflight (issue #3): it raises `NotConfigured` naming the
        missing value, and it is called before the tracker command is run, so
        a half-configured instance stops without having read or written
        anything.

        Both halves, and the instance first. An instance value that is absent
        is not caught by the script that reads it until the cycle has already
        read the queue, seeded a branch and pushed it - `observe_box` treats a
        box it cannot read as a status failure and carries on, by design - so
        checking it here is what makes "before any tracker read" true of the
        instance and not only of the targets.
        """
        Instance.from_env()
        _drain_concurrency()
        return tuple(
            cls.for_target(target)
            for target in select(load(), repo)
        )


def _drain_concurrency() -> int:
    """How many Dispatches this Cycle may hold at once.

    Unset defaults to 1, which is today's serial drain. Zero or negative is
    a configuration error: a cap of nothing is a paused instance, and pausing
    has its own control. Named at preflight like every other instance value.
    """
    raw = os.environ.get(DRAIN_CONCURRENCY_VAR, str(DEFAULT_DRAIN_CONCURRENCY))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise NotConfigured(
            f"{DRAIN_CONCURRENCY_VAR} is {raw!r}, which is not a positive"
            " whole number. It names how many Dispatches a Cycle may hold"
            " at once."
        ) from None
    if value < 1:
        raise NotConfigured(
            f"{DRAIN_CONCURRENCY_VAR} is {value}, and a concurrency of less"
            f" than 1 is not a drain: {DRAIN_CONCURRENCY_VAR} names how many"
            " Dispatches a Cycle may hold at once."
        )
    return value


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


def _literal(value: Any) -> str:
    """One TOML value, quoted the way a stanza is written by hand."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump(remaining: tuple[Target, ...]) -> str:
    """The remaining stanzas as TOML, or a comment when there are none.

    An empty instance is a file with no `[[target]]`, not a deleted file:
    the variable still names something readable, and the next load is the
    same refusal a Cycle already journals at preflight.
    """
    if not remaining:
        return (
            "# No [[target]] stanzas. A Cycle refuses at preflight until one"
            " is declared.\n"
        )
    chunks = []
    for target in remaining:
        lines = [
            "[[target]]",
            f"repo = {_literal(target.repo)}",
            f"work_repo = {_literal(str(target.work_repo))}",
            f"box_repo = {_literal(target.box_repo)}",
            f"token_file = {_literal(target.token_file)}",
            f"guest_template = {_literal(target.guest_template)}",
            f"labeler_allowlist = {_literal(target.labeler_allowlist)}",
        ]
        if target.review_cap != DEFAULT_REVIEW_CAP:
            lines.append(f"review_cap = {_literal(target.review_cap)}")
        if target.landing != LANDING_MODES[0]:
            lines.append(f"landing = {_literal(target.landing)}")
        renamed = {
            key: getattr(target.labels, key)
            for key in DEFAULT_LABELS
            if getattr(target.labels, key) != DEFAULT_LABELS[key]
        }
        if renamed:
            lines.append("")
            lines.append("[target.labels]")
            lines += [f"{key} = {_literal(value)}" for key, value in renamed.items()]
        chunks.append("\n".join(lines) + "\n")
    return "\n".join(chunks)


def _write(path: Path, remaining: tuple[Target, ...]) -> None:
    """Replace the targets file atomically with the remaining stanzas."""
    text = _dump(remaining)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if path.exists():
            tmp.chmod(path.stat().st_mode)
        else:
            tmp.chmod(0o600)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise NotConfigured(
            f"{TARGETS_FILE_VAR} names {path}, which could not be written: {exc}"
        ) from exc


def _existing(where: Path) -> tuple[Target, ...]:
    """The targets already declared, or none.

    A missing file or a file with no `[[target]]` is an empty instance,
    which is what add() writes into. A file that exists and is malformed
    is not: overwriting it would hide the diagnosis.
    """
    if not where.exists():
        return ()
    try:
        return load(where)
    except NotConfigured as exc:
        try:
            document = tomllib.loads(where.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            raise exc
        if document.get("target"):
            raise exc
        return ()


def _short_name(repo: str) -> str:
    """The last path component of `owner/name`, which is what the Host's
    checkouts and token files are named after."""
    return repo.rsplit("/", 1)[-1]


def _rename_path(value: str, old: str, new: str) -> str:
    """Replace the source Target's short name in a path, or empty.

    Only the final component is rewritten: a parent directory that
    happens to match the name (`/home/loop/loop`) stays put. A path
    that does not carry the name is not reused — two Targets sharing
    a checkout or a token is the failure this exists to prevent.
    """
    if not old:
        return ""
    parent, sep, last = value.rpartition("/")
    if last == old:
        renamed = new
    elif old in last:
        renamed = last.replace(old, new, 1)
    else:
        return ""
    return parent + sep + renamed


def draft(repo: str, source: Target) -> dict[str, Any]:
    """A stanza for `repo`, filled from an existing Target.

    Path fields that carry the source's short name have it substituted.
    guest_template and labeler_allowlist are copied: they are facts
    about the instance, not about the repository. No host path is
    invented — a layout the instance has never declared stays blank.
    """
    old = _short_name(source.repo)
    new = _short_name(repo)
    return {
        "repo": repo,
        "work_repo": _rename_path(str(source.work_repo), old, new),
        "box_repo": _rename_path(source.box_repo, old, new),
        "token_file": _rename_path(source.token_file, old, new),
        "guest_template": source.guest_template,
        "labeler_allowlist": list(source.labeler_allowlist),
    }


def _fill_from_last(stanza: dict[str, Any], current: tuple[Target, ...]) -> dict[str, Any]:
    """Blank required fields, filled from the last declared Target.

    A submitted value wins. A layout the last Target does not
    demonstrate stays blank, and Target.load refuses it by name.
    """
    repo = stanza.get("repo") or ""
    if not repo or not current:
        return stanza
    filled = draft(str(repo), current[-1])
    merged = dict(filled)
    for key, value in stanza.items():
        if value:
            merged[key] = value
    return merged


def add(stanza: dict[str, Any], path: Path | None = None) -> tuple[Target, ...]:
    """Enroll one Target: append its stanza, leave the rest in file order.

    The Host's checkouts and token files are not created. Adding a Target
    is putting it on the Cycle's list, not provisioning the machine it
    will run on. Blank path, guest, and allowlist fields are filled from
    the last declared Target, so a second enrollment may name only the
    repository.
    """
    where = path or targets_file()
    current = _existing(where)
    incoming = Target.load(
        _fill_from_last(stanza, current),
        where=str(where),
        index=len(current) + 1,
    )
    if incoming.repo in {target.repo for target in current}:
        raise NotConfigured(
            f"{where} already declares {incoming.repo}. Two stanzas for one"
            " repository would be two review caps and two work checkouts for"
            " one queue."
        )
    remaining = current + (incoming,)
    _write(where, remaining)
    return remaining


def remove(repo: str, path: Path | None = None) -> tuple[Target, ...]:
    """Unenroll one Target: drop its stanza, leave the rest in file order.

    The Host's checkouts and token files stay put. Removing a Target is
    taking it off the Cycle's list, not cleaning the machine it ran on.
    """
    where = path or targets_file()
    current = load(where)
    remaining = tuple(target for target in current if target.repo != repo)
    if len(remaining) == len(current):
        raise _unknown_repo(repo, current)
    _write(where, remaining)
    return remaining


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
    raise _unknown_repo(repo, targets)


def _unknown_repo(repo: str, declared: tuple[Target, ...]) -> NotConfigured:
    return NotConfigured(
        f"no target declares repo = {repo!r}. Declared: "
        + ", ".join(target.repo for target in declared)
    )
