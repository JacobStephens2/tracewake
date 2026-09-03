"""Tests for web/lab-preview.sh, the Attended Preview's front door.

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

SCRIPT = Path(__file__).resolve().parents[1] / "lab-preview.sh"

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

        self.relabels = root / "relabels"
        relabel = root / "fake-relabel.sh"
        relabel.write_text(f'#!/bin/bash\necho "$@" >> "{self.relabels}"\n')
        relabel.chmod(0o755)
        self.fake_relabel = relabel

    def run_script(self, *args, env=None):
        environ = {
            **os.environ,
            "LAB_PREVIEW_WORKTREE": str(self.worktree),
            "LAB_PREVIEW_LEASE": str(self.lease),
            "LAB_PREVIEW_RESTART_COMMAND": str(self.fake_restart),
            "LAB_PREVIEW_RELABEL_COMMAND": str(self.fake_relabel),
            "LAB_PREVIEW_URL": "https://lab-staging.example.invalid",
            "LAB_PREVIEW_MAX_AGE_SECONDS": str(MAX_AGE),
            "LAB_PREVIEW_OPERATOR": "jstephens",
            # No preview is running unless a test says one is.
            "LAB_PREVIEW_STATUS_COMMAND": "/bin/false",
            # ...and the one this run starts comes up, unless a test says it
            # does not. Separate from the command above on purpose: see the
            # script's comment on why "is one held" and "did mine come up" are
            # asked at different moments and cannot share an answer.
            "LAB_PREVIEW_READY_COMMAND": "/bin/true",
            "LAB_PREVIEW_READY_SETTLE_SECONDS": "0",
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

    def test_a_lease_whose_unit_has_stopped_does_not_claim_to_be_running(self):
        """Hit within minutes of provisioning: stop a preview and the next run
        answers "a preview is already running" over a unit in `inactive`. The
        refusal is right - the lease still names somebody, and it is theirs
        until it expires - but a script whose whole job is saying what is
        running must not say a thing is running when it can see that it is
        not, and forcing here displaces nobody.
        """
        self.write_lease(age_seconds=600, branch="feat/status-strip", by="vsto")
        result = self.run_script("feat/queue-board")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("already running", result.stderr)
        self.assertIn("feat/status-strip", result.stderr)
        self.assertIn("vsto", result.stderr)
        self.assertIn("--force", result.stderr)
        self.assertNotIn("does not know it", result.stderr)

    def test_a_lease_backed_by_a_running_unit_still_says_running(self):
        self.write_lease(age_seconds=600, branch="feat/status-strip", by="vsto")
        result = self.run_script(
            "feat/queue-board", env={"LAB_PREVIEW_STATUS_COMMAND": "/bin/true"}
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("already running", result.stderr)
        self.assertIn("does not know it", result.stderr)

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


class TestLivenessBacksTheLease(PreviewTestCase):
    """The lease says WHICH branch; the unit says WHETHER one is running.

    Reading the lease alone gets this wrong in the one case that matters: a
    lease that is corrupt or missing reads as free, so a preview somebody is
    actively looking at gets taken out from under them with no --force and no
    warning.
    """

    def test_a_running_preview_with_no_lease_is_still_a_running_preview(self):
        result = self.run_script(
            "feat/queue-board", env={"LAB_PREVIEW_STATUS_COMMAND": "/bin/true"}
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--force", result.stderr)
        self.assertFalse(self.restarted())

    def test_a_running_preview_with_an_unreadable_lease_is_refused(self):
        self.lease.write_text("{not json")
        result = self.run_script(
            "feat/queue-board", env={"LAB_PREVIEW_STATUS_COMMAND": "/bin/true"}
        )
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.restarted())

    def test_force_still_takes_a_running_preview(self):
        result = self.run_script(
            "--force", "feat/queue-board",
            env={"LAB_PREVIEW_STATUS_COMMAND": "/bin/true"},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.restarted())


class TestNothingHalfTaken(PreviewTestCase):
    """The tree and the lease must never disagree.

    A banner naming one branch over a checkout of another is the worst
    outcome available here: it is the failure mode the banner exists to
    prevent, wearing the banner's own authority.
    """

    def test_a_failing_restart_still_leaves_tree_and_lease_agreeing(self):
        import json
        failing = Path(self.tmp.name) / "failing-restart.sh"
        failing.write_text("#!/bin/bash\necho 'unit failed to start' >&2\nexit 1\n")
        failing.chmod(0o755)
        result = self.run_script(
            "feat/queue-board",
            env={"LAB_PREVIEW_RESTART_COMMAND": str(failing)},
        )
        self.assertNotEqual(0, result.returncode)
        lease = json.loads(self.lease.read_text())
        self.assertEqual(self.checked_out(), lease["sha"])
        self.assertEqual("feat/queue-board", lease["branch"])

    def test_a_lease_that_cannot_be_written_stops_before_the_checkout(self):
        """The lease is staged first on purpose. If it cannot be written, the
        tree must still be on whatever the previous preview was serving."""
        before = self.checked_out()
        result = self.run_script(
            "feat/queue-board",
            env={"LAB_PREVIEW_LEASE": "/proc/definitely/not/writable/lease.json"},
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.checked_out())
        self.assertFalse(self.restarted())


class TestItSaysStartedOnlyWhenItStarted(PreviewTestCase):
    """`systemctl restart` returning 0 does not mean the preview is up.

    The unit is Type=simple, so systemd calls the job done once it has
    exec'd - a process that dies immediately after (203/EXEC on a mislabelled
    venv, an import error in the branch being previewed) exits restart 0 all
    the same. Observed on the real box: the script printed "Attended Preview
    started" over a unit in `failed`, which is the one thing this script is
    supposed to make impossible - saying what is running when it is not.
    """

    def run_start_that_never_comes_up(self):
        return self.run_script(
            "feat/queue-board", env={"LAB_PREVIEW_READY_COMMAND": "/bin/false"}
        )

    def test_a_unit_that_does_not_come_up_is_not_reported_as_started(self):
        result = self.run_start_that_never_comes_up()
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Attended Preview started", result.stdout)
        self.assertTrue(self.restarted())

    def test_it_says_where_to_look_when_the_unit_did_not_come_up(self):
        result = self.run_start_that_never_comes_up()
        self.assertIn("journalctl", result.stderr)
        self.assertIn("lab-webapp-staging", result.stderr)

    def test_a_unit_that_dies_during_the_window_is_caught(self):
        """The property is that it STAYED up, not that it was up once. A
        readiness probe asked a single time cannot tell a live preview from
        one whose import error takes a second to raise."""
        counter = Path(self.tmp.name) / "probes"
        flaky = Path(self.tmp.name) / "up-then-down.sh"
        flaky.write_text(
            f'#!/bin/bash\necho . >> "{counter}"\n'
            f'[[ $(wc -c < "{counter}") -le 2 ]]\n'
        )
        flaky.chmod(0o755)

        result = self.run_script("feat/queue-board", env={
            "LAB_PREVIEW_READY_COMMAND": str(flaky),
            "LAB_PREVIEW_READY_SETTLE_SECONDS": "2",
        })
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("Attended Preview started", result.stdout)

    def test_the_lease_still_names_what_was_checked_out(self):
        """A failed start is not a half-taken preview: the tree and the lease
        agree, they just describe something that is not running."""
        self.run_start_that_never_comes_up()
        self.assertEqual(self.branch_sha, self.checked_out())
        self.assertEqual(self.branch_sha, json.loads(self.lease.read_text())["sha"])


class TestTheCheckoutIsRelabelled(PreviewTestCase):
    """git restores tracked files at the parent directory's default label.

    /srv is var_t, so every checkout hands the preview's own faked tracker and
    box scripts - the ones that keep a cycle inside this VM - back at var_t,
    undoing the relabel the role did once at provision time. Confirmed on the
    box: five preview-sources/*.sh were var_t immediately after the first
    preview started, with mtimes equal to the lease's started_at.
    """

    def test_it_relabels_the_tree_it_just_checked_out(self):
        result = self.run_script("feat/queue-board")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(str(self.worktree), self.relabels.read_text())

    def test_it_relabels_before_the_restart_not_after(self):
        """A unit started over var_t dies 203/EXEC, so relabelling after the
        restart fixes the tree for a preview that is already broken."""
        order = Path(self.tmp.name) / "order"
        steps = {}
        for name in ("relabel", "restart"):
            fake = Path(self.tmp.name) / f"ordered-{name}.sh"
            fake.write_text(f'#!/bin/bash\necho {name} >> "{order}"\n')
            fake.chmod(0o755)
            steps[name] = str(fake)

        result = self.run_script("feat/queue-board", env={
            "LAB_PREVIEW_RELABEL_COMMAND": steps["relabel"],
            "LAB_PREVIEW_RESTART_COMMAND": steps["restart"],
        })
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["relabel", "restart"], order.read_text().split())


if __name__ == "__main__":
    unittest.main()
