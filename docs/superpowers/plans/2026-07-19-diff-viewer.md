# Diff Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** View-only diff viewing for dirty repos — GitHub-Desktop-style Changes section (staged/unstaged/untracked groups, click-to-expand line diffs) on the web repo page, plus a `gitstow diff <repo>` CLI passthrough.

**Architecture:** All git calls go in `core/git.py` (project rule: nothing else shells out). A new `core/diff.py` parses unified-diff text into hunks for the Jinja template. Web renders server-side with htmx lazy-loading per file; CLI hands the TTY to git's own colored diff.

**Tech Stack:** Python 3.10+, Typer, FastAPI + Jinja2 + htmx (all already in place). Zero new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-19-diff-viewer-design.md`

## Global Constraints

- View-only: no staging, committing, discarding, editing.
- CLI never shells out to git directly — every git call lives in `core/git.py`.
- Diffs over 500 lines truncate with a notice; binary files show "binary file changed".
- Tests style: `tests/test_git.py` mocks `_run_git`; `tests/test_serve.py` monkeypatches git functions (never shells out).
- Run `pytest` (full suite) and `ruff check src/` before finishing each task.
- Do NOT release/publish anything.

---

### Task 1: `core/git.py` — `get_changed_files()`

**Files:**
- Modify: `src/gitstow/core/git.py` (add after `get_status`, ~line 250)
- Test: `tests/test_git.py` (append)

**Interfaces:**
- Consumes: existing `_run_git(args, cwd)` helper.
- Produces: `FileChange` (fields: `path: str, kind: str, added: int, removed: int, binary: bool, old_path: str`), `ChangedFiles` (fields: `staged: list[FileChange], unstaged: list[FileChange], untracked: list[str]`), `get_changed_files(repo_path: Path) -> ChangedFiles`. Task 5 (web) imports all three names from `gitstow.core.git`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_git.py`:

```python
from gitstow.core.git import ChangedFiles, FileChange, get_changed_files


def _proc(stdout="", returncode=0):
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


class TestGetChangedFiles:
    @patch("gitstow.core.git._run_git")
    def test_groups_staged_unstaged_untracked(self, mock_run):
        def fake(args, cwd=None, **kw):
            if args[0] == "status":
                return _proc(
                    "1 .M N... 100644 100644 100644 abc def src/app.py\n"
                    "1 A. N... 000000 100644 100644 abc def new.py\n"
                    "? notes.txt\n"
                )
            if "--cached" in args:
                return _proc("5\t0\tnew.py\n")
            return _proc("3\t2\tsrc/app.py\n")
        mock_run.side_effect = fake

        c = get_changed_files(Path("/repo"))
        assert c.untracked == ["notes.txt"]
        assert c.unstaged == [FileChange(path="src/app.py", kind="modified", added=3, removed=2)]
        assert c.staged == [FileChange(path="new.py", kind="added", added=5, removed=0)]

    @patch("gitstow.core.git._run_git")
    def test_partially_staged_file_appears_in_both_groups(self, mock_run):
        def fake(args, cwd=None, **kw):
            if args[0] == "status":
                return _proc("1 MM N... 100644 100644 100644 abc def both.py\n")
            return _proc("1\t1\tboth.py\n")
        mock_run.side_effect = fake

        c = get_changed_files(Path("/repo"))
        assert [f.path for f in c.staged] == ["both.py"]
        assert [f.path for f in c.unstaged] == ["both.py"]

    @patch("gitstow.core.git._run_git")
    def test_rename_and_binary(self, mock_run):
        def fake(args, cwd=None, **kw):
            if args[0] == "status":
                return _proc(
                    "2 R. N... 100644 100644 100644 abc def R100 new_name.py\told_name.py\n"
                    "1 .M N... 100644 100644 100644 abc def logo.png\n"
                )
            if "--cached" in args:
                return _proc("0\t0\told_name.py => new_name.py\n")
            return _proc("-\t-\tlogo.png\n")
        mock_run.side_effect = fake

        c = get_changed_files(Path("/repo"))
        assert c.staged == [FileChange(path="new_name.py", kind="renamed", old_path="old_name.py")]
        assert c.unstaged == [FileChange(path="logo.png", kind="modified", binary=True)]

    @patch("gitstow.core.git._run_git")
    def test_brace_rename_path_in_numstat(self, mock_run):
        def fake(args, cwd=None, **kw):
            if args[0] == "status":
                return _proc("2 R. N... 100644 100644 100644 abc def R90 src/b.py\tsrc/a.py\n")
            if "--cached" in args:
                return _proc("2\t1\tsrc/{a.py => b.py}\n")
            return _proc("")
        mock_run.side_effect = fake

        c = get_changed_files(Path("/repo"))
        assert c.staged[0].added == 2 and c.staged[0].removed == 1

    @patch("gitstow.core.git._run_git")
    def test_unreadable_repo_returns_empty(self, mock_run):
        mock_run.return_value = _proc("fatal: not a git repository", returncode=128)
        assert get_changed_files(Path("/repo")) == ChangedFiles()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git.py::TestGetChangedFiles -v`
Expected: FAIL — `ImportError: cannot import name 'ChangedFiles'`

- [ ] **Step 3: Implement** — in `src/gitstow/core/git.py`. Change the dataclasses import at the top to `from dataclasses import dataclass, field`, then add after `get_status`:

```python
@dataclass
class FileChange:
    """One changed file in the working tree or index."""

    path: str
    kind: str            # "modified" | "added" | "deleted" | "renamed"
    added: int = 0       # line counts; 0 for binary files
    removed: int = 0
    binary: bool = False
    old_path: str = ""   # set when kind == "renamed"


@dataclass
class ChangedFiles:
    """Working-tree changes grouped the way GitHub Desktop groups them."""

    staged: list[FileChange] = field(default_factory=list)
    unstaged: list[FileChange] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)


_KIND = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}


def _numstat_new_path(raw: str) -> str:
    """Numstat renders renames as 'old => new' or 'dir/{old => new}/file'."""
    if " => " not in raw:
        return raw
    if "{" in raw:
        pre, rest = raw.split("{", 1)
        mid, post = rest.split("}", 1)
        return pre + mid.split(" => ")[1] + post
    return raw.split(" => ")[1]


def _numstat_map(repo_path: Path, cached: bool) -> dict[str, tuple[int, int, bool]]:
    """path -> (added, removed, binary) from one `git diff --numstat` call."""
    args = ["diff", "--numstat"] + (["--cached"] if cached else [])
    counts: dict[str, tuple[int, int, bool]] = {}
    for line in _run_git(args, cwd=repo_path).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, r, raw = parts[0], parts[1], "\t".join(parts[2:])
        binary = a == "-"
        counts[_numstat_new_path(raw)] = (
            0 if binary else int(a),
            0 if binary else int(r),
            binary,
        )
    return counts


def _add_change(changes: ChangedFiles, xy: str, path: str, old_path: str,
                staged_counts: dict, unstaged_counts: dict) -> None:
    if xy[0] != ".":
        a, r, binary = staged_counts.get(path, (0, 0, False))
        changes.staged.append(FileChange(
            path=path, kind=_KIND.get(xy[0], "modified"),
            added=a, removed=r, binary=binary, old_path=old_path))
    if xy[1] != ".":
        a, r, binary = unstaged_counts.get(path, (0, 0, False))
        changes.unstaged.append(FileChange(
            path=path, kind=_KIND.get(xy[1], "modified"),
            added=a, removed=r, binary=binary, old_path=old_path))


def get_changed_files(repo_path: Path) -> ChangedFiles:
    """Per-file working-tree changes: one porcelain-v2 status call for
    grouping + change kind, two numstat calls for per-file line counts.

    Porcelain v2 entry formats (git-status docs):
      1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
      2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\t<origPath>
      u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
      ? <path>
    X = staged column, Y = unstaged column, "." = unchanged.
    """
    result = _run_git(["status", "--porcelain=v2"], cwd=repo_path)
    if result.returncode != 0:
        return ChangedFiles()

    staged_counts = _numstat_map(repo_path, cached=True)
    unstaged_counts = _numstat_map(repo_path, cached=False)
    changes = ChangedFiles()

    for line in result.stdout.splitlines():
        if line.startswith("? "):
            changes.untracked.append(line[2:])
        elif line.startswith("1 "):
            _add_change(changes, line.split(" ", 2)[1], line.split(" ", 8)[8],
                        "", staged_counts, unstaged_counts)
        elif line.startswith("2 "):
            path, _, old = line.split(" ", 9)[9].partition("\t")
            _add_change(changes, line.split(" ", 2)[1], path, old,
                        staged_counts, unstaged_counts)
        elif line.startswith("u "):
            # Unmerged (conflict) — show as an unstaged modification.
            _add_change(changes, ".M", line.split(" ", 10)[10],
                        "", staged_counts, unstaged_counts)
    return changes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git.py -v`
Expected: all PASS (new + pre-existing)

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`
```bash
git add src/gitstow/core/git.py tests/test_git.py
git commit -m "feat(core): get_changed_files — per-file staged/unstaged/untracked"
```

---

### Task 2: `core/git.py` — `get_file_diff()` + `run_interactive_diff()`

**Files:**
- Modify: `src/gitstow/core/git.py` (append after `get_changed_files`)
- Test: `tests/test_git.py` (append)

**Interfaces:**
- Consumes: `_run_git`.
- Produces: `get_file_diff(repo_path: Path, file: str, *, staged: bool = False, untracked: bool = False) -> str` (raw unified diff text; empty string when no diff) — used by Task 5. `run_interactive_diff(repo_path: Path, staged: bool = False) -> int` (git's exit code) — used by Task 4.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_git.py`:

```python
from gitstow.core.git import get_file_diff, run_interactive_diff


class TestGetFileDiff:
    @patch("gitstow.core.git._run_git")
    def test_unstaged_diff_args(self, mock_run):
        mock_run.return_value = _proc("diff --git a/f b/f\n")
        out = get_file_diff(Path("/repo"), "f")
        assert out == "diff --git a/f b/f\n"
        assert mock_run.call_args[0][0] == ["diff", "--", "f"]

    @patch("gitstow.core.git._run_git")
    def test_staged_diff_args(self, mock_run):
        mock_run.return_value = _proc("")
        get_file_diff(Path("/repo"), "f", staged=True)
        assert mock_run.call_args[0][0] == ["diff", "--cached", "--", "f"]

    @patch("gitstow.core.git._run_git")
    def test_untracked_diffs_against_dev_null(self, mock_run):
        mock_run.return_value = _proc("+new line\n", returncode=1)  # --no-index exits 1 on diff
        out = get_file_diff(Path("/repo"), "f", untracked=True)
        assert out == "+new line\n"
        assert mock_run.call_args[0][0] == ["diff", "--no-index", "--", "/dev/null", "f"]


class TestRunInteractiveDiff:
    @patch("gitstow.core.git.subprocess.run")
    def test_inherits_tty_and_passes_staged(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        code = run_interactive_diff(Path("/repo"), staged=True)
        assert code == 0
        args, kwargs = mock_run.call_args
        assert args[0] == ["git", "diff", "--cached"]
        assert kwargs.get("cwd") == Path("/repo")
        assert "capture_output" not in kwargs  # output goes straight to the TTY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git.py::TestGetFileDiff tests/test_git.py::TestRunInteractiveDiff -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement** — append to `src/gitstow/core/git.py`:

```python
def get_file_diff(
    repo_path: Path,
    file: str,
    *,
    staged: bool = False,
    untracked: bool = False,
) -> str:
    """Raw unified diff for one file, view-only.

    Untracked files diff against /dev/null so they render as all-new lines
    (git for Windows translates /dev/null itself). `--no-index` exits 1 when
    the files differ — that is success here, so we ignore the return code.
    """
    if untracked:
        args = ["diff", "--no-index", "--", "/dev/null", file]
    elif staged:
        args = ["diff", "--cached", "--", file]
    else:
        args = ["diff", "--", file]
    return _run_git(args, cwd=repo_path).stdout


def run_interactive_diff(repo_path: Path, staged: bool = False) -> int:
    """Hand the terminal to `git diff` — inherits TTY, color, and pager.

    The one intentional exception to captured _run_git calls: repainting
    git's terminal diff would be worse than letting git do it.
    """
    args = ["git", "diff"] + (["--cached"] if staged else [])
    return subprocess.run(args, cwd=repo_path).returncode
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`
```bash
git add src/gitstow/core/git.py tests/test_git.py
git commit -m "feat(core): get_file_diff + run_interactive_diff"
```

---

### Task 3: `core/diff.py` — unified-diff parser

**Files:**
- Create: `src/gitstow/core/diff.py`
- Test: `tests/test_diff.py` (new file)

**Interfaces:**
- Consumes: nothing from other tasks (pure text parsing).
- Produces: `DiffLine` (fields: `kind: str` — `"add"|"del"|"ctx"` — `old_no: int | None, new_no: int | None, text: str`), `Hunk` (fields: `header: str, lines: list[DiffLine]`), `ParsedDiff` (fields: `hunks: list[Hunk], binary: bool, truncated: bool`), `parse_unified_diff(text: str, max_lines: int = 500) -> ParsedDiff`. Task 5 imports `parse_unified_diff` from `gitstow.core.diff`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_diff.py`:

```python
"""Tests for the unified-diff parser feeding the web diff view."""

from gitstow.core.diff import parse_unified_diff

SIMPLE = """\
diff --git a/f.py b/f.py
index 000..111 100644
--- a/f.py
+++ b/f.py
@@ -1,3 +1,3 @@
 keep
-old line
+new line
"""


def test_parses_hunk_with_line_numbers():
    d = parse_unified_diff(SIMPLE)
    assert not d.binary and not d.truncated
    assert len(d.hunks) == 1
    h = d.hunks[0]
    assert h.header == "@@ -1,3 +1,3 @@"
    kinds = [(ln.kind, ln.old_no, ln.new_no, ln.text) for ln in h.lines]
    assert kinds == [
        ("ctx", 1, 1, "keep"),
        ("del", 2, None, "old line"),
        ("add", None, 2, "new line"),
    ]


def test_new_file_all_added():
    text = (
        "diff --git a/n b/n\n--- /dev/null\n+++ b/n\n"
        "@@ -0,0 +1,2 @@\n+one\n+two\n"
    )
    d = parse_unified_diff(text)
    assert [ln.kind for ln in d.hunks[0].lines] == ["add", "add"]
    assert [ln.new_no for ln in d.hunks[0].lines] == [1, 2]


def test_multiple_hunks():
    text = (
        "--- a/f\n+++ b/f\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "@@ -10,2 +10,2 @@\n ctx\n-c\n+d\n"
    )
    d = parse_unified_diff(text)
    assert len(d.hunks) == 2
    assert d.hunks[1].lines[0].old_no == 10


def test_binary():
    d = parse_unified_diff("diff --git a/x b/x\nBinary files a/x and b/x differ\n")
    assert d.binary is True
    assert d.hunks == []


def test_truncation():
    body = "".join(f"+line {i}\n" for i in range(600))
    d = parse_unified_diff("--- a/f\n+++ b/f\n@@ -0,0 +1,600 @@\n" + body)
    assert d.truncated is True
    assert sum(len(h.lines) for h in d.hunks) == 500


def test_no_newline_marker_skipped():
    text = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n"
    d = parse_unified_diff(text)
    assert [ln.kind for ln in d.hunks[0].lines] == ["del", "add"]


def test_empty_input():
    d = parse_unified_diff("")
    assert d.hunks == [] and not d.binary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_diff.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitstow.core.diff'`

- [ ] **Step 3: Implement** — create `src/gitstow/core/diff.py`:

```python
"""Unified-diff text → structured hunks for the web diff view.

Feeds the Jinja template only — the CLI hands the terminal to git itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LINES = 500


@dataclass
class DiffLine:
    kind: str               # "add" | "del" | "ctx"
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    header: str             # the raw "@@ -a,b +c,d @@ ..." line
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class ParsedDiff:
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False
    truncated: bool = False


def parse_unified_diff(text: str, max_lines: int = MAX_LINES) -> ParsedDiff:
    """Parse `git diff` output for ONE file into hunks with line numbers.

    Lines before the first @@ header (diff --git, index, ---, +++) are
    skipped; "\\ No newline at end of file" markers are skipped; anything
    past max_lines truncates the result.
    """
    parsed = ParsedDiff()
    old_no = new_no = 0
    shown = 0
    for line in text.splitlines():
        if line.startswith("Binary files"):
            parsed.binary = True
            return parsed
        if line.startswith("@@"):
            try:
                nums = line.split("@@")[1].split()
                old_no = int(nums[0].lstrip("-").split(",")[0])
                new_no = int(nums[1].lstrip("+").split(",")[0])
            except (IndexError, ValueError):
                continue
            parsed.hunks.append(Hunk(header=line.rstrip()))
            continue
        if not parsed.hunks or line.startswith("\\"):
            continue
        if shown >= max_lines:
            parsed.truncated = True
            return parsed
        if line.startswith("+"):
            parsed.hunks[-1].lines.append(DiffLine("add", None, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            parsed.hunks[-1].lines.append(DiffLine("del", old_no, None, line[1:]))
            old_no += 1
        else:
            parsed.hunks[-1].lines.append(DiffLine("ctx", old_no, new_no, line[1:]))
            old_no += 1
            new_no += 1
        shown += 1
    return parsed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_diff.py -v`
Expected: all PASS

- [ ] **Step 5: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`
```bash
git add src/gitstow/core/diff.py tests/test_diff.py
git commit -m "feat(core): unified-diff parser for the web diff view"
```

---

### Task 4: CLI — `gitstow diff <repo>`

**Files:**
- Create: `src/gitstow/cli/diff_cmd.py`
- Modify: `src/gitstow/cli/main.py` (import block ~line 87-110, registration block ~line 112)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `run_interactive_diff(repo_path, staged) -> int` (Task 2), `get_status` and `is_git_repo` from `gitstow.core.git`, `resolve_repo(store, settings, key, workspace_label)` from `gitstow.cli.helpers`, `load_config` from `gitstow.core.config`, `RepoStore` from `gitstow.core.repo`.
- Produces: `gitstow diff <repo> [--staged]` command. Nothing downstream consumes it.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`. First read that file's existing fixture/runner pattern (it has a Typer `CliRunner` setup — follow it exactly; the fixtures below assume a `runner` + isolated-config pattern; adapt fixture names to what the file actually uses):

```python
class TestDiffCommand:
    def _seed(self, tmp_path, monkeypatch):
        """One workspace 'ws' with one repo 'owner/repo' present on disk."""
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "owner" / "repo" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        store = RepoStore()
        store.add(Repo(key="owner/repo", remote_url="", workspace="ws"))
        return ws_dir / "owner" / "repo"

    def test_clean_repo_prints_no_changes(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.get_status", lambda p: RepoStatus(branch="main"))
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 0
        assert "no local changes" in result.output

    def test_dirty_repo_hands_off_to_git(self, tmp_path, monkeypatch):
        repo_path = self._seed(tmp_path, monkeypatch)
        monkeypatch.setattr("gitstow.cli.diff_cmd.get_status", lambda p: RepoStatus(branch="main", dirty=1))
        called = {}

        def fake_diff(path, staged=False):
            called.update(path=path, staged=staged)
            return 0

        monkeypatch.setattr("gitstow.cli.diff_cmd.run_interactive_diff", fake_diff)
        result = runner.invoke(app, ["diff", "owner/repo", "--staged"])
        assert result.exit_code == 0
        assert called == {"path": repo_path, "staged": True}

    def test_missing_repo_errors(self, tmp_path, monkeypatch):
        repo_path = self._seed(tmp_path, monkeypatch)
        import shutil
        shutil.rmtree(repo_path)
        result = runner.invoke(app, ["diff", "owner/repo"])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestDiffCommand -v`
Expected: FAIL — unknown command "diff"

- [ ] **Step 3: Implement** — create `src/gitstow/cli/diff_cmd.py`:

```python
"""gitstow diff — view a repo's local changes via git's own colored diff."""

from __future__ import annotations

import typer
from rich.console import Console

from gitstow.cli.helpers import resolve_repo
from gitstow.core.config import load_config
from gitstow.core.git import get_status, is_git_repo, run_interactive_diff
from gitstow.core.repo import RepoStore

console = Console()
err_console = Console(stderr=True)


def diff_cmd(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repo key (e.g. owner/repo, or just repo)."),
    staged: bool = typer.Option(
        False, "--staged", help="Show staged changes instead of unstaged."
    ),
) -> None:
    """Show a repo's uncommitted changes — view-only, git's own colored diff."""
    settings = load_config()
    store = RepoStore()
    ws_label = ctx.obj.get("workspace") if ctx.obj else None
    r, ws = resolve_repo(store, settings, repo, ws_label)
    path = r.get_path(ws.get_path())
    if not path.exists() or not is_git_repo(path):
        err_console.print(f"[red]Error:[/red] [bold]{r.key}[/bold] is missing on disk at {path}")
        raise typer.Exit(code=1)
    if get_status(path).clean:
        console.print(f"[green]✓[/green] [bold]{r.key}[/bold] has no local changes")
        return
    raise typer.Exit(code=run_interactive_diff(path, staged=staged))
```

Then in `src/gitstow/cli/main.py` add to the import block:

```python
from gitstow.cli.diff_cmd import diff_cmd  # noqa: E402
```

and to the registration block, after `app.command()(fetch)`:

```python
app.command("diff")(diff_cmd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Smoke-test the real command**

Run: `python -m gitstow --help | grep diff` (or `gitstow --help`)
Expected: `diff` listed with its help line.

- [ ] **Step 6: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`
```bash
git add src/gitstow/cli/diff_cmd.py src/gitstow/cli/main.py tests/test_cli.py
git commit -m "feat(cli): gitstow diff — colored git diff passthrough per repo"
```

---

### Task 5: Web — Changes section, diff endpoint, dashboard badge link

**Files:**
- Modify: `src/gitstow/web/routes/pages.py` (`render_repo_detail` + new `GET /repos/{workspace}/{key:path}/diff` route)
- Modify: `src/gitstow/web/routes/dashboard.py` (`_build_repos_data`, ~line 238 — add `changes_link`)
- Modify: `src/gitstow/web/routes/repos.py` (`_row_context` — add `changes_link`)
- Modify: `src/gitstow/web/templates/_repo_drawer.html` (include Changes section after Metadata)
- Modify: `src/gitstow/web/templates/partials/repo_row.html` (status badge links to `#changes`)
- Create: `src/gitstow/web/templates/partials/changes_section.html`
- Create: `src/gitstow/web/templates/partials/diff_view.html`
- Modify: `src/gitstow/web/static/app.css` (append diff styles)

**Interfaces:**
- Consumes: `get_changed_files`, `get_file_diff`, `ChangedFiles` (Tasks 1-2, from `gitstow.core.git`); `parse_unified_diff` (Task 3, from `gitstow.core.diff`); existing `render`, `classify`, `_present`.
- Produces: `GET /repos/{workspace}/{key:path}/diff?file=<path>&group=staged|unstaged|untracked` returning the rendered `partials/diff_view.html`; drawer context gains `changes` (a `ChangedFiles` or `None`); row contexts gain `changes_link: str`.

- [ ] **Step 1: pages.py — pass changes into the drawer.** In `render_repo_detail`, after `state = classify(...)`, add:

```python
    # Changes section data — only when there is something to show (spec:
    # section renders only for repos with local changes).
    changes = None
    if exists and state.has_local_changes:
        changes = get_changed_files(repo_path)
```

Add `get_changed_files` to the existing `from gitstow.core.git import ...` line. Pass `changes=changes` in the final `render(...)` call.

- [ ] **Step 2: pages.py — the diff endpoint.** Add imports `from gitstow.core.diff import parse_unified_diff` and `get_file_diff` (same core.git import line), then append the route:

```python
@router.get("/repos/{workspace}/{key:path}/diff", response_class=HTMLResponse)
async def file_diff(
    workspace: str, key: str, request: Request, file: str, group: str = "unstaged"
):
    """Rendered line-by-line diff for one file — htmx-loaded on expand."""
    settings = load_config()
    store = RepoStore()
    ws = settings.get_workspace(workspace)
    repo = store.get(key, workspace=workspace) if ws else None
    if ws is None or repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    repo_path = repo.get_path(ws.get_path())

    # Trust boundary: `file` comes from the query string — refuse anything
    # that resolves outside the repo (e.g. ../../etc/passwd via --no-index).
    if not (repo_path / file).resolve().is_relative_to(repo_path.resolve()):
        raise HTTPException(status_code=400, detail="file outside repo")

    raw = get_file_diff(
        repo_path, file,
        staged=(group == "staged"),
        untracked=(group == "untracked"),
    )
    return render(
        request, "partials/diff_view.html", page="dashboard",
        diff=parse_unified_diff(raw), file=file,
    )
```

(Route shape note: the greedy `{key:path}` backtracks correctly before the literal `/diff` tail, and this is the only GET under `/repos/`, so it can't shadow the `/repo/{...}` detail page.)

- [ ] **Step 3: Create `partials/changes_section.html`:**

```html
<section class="section" id="changes">
  <h4>Changes</h4>
  {% for group, files in [("staged", changes.staged), ("unstaged", changes.unstaged)] %}
    {% if files %}
    <div class="diff-group">
      <div class="diff-group-head">{{ group }} <span class="diff-count">{{ files|length }}</span></div>
      {% for f in files %}
      <details class="diff-file">
        <summary title="{{ f.kind }}: {{ f.path }} — click to view the diff">
          <span class="diff-kind diff-kind-{{ f.kind }}">{{ f.kind[0]|upper }}</span>
          <span class="diff-path">{% if f.old_path %}{{ f.old_path }} → {% endif %}{{ f.path }}</span>
          {% if f.binary %}<span class="diff-stat">binary</span>
          {% else %}<span class="diff-stat"><span class="diff-add">+{{ f.added }}</span> <span class="diff-del">−{{ f.removed }}</span></span>{% endif %}
        </summary>
        <div class="diff-body"
             hx-get="/repos/{{ repo.workspace }}/{{ repo.key }}/diff?file={{ f.path|urlencode }}&group={{ group }}"
             hx-trigger="intersect once"
             hx-swap="innerHTML"><div class="diff-note">loading…</div></div>
      </details>
      {% endfor %}
    </div>
    {% endif %}
  {% endfor %}
  {% if changes.untracked %}
  <div class="diff-group">
    <div class="diff-group-head">untracked <span class="diff-count">{{ changes.untracked|length }}</span></div>
    {% for path in changes.untracked %}
    <details class="diff-file">
      <summary title="untracked: {{ path }} — click to view the file as all-new lines">
        <span class="diff-kind diff-kind-added">U</span>
        <span class="diff-path">{{ path }}</span>
        <span class="diff-stat"><span class="diff-add">new</span></span>
      </summary>
      <div class="diff-body"
           hx-get="/repos/{{ repo.workspace }}/{{ repo.key }}/diff?file={{ path|urlencode }}&group=untracked"
           hx-trigger="intersect once"
           hx-swap="innerHTML"><div class="diff-note">loading…</div></div>
    </details>
    {% endfor %}
  </div>
  {% endif %}
</section>
```

(`hx-trigger="intersect once"` = htmx's IntersectionObserver trigger: the body of a closed `<details>` isn't visible, so the diff loads the first time the user opens it. Lazy loading with zero custom JS.)

- [ ] **Step 4: Create `partials/diff_view.html`:**

```html
{% if diff.binary %}
  <div class="diff-note">Binary file changed — no text diff.</div>
{% elif not diff.hunks %}
  <div class="diff-note">No changes to show.</div>
{% else %}
  <div class="diff-scroll">
    <table class="diff-table">
      {% for hunk in diff.hunks %}
      <tr class="diff-hunk"><td class="diff-no"></td><td class="diff-no"></td><td class="diff-text">{{ hunk.header }}</td></tr>
      {% for line in hunk.lines %}
      <tr class="diff-line-{{ line.kind }}">
        <td class="diff-no">{{ line.old_no if line.old_no is not none }}</td>
        <td class="diff-no">{{ line.new_no if line.new_no is not none }}</td>
        <td class="diff-text">{{ line.text }}</td>
      </tr>
      {% endfor %}
      {% endfor %}
    </table>
  </div>
  {% if diff.truncated %}<div class="diff-note">Truncated — showing the first 500 lines. <code>gitstow diff</code> in a terminal shows everything.</div>{% endif %}
{% endif %}
```

(Jinja autoescaping handles hostile diff content; `{{ x if cond }}` renders empty when the condition is false.)

- [ ] **Step 5: Include in `_repo_drawer.html`** — right after the closing `</section>` of the Metadata block, before the Tags section:

```html
  {% if changes %}{% include "partials/changes_section.html" %}{% endif %}
```

- [ ] **Step 6: Badge links.** In `repos.py` `_row_context` return dict AND in `dashboard.py` `_build_repos_data`'s row dict (both build the same keys for `repo_row.html`), add:

```python
        "changes_link": (
            f"/repo/{repo.workspace}/{repo.key}#changes"
            if state.presence == "ok" and state.has_local_changes else ""
        ),
```

In `partials/repo_row.html`, replace the status span line with:

```html
    {% if repo.changes_link %}<a class="status-link" href="{{ repo.changes_link }}" title="{{ repo.status_tooltip }} — click to see what changed"><span class="status status-{{ repo.status_class }}"><span class="dot"></span>{{ repo.status_label }}</span></a>
    {% else %}<span class="status status-{{ repo.status_class }}" title="{{ repo.status_tooltip }}"><span class="dot"></span>{{ repo.status_label }}</span>{% endif %}
```

- [ ] **Step 7: CSS.** Append to `src/gitstow/web/static/app.css` (uses existing vars; `--c-ok` does not exist — use the palette below):

```css
/* ---- Diff viewer (repo drawer Changes section) ---- */
.status-link { text-decoration: none; }
.status-link:hover .status { filter: brightness(1.25); }

.diff-group { margin-bottom: 1.1rem; }
.diff-group-head {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--muted); margin-bottom: 0.4rem;
}
.diff-count { color: var(--muted-soft); margin-left: 0.3rem; }

.diff-file { border: 1px solid var(--border); border-radius: 6px; margin-bottom: 0.35rem; }
.diff-file summary {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.45rem 0.7rem; cursor: pointer; list-style: none;
  font-family: 'JetBrains Mono', monospace; font-size: 0.82rem;
}
.diff-file summary::-webkit-details-marker { display: none; }
.diff-file[open] summary { border-bottom: 1px solid var(--border); }
.diff-kind { font-weight: 700; width: 1.1em; text-align: center; }
.diff-kind-modified { color: #eab308; }
.diff-kind-added    { color: #4ade80; }
.diff-kind-deleted  { color: var(--c-conflict); }
.diff-kind-renamed  { color: var(--c-behind); }
.diff-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.diff-stat { font-size: 0.78rem; }
.diff-add { color: #4ade80; }
.diff-del { color: var(--c-conflict); }

.diff-body { max-height: 30rem; overflow-y: auto; }
.diff-scroll { overflow-x: auto; }   /* wide diffs scroll here, never the page */
.diff-table {
  border-collapse: collapse; width: 100%;
  font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; line-height: 1.5;
}
.diff-table td { padding: 0 0.6rem; white-space: pre; }
.diff-no {
  color: var(--muted-soft); text-align: right; width: 1%;
  user-select: none; border-right: 1px solid var(--border);
}
.diff-hunk .diff-text { color: var(--c-behind); background: rgba(59, 130, 246, 0.08); }
.diff-line-add .diff-text { background: rgba(74, 222, 128, 0.10); color: #d8f5e0; }
.diff-line-del .diff-text { background: rgba(248, 113, 113, 0.10); color: #f5d8d8; }
.diff-note { padding: 0.5rem 0.7rem; color: var(--muted); font-size: 0.82rem; }
```

Before finishing, open `app.css` and confirm `--border`, `--muted`, `--muted-soft` exist (they are used throughout the file); if the actual names differ (e.g. `--line`), use the file's names.

- [ ] **Step 8: Run the web tests to confirm nothing broke**

Run: `pytest tests/test_serve.py -q`
Expected: PASS (new behavior is tested in Task 6)

- [ ] **Step 9: Full suite + lint, then commit**

Run: `pytest -q && ruff check src/`
```bash
git add src/gitstow/web tests
git commit -m "feat(web): Changes section with per-file lazy diffs on repo page"
```

---

### Task 6: Web tests

**Files:**
- Test: `tests/test_serve.py` (append)

**Interfaces:**
- Consumes: fixtures `isolated`, `configured`, `workspace_dir`, `client`, helper `_fake_status` already in `tests/test_serve.py`; `ChangedFiles`, `FileChange` from `gitstow.core.git`.
- Produces: regression coverage; nothing downstream.

- [ ] **Step 1: Write the tests** — append to `tests/test_serve.py`. Follow the file's existing pattern for seeding a repo (look at how existing detail-page tests create a repo on disk + `RepoStore` record; reuse their approach):

```python
class TestDiffViewer:
    def _seed_repo(self, configured, workspace_dir):
        (workspace_dir / "owner" / "repo" / ".git").mkdir(parents=True)
        store = RepoStore()
        store.add(Repo(key="owner/repo", remote_url="", workspace="test-ws"))

    def test_drawer_shows_changes_when_dirty(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(configured, workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_status", lambda p: _fake_status(dirty=1)
        )
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_changed_files",
            lambda p: ChangedFiles(
                unstaged=[FileChange(path="src/app.py", kind="modified", added=3, removed=2)],
                untracked=["notes.txt"],
            ),
        )
        r = client.get("/repo/test-ws/owner/repo")
        assert r.status_code == 200
        assert 'id="changes"' in r.text
        assert "src/app.py" in r.text and "notes.txt" in r.text
        assert "+3" in r.text and "−2" in r.text

    def test_drawer_hides_changes_when_clean(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(configured, workspace_dir)
        monkeypatch.setattr("gitstow.web.routes.pages.get_status", lambda p: _fake_status())
        r = client.get("/repo/test-ws/owner/repo")
        assert r.status_code == 200
        assert 'id="changes"' not in r.text

    def test_diff_endpoint_renders_hunks(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(configured, workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=f&group=unstaged")
        assert r.status_code == 200
        assert "diff-line-add" in r.text and "diff-line-del" in r.text
        assert "old" in r.text and "new" in r.text

    def test_diff_endpoint_rejects_path_traversal(self, client, configured, workspace_dir):
        self._seed_repo(configured, workspace_dir)
        r = client.get("/repos/test-ws/owner/repo/diff?file=../../../etc/passwd&group=untracked")
        assert r.status_code == 400

    def test_diff_endpoint_escapes_hostile_content(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(configured, workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.pages.get_file_diff",
            lambda p, f, **kw: "--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+<script>alert(1)</script>\n",
        )
        r = client.get("/repos/test-ws/owner/repo/diff?file=f&group=unstaged")
        assert "<script>alert(1)</script>" not in r.text
        assert "&lt;script&gt;" in r.text

    def test_dashboard_badge_links_to_changes(self, client, configured, workspace_dir, monkeypatch):
        self._seed_repo(configured, workspace_dir)
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status", lambda p: _fake_status(dirty=2)
        )
        r = client.get("/")
        assert '/repo/test-ws/owner/repo#changes' in r.text
```

Add `ChangedFiles, FileChange` to the existing `from gitstow.core.git import ...` line at the top of the file.

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/test_serve.py::TestDiffViewer -v`
Expected: PASS. If a test fails, fix the implementation (Task 5), not the test, unless the test itself mis-seeds fixtures.

- [ ] **Step 3: Full suite + lint, then commit**

```bash
pytest -q && ruff check src/
git add tests/test_serve.py
git commit -m "test(web): diff viewer coverage — drawer, endpoint, traversal, escaping"
```

---

### Task 7: Docs + browser verification

**Files:**
- Modify: `README.md` (command list / features — add `diff`)
- Modify: `CLAUDE.md` (All Commands section: add `diff` under Core, bump count 37→38)
- Modify: `src/gitstow/skill/SKILL.md` (mention `gitstow diff <repo>` wherever commands are cataloged)
- Modify: `CHANGELOG.md` if present (add an Unreleased entry)

**Interfaces:** none — documentation and manual verification.

- [ ] **Step 1: Update docs.** In each file above, find where commands are listed and add `diff` alongside `status`/`pull` with a one-liner: "view a repo's uncommitted changes (staged/unstaged) — view-only". Keep each file's existing format. Do NOT bump the package version or release.

- [ ] **Step 2: Browser verification (project standard — TestClient is not sufficient for web/ changes).** Create a throwaway dirty repo and view it:

```bash
cd /private/tmp/gitstow-demo 2>/dev/null || true
# use any tracked repo; make it dirty:
#   echo "// tmp" >> <some tracked file>; echo "scratch" > newfile.txt
gitstow ui  # then open the repo's detail page
```

Verify in the real browser (claude-in-chrome or ask the user):
1. Changes section appears with correct groups and counts.
2. Clicking a file expands and loads the diff (green/red lines, line numbers).
3. Untracked file renders as all-new lines.
4. A wide diff scrolls inside its own container — the page body never scrolls horizontally.
5. Dashboard badge links to `#changes`.
6. Clean repo: no Changes section.
Then revert the scratch dirt.

- [ ] **Step 3: CLI verification.** Run `gitstow diff <that-repo>` — confirm colored git output; `--staged` after staging a file; clean repo prints "no local changes".

- [ ] **Step 4: Final full suite, lint, commit**

```bash
pytest -q && ruff check src/
git add README.md CLAUDE.md src/gitstow/skill/SKILL.md CHANGELOG.md
git commit -m "docs: gitstow diff + web Changes section"
```
