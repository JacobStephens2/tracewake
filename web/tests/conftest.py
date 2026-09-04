import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]  # web/
sys.path.insert(0, str(BASE))
# The shared throwaway-test-database harness lives with the Journal it tests.
sys.path.insert(0, str(BASE.parent / "selector"))

import pytest

import fixtures
import testdb

# The end-of-run report on throwaway databases this run failed to drop
# (#178). Imported rather than restated: one definition of what a leak is.
from testdb import pytest_terminal_summary  # noqa: F401


@pytest.fixture
def db(monkeypatch):
    """A throwaway Journal database, wired into the app via the DSN env var."""
    if not testdb.available():
        pytest.skip("local Postgres is not reachable over the unix socket")
    with testdb.throwaway_db() as dsn:
        monkeypatch.setenv("SELECTOR_JOURNAL_DSN", dsn)
        yield dsn


@pytest.fixture
def dispatch():
    """Runs in the Journal, as the Selector would have written them.

    `testdb.append_run`, the same helper the Selector's own suite seeds spend
    with. The page's budget cell and the Selector's cap read the same rows, so
    a second definition of what those rows look like would be a way for the
    two to drift apart in the one place they must not.
    """
    return testdb.append_run


# --- The tracker, scripted --------------------------------------------------
#
# The queue board reads the tracker at request time (#158), so from here on
# every /loop request in this suite reaches a tracker command. Autouse and
# empty by default, because the alternative is not "no board": it is the real
# `tracker-sources/github.sh`, which means a live `gh` call against whatever
# tracker this checkout's targets file names, from a unit test.

TRACKER = """#!/usr/bin/env bash
if [[ -f "{dir}/fail" ]]; then
    cat "{dir}/fail" >&2
    exit 1
fi
printf '%s\\n' "$2" >> "{dir}/labels-asked"
queue="{dir}/queue-$2.json"
if [[ -f "${{queue}}" ]]; then
    exec cat "${{queue}}"
fi
printf '{{"issues": []}}\\n'
"""


@pytest.fixture(autouse=True)
def tracker(tmp_path, monkeypatch):
    """A tracker command answering per label; every label empty until scripted."""
    directory = tmp_path / "tracker"
    directory.mkdir()
    script = directory / "tracker.sh"
    script.write_text(TRACKER.format(dir=directory))
    script.chmod(0o755)
    monkeypatch.setenv("SELECTOR_TRACKER_COMMAND", str(script))
    # The window reads its target from the targets file like the cycle does
    # (issue #3), so the suite writes one. Autouse for the same reason the
    # tracker is scripted: without it every /loop request in this suite would
    # be reading whatever this checkout is really configured to work.
    monkeypatch.setenv(
        "TRACEWAKE_TARGETS_FILE",
        fixtures.write_targets(tmp_path / "targets.toml"),
    )
    # The window asks the same preflight question the cycle does, so the
    # suite configures a whole instance. Unreachable values: nothing in this
    # suite may reach a real box or a real forge.
    monkeypatch.setenv("SELECTOR_BOX_HOST", "root@box.invalid")
    monkeypatch.setenv("SELECTOR_PROTECTED_REPO", "acme/tracewake")
    monkeypatch.setenv("SELECTOR_PROTECTED_REF", "main")

    class Tracker:
        issue = staticmethod(fixtures.issue)

        def queue(self, label, issues):
            (directory / f"queue-{label}.json").write_text(
                json.dumps({"issues": list(issues)})
            )

        def fail(self, message="the tracker could not be reached"):
            (directory / "fail").write_text(message + "\n")

        def labels_asked(self):
            path = directory / "labels-asked"
            return sorted(path.read_text().split()) if path.exists() else []

    return Tracker()


def column(body, name):
    """The queue board column with key `name`, as HTML.

    Sliced out of the page rather than searched whole, because every board
    assertion is about which column an issue landed in - and "#646 appears
    somewhere on the page" is exactly the thing that would pass while the
    board was wrong. Here rather than in the board suite because the staging
    fixture's suite asks the same question of the preview tracker.
    """
    match = re.search(
        r'<section class="column"[^>]*data-column="%s">(.*?)</section>' % name,
        body,
        re.DOTALL,
    )
    assert match, f"no {name!r} column on the board"
    return match.group(1)
