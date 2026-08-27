"""preview-cycle.sh must redirect EVERY outward reach, not most of them.

The wrapper exists because ADR 0016's containment is on the `labstage` account
that runs the web process and does not extend to a conductor shell. That makes
it the only thing standing between a preview cycle and production - so what is
tested here is not that it redirects the obvious edges, but that it leaves no
edge on its default.

The trap this catches: dispatch.py's `SELECTOR_WORK_REPO` defaults to a real
tourbot checkout and `push()` does `git push --set-upstream origin <branch>`.
A wrapper that faked the tracker and the box and left those alone would still
push a branch to the real repository.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
WRAPPER = HERE / "preview-cycle.sh"

# Every environment variable cycle.py and dispatch.py read that names
# something outside this VM, with the default the wrapper must not leave in
# place. Adding an outward reach to the Selector means adding it here.
OUTWARD = {
    "SELECTOR_TRACKER_COMMAND": "tracker-sources/github.sh",
    "SELECTOR_BOX_COMMAND": "box-sources/ssh.sh",
    "SELECTOR_ISSUE_COMMAND": "issue-sources/github.sh",
    # The box's status read (#156). Milder than the others - it only reads -
    # but it is still an SSH session out of this VM, once per cycle, and the
    # wrapper's promise is that no edge is left on its default.
    "SELECTOR_BOX_FACTS_COMMAND": "box-sources/facts.sh",
    "SELECTOR_SEED_COMMAND": "seed-run.sh",
    "SELECTOR_WORK_REPO": "/var/lib/conductor/selector-work",
}


class TestTheWrapperRedirectsEveryEdge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.dumped = root / "env.txt"
        stub = root / "stub-python.sh"
        stub.write_text(f'#!/bin/bash\nenv > "{self.dumped}"\n')
        stub.chmod(0o755)
        self.stub = stub
        self.work = root / "work"

    def env_after_wrapper(self) -> dict:
        result = subprocess.run(
            ["bash", str(WRAPPER), "--dry-run"],
            capture_output=True, text=True,
            env={
                **os.environ,
                "SELECTOR_PYTHON": str(self.stub),
                "SELECTOR_JOURNAL_DSN": "dbname=selector_preview_unit_test",
                "LAB_PREVIEW_WORK": str(self.work),
            },
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return dict(
            line.split("=", 1)
            for line in self.dumped.read_text().splitlines()
            if "=" in line
        )

    def test_no_outward_reach_is_left_on_its_default(self):
        env = self.env_after_wrapper()
        for var, default_fragment in OUTWARD.items():
            self.assertIn(var, env, f"{var} is not set by the wrapper at all")
            self.assertNotIn(
                default_fragment, env[var],
                f"{var} still points at the real thing: {env[var]}",
            )

    def test_the_work_repo_pushes_to_a_local_bare_repo(self):
        """`push()` is unconditional - it runs before the box is reached, so
        even a preview that dispatches nothing still pushes. The remote has to
        be somewhere that is not GitHub."""
        env = self.env_after_wrapper()
        work_repo = Path(env["SELECTOR_WORK_REPO"])
        self.assertTrue(work_repo.is_dir(), f"{work_repo} was not created")
        remote = subprocess.run(
            ["git", "-C", str(work_repo), "remote", "get-url",
             env.get("SELECTOR_WORK_REMOTE", "origin")],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertNotIn("github.com", remote)
        self.assertTrue(remote.startswith(str(self.work)), remote)

    def test_it_refuses_every_spelling_of_the_live_journal(self):
        """libpq accepts several spellings of the same database, and the guard
        is only worth having if it catches the ones somebody would actually
        type after the obvious one has been refused."""
        for dsn in (
            "dbname=selector",
            "dbname=selector host=/tmp",
            "host=/tmp dbname=selector",
            "dbname='selector'",
            "postgresql:///selector",
        ):
            result = subprocess.run(
                ["bash", str(WRAPPER), "--dry-run"],
                capture_output=True, text=True,
                env={**os.environ, "SELECTOR_PYTHON": str(self.stub),
                     "SELECTOR_JOURNAL_DSN": dsn,
                     "LAB_PREVIEW_WORK": str(self.work)},
            )
            self.assertEqual(2, result.returncode, dsn)
            self.assertIn("live Journal", result.stderr)
            self.assertFalse(self.dumped.exists(), f"cycle.py ran anyway: {dsn}")

    def test_a_staging_dsn_is_not_refused(self):
        """The other half. A guard that refused everything would pass the test
        above and make the wrapper useless."""
        env = self.env_after_wrapper()
        self.assertEqual("dbname=selector_preview_unit_test",
                         env["SELECTOR_JOURNAL_DSN"])


if __name__ == "__main__":
    unittest.main()
