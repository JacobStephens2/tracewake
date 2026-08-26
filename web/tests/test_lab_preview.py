"""Tests for scripts/lab-preview.sh, the Attended Preview's front door.

ADR 0016 puts two properties in this script and nowhere else: a preview names
the branch and SHA it actually landed on, and one preview at a time is held by
a visible lease that a second operator cannot take silently. Both are tested
here against a throwaway origin and checkout, so nothing touches the real
staging tree and no systemd unit is restarted.

The restart is a substitutable command (the Loop's *_COMMAND convention), so
each test scripts a fake that records that it was called - which makes "did it
restart?" an assertion rather than a side effect nobody can see.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "lab-preview.sh"

MAX_AGE = 4 * 60 * 60


def git(cwd, *args, **kw):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, **kw
    ).stdout.strip()


class PreviewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # A bare origin with master plus one feature branch to preview.
        self.origin = root / "origin.git"
        seed = root / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "master")
        git(seed, "config", "user.email", "operator@example.invalid")
        git(seed, "config", "user.name", "Test Operator")
        (seed / "README.md").write_text("live\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", "live")
        git(seed, "checkout", "-qb", "feat/queue-board")
        (seed / "README.md").write_text("the branch under preview\n")
        git(seed, "commit", "-qam", "queue board")
        self.branch_sha = git(seed, "rev-parse", "HEAD")
        git(seed, "clone", "-q", "--bare", str(seed), str(self.origin))

        # The staging checkout the script drives.
        self.worktree = root / "staging"
        git(root, "clone", "-q", str(self.origin), str(self.worktree))
        git(self.worktree, "checkout", "-q", "master")

        self.lease = root / "lease.json"
        self.restarts = root / "restarts"
        fake = root / "fake-restart.sh"
        fake.write_text(f'#!/bin/bash\necho "$@" >> "{self.restarts}"\n')
        fake.chmod(0o755)
        self.fake_restart = fake

    def run_script(self, *args, env=None):
        environ = {
            **os.environ,
            "LAB_PREVIEW_WORKTREE": str(self.worktree),
            "LAB_PREVIEW_LEASE": str(self.lease),
            "LAB_PREVIEW_RESTART_COMMAND": str(self.fake_restart),
            "LAB_PREVIEW_URL": "https://lab-staging.example.invalid",
            "LAB_PREVIEW_MAX_AGE_SECONDS": str(MAX_AGE),
            "LAB_PREVIEW_OPERATOR": "jstephens",
        }
        environ.update(env or {})
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=environ,
        )

    def write_lease(self, *, age_seconds, branch="feat/other", by="vsto"):
        started = time.time() - age_seconds
        self.lease.write_text(json.dumps({
            "branch": branch,
            "sha": "0" * 40,
            "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(started)),
            "started_by": by,
        }))

    def restarted(self) -> bool:
        return self.restarts.exists()

    def checked_out(self) -> str:
        return git(self.worktree, "rev-parse", "HEAD")


class TestUsage(PreviewTestCase):
    def test_help_explains_itself_and_names_the_url(self):
        """An agent runs `--help` before it runs the thing (#151)."""
        result = self.run_script("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("lab-staging", result.stdout)
        self.assertIn("--force", result.stdout)

    def test_no_branch_is_a_usage_error_not_a_default(self):
        result = self.run_script()
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("restart", result.stdout.lower())
        self.assertFalse(self.restarted())


class TestStartingAPreview(PreviewTestCase):
    def test_it_checks_out_the_branch_and_restarts_the_unit(self):
        result = self.run_script("feat/queue-board")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.branch_sha, self.checked_out())
        self.assertTrue(self.restarted())

    def test_it_prints_the_sha_it_landed_on(self):
        """The whole point of printing it: "am I looking at what I think I am"
        has to be answerable without a second command."""
        result = self.run_script("feat/queue-board")
        self.assertIn(self.branch_sha[:7], result.stdout)
        self.assertIn("https://lab-staging.example.invalid", result.stdout)

    def test_it_writes_a_lease_naming_branch_sha_and_operator(self):
        self.run_script("feat/queue-board")
        lease = json.loads(self.lease.read_text())
        self.assertEqual("feat/queue-board", lease["branch"])
        self.assertEqual(self.branch_sha, lease["sha"])
        self.assertEqual("jstephens", lease["started_by"])
        self.assertIn("T", lease["started_at"])

    def test_a_branch_that_is_not_on_origin_is_refused(self):
        """Refused rather than created. A preview of a branch only this box has
        is a preview of something nobody can review."""
        result = self.run_script("feat/never-pushed")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("feat/never-pushed", result.stderr)
        self.assertFalse(self.restarted())
        self.assertFalse(self.lease.exists())


class TestTheLease(PreviewTestCase):
    def test_a_live_lease_is_refused_and_names_who_holds_it(self):
        self.write_lease(age_seconds=600, branch="feat/status-strip", by="vsto")
        result = self.run_script("feat/queue-board")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("feat/status-strip", result.stderr)
        self.assertIn("vsto", result.stderr)
        self.assertIn("--force", result.stderr)
        self.assertFalse(self.restarted())

    def test_a_refused_run_leaves_the_existing_preview_alone(self):
        """The refusal has to be total. Half-taking a preview - checking the
        branch out but leaving the lease - is worse than either outcome,
        because the banner would then name the wrong branch."""
        self.write_lease(age_seconds=600, branch="feat/status-strip")
        before = self.checked_out()
        self.run_script("feat/queue-board")
        self.assertEqual(before, self.checked_out())
        self.assertEqual("feat/status-strip", json.loads(self.lease.read_text())["branch"])

    def test_force_takes_a_live_lease(self):
        self.write_lease(age_seconds=600, branch="feat/status-strip")
        result = self.run_script("--force", "feat/queue-board")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("feat/queue-board", json.loads(self.lease.read_text())["branch"])
        self.assertTrue(self.restarted())

    def test_an_expired_lease_needs_no_force(self):
        """The unit exits after MAX_AGE, so a lease older than that is held by
        a preview that is no longer running. Demanding --force for it would
        train the operator to pass --force by reflex, which is the one habit
        that makes the lease worthless."""
        self.write_lease(age_seconds=MAX_AGE + 60, branch="feat/status-strip")
        result = self.run_script("feat/queue-board")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("feat/queue-board", json.loads(self.lease.read_text())["branch"])

    def test_an_unreadable_lease_does_not_wedge_the_script(self):
        """A half-written lease must not make the preview unstartable - that
        would need a human with a text editor to clear, at the moment they are
        trying to look at something."""
        self.lease.write_text("{not json")
        result = self.run_script("feat/queue-board")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("feat/queue-board", json.loads(self.lease.read_text())["branch"])


if __name__ == "__main__":
    unittest.main()
