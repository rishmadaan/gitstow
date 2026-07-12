"""Shared bulk-operation layer — the one place that knows how to filter a
repo collection and run a worker across it with retries and bounded
concurrency. Consumed by the CLI (pull/fetch) and the MCP server so the
surfaces can't drift."""

from __future__ import annotations

from typing import Callable, Optional

from gitstow.core.config import Workspace
from gitstow.core.parallel import run_parallel_sync
from gitstow.core.repo import Repo

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
