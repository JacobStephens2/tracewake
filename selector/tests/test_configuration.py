"""No default in the shipping tree names a company, a host, a person or a
repository - and the configuration that replaces them refuses by name.

Issue #3's third acceptance criterion asks for a *test* rather than a sweep,
because a sweep is true on the day it is run. The Loop and the Selector were
built inside one company's repository and every fact about that company was a
default in code; the way that comes back is one convenient default at a time,
each of them reasonable on its own.

So this grades the tree mechanically, in two directions:

**Shape.** A default that carries an email address, a hostname, or a bare
`owner/name` repository slug is refused whatever it says. That catches the
next company as readily as the last one.

**The examples.** `examples/` carries one real instance's values - that is
what makes it useful - so every host, address, account and repository in it is
by definition an instance fact. No shipping default may contain any of them.
The two files grade each other: filling `examples/` in more completely makes
this test stricter, and a default that crept back in fails against the very
file that shows where it belongs.

What counts as a *default* is deliberately broad: `${VAR:-value}` and bare
assignments in shell, `env("VAR", "value")` and module constants in Python,
scalar role defaults in YAML. Reading the tree rather than a list, because a
list is a second place to keep in step.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import targets  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"

# What ships: the Run's scripts, the controller, the window, the plays that
# install them, and the walkthroughs an operator runs. Tests are excluded -
# a fixture naming a fake company is a fixture - and so are the venvs, the
# notes and the research, which are evidence rather than code.
SHIPPING_TREES = ("loop", "selector", "web", "deploy", "wizards")
SKIP_PARTS = {"tests", ".venv", "site-packages", "__pycache__", "node_modules"}

# Test support that happens to sit beside the code it supports rather than
# under `tests/`, because two suites import it. Nothing ships from these: the
# canned tracker records and the throwaway-database harness are imported by
# tests and by nothing else.
SKIP_FILES = {"selector/fixtures.py", "selector/testdb.py"}

_SHELL_DEFAULT = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")
# Any assignment, however it is spelled. Case-insensitive in the name,
# leading whitespace allowed, `export`, `local`, `declare` and `readonly`
# skipped past: `SELECTOR_BOX_HOST=root@a.host` is the most natural way to
# put a default back, and a pattern that only matched lowercase names at
# column zero would not have seen it. Found by review, 2026-09-04.
_SHELL_ASSIGN = re.compile(
    r"^[ \t]*(?:export[ \t]+|local[ \t]+|declare[ \t]+|readonly[ \t]+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)=[\"']?([^\"'\n$`]+)[\"']?$",
    re.M,
)
_PY_GET = re.compile(
    r"""(?:env|os\.environ\.get)\(\s*["']([A-Z][A-Z0-9_]*)["']\s*,\s*["']([^"']*)["']"""
)
_PY_OR = re.compile(
    r"""(?:env|os\.environ\.get)\(\s*["']([A-Z][A-Z0-9_]*)["']\s*\)\s*or\s+["']([^"']*)["']"""
)
# Indented too, because a class or dataclass attribute is where a
# re-introduced default is most natural, and an anchor at column zero would
# have let every one of them through. Found by review, 2026-09-04.
_PY_CONST = re.compile(
    r'^[ \t]*([_A-Z][A-Z0-9_]*)\s*=\s*["\']([^"\']*)["\']\s*$', re.M)
# Scalar role defaults only. A list or a folded block is a package list or an
# egress allowlist - third-party services the product itself talks to, which
# are not an instance's own machines and are reviewed as what they are.
_YAML_SCALAR = re.compile(r"^([a-z][a-z0-9_]*):\s*([^\s#{>|\[-][^\n#]*?)\s*$", re.M)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# A hostname is a dotted name ending in a real public suffix. The pseudo-TLDs
# reserved for documentation and testing are deliberately absent from the
# list, because a fixture host is exactly what a default should say if it says
# anything.
_HOST = re.compile(
    r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)*"
    r"\.(?:com|org|net|io|dev|ai|co|app|cloud|me|us|uk|edu|gov)\b"
)
# `owner/name`, whole and on its own: two segments, no leading slash, no dot in
# the second - which is what tells a repository slug from a relative path to a
# file.
_REPO_SLUG = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w-]*$")

# Values that look like a hostname but are a filename. `.sh` and friends are
# not public suffixes here, but a name like `grok.sh` still has the shape, so
# the extension list is what tells them apart.
_FILE_SUFFIXES = (".sh", ".py", ".md", ".txt", ".toml", ".yml", ".yaml",
                  ".json", ".html", ".css", ".js", ".sql", ".service",
                  ".timer", ".tf", ".j2", ".bash", ".log", ".asc", ".env")


def _default_sources():
    for tree in SHIPPING_TREES:
        for path in sorted((ROOT / tree).rglob("*")):
            if not path.is_file() or SKIP_PARTS & set(path.parts):
                continue
            if str(path.relative_to(ROOT)) in SKIP_FILES:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            yield path, text


def defaults() -> list[tuple[str, str, str]]:
    """Every default in the shipping tree, as (where, name, value)."""
    found = []
    for path, text in _default_sources():
        where = str(path.relative_to(ROOT))
        readers = []
        if path.suffix in (".sh", ".j2", ".bash"):
            readers = [_SHELL_DEFAULT, _SHELL_ASSIGN]
        elif path.suffix == ".py":
            readers = [_PY_GET, _PY_OR, _PY_CONST]
        elif path.suffix in (".yml", ".yaml"):
            readers = [_YAML_SCALAR]
        for reader in readers:
            for match in reader.finditer(text):
                found.append((where, match.group(1), match.group(2)))
    return found


def _looks_like_a_file(value: str) -> bool:
    return value.endswith(_FILE_SUFFIXES)


def instance_facts() -> set[str]:
    """Every host, address, account and repository `examples/` declares.

    Read out of the example instance rather than listed here, so that the two
    files grade each other: a value shown in `examples/` is by construction a
    fact about an instance, and the test gets stricter as the examples get
    fuller.
    """
    facts: set[str] = set()
    for path in sorted(EXAMPLES.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text()
        facts |= set(_EMAIL.findall(text))
        facts |= {h for h in _HOST.findall(text) if not _looks_like_a_file(h)}
        # Bounded on both sides, so that `home/loop` inside `/home/loop/x` is
        # not read as a repository. A slug stands on its own or it is a path.
        for word in re.findall(
            r"(?<![\w./-])[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w-]*(?![\w./-])", text
        ):
            if _REPO_SLUG.match(word) and not _looks_like_a_file(word):
                facts.add(word)
    # The accounts the example allowlist names. A person's account has no
    # shape of its own - it is only ever a bare word - so the one place it can
    # be learned from is the example that declares it.
    for target in targets.load(EXAMPLES / "targets.toml"):
        facts |= set(target.labeler_allowlist)
    return facts


def test_the_shipping_tree_has_defaults_to_grade():
    """A guard on the guard. Every regex above could stop matching - a rename,
    a reformat - and this test would then pass by reading nothing."""
    assert len(defaults()) > 100


def test_no_default_names_a_host_a_person_or_a_repository():
    offenders = []
    for where, name, value in defaults():
        if _looks_like_a_file(value):
            continue
        if _EMAIL.search(value):
            offenders.append((where, name, value, "an email address"))
        elif _HOST.search(value):
            offenders.append((where, name, value, "a hostname"))
        elif _REPO_SLUG.match(value):
            offenders.append((where, name, value, "a repository"))
    assert not offenders, "\n".join(
        f"{where}: {name} defaults to {value!r}, which names {what}."
        " It is a fact about an instance: refuse it by name instead"
        " (targets.missing / the `require` helper), and show the value in"
        " examples/."
        for where, name, value, what in offenders
    )


def test_no_default_carries_a_value_the_examples_declare():
    facts = instance_facts()
    assert facts, "examples/ declares no instance facts; this test is inert"
    offenders = [
        (where, name, value, fact)
        for where, name, value in defaults()
        for fact in facts
        if fact in value
    ]
    assert not offenders, "\n".join(
        f"{where}: {name} defaults to {value!r}, which carries {fact!r} -"
        " a value examples/ declares, and therefore an instance's fact."
        for where, name, value, fact in offenders
    )


def test_the_examples_carry_no_secret():
    """`examples/` is read by the two tests above, committed, and public. What
    it is for is addresses and paths; a token in it would be a credential in
    the repository AND a value every default is graded against."""
    patterns = {
        "a GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"),
        "an Anthropic key or setup token": re.compile(r"sk-ant-[A-Za-z0-9-]{10,}"),
        "a private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "an xAI key": re.compile(r"xai-[A-Za-z0-9]{16,}"),
        "an AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    }
    for path in sorted(EXAMPLES.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text()
        for what, pattern in patterns.items():
            assert not pattern.search(text), f"{path.name} carries {what}"
