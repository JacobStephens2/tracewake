"""No default in the shipping tree names a company, a host, a person or a
repository - and the configuration that replaces them refuses by name.

Issue #3's third acceptance criterion asks for a *test* rather than a sweep,
because a sweep is true on the day it is run. The Loop and the Selector were
built inside one company's repository and every fact about that company was a
default in code; the way that comes back is one convenient default at a time,
each of them reasonable on its own.

Issue #54 keeps that shape test and drops the filled-in Instance that used to
grade it from the other side. A default that looks like an email, a hostname,
or a bare `owner/name` slug is still refused. The product ships no sample
Instance for the test to read: instance facts belong on the Host.

What counts as a *default* is deliberately broad: `${VAR:-value}` and bare
assignments in shell, `env("VAR", "value")` and module constants in Python,
scalar role defaults in YAML. Reading the tree rather than a list, because a
list is a second place to keep in step.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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
        " (targets.missing / the `require` helper)."
        for where, name, value, what in offenders
    )


def test_the_tree_contains_no_filled_in_instance_sample():
    """Issue #54: instance facts belong on the Host, not in the product as
    'the example'. A directory of filled-in env, targets, inventory and
    droplet files is that sample even when it carries no secrets."""
    examples = ROOT / "examples"
    assert not examples.exists(), (
        "examples/ is a filled-in Instance sample. Remove it; INSTALL.md "
        "shows the Single-Host shape without someone else's hostnames, mail "
        "or repositories."
    )


def test_install_and_readme_describe_only_single_host():
    """The product teaches one setup. A remote Box stays a substituted
    command in the tree; standing it up is not a second walkthrough."""
    for name in ("INSTALL.md", "README.md"):
        text = (ROOT / name).read_text()
        lowered = text.lower()
        assert "single-host" in lowered, (
            f"{name} does not describe Single-Host, which is the product's "
            "one taught setup (issue #54)."
        )
        for phrase in ("two-machine", "two machine"):
            assert phrase not in lowered, (
                f"{name} still teaches {phrase!r}. Single-Host is the setup; "
                "a remote Box is a substituted command, not a documented "
                "topology (issue #54)."
            )
