# Wave 1 — Correctness & Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the correctness bugs and safety gaps found in the v0.2.7 audit (`docs/building/audit-2026-07-06.md`) — URL parsing, data-file integrity, git subprocess hardening, workspace resolution, web CSRF, and the publish pipeline.

**Architecture:** Surgical fixes at the layer each bug lives in: `core/git.py` env hardening, a new `core/locking.py` + atomic writes in `core/repo.py`, host-aware guards in `core/url_parser.py`, a rewritten workspace-era `config migrate-root`, `resolve_repo`-based lookup in `cli/manage.py`, an Origin/Host middleware in `web/server.py`, and a test gate in `publish.yml`. No new dependencies.

**Tech Stack:** Python 3.10+, Typer, FastAPI/Starlette middleware, pytest (mocked subprocess — tests never shell to real git), GitHub Actions.

## Global Constraints

- Python floor: `>=3.10` (from pyproject). `Path.is_relative_to` is available.
- No new runtime dependencies. Locking uses stdlib `fcntl`/`msvcrt`.
- Existing JSON output shapes must not lose keys (additive changes only).
- All 139 baseline tests must stay green after every task.
- Run tests with: `.venv/bin/python -m pytest -q` (or `pytest -q` in the dev env).
- Lint with: `ruff check src/` — must pass before each commit.
- Do NOT release during this wave. Version bump (0.2.8) happens after the wave is reviewed, via `scripts/release.sh`.
- Commit messages: conventional style (`fix:`, `feat:`, `test:`, `ci:`), each ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Audit coverage matrix

| Audit ID | Task |
|----------|------|
| B4 (git env hardening) | Task 1 |
| B3 (atomic writes + locking) | Task 2 |
| B1 (deep-URL parsing) | Task 3 |
| B2 + DOC4 (migrate-root, root_path help) | Task 4 |
| B5 + B13 (manage.py resolution, dead code) | Task 5 |
| B10 (pull frozen global keys) | Task 6 |
| S1 (web Origin/Host protection) | Task 7 |
| B12 (CLI delete containment) | Task 8 |
| B11 (workspace label validation) | Task 9 |
| D1 (publish test gate) | Task 10 |

---

### Task 1: Git subprocess hardening (B4)

**Files:**
- Modify: `src/gitstow/core/git.py:83-98` (`_run_git`)
- Test: `tests/test_git.py`

**Interfaces:**
- Produces: `_run_git` unchanged signature; every git subprocess now runs with `GIT_TERMINAL_PROMPT=0` and `LC_ALL=C` merged over `os.environ`. Later tasks and waves rely on git output being English and never blocking on credential prompts.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_git.py`:

```python
class TestRunGitEnv:
    @patch("gitstow.core.git.subprocess.run")
    def test_run_git_sets_safe_env(self, mock_run):
        from gitstow.core.git import _run_git

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _run_git(["status"])

        env = mock_run.call_args.kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"   # never hang on auth prompts
        assert env["LC_ALL"] == "C"                # stable English output
        assert "PATH" in env                       # inherited environment preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git.py::TestRunGitEnv -v`
Expected: FAIL with `KeyError: 'env'` (no `env` kwarg passed today).

- [ ] **Step 3: Implement**

In `src/gitstow/core/git.py`, add `import os` to the imports, then modify `_run_git`:

```python
def _run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a git command and return the result.

    GIT_TERMINAL_PROMPT=0 — a repo needing credentials fails fast instead of
    hanging the whole bulk operation on an invisible username prompt.
    LC_ALL=C — git output stays English so message matching is stable.
    """
    cmd = ["git"] + args
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: all pass (existing tests mock `_run_git` or `subprocess.run` loosely).

- [ ] **Step 5: Commit**

```bash
git add src/gitstow/core/git.py tests/test_git.py
git commit -m "fix: harden git subprocess env — no auth prompts, stable locale

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Atomic writes + cross-process locking for repos.yaml (B3)

**Files:**
- Create: `src/gitstow/core/locking.py`
- Modify: `src/gitstow/core/repo.py` (save/add/remove/update/load)
- Test: `tests/test_locking.py` (new), `tests/test_repo.py`

**Interfaces:**
- Produces: `gitstow.core.locking.file_lock(lock_path: Path, timeout: float = 10.0)` context manager; `LockTimeout(Exception)`. `RepoStore` gains private `_write()` (atomic serialize, caller holds lock) and `_mutate()` (context manager: lock → fresh `load()` → yield → `_write()`). Public API (`save/add/remove/update/get/list_*`) is unchanged. Wave 2's `bulk()` builds on `_mutate()`.

- [ ] **Step 1: Write the locking module**

Create `src/gitstow/core/locking.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_locking.py`:

```python
"""Tests for atomic writes and cross-process locking on repos.yaml."""

import threading

from gitstow.core.locking import file_lock
from gitstow.core.repo import Repo, RepoStore


def _repo(owner: str, name: str) -> Repo:
    return Repo(owner=owner, name=name, remote_url=f"https://github.com/{owner}/{name}.git", workspace="oss")


class TestFileLock:
    def test_lock_is_exclusive(self, tmp_path):
        lock_path = tmp_path / "x.lock"
        order = []

        def worker():
            with file_lock(lock_path):
                order.append("worker")

        with file_lock(lock_path):
            t = threading.Thread(target=worker)
            t.start()
            order.append("holder")
        t.join(timeout=5)
        assert order == ["holder", "worker"]


class TestAtomicStore:
    def test_no_tmp_file_left_behind(self, tmp_repos_file):
        store = RepoStore(path=tmp_repos_file)
        store.add(_repo("a", "one"))
        assert tmp_repos_file.exists()
        assert not tmp_repos_file.with_name(tmp_repos_file.name + ".tmp").exists()

    def test_interleaved_stores_do_not_lose_writes(self, tmp_repos_file):
        # Two store instances loaded before either writes — the classic lost-update.
        s1 = RepoStore(path=tmp_repos_file)
        s2 = RepoStore(path=tmp_repos_file)
        s1.load()
        s2.load()
        s1.add(_repo("a", "one"))
        s2.add(_repo("b", "two"))  # must NOT clobber s1's write

        fresh = RepoStore(path=tmp_repos_file)
        keys = {r.global_key for r in fresh.list_all()}
        assert keys == {"oss:a/one", "oss:b/two"}

    def test_interleaved_updates_do_not_lose_fields(self, tmp_repos_file):
        seed = RepoStore(path=tmp_repos_file)
        seed.add(_repo("a", "one"))
        seed.add(_repo("b", "two"))

        s1 = RepoStore(path=tmp_repos_file)
        s2 = RepoStore(path=tmp_repos_file)
        s1.load()
        s2.load()
        s1.update("a/one", workspace="oss", frozen=True)
        s2.update("b/two", workspace="oss", tags=["x"])

        fresh = RepoStore(path=tmp_repos_file)
        assert fresh.get("a/one", workspace="oss").frozen is True
        assert fresh.get("b/two", workspace="oss").tags == ["x"]
```

- [ ] **Step 3: Run tests to verify failures**

Run: `pytest tests/test_locking.py -v`
Expected: `TestFileLock` passes (new module), both `test_interleaved_*` FAIL (lost update in current implementation).

- [ ] **Step 4: Implement in RepoStore**

In `src/gitstow/core/repo.py`: add imports `import contextlib`, `import os`, and `from gitstow.core.locking import file_lock`. Then:

Replace `save()` and add `_write()`, `_lock_path()`, `_mutate()`:

```python
    def _lock_path(self) -> Path:
        return self._path.with_suffix(".lock")

    def _write(self) -> None:
        """Atomically serialize current state to disk. Caller holds the lock
        (or accepts last-writer-wins, e.g. the legacy-migration path)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        by_workspace: dict[str, dict[str, dict]] = {}
        for repo in sorted(self._repos.values(), key=lambda r: r.global_key):
            ws = repo.workspace or "oss"
            if ws not in by_workspace:
                by_workspace[ws] = {}
            by_workspace[ws][repo.key] = repo.to_dict()

        data = {k: by_workspace[k] for k in sorted(by_workspace)}
        tmp = self._path.with_name(self._path.name + ".tmp")
        with open(tmp, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, self._path)

    def save(self) -> None:
        """Write repos to repos.yaml in nested workspace format."""
        with file_lock(self._lock_path()):
            self._write()

    @contextlib.contextmanager
    def _mutate(self):
        """Locked read-modify-write cycle: reload fresh state, apply the
        caller's mutation, write atomically. Prevents lost updates when the
        CLI and web UI run concurrently."""
        with file_lock(self._lock_path()):
            self.load()
            yield
            self._write()
```

In `load()`, the legacy-migration branch currently calls `self.save()`; change that one line to `self._write()` (taking the lock inside `load()` would deadlock when `load()` is called from `_mutate()`):

```python
        if _is_legacy_format(data):
            for key, repo_data in data.items():
                if isinstance(repo_data, dict):
                    repo = Repo.from_dict(key, repo_data, workspace="oss")
                    self._repos[repo.global_key] = repo
            self._loaded = True
            self._write()  # migrate to new format on disk (atomic; lock-free is fine here)
            return
```

Rewrite the three mutators to use `_mutate()`:

```python
    def add(self, repo: Repo) -> None:
        """Add a repo. Overwrites if global_key already exists."""
        with self._mutate():
            if not repo.added:
                repo.added = datetime.now().strftime("%Y-%m-%d")
            self._repos[repo.global_key] = repo

    def remove(self, key: str, workspace: str | None = None) -> bool:
        """Remove a repo by key. If workspace is None, tries to find a unique match."""
        with self._mutate():
            global_key = self._resolve_global_key(key, workspace)
            if global_key and global_key in self._repos:
                del self._repos[global_key]
                return True
            return False

    def update(self, key: str, workspace: str | None = None, **kwargs) -> bool:
        """Update specific fields on a repo. Returns True if repo exists."""
        with self._mutate():
            global_key = self._resolve_global_key(key, workspace)
            if not global_key:
                return False
            repo = self._repos.get(global_key)
            if not repo:
                return False
            for field_name, value in kwargs.items():
                if hasattr(repo, field_name):
                    setattr(repo, field_name, value)
            return True
```

Note: `return` inside the `with` still runs `_write()` via the context manager — `remove`/`update` that find nothing rewrite an unchanged file; that's harmless and keeps the code simple.

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_locking.py tests/test_repo.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`

```bash
git add src/gitstow/core/locking.py src/gitstow/core/repo.py tests/test_locking.py
git commit -m "fix: atomic repos.yaml writes with cross-process locking

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Deep-URL parsing guards (B1)

**Files:**
- Modify: `src/gitstow/core/url_parser.py:134-145` (`_split_owner_repo` and its call sites)
- Test: `tests/test_url_parser.py`

**Interfaces:**
- Produces: `_extract_owner_repo(host: str, path: str) -> tuple[str, str]` replacing `_split_owner_repo(path)`. `parse_git_url` signature/behavior unchanged for valid inputs; deep browse URLs now resolve to the true `owner/repo`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_url_parser.py`:

```python
class TestDeepLinks:
    """Pasted browse URLs (tree/blob/pull/...) must resolve to the repo root."""

    @pytest.mark.parametrize("url,owner,repo", [
        ("https://github.com/anthropics/claude-code/tree/main/src", "anthropics", "claude-code"),
        ("https://github.com/owner/repo/blob/main/README.md", "owner", "repo"),
        ("https://github.com/owner/repo/pull/123", "owner", "repo"),
        ("https://github.com/owner/repo/issues", "owner", "repo"),
        ("https://github.com/owner/repo/releases/tag/v1.0", "owner", "repo"),
        ("github.com/owner/repo/actions", "owner", "repo"),
        ("https://gitlab.com/group/subgroup/repo/-/tree/main", "group/subgroup", "repo"),
        ("https://gitlab.example.com/group/sub/repo/-/blob/main/x.py", "group/sub", "repo"),
    ])
    def test_deep_url_resolves_to_repo_root(self, url, owner, repo):
        parsed = parse_git_url(url)
        assert parsed.owner == owner
        assert parsed.repo == repo
        assert parsed.clone_url.endswith(f"{owner}/{repo}.git")

    def test_gitlab_nested_groups_still_work(self):
        parsed = parse_git_url("https://gitlab.com/group/subgroup/repo")
        assert parsed.owner == "group/subgroup"
        assert parsed.repo == "repo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_url_parser.py::TestDeepLinks -v`
Expected: every deep-URL case FAILS (owner comes back as e.g. `anthropics/claude-code/tree/main`); the nested-groups case passes.

- [ ] **Step 3: Implement**

In `src/gitstow/core/url_parser.py`, add near the top (after the regex patterns):

```python
# Hosts whose repo path is always exactly owner/repo — never nested groups.
_TWO_SEGMENT_HOSTS = {"github.com", "bitbucket.org", "codeberg.org", "gitea.com"}

# Path segments that begin a browse-UI suffix rather than the repo path.
# GitLab separates repo path from browse UI with "/-/"; GitHub-style hosts
# use these words directly after owner/repo.
_DEEP_LINK_MARKERS = {
    "-", "tree", "blob", "pull", "pulls", "issues", "commit", "commits",
    "releases", "actions", "wiki", "compare", "raw", "src",
}
```

Replace `_split_owner_repo` with a host-aware version:

```python
def _extract_owner_repo(host: str, path: str) -> tuple[str, str]:
    """Split a URL path into owner and repo.

    Handles nested groups (group/subgroup/repo → owner="group/subgroup"),
    truncates browse-UI suffixes (…/repo/tree/main/… → …/repo), and caps
    known single-owner hosts at exactly two segments.
    """
    parts = [p for p in path.split("/") if p]

    # A marker at index >= 2 means everything from it onward is browse UI.
    for i, seg in enumerate(parts):
        if i >= 2 and seg in _DEEP_LINK_MARKERS:
            parts = parts[:i]
            break

    if host in _TWO_SEGMENT_HOSTS and len(parts) > 2:
        parts = parts[:2]

    if len(parts) < 2:
        return "", parts[0] if parts else ""
    return "/".join(parts[:-1]), parts[-1]
```

Update the two call sites in `parse_git_url` (the Azure branch's fallback and the general branch) from `_split_owner_repo(path)` to `_extract_owner_repo(host, path)`, and delete the old `_split_owner_repo`.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_url_parser.py -v`
Expected: all PASS, including all pre-existing URL format tests.

- [ ] **Step 5: Full suite + commit**

Run: `pytest -q && ruff check src/`

```bash
git add src/gitstow/core/url_parser.py tests/test_url_parser.py
git commit -m "fix: resolve pasted deep URLs (tree/blob/pull/...) to the repo root

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Rewrite `config migrate-root` for the workspace era (B2, DOC4)

**Files:**
- Modify: `src/gitstow/cli/config_cmd.py:71-113` (`config_set` docstring) and `:122-236` (`config_migrate_root`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `resolve_workspaces` from `cli/helpers.py`; `save_config`/`load_config` from `core/config.py`.
- Produces: `gitstow config migrate-root NEW_PATH [--workspace LABEL] [--copy] [--yes]` — moves ONE workspace's repos and updates that workspace's `path` in config. No more writes to legacy `root_path`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
class TestMigrateRoot:
    def test_migrate_root_updates_workspace_path(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.core.config import Settings, Workspace, save_config, load_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        old_root = tmp_path / "old"
        (old_root / "anthropic" / "claude-code" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(old_root), label="oss", layout="structured")]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="anthropic", name="claude-code",
                       remote_url="https://github.com/anthropic/claude-code.git", workspace="oss"))

        new_root = tmp_path / "new"
        from gitstow.cli.main import app
        result = CliRunner().invoke(app, ["config", "migrate-root", str(new_root), "--yes"])

        assert result.exit_code == 0
        assert (new_root / "anthropic" / "claude-code" / ".git").exists()
        reloaded = load_config()
        assert reloaded.get_workspace("oss").get_path() == new_root.resolve()

    def test_config_set_rejects_root_path_without_advertising_it(self):
        from gitstow.cli.config_cmd import config_set
        assert "root_path" not in (config_set.__doc__ or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestMigrateRoot -v`
Expected: first test FAILS (config still points at old root — `root_path` write is dropped by serialization); second FAILS (docstring advertises `root_path`).

- [ ] **Step 3: Implement**

In `src/gitstow/cli/config_cmd.py`, fix the `config_set` docstring (remove the `root_path` example line):

```python
    """Set a configuration value.

    \b
    Examples:
      gitstow config set default_host gitlab.com
      gitstow config set prefer_ssh true
      gitstow config set parallel_limit 8

    Workspace paths are managed with 'gitstow workspace add/remove'
    and 'gitstow config migrate-root'.
    """
```

Rewrite `config_migrate_root`:

```python
@config_app.command("migrate-root")
def config_migrate_root(
    new_root: str = typer.Argument(help="New directory for the workspace's repos."),
    workspace: str = typer.Option(
        None, "--workspace", "-w", help="Workspace to migrate (default: first workspace).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    copy: bool = typer.Option(False, "--copy", help="Copy instead of move (keeps old root)."),
) -> None:
    """Move one workspace's repos to a new directory and update its config.

    \b
    Examples:
      gitstow config migrate-root ~/new-location            # default workspace
      gitstow config migrate-root ~/new-location -w active  # specific workspace
    """
    import shutil
    from pathlib import Path

    from gitstow.core.git import is_git_repo

    settings = load_config()
    store = RepoStore()

    # Ensure the workspace list is materialized (legacy configs synthesize it).
    if not settings.workspaces:
        settings.workspaces = settings.get_workspaces()

    ws_label = workspace or settings.get_default_workspace().label
    ws = settings.get_workspace(ws_label)
    if ws is None:
        labels = ", ".join(w.label for w in settings.get_workspaces())
        err_console.print(f"[red]Error:[/red] Unknown workspace [bold]{ws_label}[/bold]. Available: {labels}")
        raise typer.Exit(code=1)

    old_root = ws.get_path()
    new_root_path = Path(new_root).expanduser().resolve()

    if old_root == new_root_path:
        console.print("  [dim]New root is the same as current root. Nothing to do.[/dim]")
        return

    repos = store.list_by_workspace(ws.label)

    def _update_config() -> None:
        for w in settings.workspaces:
            if w.label == ws.label:
                w.path = str(new_root_path)
        save_config(settings)

    if not repos:
        _update_config()
        new_root_path.mkdir(parents=True, exist_ok=True)
        console.print(f"  [green]✓[/green] Workspace [bold]{ws.label}[/bold] moved to {new_root_path} (no repos to move).")
        return

    action = "Copy" if copy else "Move"
    console.print(f"\n  [bold]{action} {len(repos)} repos in workspace '{ws.label}'[/bold]\n")
    console.print(f"    From: {old_root}")
    console.print(f"    To:   {new_root_path}\n")

    movable, missing = [], []
    for repo in repos:
        src = repo.get_path(old_root)
        (movable if src.exists() and is_git_repo(src) else missing).append(repo)

    if missing:
        console.print(f"  [yellow]⚠ {len(missing)} repos not found on disk (config will still update):[/yellow]")
        for r in missing:
            console.print(f"    {r.key}")
        console.print()

    console.print(f"  {len(movable)} repos to {action.lower()}")

    if not yes:
        if not typer.confirm(f"\n  Proceed with {action.lower()}?"):
            console.print("  [dim]Cancelled.[/dim]")
            raise typer.Exit()

    new_root_path.mkdir(parents=True, exist_ok=True)

    succeeded = failed = 0
    for repo in movable:
        src = repo.get_path(old_root)
        dst = repo.get_path(new_root_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dst.exists():
                console.print(f"  [yellow]⚠[/yellow] {repo.key}: target already exists, skipping")
                continue
            if copy:
                shutil.copytree(src, dst, symlinks=True)
            else:
                try:
                    src.rename(dst)
                except OSError:
                    shutil.copytree(src, dst, symlinks=True)
                    shutil.rmtree(src)
            succeeded += 1
            console.print(f"  [green]✓[/green] {repo.key}")
        except Exception as e:
            failed += 1
            err_console.print(f"  [red]✗[/red] {repo.key}: {e}")

    _update_config()

    if not copy and old_root.exists():
        for owner_dir in old_root.iterdir():
            if owner_dir.is_dir() and not any(owner_dir.iterdir()):
                owner_dir.rmdir()

    console.print(f"\n  Done: {succeeded} {action.lower()}d", end="")
    if failed:
        console.print(f", [red]{failed} failed[/red]", end="")
    console.print(f"\n  Workspace [bold]{ws.label}[/bold] now at: {new_root_path}\n")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + commit**

```bash
git add src/gitstow/cli/config_cmd.py tests/test_config.py
git commit -m "fix: migrate-root now updates workspace paths (was writing dropped legacy root_path)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Workspace-aware resolution in `repo` subcommands (B5, B13)

**Files:**
- Modify: `src/gitstow/cli/manage.py` (freeze, unfreeze, add_tags, remove_tags), `src/gitstow/core/repo.py:197-215` (`_resolve_global_key`)
- Test: `tests/test_repo.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `resolve_repo(store, settings, key, workspace_label)` from `cli/helpers.py` (returns `(Repo, Workspace)` or exits with a clear error, prompting interactively when ambiguous).
- Produces: `repo freeze/unfreeze/tag/untag` accept the global `-w` flag and give a "exists in multiple workspaces" error instead of "not tracked" on ambiguity. `_resolve_global_key` loses its dead trailing line.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestManageWorkspaceResolution:
    def _seed_two_workspaces(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_a = tmp_path / "a"; ws_a.mkdir()
        ws_b = tmp_path / "b"; ws_b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_a), label="a", layout="flat"),
            Workspace(path=str(ws_b), label="b", layout="flat"),
        ]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="dupe", remote_url="https://github.com/x/dupe.git", workspace="a"))
        store.add(Repo(owner="", name="dupe", remote_url="https://github.com/y/dupe.git", workspace="b"))
        return repos_file

    def test_freeze_with_workspace_flag_targets_right_repo(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.repo import RepoStore

        repos_file = self._seed_two_workspaces(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["-w", "b", "repo", "freeze", "dupe"])
        assert result.exit_code == 0

        store = RepoStore(path=repos_file)
        assert store.get("dupe", workspace="b").frozen is True
        assert store.get("dupe", workspace="a").frozen is False

    def test_freeze_ambiguous_without_flag_errors_clearly(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        self._seed_two_workspaces(tmp_path, monkeypatch)
        result = CliRunner().invoke(app, ["repo", "freeze", "dupe"])
        assert result.exit_code == 1
        combined = (result.output or "") + str(result.exception or "")
        assert "multiple workspaces" in combined or "multiple workspaces" in (result.stderr or "")
```

(Note: `CliRunner().invoke` runs non-interactively, so `resolve_repo`'s piped-stdin branch fires — the "exists in multiple workspaces" error.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestManageWorkspaceResolution -v`
Expected: FAIL — today `freeze` ignores `-w` and prints "not tracked" for ambiguous keys.

- [ ] **Step 3: Implement in manage.py**

Rewrite the four mutating commands to resolve via `resolve_repo`. Pattern for `freeze` (apply the same to `unfreeze`, `add_tags`, `remove_tags`):

```python
@manage_app.command()
def freeze(
    ctx: typer.Context,
    repo_key: Optional[str] = typer.Argument(default=None, help="Repo to freeze (owner/repo). Optional if --tag is used."),
    tag_filter: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Freeze all repos with this tag instead.",
    ),
) -> None:
    """[bold cyan]Freeze[/bold cyan] a repo — skip it during pull.

    \b
    Examples:
      gitstow repo freeze facebook/react
      gitstow repo freeze --tag archived
      gitstow -w oss repo freeze dupe
    """
    settings = load_config()
    store = RepoStore()
    ws_label = (ctx.obj or {}).get("workspace")

    if tag_filter:
        repos = store.list_by_tag(tag_filter)
        if ws_label:
            repos = [r for r in repos if r.workspace == ws_label]
        if not repos:
            err_console.print(f"[yellow]No repos with tag '{tag_filter}'.[/yellow]")
            return
        for repo in repos:
            store.update(repo.key, workspace=repo.workspace, frozen=True)
        console.print(f"  [cyan]❄[/cyan] Froze {len(repos)} repos with tag '{tag_filter}'.")
        return

    if not repo_key:
        err_console.print("[red]Error:[/red] Provide a repo key or use --tag.")
        raise typer.Exit(code=1)

    repo, _ = resolve_repo(store, settings, repo_key, ws_label)
    if repo.frozen:
        console.print(f"  [dim]{repo.key} is already frozen.[/dim]")
        return
    store.update(repo.key, workspace=repo.workspace, frozen=True)
    console.print(f"  [cyan]❄[/cyan] {repo.key} frozen. It will be skipped during pull.")
```

`unfreeze` mirrors it with `frozen=False` and the tag branch filtering `[r for r in repos if r.frozen]`. For `add_tags` and `remove_tags` add `ctx: typer.Context` as first parameter, then:

```python
    settings = load_config()
    store = RepoStore()
    ws_label = (ctx.obj or {}).get("workspace")
    repo, _ = resolve_repo(store, settings, repo_key, ws_label)
    # add_tags body:
    new_tags = sorted(set(repo.tags + [t.lower() for t in tags]))
    added = [t for t in new_tags if t not in repo.tags]
    store.update(repo.key, workspace=repo.workspace, tags=new_tags)
    # remove_tags body:
    removed = [t for t in tags if t in repo.tags]
    new_tags = [t for t in repo.tags if t not in tags]
    store.update(repo.key, workspace=repo.workspace, tags=new_tags)
```

(`sorted(set(...))` also fixes the nondeterministic tag order from `list(set(...))`.)

Add the needed imports at the top of `manage.py`: `from gitstow.core.config import load_config` (already present) and `from gitstow.cli.helpers import resolve_repo` (already present — verify).

- [ ] **Step 4: Fix `_resolve_global_key` dead code (B13)**

In `src/gitstow/core/repo.py`, replace the tail of `_resolve_global_key`:

```python
        # Search across all workspaces — resolve only when unique.
        matches = [gk for gk, r in self._repos.items() if r.key == key]
        return matches[0] if len(matches) == 1 else None
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_cli.py tests/test_repo.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite + commit**

```bash
git add src/gitstow/cli/manage.py src/gitstow/core/repo.py tests/test_cli.py
git commit -m "fix: repo freeze/tag subcommands honor -w and report ambiguity clearly

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Pull frozen bookkeeping uses full identity (B10)

**Files:**
- Modify: `src/gitstow/cli/pull.py:110-116` and `:201-204`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: pull's frozen summary rows carry `"repo"` (key) plus a new additive `"workspace"` field; same-named frozen repos in two workspaces produce two rows, not one.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (reuse `_seed_two_workspaces` from Task 5's class or duplicate the seeding helper):

```python
class TestPullFrozenIdentity:
    def test_frozen_repos_with_same_key_both_reported(self, tmp_path, monkeypatch):
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

        ws_a = tmp_path / "a"; ws_a.mkdir()
        ws_b = tmp_path / "b"; ws_b.mkdir()
        save_config(Settings(workspaces=[
            Workspace(path=str(ws_a), label="a", layout="flat"),
            Workspace(path=str(ws_b), label="b", layout="flat"),
        ]))
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="dupe", remote_url="u", workspace="a", frozen=True))
        store.add(Repo(owner="", name="dupe", remote_url="u", workspace="b", frozen=True))

        result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)
        frozen_rows = [r for r in payload["results"] if r["status"] == "frozen"]
        assert len(frozen_rows) == 2
        assert {r["workspace"] for r in frozen_rows} == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestPullFrozenIdentity -v`
Expected: FAIL — the `frozen_keys` set collapses both to one row, and no `workspace` field exists.

- [ ] **Step 3: Implement**

In `src/gitstow/cli/pull.py`, replace the frozen bookkeeping (`pull.py:110-116`):

```python
    # Apply filters
    if not include_frozen:
        frozen_repos = [r for r, _ in targets if r.frozen]
        targets = [(r, ws) for r, ws in targets if not r.frozen]
    else:
        frozen_repos = []
```

and the summary block (`pull.py:201-204`):

```python
    # Add frozen repos to results for completeness
    for r in sorted(frozen_repos, key=lambda x: x.global_key):
        result_dicts.append(
            {"repo": r.key, "workspace": r.workspace, "status": "frozen", "detail": "Skipped (frozen)"}
        )
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py -v && pytest -q`

```bash
git add src/gitstow/cli/pull.py tests/test_cli.py
git commit -m "fix: pull frozen summary keeps per-workspace identity

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Web UI cross-origin write protection (S1)

**Files:**
- Modify: `src/gitstow/web/server.py` (`create_app`), `tests/test_serve.py` (`client` fixture)
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: every POST with an `Origin` whose hostname isn't `127.0.0.1`/`localhost`/`::1` → 403 JSON; every POST whose `Host` hostname isn't in that set → 403 (DNS-rebinding guard). GETs and header-less POSTs (curl, scripts) unaffected.

- [ ] **Step 1: Update the test fixture so requests carry a localhost Host**

In `tests/test_serve.py`, change the `client` fixture (TestClient's default `Host: testserver` would trip the new guard — the tests should look like the real browser traffic):

```python
@pytest.fixture
def client(isolated):
    """TestClient against a freshly-built FastAPI app."""
    app = create_app()

    class _StubServer:
        should_exit = False

    app.state.server = _StubServer()
    return TestClient(app, base_url="http://127.0.0.1")
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_serve.py`:

```python
class TestCrossOriginProtection:
    def test_cross_origin_post_rejected(self, client, configured):
        r = client.post("/shutdown", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403

    def test_localhost_origin_post_allowed(self, client, configured):
        r = client.post("/shutdown", headers={"Origin": "http://127.0.0.1:7853"})
        assert r.status_code == 200

    def test_post_without_origin_allowed(self, client, configured):
        # curl / scripts don't send Origin — CSRF is a browser-only vector.
        r = client.post("/shutdown")
        assert r.status_code == 200

    def test_dns_rebinding_host_rejected(self, client, configured):
        r = client.post("/shutdown", headers={"Host": "evil.example"})
        assert r.status_code == 403

    def test_get_never_blocked(self, client, configured):
        r = client.get("/", headers={"Origin": "http://evil.example"})
        assert r.status_code == 200
```

- [ ] **Step 3: Run tests to verify failures**

Run: `pytest tests/test_serve.py::TestCrossOriginProtection -v`
Expected: `test_cross_origin_post_rejected` and `test_dns_rebinding_host_rejected` FAIL (200 today); others pass.

- [ ] **Step 4: Implement the middleware**

In `src/gitstow/web/server.py`, add imports:

```python
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
```

Add above `create_app`:

```python
# gitstow ui executes git and deletes directories. Binding to 127.0.0.1 stops
# LAN access, but NOT cross-origin form POSTs from any website the user visits,
# nor DNS-rebinding. Browsers attach Origin to all cross-origin POSTs — reject
# anything that isn't loopback. Header-less requests (curl) pass: CSRF is a
# browser vector, and this is not authentication.
_ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _header_hostname(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname
```

Inside `create_app()`, right after the `FastAPI(...)` construction:

```python
    @app.middleware("http")
    async def _reject_cross_origin_writes(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and _header_hostname(origin) not in _ALLOWED_HOSTNAMES:
                return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
            host = _header_hostname(request.headers.get("host"))
            if host is not None and host not in _ALLOWED_HOSTNAMES:
                return JSONResponse({"error": "unexpected Host header"}, status_code=403)
        return await call_next(request)
```

- [ ] **Step 5: Run the web suite**

Run: `pytest tests/test_serve.py -v`
Expected: all PASS (including all 34 pre-existing web tests — they now ride on `base_url="http://127.0.0.1"`).

- [ ] **Step 6: Full suite + commit**

```bash
git add src/gitstow/web/server.py tests/test_serve.py
git commit -m "fix(web): reject cross-origin and DNS-rebinding POSTs to the local UI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Path containment check in CLI `remove --delete` (B12)

**Files:**
- Modify: `src/gitstow/cli/remove.py:63-73`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `remove --delete` refuses (exit 1, nothing deleted, nothing untracked) when the repo's resolved path is not inside the workspace root — same defensive rule the web delete route already enforces.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestRemoveContainment:
    def test_delete_refuses_path_outside_workspace(self, tmp_path, monkeypatch):
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
        outside = tmp_path / "outside-target"
        (outside / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="flat")]))
        # A traversal-shaped name resolves outside the workspace root.
        store = RepoStore(path=repos_file)
        store.add(Repo(owner="", name="../outside-target", remote_url="u", workspace="ws"))

        result = CliRunner().invoke(app, ["remove", "../outside-target", "--yes", "--delete"])

        assert result.exit_code == 1
        assert outside.exists()  # nothing was deleted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestRemoveContainment -v`
Expected: FAIL — current code rmtree's the outside path.

- [ ] **Step 3: Implement**

In `src/gitstow/cli/remove.py`, insert the guard before the store removal (so a refused delete changes nothing), replacing the block starting at the `# Remove from store` comment:

```python
    # Containment guard — never delete outside the workspace root
    # (mirrors the web delete route's defensive check).
    if delete_files and path.exists():
        ws_root = ws.get_path().resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(ws_root):
            err_console.print(
                f"[red]Error:[/red] refusing to delete path outside workspace: {resolved}"
            )
            raise typer.Exit(code=1)

    # Remove from store
    store.remove(repo.key, workspace=ws.label)
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py -v && pytest -q`

```bash
git add src/gitstow/cli/remove.py tests/test_cli.py
git commit -m "fix: remove --delete refuses paths outside the workspace root

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Workspace label validation (B11)

**Files:**
- Modify: `src/gitstow/cli/workspace_cmd.py` (`workspace_add`), `src/gitstow/cli/onboard.py` (`_setup_workspace`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `is_valid_label(label: str) -> bool` in `workspace_cmd.py` (regex `^[a-z0-9][a-z0-9_-]*$`), enforced by `workspace add` (exit 1 with the allowed charset) and by the onboarding prompt (re-prompts until valid). Labels can never contain `:` or `/`, which would corrupt `global_key` parsing and web routes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestWorkspaceLabelValidation:
    @pytest.mark.parametrize("bad_label", ["has:colon", "has/slash", "Has Space", "UPPER", ""])
    def test_workspace_add_rejects_invalid_labels(self, bad_label, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config

        config_file = tmp_path / "config.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", tmp_path / "repos.yaml")
        save_config(Settings(workspaces=[Workspace(path=str(tmp_path / "w"), label="oss", layout="structured")]))

        result = CliRunner().invoke(app, ["workspace", "add", str(tmp_path / "x"), "--label", bad_label])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestWorkspaceLabelValidation -v`
Expected: FAIL for every parametrized label (exit 0 today).

- [ ] **Step 3: Implement**

In `src/gitstow/cli/workspace_cmd.py`, add near the top:

```python
import re

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def is_valid_label(label: str) -> bool:
    """Labels appear in global keys (workspace:key) and URLs — restrict the charset."""
    return bool(_LABEL_RE.match(label))
```

In `workspace_add`, before the uniqueness check:

```python
    if not is_valid_label(label):
        err_console.print(
            f"[red]Error:[/red] Invalid label '{label}'. "
            "Use lowercase letters, digits, '-' or '_' (must start with a letter or digit)."
        )
        raise typer.Exit(code=1)
```

In `src/gitstow/cli/onboard.py` `_setup_workspace`, replace the label prompt with a validating loop:

```python
    from gitstow.cli.workspace_cmd import is_valid_label

    label_default = default_label or ws_path.name.lower()
    while True:
        label = typer.prompt("     Label", default=label_default, show_default=True).strip().lower()
        if is_valid_label(label):
            break
        console.print("     [red]Invalid label[/red] — lowercase letters, digits, '-' or '_' only.")
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py tests/test_onboard.py -v && pytest -q`

```bash
git add src/gitstow/cli/workspace_cmd.py src/gitstow/cli/onboard.py tests/test_cli.py
git commit -m "fix: validate workspace labels (no ':' or '/' that break global keys)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Test gate before PyPI publish (D1)

**Files:**
- Modify: `.github/workflows/publish.yml`

**Interfaces:**
- Produces: a `test` job (install `.[dev]`, ruff, pytest) that the `publish` job `needs:` — a failing suite blocks the release.

- [ ] **Step 1: Add the test job**

In `.github/workflows/publish.yml`, insert before the `publish` job and gate publish on it:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check src/

      - name: Test
        run: pytest --tb=short

  publish:
    needs: test
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      contents: read
      id-token: write
    steps:
      # ... existing steps unchanged ...
```

- [ ] **Step 2: Validate the workflow syntax**

Run: `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/publish.yml').read_text()); print('workflow OK')"`
Expected: `workflow OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: gate PyPI publish on lint + tests

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Wave completion checklist

- [ ] `pytest -q` — full suite green
- [ ] `ruff check src/` — clean
- [ ] Manual smoke: `gitstow add https://github.com/octocat/Hello-World/tree/master` clones `octocat/Hello-World` (deep-URL fix, needs network)
- [ ] Manual smoke: `gitstow ui` still serves and mutates normally from the browser
- [ ] Check off Wave 1 items in `docs/building/audit-2026-07-06.md`
- [ ] Update `CHANGELOG.md` under `[Unreleased]` → becomes 0.2.8 at release
