"""Cross-process file lock — guards repos.yaml against concurrent CLI/web writes."""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path


class LockTimeout(Exception):
    """Raised when the lock cannot be acquired within the timeout."""


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0):
    """Hold an exclusive cross-process lock on lock_path for the block's duration."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        _acquire(handle, timeout, lock_path)
        yield
    finally:
        _release(handle)
        handle.close()


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
