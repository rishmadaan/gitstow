"""Parallel execution — async operations with bounded concurrency.

Uses asyncio with a semaphore to prevent SSH connection storms
(gita's biggest complaint: 87 repos opens 87 SSH connections simultaneously).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass
class TaskResult:
    """Result of a single parallel task."""

    key: str                # Identifier (e.g., "owner/repo")
    success: bool
    data: Any = None        # The result value on success
    error: str = ""         # Error message on failure


async def _run_in_executor(
    func: Callable,
    *args,
    **kwargs,
) -> Any:
    """Run a blocking function in the default thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


async def run_parallel(
    tasks: list[tuple[str, Callable]],
    max_concurrent: int = 6,
    on_progress: Callable[[str, bool, str], None] | None = None,
) -> list[TaskResult]:
    """Run tasks concurrently with bounded concurrency.

    Args:
        tasks: List of (key, callable) pairs. Each callable takes no args.
        max_concurrent: Maximum concurrent operations (semaphore size).
        on_progress: Optional callback(key, success, message) called after each task.

    Returns:
        List of TaskResult in the same order as input tasks.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_task(key: str, func: Callable) -> TaskResult:
        async with semaphore:
            try:
                result = await _run_in_executor(func)
                task_result = TaskResult(key=key, success=True, data=result)
            except Exception as e:
                task_result = TaskResult(key=key, success=False, error=str(e))

            if on_progress:
                on_progress(
                    key,
                    task_result.success,
                    task_result.error or "OK",
                )
            return task_result

    gathered = await asyncio.gather(
        *[bounded_task(key, func) for key, func in tasks],
        return_exceptions=False,
    )
    return list(gathered)


def run_parallel_sync(
    tasks: list[tuple[str, Callable]],
    max_concurrent: int = 6,
    on_progress: Callable[[str, bool, str], None] | None = None,
) -> list[TaskResult]:
    """Synchronous wrapper for run_parallel.

    Use this from non-async CLI code.
    """
    return asyncio.run(run_parallel(tasks, max_concurrent, on_progress))
