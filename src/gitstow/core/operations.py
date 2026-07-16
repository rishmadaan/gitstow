"""Shared bulk-operation layer — the one place that knows how to filter a
repo collection and run a worker across it with retries and bounded
concurrency. Consumed by the CLI (pull/fetch) and the MCP server so the
surfaces can't drift."""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
from pathlib import Path
from typing import Callable, Optional

from gitstow.core.config import Settings, Workspace
from gitstow.core.parallel import run_parallel_sync
from gitstow.core.repo import Repo, RepoStore
from gitstow.core.url_parser import parse_git_url

Pair = tuple[Repo, Workspace]

#: worker statuses that qualify for a retry round
RETRYABLE = {"error", "missing"}


def filter_repo_pairs(
    pairs: list[Pair],
    *,
    tags: Optional[list[str]] = None,
    exclude_tags: Optional[list[str]] = None,
    owner: Optional[str] = None,
    frozen: Optional[bool] = None,
    query: Optional[str] = None,
) -> list[Pair]:
    """Apply the standard repo filters. frozen=None keeps all; True/False select."""
    out = pairs
    if tags:
        tag_set = set(tags)
        out = [(r, w) for r, w in out if tag_set.intersection(r.tags)]
    if exclude_tags:
        ex = set(exclude_tags)
        out = [(r, w) for r, w in out if not ex.intersection(r.tags)]
    if owner:
        out = [(r, w) for r, w in out if r.owner == owner]
    if frozen is not None:
        out = [(r, w) for r, w in out if r.frozen is frozen]
    if query:
        q = query.lower()
        out = [(r, w) for r, w in out if q in r.key.lower()]
    return out


def run_bulk(
    targets: list[Pair],
    worker: Callable[[Repo, Workspace], dict],
    *,
    parallel_limit: int = 6,
    retry: int = 0,
    on_attempt: Optional[Callable[[int, int], None]] = None,
    on_progress: Optional[Callable[[str, bool, str], None]] = None,
) -> list[dict]:
    """Run worker over every target with bounded concurrency and retries.

    worker returns a dict with a "status" key; statuses in RETRYABLE are
    re-run up to `retry` extra rounds. Each target contributes exactly one
    dict to the result (its final outcome), ordered by input order.
    """
    final: dict[str, dict] = {}
    remaining = list(targets)

    for attempt in range(1 + retry):
        if not remaining:
            break
        if attempt > 0 and on_attempt:
            on_attempt(attempt, len(remaining))

        tasks = [
            (repo.global_key, lambda r=repo, w=ws: worker(r, w))
            for repo, ws in remaining
        ]
        results = run_parallel_sync(tasks, max_concurrent=parallel_limit, on_progress=on_progress)

        next_round = []
        outcome_by_key = {}
        for task_result in results:
            if task_result.success and task_result.data:
                outcome_by_key[task_result.key] = task_result.data
            else:
                outcome_by_key[task_result.key] = {
                    "repo": task_result.key,
                    "status": "error",
                    "detail": task_result.error,
                }

        for repo, ws in remaining:
            outcome = outcome_by_key[repo.global_key]
            retryable = outcome.get("status") in RETRYABLE and attempt < retry
            if retryable:
                next_round.append((repo, ws))
            else:
                final[repo.global_key] = outcome
        remaining = next_round

    return [final[r.global_key] for r, _ in targets if r.global_key in final]


def _move_dir(src: Path, dst: Path) -> None:
    """Relocate a directory with no ambiguous partial states.

    Same filesystem: one atomic rename. Cross filesystem: copy, then delete
    the source — a failed copy removes the partial destination (the source is
    untouched); a failed source-delete keeps the complete destination and
    leaves the stale source behind rather than risk deleting the only good
    copy. shutil.move's combined fallback can't tell those two failures apart.
    """
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    try:
        shutil.copytree(src, dst, symlinks=True)
    except FileExistsError:
        # dst appeared concurrently — copytree didn't create it, so it isn't
        # ours to delete.
        raise
    except BaseException:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    shutil.rmtree(src, ignore_errors=True)


def move_repo(
    store: RepoStore, settings: Settings, key: str, from_ws: str, to_ws: str
) -> Repo:
    """Reassign a repo from one workspace to another.

    Moves the folder on disk to the target workspace's directory and updates
    the catalog to match. The whole operation — validation, disk move, catalog
    mutation — runs inside one locked bulk() block so a concurrent CLI/web
    operation can't slip between the collision checks and the mutation. Raises
    ValueError with a human-readable message on any rule violation; on success
    returns the new Repo. If the catalog write fails after the folder moved,
    the folder is moved back (best effort) before the error propagates.
    """
    moved_src: Path | None = None
    dest_path: Path | None = None
    try:
        with store.bulk():
            # 1. Resolve (against fresh state — bulk() reloads under the lock)
            src = store.get(key, workspace=from_ws)
            if src is None:
                raise ValueError(f"Repo '{key}' not found in workspace '{from_ws}'.")
            target = settings.get_workspace(to_ws)
            if target is None:
                raise ValueError(f"Workspace '{to_ws}' not found.")
            if to_ws == from_ws:
                raise ValueError(f"Repo '{key}' is already in workspace '{to_ws}'.")

            # 2. Re-key by target layout
            if target.layout == "flat":
                new_owner = ""
                # Nested keys (e.g. GitLab "group/subgroup/repo") parse as
                # name="subgroup/repo" — a flat key must be the basename only.
                new_name = src.name.rsplit("/", 1)[-1]
            else:
                new_owner = src.owner
                if not new_owner and src.remote_url:
                    with contextlib.suppress(ValueError):
                        new_owner = parse_git_url(
                            src.remote_url, default_host=settings.default_host
                        ).owner
                if not new_owner:
                    raise ValueError(
                        f"Cannot move '{key}' into structured workspace '{to_ws}': no owner "
                        f"is known and none can be parsed from the remote URL. A structured "
                        f"workspace files repos under owner/repo, so discovery would never "
                        f"find a bare folder."
                    )
                new_name = src.name

            new_repo = Repo(
                owner=new_owner,
                name=new_name,
                remote_url=src.remote_url,
                workspace=to_ws,
                frozen=src.frozen,
                tags=list(dict.fromkeys(list(src.tags) + list(target.auto_tags))),
                added=src.added,
                last_pulled=src.last_pulled,
                last_fetched=src.last_fetched,
            )

            # 3. Collision checks
            if store.get(new_repo.key, workspace=to_ws) is not None:
                raise ValueError(
                    f"A repo '{new_repo.key}' already exists in workspace '{to_ws}'."
                )
            dest_path = new_repo.get_path(target.get_path())
            if dest_path.exists():
                raise ValueError(f"Destination path already exists: {dest_path}")

            # 4. Disk move — skip when the source folder is already missing.
            source = settings.get_workspace(from_ws)
            if source is None:
                # Catalog entries can outlive their workspace (removed with
                # repos kept). Without the source path we can't move the
                # folder, and a silent catalog-only "move" would lie about it.
                raise ValueError(
                    f"Source workspace '{from_ws}' is no longer configured, so the "
                    f"folder's location is unknown. Re-add the workspace, or remove "
                    f"and re-add the repo in its new workspace."
                )
            src_path = src.get_path(source.get_path())
            if src_path.exists():
                if (src_path / ".git").is_file():
                    # A gitfile (linked git worktree): a plain rename breaks the
                    # main repo's worktree metadata and the repo dies on the
                    # next `git worktree prune`.
                    raise ValueError(
                        f"'{key}' is a linked git worktree (.git is a file). Move it "
                        f"with 'git worktree move' instead, then rescan both workspaces."
                    )
                # Always ensure the parent exists (the workspace root may not
                # have been created yet) — a missing parent would downgrade
                # the rename to a full cross-directory copy.
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                _move_dir(src_path, dest_path)
                moved_src = src_path
                if src.owner:
                    # Remove the now-empty owner directory (ignore if not empty).
                    with contextlib.suppress(OSError):
                        src_path.parent.rmdir()

            # 5. Catalog update — written on bulk() exit, still under the lock.
            store.remove(key, workspace=from_ws)
            store.add(new_repo)
    except BaseException:
        # Catalog write failed — or Ctrl-C landed — after the folder moved:
        # roll the folder back so disk and catalog stay consistent.
        if moved_src is not None and dest_path.exists() and not moved_src.exists():
            with contextlib.suppress(OSError, shutil.Error):
                moved_src.parent.mkdir(parents=True, exist_ok=True)
                _move_dir(dest_path, moved_src)
        raise

    return new_repo
