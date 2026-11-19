# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one safety invariant every real-git pull test checks.

A pull either refuses, leaving HEAD, the stash list, the staged state and the
local files exactly as they were, or lands HEAD exactly on upstream with the
local files, staged state and stash list unchanged. Its reported outcome must
agree with what happened to HEAD.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(clone: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", *args], cwd=clone, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode().strip()


def snapshot(clone: Path, files: list[str]) -> dict:
    return {
        "head": _git(clone, "rev-parse", "HEAD"),
        "stash": _git(clone, "stash", "list"),
        "cached": _git(clone, "diff", "--cached", "--binary", binary=True),
        "bytes": {f: (clone / f).read_bytes() if (clone / f).exists() else None for f in files},
    }


def pull_problems(before: dict, after: dict, upstream: str, success: bool) -> list[str]:
    problems = []
    if after["bytes"] != before["bytes"]:
        problems.append("local file bytes changed")
    if after["stash"] != before["stash"]:
        problems.append("stash list changed")
    if after["cached"] != before["cached"]:
        problems.append("staged state changed")
    if success and after["head"] != upstream:
        problems.append("reported success but HEAD is not exactly at upstream")
    if not success and after["head"] != before["head"]:
        problems.append("reported failure/skip but HEAD moved")
    return problems


def check_pull(clone: Path, files: list[str], upstream: str, run) -> tuple[bool, str]:
    """Run `run() -> (success, message)` and assert the invariant; return its result."""
    before = snapshot(clone, files)
    success, message = run()
    after = snapshot(clone, files)
    problems = pull_problems(before, after, upstream, success)
    assert problems == [], (problems, message)
    return success, message
