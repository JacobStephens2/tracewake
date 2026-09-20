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

Issue #55 adds the provision module to the scan. Its five variables are
required, so a `default` line in any `variable` block is the same offence as
a default anywhere else: an instance fact checked into the product.

What counts as a *default* is deliberately broad: `${VAR:-value}` and bare
assignments in shell, `env("VAR", "value")` and module constants in Python,
scalar role defaults in YAML, `default = ...` in an OpenTofu `variable`
block. Reading the tree rather than a list, because a list is a second place
to keep in step.
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
# An OpenTofu variable header. The block that follows is found by brace
# counting rather than by regex, because a nested block - a `validation`
# stanza, or a brace inside a description - closes a brace before any
# `default` line.
_TF_VARIABLE_HEADER = re.compile(r"variable\s+\"([^\"]+)\"\s*\{")
_TF_DEFAULT_LINE = re.compile(r"^\s*default\s*=\s*(.+?)\s*$", re.M)
# A quoted string, for seeing past brackets that are string contents rather
# than structure when a default continues onto following lines.
_TF_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _tf_block_end(text: str, opening: int) -> int:
    """Index just past the `}` closing the brace at `opening`.

    Braces inside quoted strings do not count: a description is the most
    natural place for one to appear.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(opening, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unbalanced braces in variable block")


def _brackets_open(text: str) -> bool:
    """Whether brackets stay unclosed, ignoring string contents: a `[` in a
    description is prose, not structure."""
    depth = 0
    for char in _TF_STRING.sub("", text):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
    return depth > 0


def tf_variable_defaults(text: str) -> list[tuple[str, str]]:
    """Every `default` in an OpenTofu file, as (variable name, value).

    A list or map default may continue onto following lines; those lines
    belong to the value while brackets stay open.
    """
    found = []
    for match in _TF_VARIABLE_HEADER.finditer(text):
        body = text[match.end():_tf_block_end(text, match.end() - 1) - 1]
        default = _TF_DEFAULT_LINE.search(body)
        if not default:
            continue
        lines = [default.group(1)]
        # The split's first line is the tail of the default line itself,
        # already captured above; what follows are the continuation lines.
        rest = body[default.end():].splitlines()[1:]
        while _brackets_open("\n".join(lines)) and rest:
            lines.append(rest.pop(0))
        value = re.sub(r"\s+#.*$", "", "\n".join(lines)).strip().strip('"')
        found.append((match.group(1), value))
    return found

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
        if path.suffix == ".tf":
            for name, value in tf_variable_defaults(text):
                found.append((where, name, value))
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


def test_tofu_host_variables_are_required():
    """Issue #55: region, size, VPC, SSH key and name are required variables
    with no product defaults. A default would let an apply silently build
    somebody else's host shape, and omitting a variable must fail before
    apply - which is what having no default does."""
    variables = ROOT / "deploy" / "tofu" / "variables.tf"
    assert variables.is_file(), (
        "deploy/tofu/variables.tf is missing. The provision module declares "
        "the Host's region, size, VPC, SSH key and name (issue #55)."
    )
    text = variables.read_text()
    names = _TF_VARIABLE_HEADER.findall(text)
    for expected in ("name", "region", "size", "vpc_uuid", "ssh_key"):
        assert expected in names, (
            f"deploy/tofu/variables.tf declares no {expected!r} variable. "
            "All five are required (issue #55)."
        )
    assert tf_variable_defaults(text) == [], (
        "deploy/tofu/variables.tf sets a default. Required variables take "
        "none, so omitting one fails before apply (issue #55)."
    )


def test_tofu_host_is_one_ubuntu_droplet():
    """Issue #55: applying the module creates one Ubuntu 24.04 droplet that
    can be a Tracewake Host - the image `sbx` supports, on a shape with
    nested virtualization. The live apply itself is a human wizard step, so
    this pins the resource the apply would build."""
    main = ROOT / "deploy" / "tofu" / "main.tf"
    assert main.is_file(), (
        "deploy/tofu/main.tf is missing. The provision module builds the "
        "Host droplet (issue #55)."
    )
    text = main.read_text()
    droplets = re.findall(r'resource\s+"digitalocean_droplet"\s+"[^"]+"', text)
    assert len(droplets) == 1, (
        f"deploy/tofu/main.tf declares {len(droplets)} droplets, not one "
        "(issue #55)."
    )
    assert re.search(r'image\s*=\s*"ubuntu-24-04-x64"', text), (
        "deploy/tofu/main.tf does not build Ubuntu 24.04, the image the "
        "Execution Boundary supports (issue #55)."
    )


def test_tofu_local_host_variables_are_required():
    """Issue #107: name, checkout and http_port are required variables with
    no product defaults. A default would let an apply silently build
    somebody else's local Host, and omitting a variable must fail before
    apply - the same rule as the droplet module (issue #55)."""
    variables = ROOT / "deploy" / "tofu" / "local" / "variables.tf"
    assert variables.is_file(), (
        "deploy/tofu/local/variables.tf is missing. The local provision "
        "module declares the container name, the checkout to bind-mount, "
        "and the host port Caddy is published on (issue #107)."
    )
    text = variables.read_text()
    names = _TF_VARIABLE_HEADER.findall(text)
    for expected in ("name", "checkout", "http_port"):
        assert expected in names, (
            f"deploy/tofu/local/variables.tf declares no {expected!r} "
            "variable. All three are required (issue #107)."
        )
    assert tf_variable_defaults(text) == [], (
        "deploy/tofu/local/variables.tf sets a default. Required variables "
        "take none, so omitting one fails before apply (issue #107)."
    )


def test_tofu_local_host_is_one_ubuntu_container():
    """Issue #107: applying the local module creates one Ubuntu 24.04
    systemd container that can be a Tracewake Host. The Dockerfile is the
    local analogue of ubuntu-24-04-x64: systemd and Python for Ansible,
    nothing the play is supposed to install. The live apply itself is a
    human step, so this pins the resource the apply would build."""
    main = ROOT / "deploy" / "tofu" / "local" / "main.tf"
    assert main.is_file(), (
        "deploy/tofu/local/main.tf is missing. The local provision module "
        "builds the Host container (issue #107)."
    )
    text = main.read_text()
    containers = re.findall(r'resource\s+"docker_container"\s+"[^"]+"', text)
    assert len(containers) == 1, (
        f"deploy/tofu/local/main.tf declares {len(containers)} containers, "
        "not one (issue #107)."
    )
    assert "/srv/tracewake" in text, (
        "the local module must bind-mount the checkout at /srv/tracewake, "
        "the Host's product directory"
    )
    dockerfile = ROOT / "deploy" / "tofu" / "local" / "machine" / "Dockerfile"
    assert dockerfile.is_file(), (
        "deploy/tofu/local/machine/Dockerfile is missing. It is the local "
        "analogue of DigitalOcean's ubuntu-24-04-x64 image (issue #107)."
    )
    image = dockerfile.read_text()
    assert re.search(r"^FROM ubuntu:24\.04\b", image, re.M), (
        "the local machine image must be Ubuntu 24.04, the image the "
        "Execution Boundary supports (issue #107)."
    )
    assert "systemd" in image, (
        "the local machine image must run systemd, so the Host play's units "
        "have a manager (issue #107)."
    )
    lowered = image.lower()
    for forbidden in ("tracewake", "caddy", "postgresql", "docker-sbx", "ansible"):
        assert forbidden not in lowered, (
            f"the local machine image installs {forbidden!r}. That is the "
            "play's job; the image is an empty Ubuntu Host (issue #107)."
        )
    assert "rm -rf /var/lib/apt/lists" not in image, (
        "wiping apt lists in the image makes host.yml --check fail with "
        "'No package matching git'. The cloud image keeps its lists; so "
        "does this one (issue #107)."
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


def test_install_describes_the_local_host():
    """Issue #107: a laptop stands up Single-Host on Docker. INSTALL names
    the local module and the same play; it is not a second topology."""
    text = (ROOT / "INSTALL.md").read_text()
    assert "deploy/tofu/local" in text, (
        "INSTALL.md does not name deploy/tofu/local, the OpenTofu module "
        "that creates the local Host (issue #107)."
    )
    assert "deploy/ansible/host.yml" in text
    readme = (ROOT / "README.md").read_text()
    assert "deploy/tofu" in readme, (
        "README.md does not name deploy/tofu, where both Host machines "
        "are provisioned (issue #107)."
    )


def test_install_names_the_local_host_wizard():
    """Issue #114: standing the local Host up is a wizard, the same way
    the droplet is wizards/host-up.sh. INSTALL names that walkthrough;
    host-up.sh stays the droplet path."""
    install = (ROOT / "INSTALL.md").read_text()
    assert "wizards/local-host-up.sh" in install, (
        "INSTALL.md does not name wizards/local-host-up.sh, the laptop "
        "walkthrough that stands the local Host up (issue #114)."
    )
    readme = (ROOT / "README.md").read_text()
    assert "local-host-up.sh" in readme, (
        "README.md does not name local-host-up.sh next to host-up.sh "
        "(issue #114)."
    )
    assert "host-up.sh" in readme
    adr = (ROOT / "docs" / "adr" / "0031-a-local-host-is-the-same-play.md").read_text()
    assert "wizards/host-up.sh" in adr
    assert "local-host-up.sh" in adr, (
        "ADR 0031 must name the laptop wizard so host-up.sh staying the "
        "droplet path is not read as 'there is no local walkthrough' "
        "(issue #114)."
    )


def test_tofu_local_host_keeps_the_journal_and_instance_files():
    """Issue #114: recreating the container must not drop the Journal or
    the instance files. Named volumes at /var/lib/postgresql and
    /etc/tracewake, the same way the module already keeps the Linux venvs.
    """
    main = ROOT / "deploy" / "tofu" / "local" / "main.tf"
    text = main.read_text()
    for target in ("/var/lib/postgresql", "/etc/tracewake"):
        assert target in text, (
            f"the local module must keep {target} on a named volume so "
            "recreating the container does not drop the Journal or the "
            "instance files (issue #114)."
        )
        nearby = text[max(0, text.index(target) - 250):text.index(target) + 80]
        assert 'type   = "volume"' in nearby or 'type = "volume"' in nearby, (
            f"{target} must be a named docker volume, not a bind of a "
            "laptop path (issue #114)."
        )
        assert "docker_volume." in nearby, (
            f"{target} must source a docker_volume resource (issue #114)."
        )


def test_local_host_wizard_is_the_laptop_walkthrough():
    """Issue #114: standing a local Host up is a wizard, not a paste.
    host-up.sh stays the droplet path (ADR 0031).
    """
    wizard = ROOT / "wizards" / "local-host-up.sh"
    assert wizard.is_file(), (
        "wizards/local-host-up.sh is the laptop Host walkthrough "
        "(issue #114)."
    )
    text = wizard.read_text()
    assert "TRACEWAKE_CONF" in text, (
        "the wizard must honour TRACEWAKE_CONF the same way "
        "deploy/tofu/local/up.sh does (issue #114)."
    )
    assert "local.tfvars" in text
    assert "local-inventory.yml" in text
    assert "~/.config/tracewake" in text or "$HOME/.config/tracewake" in text
    assert ".config/tracewake" in text, (
        "the wizard must write instance facts under ~/.config/tracewake "
        "(or TRACEWAKE_CONF), not in the tree (issue #114)."
    )
    for flag in (
        "community.docker.docker",
        "tracewake_tls: false",
        "tracewake_manage_checkout: false",
        "tracewake_web_reload: true",
    ):
        assert flag in text, (
            f"the inventory the wizard writes must include {flag!r} "
            "(issue #114)."
        )
    assert "deploy/tofu/local/up.sh" in text, (
        "the wizard runs deploy/tofu/local/up.sh (issue #114)."
    )
    assert "host.yml" in text, (
        "the wizard runs ansible-playbook against host.yml (issue #114)."
    )
    assert "seed-admin.py" in text, (
        "the wizard seeds an admin via web/seed-admin.py (issue #114)."
    )
    assert "WINDOW_COOKIE_SECURE=0" in text, (
        "the wizard sets WINDOW_COOKIE_SECURE=0 so a plain-HTTP window "
        "can sign in (issue #114)."
    )
    assert not re.search(r"(?m)^export WINDOW_COOKIE_SECURE=", text), (
        "systemd EnvironmentFile ignores 'export KEY=value'; the instance "
        "file must be KEY=value so the window process sees "
        "WINDOW_COOKIE_SECURE=0 (issue #114)."
    )
    assert "127.0.0.1" in text and "/healthz" in text, (
        "curl http://127.0.0.1:<http_port>/healthz is the health check "
        "the wizard waits on (issue #114)."
    )

    install = (ROOT / "INSTALL.md").read_text()
    assert "wizards/local-host-up.sh" in install, (
        "INSTALL.md names wizards/local-host-up.sh as the local Host "
        "walkthrough (issue #114)."
    )
    readme = (ROOT / "README.md").read_text()
    assert "local-host-up.sh" in readme, (
        "README.md's wizards line names local-host-up.sh (issue #114)."
    )
    host_up = (ROOT / "wizards" / "host-up.sh").read_text()
    assert "droplet" in host_up.lower(), (
        "wizards/host-up.sh stays the droplet path (issue #114, ADR 0031)."
    )
