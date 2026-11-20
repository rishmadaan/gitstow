# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real-git repos for the incoming view: a bare upstream, a pusher, and a clone behind it.

Shared by tests/test_incoming.py and by the browser walk, so both look at the same shapes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

IDENT = ["-c", "user.name=Test Author", "-c", "user.email=t@example.invalid"]


def git(*args, cwd=None, check=True) -> str:
    r = subprocess.run(["git", *IDENT, *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str | bytes | None], author: str = "") -> str:
    """Write (str/bytes) or delete (None) files, commit them, return the full id."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    git("add", "-A", cwd=repo)
    extra = ["--author", f"{author} <a@example.invalid>"] if author else []
    git("commit", "-q", "-m", message, *extra, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def lines(n: int, tag: str) -> str:
    return "".join(f"{tag} line {i}\n" for i in range(n))


def make_upstream(root: Path, name: str = "repo") -> tuple[Path, Path]:
    """A bare upstream with one commit, and a pusher clone of it."""
    up = root / f"{name}.git"
    git("init", "-q", "--bare", "-b", "main", str(up))
    pusher = root / f"{name}-pusher"
    git("init", "-q", "-b", "main", str(pusher))
    git("remote", "add", "origin", str(up), cwd=pusher)
    commit(pusher, "Initial", {
        "README.md": "readme\n", "requirements.lock": lines(10, "req"),
        "docs/cli.md": lines(40, "doc"), "src/toc.py": lines(20, "toc"),
        "src/slug.py": lines(9, "slug"), ".gitignore": "secret.env\n",
        "scripts/build.sh": "#!/bin/sh\necho build\n", "docs/guide.md": lines(5, "guide"),
        "assets/logo.png": bytes(range(256)) * 10,
    })
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
    return up, pusher


def clone_into(up: Path, target: Path) -> Path:
    git("clone", "-q", str(up), str(target))
    return target


def push_three(pusher: Path) -> list[str]:
    """Three commits with a repeated edit: src/toc.py is changed by two of them, the
    second removing 2 of the lines the first added. Per-commit rows then add up to
    more file appearances and more lines than the update's own totals."""
    toc = (pusher / "src/toc.py").read_text()
    c1 = commit(pusher, "Pin tools and bump the lock file", {
        ".tool-versions": "python 3.12\nnode 22\n",
        "requirements.lock": lines(5, "req") + lines(5, "new-req"),
    }, author="Tobin Achterberg")
    added = "".join(f"draft {i}\n" for i in range(4))
    c2 = commit(pusher, "Add --draft flag to preview\n\nPreview only; build never publishes drafts.", {
        "docs/cli.md": lines(36, "doc") + lines(6, "draft-doc"),
        "src/toc.py": toc.replace("toc line 0\n", "") + added,
        "tests/test_draft.py": lines(38, "test"),
    }, author="Tobin Achterberg")
    c3 = commit(pusher, "Fix broken anchor links in the table of contents (#412)", {
        "src/toc.py": (pusher / "src/toc.py").read_text().replace("draft 0\ndraft 1\n", "")
        + lines(5, "anchor"),
        "src/slug.py": lines(7, "slug") + lines(4, "slug2"),
    }, author="Ines Varga")
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
    return [c1, c2, c3]


def push_shapes(pusher: Path) -> str:
    """A binary change, a pure rename, a mode change and a deletion, in one commit."""
    git("mv", "docs/guide.md", "docs/usage.md", cwd=pusher)
    (pusher / "scripts/build.sh").chmod(0o755)
    oid = commit(pusher, "Refresh logo, rename the guide, make build.sh executable", {
        "assets/logo.png": bytes(range(256)) * 14,
        "assets/new.webp": bytes(range(200, 256)) * 9 + b"\0",
    })
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=pusher)
    return oid


def snapshot(repo: Path, files: list[str]) -> dict:
    """What must not change when a pull refuses: HEAD, the stash list, staged state, file bytes."""
    return {
        "head": git("rev-parse", "HEAD", cwd=repo),
        "branch": git("symbolic-ref", "-q", "HEAD", cwd=repo, check=False),
        "stash": git("stash", "list", cwd=repo),
        "cached": git("diff", "--cached", "--binary", cwd=repo),
        "bytes": {f: (repo / f).read_bytes() if (repo / f).exists() else None for f in files},
    }
