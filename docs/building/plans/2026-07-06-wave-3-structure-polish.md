# Wave 3 — Structure & Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the duplicated bulk/filter logic into a `core/operations.py` layer shared by CLI and MCP, parallelize `add`/`search`, surface untracked and orphaned repos, fix import/export round-trips and `open`, move the web stack to a `[ui]` extra, and close out CI/docs/release-process gaps — completing the audit.

**Architecture:** `core/operations.py` gets `filter_repo_pairs()` and `run_bulk()` (the retry+progress loop currently duplicated in pull/fetch); `cli/pull.py`, `cli/fetch.py`, and the MCP server consume them. `add` becomes parse → classify → parallel-clone → register. Discovery gains a cheap no-remote mode for reconciliation hints. Packaging: fastapi/uvicorn/jinja2/python-multipart move to `[project.optional-dependencies].ui` (0.3.0).

**Tech Stack:** Python, `core/parallel.py`, Typer, Hatchling/PEP 621 extras, GitHub Actions.

## Global Constraints

- **Prerequisite: Waves 1 and 2 merged** (status model, locking, bulk writes all exist).
- Product decision (locked 2026-07-06): web stack moves to a `[ui]` extra; ships in 0.3.0 together with Wave 2's TUI removal.
- JSON compatibility: `list --json` and `status --json` stay ARRAYS (no shape change); reconciliation hints are human-output only — `doctor --json` remains the machine source of truth for orphans/untracked.
- Existing `pull`/`fetch` JSON payload shapes must survive the operations-layer refactor byte-compatible (keys and statuses unchanged).
- All tests green + `ruff check src/` clean before each commit; no release mid-wave — 0.3.0 at the end via `scripts/release.sh`.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Audit coverage matrix

| Audit ID | Task |
|----------|------|
| P1 + E3 (parallel add) + P2 (remote-mismatch) | Task 1 |
| E4 (parallel search) | Task 2 |
| A4 (operations layer) | Task 3 |
| A3 + DOC3 (MCP dedup, last_fetched) | Task 4 |
| P3 (reconciliation hints) | Task 5 |
| B7 (import workspace + loud version error) | Task 6 |
| B8 (orphaned-workspace detection) | Task 7 |
| B9 (open editor logic) | Task 8 |
| A2 (ui extra) | Task 9 |
| E5 (disk size via du) | Task 10 |
| E6 (configurable clone timeout) | Task 10 |
| D2 (macOS CI), D3 (changelog gate), D4 (typer extra), D5 (fetch/MCP tests) | Task 11 |
| DOC1, DOC2 (docs sync) | Task 12 |
| P6 (changelog discipline — enforced by D3) | Task 11 |

---

### Task 1: Parallel `add` with remote-mismatch detection (P1, P2, E3)

**Files:**
- Modify: `src/gitstow/cli/add.py` (main loop → classify/clone/register phases)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_parallel_sync` from `core/parallel.py`; `parse_git_url`.
- Produces: multi-URL `add` clones concurrently (semaphore = `parallel_limit`); an existing untracked dir whose remote differs from the requested URL yields `{"status": "error", "error": "remote mismatch: ..."}` instead of silent registration. Result dict statuses unchanged otherwise (`cloned|registered|exists|updated|error`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestAddParallelAndConflicts:
    def _setup(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        return ws_dir

    def test_multiple_clones_run_concurrently(self, tmp_path, monkeypatch):
        import threading, time
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._setup(tmp_path, monkeypatch)
        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_clone(url, target, **kw):
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            (target / ".git").mkdir(parents=True)
            with lock:
                concurrent["now"] -= 1
            return True, ""

        with patch("gitstow.cli.add.git_clone", side_effect=slow_clone):
            result = CliRunner().invoke(app, ["add", "a/one", "b/two", "c/three", "--quiet"])

        assert result.exit_code == 0
        assert concurrent["max"] >= 2  # sequential implementation never exceeds 1

    def test_remote_mismatch_errors_instead_of_silent_register(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        ws_dir = self._setup(tmp_path, monkeypatch)
        # On-disk untracked repo whose remote is a DIFFERENT project
        target = ws_dir / "owner" / "repo"
        (target / ".git").mkdir(parents=True)

        with patch("gitstow.cli.add.get_remote_url", return_value="https://github.com/someone-else/other.git"):
            result = CliRunner().invoke(app, ["add", "owner/repo", "--json"])

        payload = json.loads(result.output)
        assert result.exit_code == 1
        assert payload["results"][0]["status"] == "error"
        assert "mismatch" in payload["results"][0]["error"]
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py::TestAddParallelAndConflicts -v`
Expected: concurrency test FAILS (max stays 1); mismatch test FAILS (status is `registered` today).

- [ ] **Step 3: Implement**

Restructure `src/gitstow/cli/add.py`. Add imports:

```python
from gitstow.core.parallel import run_parallel_sync
```

Add the remote-comparison helper:

```python
def _same_repo(url_a: str, url_b: str, default_host: str) -> bool:
    """Whether two remote URLs point at the same host/owner/repo (protocol-agnostic)."""
    try:
        pa = parse_git_url(url_a, default_host=default_host)
        pb = parse_git_url(url_b, default_host=default_host)
    except ValueError:
        return False
    return (pa.host, pa.owner, pa.repo) == (pb.host, pb.owner, pb.repo)
```

Replace the sequential `for parsed in parsed_urls:` body with three phases:

```python
    # Phase 1 — classify every target without touching the network
    to_clone: list = []      # (parsed, target, repo_owner, repo_key)
    results = []

    for parsed in parsed_urls:
        if ws.layout == "flat":
            target = root / parsed.repo
            repo_owner = ""
        else:
            target = root / parsed.owner / parsed.repo
            repo_owner = parsed.owner
        repo_key = f"{repo_owner}/{parsed.repo}" if repo_owner else parsed.repo

        existing = store.get(repo_key, workspace=ws.label)
        if existing:
            if update:
                if not quiet:
                    console.print(f"  [dim]Updating[/dim] {repo_key}...")
                from gitstow.core.git import pull as git_pull
                pull_result = git_pull(target)
                if pull_result.success:
                    from datetime import datetime
                    store.update(repo_key, workspace=ws.label, last_pulled=datetime.now().isoformat())
                    results.append({"repo": repo_key, "status": "updated"})
                    if not quiet:
                        console.print(f"  [green]✓[/green] {repo_key} updated")
                else:
                    results.append({"repo": repo_key, "status": "error", "error": pull_result.error})
                    if not quiet:
                        err_console.print(f"  [red]✗[/red] {repo_key}: {pull_result.error}")
            else:
                results.append({"repo": repo_key, "status": "exists"})
                if not quiet:
                    console.print(f"  [yellow]○[/yellow] {repo_key} already tracked. Use --update to pull.")
            continue

        if target.exists() and is_git_repo(target):
            remote = get_remote_url(target)
            if remote and not _same_repo(remote, parsed.clone_url, settings.default_host):
                # Plan said: different remote → error, explain conflict. Now it does.
                results.append({
                    "repo": repo_key,
                    "status": "error",
                    "error": (
                        f"remote mismatch: directory exists with remote {remote}, "
                        f"but you asked for {parsed.clone_url}. Move the directory or pick another workspace."
                    ),
                })
                if not quiet:
                    err_console.print(f"  [red]✗[/red] {repo_key}: remote mismatch ({remote})")
                continue
            if remote:
                repo = Repo(owner=repo_owner, name=parsed.repo, remote_url=remote,
                            workspace=ws.label, tags=list(tags))
                store.add(repo)
                results.append({"repo": repo_key, "status": "registered"})
                if not quiet:
                    console.print(f"  [green]✓[/green] {repo_key} registered (already on disk)")
                continue

        if target.exists() and not is_git_repo(target):
            results.append({"repo": repo_key, "status": "error", "error": "Path exists but is not a git repo"})
            if not quiet:
                err_console.print(f"  [red]✗[/red] {repo_key}: path exists but is not a git repo")
            continue

        to_clone.append((parsed, target, repo_owner, repo_key))

    # Phase 2 — clone concurrently (semaphore = parallel_limit), retry inside the worker
    def _clone_worker(parsed, target, repo_key):
        target.parent.mkdir(parents=True, exist_ok=True)
        success, error = False, ""
        for attempt in range(1 + retry):
            if attempt > 0:
                import shutil
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
            success, error = git_clone(
                url=parsed.clone_url, target=target,
                shallow=shallow, branch=branch, recursive=recursive,
            )
            if success:
                break
        return {"repo": repo_key, "success": success, "error": error}

    if to_clone:
        if not quiet:
            console.print(f"  [dim]Cloning {len(to_clone)} repo{'s' if len(to_clone) != 1 else ''} → {ws.label}...[/dim]")
        tasks = [
            (key, lambda p=parsed, t=target, k=key: _clone_worker(p, t, k))
            for parsed, target, _owner, key in to_clone
        ]
        clone_results = run_parallel_sync(tasks, max_concurrent=settings.parallel_limit)
        outcome_by_key = {
            r.key: (r.data if r.success else {"repo": r.key, "success": False, "error": r.error})
            for r in clone_results
        }

        # Phase 3 — register successes, report failures (store writes stay on the main thread)
        from datetime import datetime
        for parsed, target, repo_owner, repo_key in to_clone:
            outcome = outcome_by_key[repo_key]
            if outcome["success"]:
                repo = Repo(
                    owner=repo_owner, name=parsed.repo, remote_url=parsed.clone_url,
                    workspace=ws.label, tags=list(tags),
                    last_pulled=datetime.now().isoformat(),
                )
                store.add(repo)
                results.append({"repo": repo_key, "status": "cloned"})
                if not quiet:
                    console.print(f"  [green]✓[/green] {repo_key} cloned")
            else:
                results.append({"repo": repo_key, "status": "error", "error": outcome["error"]})
                if not quiet:
                    err_console.print(f"  [red]✗[/red] {repo_key}: {outcome['error']}")
                    hint = _clone_error_hint(outcome["error"])
                    if hint:
                        err_console.print(f"      [dim]{hint}[/dim]")
```

(The summary/JSON block below this is unchanged.)

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py -v && pytest -q`

```bash
git add src/gitstow/cli/add.py tests/test_cli.py
git commit -m "feat: parallel clones on add + remote-mismatch conflict detection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Parallel `search` (E4)

**Files:**
- Modify: `src/gitstow/cli/search.py` (per-repo loop → `run_parallel_sync`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_parallel_sync`.
- Produces: identical output/JSON, deterministic repo ordering (sorted by key after the parallel gather).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestSearchParallel:
    def test_searches_run_concurrently(self, tmp_path, monkeypatch):
        import threading, time
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        store = RepoStore(path=repos_file)
        for i in range(4):
            (ws_dir / "o" / f"r{i}").mkdir(parents=True)
            store.add(Repo(owner="o", name=f"r{i}", remote_url="u", workspace="ws"))

        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_search(path, *a, **kw):
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            with lock:
                concurrent["now"] -= 1
            return [{"file": "x.py", "line_number": "1", "text": "hit"}]

        with patch("gitstow.cli.search._search_repo", side_effect=slow_search):
            result = CliRunner().invoke(app, ["search", "hit", "--quiet"])

        assert result.exit_code == 0
        assert concurrent["max"] >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestSearchParallel -v`
Expected: FAIL (`max == 1`).

- [ ] **Step 3: Implement**

In `src/gitstow/cli/search.py`, add `from gitstow.core.parallel import run_parallel_sync` and replace the sequential loop:

```python
    use_rg = _has_ripgrep()

    searchable = [
        (repo, ws) for repo, ws in repo_ws_pairs
        if repo.get_path(ws.get_path()).exists()
    ]

    tasks = [
        (
            repo.global_key,
            lambda r=repo, w=ws: _search_repo(
                r.get_path(w.get_path()), pattern, use_rg,
                glob_filter=glob_filter, case_insensitive=case_insensitive,
                files_only=files_only, max_results=max_results,
            ),
        )
        for repo, ws in searchable
    ]
    task_results = run_parallel_sync(tasks, max_concurrent=settings.parallel_limit)
    matches_by_key = {r.key: (r.data or []) for r in task_results if r.success}

    all_results = []
    total_matches = 0
    for repo, _ws in sorted(searchable, key=lambda p: p[0].global_key):
        matches = matches_by_key.get(repo.global_key, [])
        if not matches:
            continue
        total_matches += len(matches)
        all_results.append({"repo": repo.key, "matches": matches, "count": len(matches)})

        if not output_json and not quiet:
            console.print(f"\n  [bold]{repo.key}[/bold] [dim]({len(matches)} matches)[/dim]")
            for match in matches:
                if files_only:
                    console.print(f"    {match['file']}")
                else:
                    console.print(f"    [dim]{match['file']}:{match.get('line_number', '')}:[/dim] {match.get('text', '')}")
```

(The JSON/summary tail is unchanged.)

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/cli/search.py tests/test_cli.py
git commit -m "perf: search repos in parallel

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `core/operations.py` — shared filters + bulk runner (A4)

**Files:**
- Create: `src/gitstow/core/operations.py`
- Modify: `src/gitstow/cli/pull.py`, `src/gitstow/cli/fetch.py` (consume it)
- Test: `tests/test_operations.py` (new)

**Interfaces:**
- Consumes: `run_parallel_sync`, `TaskResult` from `core/parallel.py`.
- Produces (MCP in Task 4 also consumes these):
  - `filter_repo_pairs(pairs, *, tags=None, exclude_tags=None, owner=None, frozen=None, query=None) -> list` — pairs are `(Repo, Workspace)`; `frozen=True` keeps only frozen, `frozen=False` drops frozen, `None` keeps all.
  - `run_bulk(targets, worker, *, parallel_limit=6, retry=0, on_attempt=None, on_progress=None) -> list[dict]` — `targets` is `list[(Repo, Workspace)]`; `worker(repo, ws) -> dict` with a `"status"` key; statuses in `RETRYABLE = {"error", "missing"}` re-run up to `retry` times; returns one dict per target (retried targets appear once, with their final outcome). `on_attempt(attempt, remaining_count)` fires at the start of retry rounds; `on_progress(key, success, message)` per completion.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_operations.py`:

```python
"""Tests for the shared bulk-operation layer."""

from gitstow.core.config import Workspace
from gitstow.core.operations import filter_repo_pairs, run_bulk
from gitstow.core.repo import Repo


def _pair(name, *, tags=(), owner="o", frozen=False, ws_label="ws"):
    repo = Repo(owner=owner, name=name, remote_url="u", workspace=ws_label,
                frozen=frozen, tags=list(tags))
    return repo, Workspace(path="/tmp/x", label=ws_label, layout="structured")


class TestFilterRepoPairs:
    def test_tag_filter(self):
        pairs = [_pair("a", tags=["ai"]), _pair("b", tags=["web"])]
        out = filter_repo_pairs(pairs, tags=["ai"])
        assert [r.name for r, _ in out] == ["a"]

    def test_exclude_tag(self):
        pairs = [_pair("a", tags=["stale"]), _pair("b")]
        out = filter_repo_pairs(pairs, exclude_tags=["stale"])
        assert [r.name for r, _ in out] == ["b"]

    def test_owner_and_frozen(self):
        pairs = [_pair("a", owner="x", frozen=True), _pair("b", owner="x"), _pair("c", owner="y")]
        assert [r.name for r, _ in filter_repo_pairs(pairs, owner="x")] == ["a", "b"]
        assert [r.name for r, _ in filter_repo_pairs(pairs, frozen=False)] == ["b", "c"]
        assert [r.name for r, _ in filter_repo_pairs(pairs, frozen=True)] == ["a"]

    def test_query_substring(self):
        pairs = [_pair("gitstow"), _pair("other")]
        assert [r.name for r, _ in filter_repo_pairs(pairs, query="stow")] == ["gitstow"]


class TestRunBulk:
    def test_results_in_order_with_status(self):
        pairs = [_pair("a"), _pair("b")]
        results = run_bulk(pairs, lambda r, w: {"repo": r.key, "status": "ok"}, parallel_limit=2)
        assert [r["repo"] for r in results] == ["o/a", "o/b"]

    def test_retry_reruns_only_failures(self):
        pairs = [_pair("good"), _pair("flaky")]
        attempts = {"flaky": 0}

        def worker(repo, ws):
            if repo.name == "flaky":
                attempts["flaky"] += 1
                if attempts["flaky"] == 1:
                    return {"repo": repo.key, "status": "error", "detail": "boom"}
            return {"repo": repo.key, "status": "ok"}

        results = run_bulk(pairs, worker, retry=1)
        assert attempts["flaky"] == 2
        by_repo = {r["repo"]: r["status"] for r in results}
        assert by_repo == {"o/good": "ok", "o/flaky": "ok"}
        assert len(results) == 2  # retried target appears once

    def test_exception_becomes_error_result(self):
        pairs = [_pair("boom")]

        def worker(repo, ws):
            raise RuntimeError("kaput")

        results = run_bulk(pairs, worker)
        assert results[0]["status"] == "error"
        assert "kaput" in results[0]["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_operations.py -v`
Expected: FAIL with `ModuleNotFoundError: gitstow.core.operations`.

- [ ] **Step 3: Implement**

Create `src/gitstow/core/operations.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_operations.py -v`
Expected: all PASS.

- [ ] **Step 5: Refactor pull.py onto the layer**

In `src/gitstow/cli/pull.py`, replace the filter block and retry loop. Imports:

```python
from gitstow.core.operations import filter_repo_pairs, run_bulk
```

Filters (replacing the four inline `if tag / exclude_tag / owner` blocks — frozen bookkeeping from Wave 1 Task 6 stays):

```python
    if not include_frozen:
        frozen_repos = [r for r, _ in targets if r.frozen]
        targets = filter_repo_pairs(targets, frozen=False)
    else:
        frozen_repos = []
    targets = filter_repo_pairs(targets, tags=tag, exclude_tags=exclude_tag, owner=owner)
```

The whole `for attempt in range(1 + retry):` block collapses to:

```python
    progress_count = [0]

    def _on_progress(key: str, success: bool, message: str) -> None:
        progress_count[0] += 1
        console.print(
            f"  [{progress_count[0]}/{len(targets)}] {key.split(':', 1)[-1]}",
            end="\r", highlight=False,
        )

    def _on_attempt(attempt: int, remaining: int) -> None:
        console.print(f"\n  [dim]Retry {attempt}/{retry} — {remaining} failed repos...[/dim]\n")

    result_dicts = run_bulk(
        targets,
        _pull_one_repo,
        parallel_limit=settings.parallel_limit,
        retry=retry,
        on_attempt=None if quiet else _on_attempt,
        on_progress=None if quiet else _on_progress,
    )

    # Stamp successful pulls in one locked write. run_bulk returns one outcome
    # per target IN TARGET ORDER, so zip is the collision-safe pairing (matching
    # on outcome["repo"] would confuse same-named repos in two workspaces).
    now_iso = datetime.now().isoformat()
    with store.bulk():
        for (repo, _ws), outcome in zip(targets, result_dicts):
            if outcome["status"] in ("pulled", "up_to_date"):
                store.update(repo.key, workspace=repo.workspace, last_pulled=now_iso)
```

Note: `_pull_one_repo(repo, ws)` already has the right worker signature. Keep the frozen-rows append, output table, JSON payload, and exit-code logic exactly as they are — the JSON shape must not change (verify against the existing tests).

- [ ] **Step 6: Refactor fetch.py the same way**

Same transformation in `src/gitstow/cli/fetch.py`: `filter_repo_pairs(targets, tags=tag, exclude_tags=exclude_tag, owner=owner)` (no frozen filter — fetch includes frozen by design), `run_bulk(targets, _fetch_one_repo, ...)`, single `store.bulk()` stamping loop for `last_fetched`. Output/JSON tail unchanged.

- [ ] **Step 7: Run full suite (pull/fetch behavior must be identical), commit**

Run: `pytest -q && ruff check src/`

```bash
git add src/gitstow/core/operations.py src/gitstow/cli/pull.py src/gitstow/cli/fetch.py tests/test_operations.py
git commit -m "refactor: shared filter + bulk-runner layer consumed by pull and fetch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: MCP server consumes shared core + `last_fetched` everywhere (A3, DOC3)

**Files:**
- Modify: `src/gitstow/mcp/server.py` (list/pull/add tools), `src/gitstow/cli/list_cmd.py` (JSON payload)
- Test: `tests/test_mcp.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `filter_repo_pairs`, `run_bulk` (Task 3); `classify` (Wave 2).
- Produces: `list --json` and MCP `list_repos` both include `last_fetched`; MCP `pull_repos` delegates to `run_bulk` with the same worker semantics as the CLI; new MCP tool `fetch_repos`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestListJsonLastFetched:
    def test_list_json_includes_last_fetched(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
        RepoStore(path=repos_file).add(
            Repo(owner="", name="x", remote_url="u", workspace="ws", last_fetched="2026-07-01T00:00:00")
        )

        result = CliRunner().invoke(app, ["list", "--json"])
        payload = json.loads(result.output)
        assert payload[0]["last_fetched"] == "2026-07-01T00:00:00"
```

Create `tests/test_mcp.py`:

```python
"""Smoke tests for the MCP server's tool functions (called directly —
transport behavior belongs to the mcp library, not us)."""

import json

import pytest

pytest.importorskip("mcp")


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    repos_file = tmp_path / "repos.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
    monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
    return tmp_path


def test_list_repos_includes_last_fetched(isolated):
    from gitstow.core.config import Settings, Workspace, save_config
    from gitstow.core.repo import Repo, RepoStore
    from gitstow.mcp.server import list_repos

    ws_dir = isolated / "ws"; ws_dir.mkdir()
    save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
    RepoStore().add(Repo(owner="", name="x", remote_url="u", workspace="ws",
                         last_fetched="2026-07-01T00:00:00"))

    payload = json.loads(list_repos())
    assert payload[0]["last_fetched"] == "2026-07-01T00:00:00"
```

(Note: FastMCP's `@mcp.tool()` returns the original function, so tools are directly callable in tests.)

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py::TestListJsonLastFetched tests/test_mcp.py -v`
Expected: both FAIL with `KeyError: 'last_fetched'` (test_mcp skips if `mcp` isn't installed — install dev extras per Task 11 first if needed: `uv pip install -e ".[dev,mcp]" --python .venv/bin/python`).

- [ ] **Step 3: Implement**

`src/gitstow/cli/list_cmd.py` — add to the JSON dict (after `"last_pulled"`):

```python
                    "last_fetched": r.last_fetched,
```

`src/gitstow/mcp/server.py`:
1. Add `"last_fetched": r.last_fetched,` to the `list_repos` payload (and to the repo-info tool's payload — locate the `@mcp.tool()` whose dict includes `last_pulled` and mirror it).
2. Refactor `pull_repos` to build `(Repo, Workspace)` pairs, apply `filter_repo_pairs`, and call `run_bulk` with a worker matching `cli/pull.py`'s `_pull_one_repo` semantics (import it: `from gitstow.cli.pull import _pull_one_repo`), then stamp `last_pulled` inside `store.bulk()`.
3. Add a `fetch_repos` tool mirroring `pull_repos` but using `from gitstow.cli.fetch import _fetch_one_repo` and stamping `last_fetched`:

```python
@mcp.tool()
def fetch_repos(
    tag: Optional[str] = None,
    owner: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Fetch all remotes (git fetch --all --prune) without merging — updates
    ahead/behind counts. Includes frozen repos (fetch is non-destructive).

    Returns: JSON summary {total, fetched, errors, results}.
    """
    from datetime import datetime

    from gitstow.cli.fetch import _fetch_one_repo
    from gitstow.core.operations import filter_repo_pairs, run_bulk

    settings, store = _get_settings_and_store()
    pairs = []
    for r in store.list_all():
        ws = settings.get_workspace(r.workspace)
        if ws and (not workspace or r.workspace == workspace):
            pairs.append((r, ws))
    pairs = filter_repo_pairs(pairs, tags=[tag] if tag else None, owner=owner)

    results = run_bulk(pairs, _fetch_one_repo, parallel_limit=settings.parallel_limit)

    now = datetime.now().isoformat()
    with store.bulk():
        for outcome, (r, _ws) in zip(results, pairs):
            if outcome.get("status") == "fetched":
                store.update(r.key, workspace=r.workspace, last_fetched=now)

    fetched = sum(1 for r in results if r["status"] == "fetched")
    errors = sum(1 for r in results if r["status"] in ("error", "missing"))
    return json.dumps({"total": len(results), "fetched": fetched, "errors": errors, "results": results}, indent=2)
```

(Implementation note for the executor: `_pull_one_repo`/`_fetch_one_repo` importing from `cli/` into `mcp/` is acceptable coupling for now — the workers are pure functions over `(Repo, Workspace)`. If ruff flags an import cycle, move the two workers into `core/operations.py` and import them in both places; keep their exact bodies.)

Update the tool-count references: `docs/user/commands.md` "MCP Tools (12)" → count the real number after adding `fetch_repos` and correct the heading; `CLAUDE.md` mentions "13 tools + 3 resources" → recount.

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_mcp.py tests/test_cli.py -v && pytest -q`

```bash
git add src/gitstow/mcp/server.py src/gitstow/cli/list_cmd.py tests/test_mcp.py tests/test_cli.py docs/user/commands.md CLAUDE.md
git commit -m "refactor: MCP rides the shared operations layer; last_fetched exposed in all JSON

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Reconciliation hints on list/status (P3)

**Files:**
- Modify: `src/gitstow/core/discovery.py` (`include_remotes` param), `src/gitstow/cli/list_cmd.py`, `src/gitstow/cli/status.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `discover_repos(root, layout)` — gains keyword `include_remotes: bool = True`; `False` skips the per-repo `get_remote_url` subprocess (cheap walk).
- Produces: human-mode `list` and `status` end with a hint line when untracked repos exist on disk: `⚠ N untracked repos in <ws> — run 'gitstow workspace scan <ws>'`. JSON output UNCHANGED (arrays stay arrays; `doctor --json` remains the machine source).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestReconciliationHints:
    def test_list_hints_untracked_repos(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "tracked" / ".git").mkdir(parents=True)
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="tracked", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["list"])
        assert "1 untracked" in result.output
        assert "workspace scan" in result.output

    def test_list_json_shape_unchanged(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "b" / "untracked" / ".git").mkdir(parents=True)
        (ws_dir / "a" / "tracked").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="tracked", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["list", "--json"])
        payload = json.loads(result.output)
        assert isinstance(payload, list)  # still a bare array — no shape change
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py::TestReconciliationHints -v`
Expected: hint test FAILS; shape test passes (guard).

- [ ] **Step 3: Implement**

`src/gitstow/core/discovery.py` — thread the flag through:

```python
def discover_repos(root: Path, layout: str = "structured", include_remotes: bool = True) -> list[DiscoveredRepo]:
    ...
    if layout == "flat":
        return _discover_flat(root, include_remotes)
    return _discover_structured(root, include_remotes)
```

In both `_discover_structured(root, include_remotes)` and `_discover_flat(root, include_remotes)` replace `remote = get_remote_url(repo_dir)` with:

```python
                remote = get_remote_url(repo_dir) if include_remotes else None
```

Add a shared hint helper in `src/gitstow/cli/helpers.py`:

```python
def print_untracked_hint(settings: Settings, store: RepoStore, workspace_label: str | None = None) -> None:
    """Human-mode footer: point at untracked repos on disk (cheap walk, no git calls)."""
    from gitstow.core.discovery import discover_repos

    for ws in resolve_workspaces(settings, workspace_label):
        root = ws.get_path()
        if not root.is_dir():
            continue
        on_disk = {d.key for d in discover_repos(root, layout=ws.layout, include_remotes=False)}
        tracked = {r.key for r in store.list_by_workspace(ws.label)}
        untracked = on_disk - tracked
        if untracked:
            err_console.print(
                f"  [yellow]⚠ {len(untracked)} untracked repo{'s' if len(untracked) != 1 else ''} "
                f"in [bold]{ws.label}[/bold][/yellow] — run [bold]gitstow workspace scan {ws.label}[/bold]"
            )
```

Call it at the end of the human paths only:
- `list_cmd.py` — at the end of `list_repos` after the grouped printing (NOT in the `--json` or `--quiet` early-return paths):

```python
    from gitstow.cli.helpers import print_untracked_hint
    print_untracked_hint(settings, store, ws_label)
```
- `status.py` — same call after the summary line (again, human path only).

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/core/discovery.py src/gitstow/cli/helpers.py src/gitstow/cli/list_cmd.py src/gitstow/cli/status.py tests/test_cli.py
git commit -m "feat: list/status surface untracked repos on disk (plan P3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Import preserves workspaces + loud version errors (B7)

**Files:**
- Modify: `src/gitstow/cli/export_cmd.py` (`import_collection`, `_parse_import_file`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `_parse_import_file` raises `ValueError("collection file version N is newer than supported 1 — run 'gitstow update'")` instead of a silent `typer.Exit`; import routes each entry to its recorded `workspace` when that label is configured, else falls back to the `-w`/default workspace with a printed note. Entries keep `{"key", "url", "tags", "frozen", "workspace"}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestImportRoundTrip:
    def _two_ws(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(a), label="a", layout="flat"),
            Workspace(path=str(b), label="b", layout="flat"),
        ]))
        return repos_file

    def test_import_honors_recorded_workspace(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        repos_file = self._two_ws(tmp_path, monkeypatch)
        f = tmp_path / "coll.yaml"
        f.write_text(
            "version: 1\n"
            "repos:\n"
            "  one:\n    remote_url: https://github.com/x/one.git\n    workspace: a\n"
            "  two:\n    remote_url: https://github.com/x/two.git\n    workspace: b\n"
        )

        def fake_clone(url, target, **kw):
            (target / ".git").mkdir(parents=True)
            return True, ""

        with patch("gitstow.cli.export_cmd.git_clone", side_effect=fake_clone):
            result = CliRunner().invoke(app, ["collection", "import", str(f)])

        assert result.exit_code == 0
        store = RepoStore(path=repos_file)
        assert store.get("one", workspace="a") is not None
        assert store.get("two", workspace="b") is not None

    def test_newer_version_fails_loudly(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._two_ws(tmp_path, monkeypatch)
        f = tmp_path / "coll.yaml"
        f.write_text("version: 99\nrepos: {}\n")

        result = CliRunner().invoke(app, ["collection", "import", str(f)])
        assert result.exit_code == 1
        assert "version 99" in result.output or "version 99" in str(result.exception or "")
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py::TestImportRoundTrip -v`
Expected: workspace test FAILS (both land in workspace "a" today); version test FAILS (exit 1 but no message anywhere).

- [ ] **Step 3: Implement**

In `_parse_import_file`, replace both `raise typer.Exit(code=1)` version checks with:

```python
                if version > EXPORT_FORMAT_VERSION:
                    raise ValueError(
                        f"collection file version {version} is newer than supported "
                        f"{EXPORT_FORMAT_VERSION} — run 'gitstow update' first"
                    )
```

and preserve the workspace field in every parsed entry (YAML dict branch shown; mirror in the legacy-dict and JSON branches):

```python
                    return [
                        {
                            "key": k,
                            "url": v.get("remote_url", ""),
                            "tags": v.get("tags", []),
                            "frozen": v.get("frozen", False),
                            "workspace": v.get("workspace", ""),
                        }
                        for k, v in repos_data.items()
                        if isinstance(v, dict)
                    ]
```

In `import_collection`: wrap the parse call:

```python
    try:
        repos_to_import = _parse_import_file(content, path.suffix)
    except ValueError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
```

and route each entry to its workspace inside the import loop (replacing the single `ws` resolution — keep the `-w`/default `ws` as the fallback):

```python
    for entry in new_repos:
        url = entry.get("url") or entry.get("remote_url", "")
        entry_ws = ws
        recorded = entry.get("workspace", "")
        if recorded:
            candidate = settings.get_workspace(recorded)
            if candidate is not None:
                entry_ws = candidate
            else:
                console.print(
                    f"  [dim]workspace '{recorded}' not configured — importing into '{ws.label}'[/dim]"
                )
        entry_tags = entry.get("tags", []) + tags + list(entry_ws.auto_tags)
        frozen = entry.get("frozen", False)
        root = entry_ws.get_path()
        # ... existing target computation and clone/register flow, using entry_ws
        #     in place of ws (layout, label, auto_tags) ...
```

(Every use of `ws.layout` / `ws.label` inside the loop becomes `entry_ws.layout` / `entry_ws.label`.)

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/cli/export_cmd.py tests/test_cli.py
git commit -m "fix: collection import honors recorded workspaces and fails loudly on newer versions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Orphaned-workspace detection (B8)

**Files:**
- Modify: `src/gitstow/cli/doctor.py`, `src/gitstow/cli/workspace_cmd.py` (`workspace_remove`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `doctor` output (human + JSON key `orphaned_workspaces`) lists store workspace labels not present in config, with repo counts and a re-add/untrack hint; `workspace remove --keep-repos` prints how many repos become invisible and how to get them back.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestOrphanedWorkspaces:
    def test_doctor_reports_orphaned_workspace(self, tmp_path, monkeypatch):
        import json
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"; ws_dir.mkdir()
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="live", layout="flat")]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="ghost", remote_url="u", workspace="removed-ws"))

        result = CliRunner().invoke(app, ["doctor", "--json"])
        payload = json.loads(result.output)
        assert payload["orphaned_workspaces"] == {"removed-ws": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestOrphanedWorkspaces -v`
Expected: FAIL with `KeyError: 'orphaned_workspaces'`.

- [ ] **Step 3: Implement**

In `src/gitstow/cli/doctor.py`, after the per-workspace loop:

```python
    # 4. Orphaned workspaces — repos tracked under labels no longer configured
    configured = {ws.label for ws in workspaces}
    orphaned_ws: dict[str, int] = {}
    for label, count in store.all_workspaces().items():
        if label not in configured:
            orphaned_ws[label] = count
    checks["orphaned_workspaces"] = orphaned_ws
```

and in the human output, after the missing/orphaned block:

```python
    if orphaned_ws:
        console.print("\n     [yellow]⚠ Repos tracked under removed workspaces (invisible to list/status):[/yellow]")
        for label, count in orphaned_ws.items():
            console.print(f"       [{label}] {count} repo{'s' if count != 1 else ''}")
        console.print(
            "       [dim]Re-add the workspace with 'gitstow workspace add <path> --label <label>' "
            "or untrack the repos by editing ~/.gitstow/repos.yaml.[/dim]"
        )
```

In `workspace_remove` (workspace_cmd.py), in the `keep_repos` branch after removal:

```python
    if keep_repos:
        store = RepoStore()
        remaining = len(store.list_by_workspace(label))
        if remaining:
            console.print(
                f"  [yellow]⚠ {remaining} repos remain tracked under '{label}' and are now invisible "
                f"to list/status.[/yellow] Re-add the workspace to see them, or re-run with "
                f"[bold]--untrack-repos[/bold]."
            )
```

(Place it before the final "Workspace removed" line; `RepoStore` import already exists.)

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/cli/doctor.py src/gitstow/cli/workspace_cmd.py tests/test_cli.py
git commit -m "feat: doctor flags repos orphaned by removed workspaces

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Fix `open` editor logic (B9)

**Files:**
- Modify: `src/gitstow/cli/open_cmd.py` (`open_repo` flag wiring, `_open_in_editor`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `$VISUAL`/`$EDITOR` wins over `code`/`cursor`; known terminal editors run in the foreground (`subprocess.run`), GUI editors detach (`Popen`). The `--editor` flag now explicitly forces editor mode (it's also the default).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestOpenEditorPreference:
    def test_editor_env_wins_over_code(self, monkeypatch):
        import shutil as _shutil
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.setenv("EDITOR", "myeditor")
        with patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen, \
             patch.object(_shutil, "which", return_value="/usr/bin/whatever"):
            _open_in_editor("/some/path")
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "myeditor"   # $EDITOR was ignored in favor of code/cursor before

    def test_terminal_editor_runs_foreground(self, monkeypatch):
        from unittest.mock import patch
        from gitstow.cli.open_cmd import _open_in_editor

        monkeypatch.setenv("EDITOR", "vim")
        with patch("gitstow.cli.open_cmd.subprocess.run") as mock_run, \
             patch("gitstow.cli.open_cmd.subprocess.Popen") as mock_popen:
            _open_in_editor("/some/path")
        assert mock_run.called        # vim needs the terminal — foreground
        assert not mock_popen.called
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py::TestOpenEditorPreference -v`
Expected: both FAIL (code/cursor shortcut wins; everything is Popen today).

- [ ] **Step 3: Implement**

Rewrite `_open_in_editor` in `src/gitstow/cli/open_cmd.py` (move `import os, shutil, subprocess` to module level; `subprocess` is already there):

```python
_TERMINAL_EDITORS = {"vi", "vim", "nvim", "nano", "emacs", "hx", "kak", "micro"}


def _open_in_editor(path) -> None:
    """Open a directory in the user's editor.

    Preference order: $VISUAL / $EDITOR (the user said so explicitly),
    then VS Code / Cursor, then the platform opener. Terminal editors
    need the TTY, so they run in the foreground.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")

    if editor:
        base = os.path.basename(editor.split()[0])
        if base in _TERMINAL_EDITORS:
            subprocess.run([*editor.split(), str(path)])
        else:
            subprocess.Popen([*editor.split(), str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if shutil.which("code"):
        subprocess.Popen(["code", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("cursor"):
        subprocess.Popen(["cursor", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
```

Wire the dead flag in `open_repo` — the mode ladder becomes explicit (replace the current if-chain tail):

```python
    if browser:
        # ... existing browser block unchanged ...
        return
    if finder:
        # ... existing finder block unchanged ...
        return

    # Editor mode — the default, and what --editor/-e explicitly requests.
    _open_in_editor(repo_path)
    console.print(f"  [green]✓[/green] Opened {repo.key} in editor")
```

and update the `--editor` help text: `help="Open in your editor ($VISUAL/$EDITOR, then VS Code/Cursor). This is the default."`.

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/cli/open_cmd.py tests/test_cli.py
git commit -m "fix: open respects \$VISUAL/\$EDITOR and runs terminal editors in the foreground

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Move the web stack to a `[ui]` extra (A2)

**Files:**
- Modify: `pyproject.toml`, `src/gitstow/cli/serve.py` (error copy), `README.md`, `docs/user/getting-started.md`, `docs/user/commands.md`, `src/gitstow/cli/onboard.py` (quick-start panel), `src/gitstow/skill/SKILL.md`, `.github/workflows/ci.yml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `pip install gitstow` no longer pulls FastAPI; `pip install "gitstow[ui]"` / `pipx install "gitstow[ui]"` does. `gitstow ui` without the extra prints the install hint and exits 1 (mechanism already exists in `serve.py`). Dev installs get web deps via the `dev` extra.

- [ ] **Step 1: Update packaging**

In `pyproject.toml`, move the four web deps out of `dependencies`:

```toml
dependencies = [
    "typer>=0.9",
    "pyyaml>=6.0",
    "rich>=13.0",
    "beaupy>=3.0",
]

[project.optional-dependencies]
ui = [
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
    "httpx>=0.27",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
mcp = [
    "mcp>=1.0",
]
```

(Note: `typer[all]` → `typer` lands here too — D4; the `[all]` extra is deprecated no-op in modern Typer. Web deps are duplicated into `dev` rather than self-referencing `gitstow[ui]` — self-referential extras confuse some older pip versions, and CI must always have the web test deps.)

- [ ] **Step 2: Update the ImportError copy in serve.py**

```python
        err_console.print(
            f"[red]Error:[/red] Web dependencies not installed: {exc}\n"
            "The browser dashboard is an optional extra. Install it with:\n"
            "  [bold]pip install \"gitstow\\[ui]\"[/bold]   (or: pipx install \"gitstow\\[ui]\")"
        )
```

- [ ] **Step 3: Write the smoke test**

Add to `tests/test_cli.py`:

```python
class TestUiExtra:
    def test_ui_import_error_prints_install_hint(self, monkeypatch):
        from typer.testing import CliRunner
        import builtins
        from gitstow.cli.main import app

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name.startswith("gitstow.web"):
                raise ImportError("No module named 'fastapi'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        result = CliRunner().invoke(app, ["ui", "--no-browser"])
        assert result.exit_code == 1
        assert "gitstow[ui]" in result.output.replace("\\", "")
```

Run: `pytest tests/test_cli.py::TestUiExtra -v` — should pass once serve.py copy is updated (the ImportError branch already exists).

- [ ] **Step 4: Sweep install docs**

Run: `grep -rn "pip install gitstow\|pipx install gitstow" README.md docs/ src/gitstow/skill/SKILL.md src/gitstow/cli/`

Update each hit to show both forms — core install and `"gitstow[ui]"` for the dashboard. README Quick Start becomes:

```bash
pipx install "gitstow[ui]"   # recommended — CLI + browser dashboard
# or: pip install gitstow    # CLI only; add [ui] for the dashboard
```

Update `.github/workflows/ci.yml` install line to `pip install -e ".[dev]"` (unchanged — dev now carries web deps; verify).

Add CHANGELOG `[Unreleased]` entry: `### Changed — the web dashboard's dependencies (FastAPI, uvicorn, Jinja2) moved to the optional [ui] extra. Existing installs keep working; fresh CLI-only installs are ~15 packages lighter. Install with pip install "gitstow[ui]" to keep the dashboard.`

- [ ] **Step 5: Verify from a clean venv**

```bash
python -m venv /tmp/gitstow-core-test && /tmp/gitstow-core-test/bin/pip install -q . \
  && /tmp/gitstow-core-test/bin/python -c "import fastapi" 2>&1 | grep -q "No module" && echo "CORE INSTALL CLEAN" \
  && /tmp/gitstow-core-test/bin/gitstow --version
rm -rf /tmp/gitstow-core-test
```

Expected: `CORE INSTALL CLEAN` + version prints.

- [ ] **Step 6: Full suite + commit**

```bash
git add pyproject.toml src/gitstow/cli/serve.py README.md docs/ src/gitstow/skill/SKILL.md src/gitstow/cli/onboard.py tests/test_cli.py CHANGELOG.md .github/workflows/ci.yml
git commit -m "feat!: web dashboard moves to the optional [ui] extra (0.3.0)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Fast disk sizing + configurable clone timeout (E5, E6)

**Files:**
- Modify: `src/gitstow/core/git.py` (`get_disk_size`, `clone`), `src/gitstow/core/config.py` (`Settings.clone_timeout`), `src/gitstow/cli/config_cmd.py` (valid keys), `src/gitstow/cli/add.py` + `src/gitstow/cli/export_cmd.py` (pass timeout)
- Test: `tests/test_git.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `get_disk_size(path)` uses `du -sk` when available (fallback: existing rglob); `clone(..., timeout: int = 300)` parameter; `Settings.clone_timeout: int = 300` round-trips through config and is passed by both clone call sites; `config set clone_timeout 900` accepted.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_git.py`:

```python
class TestDiskSize:
    def test_du_fast_path(self, tmp_path):
        from gitstow.core.git import get_disk_size

        (tmp_path / "f.txt").write_text("x" * 4096)
        size = get_disk_size(tmp_path)
        assert size >= 4096  # du reports blocks; must be at least the content


class TestCloneTimeout:
    @patch("gitstow.core.git._run_git")
    def test_clone_passes_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        clone("https://example.com/r.git", Path("/tmp/r"), timeout=900)
        assert mock_run.call_args.kwargs["timeout"] == 900
```

Add to `tests/test_config.py`:

```python
def test_clone_timeout_roundtrip(tmp_path, monkeypatch):
    from gitstow.core.config import Settings, load_config, save_config

    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
    save_config(Settings(clone_timeout=900))
    assert load_config().clone_timeout == 900
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_git.py::TestCloneTimeout tests/test_config.py::test_clone_timeout_roundtrip -v`
Expected: FAIL (`clone()` has no `timeout` param; `Settings` has no `clone_timeout`).

- [ ] **Step 3: Implement**

`core/git.py` — `clone` gains the parameter:

```python
def clone(
    url: str,
    target: Path,
    shallow: bool = False,
    branch: str | None = None,
    recursive: bool = False,
    timeout: int = 300,
) -> tuple[bool, str]:
    ...
    try:
        result = _run_git(args, timeout=timeout)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, f"Clone timed out ({timeout // 60} minutes) — raise it with: gitstow config set clone_timeout <seconds>"
```

`get_disk_size` fast path:

```python
def get_disk_size(path: Path) -> int:
    """Total disk size of a directory in bytes — `du` when available (fast),
    Python walk otherwise."""
    import shutil as _shutil

    if _shutil.which("du"):
        result = subprocess.run(
            ["du", "-sk", str(path)], capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.split()[0]) * 1024
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
```

`core/config.py` — add the field and thread it through `to_dict`/`from_dict`:

```python
    clone_timeout: int = 300     # seconds; large repos may need more
```
```python
            "clone_timeout": self.clone_timeout,
```
```python
            clone_timeout=data.get("clone_timeout", 300),
```

`cli/config_cmd.py` — add to `valid_keys` and the int branch:

```python
    valid_keys = {"default_host", "prefer_ssh", "parallel_limit", "clone_timeout"}
```
```python
    elif key in ("parallel_limit", "clone_timeout"):
```

Pass it at both clone call sites — `cli/add.py` `_clone_worker`:

```python
            success, error = git_clone(
                url=parsed.clone_url, target=target,
                shallow=shallow, branch=branch, recursive=recursive,
                timeout=settings.clone_timeout,
            )
```

and `cli/export_cmd.py` import loop:

```python
        success, error = git_clone(url=parsed.clone_url, target=target, shallow=shallow,
                                   timeout=settings.clone_timeout)
```

(Web `add_repo` route: add `timeout=settings.clone_timeout` to its `asyncio.to_thread(git_clone, ...)` call via `functools.partial(git_clone, parsed.clone_url, target, timeout=settings.clone_timeout)`.)

- [ ] **Step 4: Run tests, full suite, commit**

```bash
git add src/gitstow/core/git.py src/gitstow/core/config.py src/gitstow/cli/config_cmd.py src/gitstow/cli/add.py src/gitstow/cli/export_cmd.py src/gitstow/web/routes/repos.py tests/
git commit -m "perf: du-backed disk sizing + configurable clone_timeout

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: CI matrix, changelog gate, fetch tests (D2, D3, D5, P6)

**Files:**
- Modify: `.github/workflows/ci.yml`, `scripts/release.sh`
- Test: `tests/test_cli.py` (fetch coverage)

**Interfaces:**
- Produces: CI runs ubuntu 3.10–3.13 plus macOS 3.12; `release.sh` aborts unless `CHANGELOG.md` has a `## [X.Y.Z]` section for the version being released; `gitstow fetch` has CLI test coverage (MCP tests landed in Task 4).

- [ ] **Step 1: CI matrix**

In `.github/workflows/ci.yml`:

```yaml
    strategy:
      matrix:
        os: [ubuntu-latest]
        python-version: ["3.10", "3.11", "3.12", "3.13"]
        include:
          - os: macos-latest
            python-version: "3.12"
    runs-on: ${{ matrix.os }}
```

Validate: `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('OK')"`

- [ ] **Step 2: Changelog gate in release.sh**

After the version-format check in `scripts/release.sh`:

```bash
if ! grep -q "^## \[$VERSION\]" CHANGELOG.md; then
    echo "Error: CHANGELOG.md has no '## [$VERSION]' section."
    echo "Document the release before shipping it (this is how 0.2.6 went missing)."
    exit 1
fi
```

Verify: `bash scripts/release.sh 9.9.9` → must print the CHANGELOG error and exit 1 (it fails before touching anything since 9.9.9 has no entry).

- [ ] **Step 3: Fetch CLI coverage (D5)**

Add to `tests/test_cli.py`:

```python
class TestFetchCommand:
    def test_fetch_includes_frozen_and_stamps_last_fetched(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import FetchResult
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "frozen-one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(
            Repo(owner="a", name="frozen-one", remote_url="u", workspace="ws", frozen=True)
        )

        with patch("gitstow.cli.fetch.git_fetch", return_value=FetchResult(success=True, output="ok")):
            result = CliRunner().invoke(app, ["fetch", "--json"])

        payload = json.loads(result.output)
        assert payload["fetched"] == 1  # frozen repos ARE fetched
        store = RepoStore(path=repos_file)
        assert store.get("a/frozen-one", workspace="ws").last_fetched != ""
```

Run: `pytest tests/test_cli.py::TestFetchCommand -v` — expected PASS immediately (it documents existing behavior; if it fails, that's a real regression from Task 3's refactor — fix run_bulk wiring, not the test).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml scripts/release.sh tests/test_cli.py
git commit -m "ci: macOS in the test matrix; release.sh requires a CHANGELOG entry; fetch coverage

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Documentation sync (DOC1, DOC2)

**Files:**
- Modify: `CLAUDE.md`, `BACKLOG.md`, `docs/user/commands.md`, `README.md`, `src/gitstow/skill/SKILL.md`

**Interfaces:**
- Produces: every doc surface reflects post-wave reality. No hardcoded stale counts.

- [ ] **Step 1: Run the drift sweep**

```bash
grep -rn "44 tests\|32)\|32 commands\|release.yml\|root_path\|tui\|serve\b" \
  CLAUDE.md BACKLOG.md README.md docs/ src/gitstow/skill/SKILL.md | grep -v Binary
```

- [ ] **Step 2: Fix each class of hit**

- `CLAUDE.md`: replace `pytest # 44 tests` with `pytest # full suite — keep green`; recount "All Commands (32)" after `tui` removal and any additions; architecture tree drops `tui/`, gains `core/status_model.py`, `core/operations.py`, `core/locking.py`; Data Files section notes the `.lock` file; AI Integration section notes the added MCP `fetch_repos` tool.
- `BACKLOG.md`: `release.yml` → `publish.yml`; mark the TUI line resolved-by-retirement (done in Wave 2 — verify).
- `docs/user/commands.md`: `config set` key list gains `clone_timeout`; `config migrate-root` section documents the `--workspace` form; MCP tool count corrected; add/list/status sections mention parallel clones, untracked hints, composition columns.
- `README.md`: features list reflects composition-aware status; install shows `[ui]` extra (done in Task 9 — verify).
- `SKILL.md`: file-locations section notes `[ui]` extra for the dashboard row; decision-tree unchanged rows verified against real commands.

- [ ] **Step 3: Verify commands count claim**

Run: `.venv/bin/gitstow --help | grep -c '^  [a-z]'` (adjust the pattern to Typer's help layout) and reconcile with whatever number the docs now claim.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md BACKLOG.md docs/ README.md src/gitstow/skill/SKILL.md
git commit -m "docs: sync all surfaces with waves 1-3 reality

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Wave completion checklist

- [ ] `pytest -q` green, `ruff check src/` clean
- [ ] Clean-venv verification from Task 9 passes (core install has no fastapi)
- [ ] Manual smoke: `gitstow add owner/a owner/b owner/c` shows concurrent cloning; deep-URL + mismatch errors read well
- [ ] Manual smoke: `gitstow ui` works from a `[dev]` install
- [ ] Check off Wave 3 items in `docs/building/audit-2026-07-06.md` — audit fully closed
- [ ] CHANGELOG: write the consolidated `## [0.3.0]` section (TUI removal, [ui] extra, status model, pull semantics, everything else under Added/Changed/Fixed/Removed)
- [ ] Release: `bash scripts/release.sh 0.3.0 "status model, [ui] extra, operations layer"` (the new changelog gate must pass)
