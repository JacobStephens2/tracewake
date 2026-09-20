"""CD's git fetch as the instance user, after a shared git dir has
object shards the fetch user cannot write.

GitHub Actions run 35517678658 failed with:

    error: insufficient permission for adding an object to repository database .git/objects
    fatal: unpack-objects failed

Root-owned worktrees share /srv/tracewake/.git and leave shard directories
that unpack-objects cannot create files in. Root bypasses mode 0555, so
when this suite runs as root the fetch user is a non-root account; on a
normal CI runner the same condition is a shard with mode 0555.
"""
from __future__ import annotations

from pathlib import Path
import os
import pwd
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CD_UPDATE = ROOT / "deploy" / "cd-update.sh"


def _fetch_user() -> str | None:
    """Non-root uid to fetch as when the suite itself is root."""
    if os.geteuid() != 0:
        return None
    for name in ("conductor", "nobody"):
        try:
            pwd.getpwnam(name)
            return name
        except KeyError:
            continue
    pytest.skip("need a non-root user to reproduce object-store permission errors")


def _chown_r(path: Path, user: str) -> None:
    info = pwd.getpwnam(user)
    subprocess.run(
        ["chown", "-R", f"{info.pw_uid}:{info.pw_gid}", str(path)],
        check=True,
    )


def _git(
    cwd: Path, *args: str, check: bool = True, user: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(cwd),
        "GIT_AUTHOR_NAME": "cd-update test",
        "GIT_AUTHOR_EMAIL": "cd-update@example.test",
        "GIT_COMMITTER_NAME": "cd-update test",
        "GIT_COMMITTER_EMAIL": "cd-update@example.test",
    }
    cmd = [
        "git",
        "-c", "commit.gpgsign=false",
        "-c", "init.defaultBranch=main",
        "-c", "user.name=cd-update test",
        "-c", "user.email=cd-update@example.test",
        *args,
    ]
    if user:
        cmd = [
            "sudo", "-u", user, "env",
            f"HOME={cwd}",
            "GIT_AUTHOR_NAME=cd-update test",
            "GIT_AUTHOR_EMAIL=cd-update@example.test",
            "GIT_COMMITTER_NAME=cd-update test",
            "GIT_COMMITTER_EMAIL=cd-update@example.test",
            *cmd,
        ]
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def _behind_clone(tmp_path: Path, user: str | None) -> Path:
    """A checkout one commit behind its origin, so fetch unpacks new objects."""
    if user:
        # pytest's tmp dir is 0700 root; the fetch user must traverse it.
        for parent in (tmp_path, *tmp_path.parents):
            if parent in (Path("/"), Path("/tmp")):
                break
            parent.chmod(parent.stat().st_mode | 0o111)
        _chown_r(tmp_path, user)
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "--bare", str(remote), user=user)
    seed.mkdir()
    if user:
        _chown_r(seed, user)
    _git(seed, "init", user=user)
    readme = seed / "README"
    if user:
        subprocess.run(
            ["sudo", "-u", user, "tee", str(readme)],
            input="first\n",
            text=True,
            check=True,
            capture_output=True,
        )
    else:
        readme.write_text("first\n")
    _git(seed, "add", "README", user=user)
    _git(seed, "commit", "-m", "first", user=user)
    _git(seed, "remote", "add", "origin", str(remote), user=user)
    _git(seed, "push", "origin", "HEAD:main", user=user)
    _git(
        tmp_path,
        "clone",
        "--no-local",
        "--branch",
        "main",
        str(remote),
        str(checkout),
        user=user,
    )
    if user:
        subprocess.run(
            ["sudo", "-u", user, "tee", str(readme)],
            input="second\n",
            text=True,
            check=True,
            capture_output=True,
        )
    else:
        readme.write_text("second\n")
    _git(seed, "commit", "-am", "second", user=user)
    _git(seed, "push", "origin", "HEAD:main", user=user)
    return checkout


def _new_object_prefixes(seed: Path, checkout: Path, user: str | None) -> set[str]:
    listed = _git(seed, "rev-list", "--objects", "HEAD", user=user).stdout.splitlines()
    prefixes: set[str] = set()
    for line in listed:
        obj = line.split()[0]
        probe = _git(checkout, "cat-file", "-e", obj, check=False, user=user)
        if probe.returncode != 0:
            prefixes.add(obj[:2])
    assert prefixes, "fixture must have objects the checkout does not yet have"
    return prefixes


def _plant_unwritable_shards(checkout: Path, prefixes: set[str], user: str | None) -> None:
    objects = checkout / ".git" / "objects"
    for prefix in prefixes:
        shard = objects / prefix
        shard.mkdir(exist_ok=True)
        if user:
            # Host form: root-owned 0755, the fetch user cannot write.
            os.chown(shard, 0, 0)
            shard.chmod(0o755)
        else:
            shard.chmod(0o555)


def _repair_object_store(checkout: Path, user: str | None) -> None:
    """The two commands CD must run before fetch."""
    objects = checkout / ".git" / "objects"
    owner = user or pwd.getpwuid(os.getuid()).pw_name
    _chown_r(objects, owner)
    subprocess.run(
        ["find", str(objects), "-type", "d", "-exec", "chmod", "u+rwx", "{}", "+"],
        check=True,
    )


def _fetch(checkout: Path, user: str | None) -> subprocess.CompletedProcess[str]:
    return _git(checkout, "fetch", "--prune", "origin", "main", check=False, user=user)


def test_unwritable_object_shards_fail_fetch_with_the_cd_symptom(tmp_path):
    """The exact Actions failure: unpack-objects cannot write the new objects."""
    user = _fetch_user()
    checkout = _behind_clone(tmp_path, user)
    prefixes = _new_object_prefixes(tmp_path / "seed", checkout, user)
    _plant_unwritable_shards(checkout, prefixes, user)
    result = _fetch(checkout, user)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "insufficient permission for adding an object to repository database" in (
        result.stderr + result.stdout
    )


def test_repairing_object_store_writability_lets_fetch_proceed(tmp_path):
    """chown the object store to the fetch user and chmod directories u+rwx."""
    user = _fetch_user()
    checkout = _behind_clone(tmp_path, user)
    prefixes = _new_object_prefixes(tmp_path / "seed", checkout, user)
    _plant_unwritable_shards(checkout, prefixes, user)
    _repair_object_store(checkout, user)
    result = _fetch(checkout, user)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cd_update_repairs_object_store_before_fetch():
    """Root-owned worktrees share the product git dir. CD fetches as the
    instance user, so it must make the object store writable first - the
    fetch is the line Actions run 35517678658 died on.
    """
    script = CD_UPDATE.read_text()
    fetch_at = script.index('git -C "$TRACEWAKE_DIR" fetch --prune origin')
    before = script[:fetch_at]
    assert 'chown -R "$TRACEWAKE_USER:$TRACEWAKE_USER"' in before
    assert '"$TRACEWAKE_DIR/.git/objects"' in before
    assert "chmod u+rwx" in before
    assert "find" in before
