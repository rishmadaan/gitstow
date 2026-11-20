# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cross-process file locks — repos.yaml writes, and one pull at a time per repo folder."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import time
from pathlib import Path

#: How long a pull waits for another gitstow pull of the same folder before refusing.
PULL_LOCK_WAIT = 3.0


class LockTimeout(Exception):
    """Raised when the lock cannot be acquired within the timeout."""


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0):
    """Hold an exclusive cross-process lock on lock_path for the block's duration."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as handle:
        try:
            _acquire(handle, timeout, lock_path)
            yield
        finally:
            _release(handle)


def repo_lock_path(repo_path: Path) -> Path:
    """The lock file for one repo folder: under ~/.gitstow/locks, never inside the user's .git.

    Keyed by the folder's resolved path, so every route to the same worktree (a
    symlinked workspace, a relative path) meets the same lock. Two worktrees of one
    repository are separate folders with their own checked-out branch: separate locks.
    """
    from gitstow.core import paths  # read at call time, so a test's patched home applies

    key = hashlib.sha256(os.fsencode(os.path.realpath(repo_path))).hexdigest()[:32]
    return paths.APP_HOME / "locks" / f"pull-{key}.lock"


@contextlib.contextmanager
def repo_lock(repo_path: Path, timeout: float | None = None):
    """Hold the pull lock of one repo folder. Raises LockTimeout if another pull holds it.

    Every gitstow pull path (fetching pull, exact pull) takes this lock across its
    checks and its merge. An OS lock (flock / msvcrt), released by the kernel if the
    holder dies, so a crashed pull never leaves the repo locked.
    """
    with file_lock(repo_lock_path(repo_path), timeout=PULL_LOCK_WAIT if timeout is None else timeout):
        yield


if sys.platform == "win32":
    import msvcrt

    def _acquire(handle, timeout: float, lock_path: Path) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"Could not lock {lock_path} within {timeout}s")
                time.sleep(0.05)

    def _release(handle) -> None:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _acquire(handle, timeout: float, lock_path: Path) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"Could not lock {lock_path} within {timeout}s")
                time.sleep(0.05)

    def _release(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
