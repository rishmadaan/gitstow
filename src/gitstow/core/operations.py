"""Shared bulk-operation layer — the one place that knows how to filter a
repo collection and run a worker across it with retries and bounded
concurrency. Consumed by the CLI (pull/fetch) and the MCP server so the
surfaces can't drift."""

from __future__ import annotations

import contextlib
import shutil
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


def move_repo(
    store: RepoStore, settings: Settings, key: str, from_ws: str, to_ws: str
) -> Repo:
    """Reassign a repo from one workspace to another.

    Moves the folder on disk to the target workspace's directory and updates
    the catalog to match. Validates everything first, then moves the folder,
    then writes the catalog. Raises ValueError with a human-readable message on
    any rule violation; on success returns the new Repo.
    """
    # 1. Resolve
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

    new_repo = Repo(
        owner=new_owner,
        name=src.name,
        remote_url=src.remote_url,
        workspace=to_ws,
        frozen=src.frozen,
        tags=list(dict.fromkeys(list(src.tags) + list(target.auto_tags))),
        added=src.added,
        last_pulled=src.last_pulled,
        last_fetched=src.last_fetched,
    )

    # 3. Collision checks (before touching anything)
    if store.get(new_repo.key, workspace=to_ws) is not None:
        raise ValueError(f"A repo '{new_repo.key}' already exists in workspace '{to_ws}'.")
    dest_path = new_repo.get_path(target.get_path())
    if dest_path.exists():
        raise ValueError(f"Destination path already exists: {dest_path}")

    # 4. Disk move — skip when the source folder is already missing.
    source = settings.get_workspace(from_ws)
    src_path = src.get_path(source.get_path()) if source else None
    if src_path and src_path.exists():
        if new_owner:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dest_path))
        if src.owner:
            # Remove the now-empty owner directory (ignore if not empty).
            with contextlib.suppress(OSError):
                src_path.parent.rmdir()

    # 5. Catalog update — single locked mutation.
    with store.bulk():
        store.remove(key, workspace=from_ws)
        store.add(new_repo)

    return new_repo
