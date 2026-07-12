# Wave 2 — Shared Status Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One classifier for repo state — local composition (modified/staged/untracked) kept separate from remote relationship (in-sync/ahead/behind/diverged) — consumed by CLI, web, and JSON, implementing the CLAUDE.md status standard; plus the two big performance fixes (parallel web status, batched store writes) and pull-semantics alignment.

**Architecture:** New `core/status_model.py` with a frozen `RepoState` dataclass and a `classify()` factory. The Textual TUI is retired first (product decision 2026-07-06) so only two surfaces consume the model. Web `_classify`/`_delta` become thin adapters over `RepoState`. Pull decisions flow from `RepoState.blocks_pull` (modified/staged block; untracked alone does not — product decision 2026-07-06) in both CLI and web. `_build_repos_data` goes parallel via the existing `run_parallel`. `RepoStore.bulk()` batches N updates into one locked write.

**Tech Stack:** Python dataclasses, asyncio (`core/parallel.py`), FastAPI/Jinja2 templates, pytest.

## Global Constraints

- **Prerequisite: Wave 1 is merged** (this wave builds on `_mutate()` in RepoStore and the hardened git env).
- Product decisions (locked 2026-07-06): TUI is retired, not repaired. Bulk pull skips only repos with modified/staged changes — untracked-only repos ARE pulled.
- JSON compatibility: existing keys in `status --json` (`dirty`, `staged`, `untracked`, `ahead`, `behind`, `clean`, `status_symbol`, `ahead_behind`) are kept; new keys are additive.
- CSS class names in web templates (`clean`, `dirty`, `conflict`, `behind`, `ahead`, `frozen`) are kept — only the logic assigning them changes.
- The user-facing word for local changes is "local changes" with composition, never a bare "dirty" bucket (CLAUDE.md standard).
- All tests green + `ruff check src/` clean before each commit.
- No release mid-wave. This wave lands in 0.3.0 (TUI removal is the breaking change that justifies the minor bump).
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Audit coverage matrix

| Audit ID | Task |
|----------|------|
| P5 (TUI decision → retire) | Task 1 |
| A1 + P4 (shared classifier, standards) | Tasks 2–4 |
| B6 (pull semantics alignment) | Task 5 |
| E1 (parallel web status) | Task 6 |
| E2 (batched store writes) | Task 7 |

---

### Task 1: Retire the TUI (P5)

**Files:**
- Delete: `src/gitstow/tui/app.py`, `src/gitstow/tui/__init__.py`, `src/gitstow/cli/tui.py`
- Modify: `src/gitstow/cli/main.py` (imports + registration), `pyproject.toml` (`[tui]` extra), `README.md`, `docs/user/commands.md`, `CLAUDE.md`, `CHANGELOG.md`, `BACKLOG.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `gitstow tui` no longer exists; `gitstow --help` doesn't mention it. Git history preserves the code if the SSH use case ever returns.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestTuiRetired:
    def test_tui_command_gone(self):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        result = CliRunner().invoke(app, ["tui"])
        assert result.exit_code != 0

    def test_help_does_not_mention_tui(self):
        from typer.testing import CliRunner
        from gitstow.cli.main import app

        result = CliRunner().invoke(app, ["--help"])
        assert "tui" not in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestTuiRetired -v`
Expected: FAIL (`tui` runs / appears in help today).

- [ ] **Step 3: Remove the code**

```bash
git rm -r src/gitstow/tui src/gitstow/cli/tui.py
```

In `src/gitstow/cli/main.py` delete these two lines:

```python
from gitstow.cli.tui import tui_cmd  # noqa: E402
```
```python
app.command("tui")(tui_cmd)
```

In `pyproject.toml` delete the extra:

```toml
tui = [
    "textual>=0.50",
]
```

- [ ] **Step 4: Sweep the doc surfaces (grep-for-old-sibling)**

Run: `grep -rn -i "tui\b\|textual" README.md CLAUDE.md BACKLOG.md docs/ src/gitstow/skill/SKILL.md`

For every hit: remove the TUI command row/section, or reword to point at `gitstow ui`. Specifically:
- `README.md` — remove the TUI feature bullet/section.
- `docs/user/commands.md` — remove the `### gitstow tui` section (line ~520); adjust shell-integration copy if it references tui.
- `CLAUDE.md` — remove `tui/` from the architecture tree, `tui.py` from the cli listing, `tui` from the command list (count 32 → 31), and the `pip install -e ".[tui]"` dev line.
- `BACKLOG.md` — mark the "Fix TUI breakage" item as resolved-by-retirement with a one-line note.
- `CHANGELOG.md` — under `[Unreleased]`: `### Removed — the Textual TUI (gitstow tui). The web dashboard (gitstow ui) replaced it as the visual surface in v0.2.0; the TUI had been broken and parked since. Git history preserves it.`

- [ ] **Step 5: Run tests to verify pass, full suite**

Run: `pytest -q && ruff check src/`
Expected: all PASS, no dangling imports.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat!: retire the broken Textual TUI — gitstow ui is the visual surface

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `core/status_model.py` — the shared classifier (A1, P4)

**Files:**
- Create: `src/gitstow/core/status_model.py`
- Test: `tests/test_status_model.py` (new)

**Interfaces:**
- Consumes: `RepoStatus` from `core/git.py` (fields: `branch`, `dirty`, `staged`, `untracked`, `ahead`, `behind`, `has_upstream`).
- Produces (later tasks and all surfaces rely on these exact names):
  - `classify(*, exists: bool, frozen: bool, status: RepoStatus | None) -> RepoState`
  - `RepoState` frozen dataclass: fields `presence` ("ok"|"missing"|"unreadable"), `frozen: bool`, `branch: str`, `modified: int`, `staged: int`, `untracked: int`, `ahead: int`, `behind: int`, `has_upstream: bool`
  - properties: `has_local_changes: bool`, `blocks_pull: bool`, `local_summary: str` ("2 modified · 1 staged · 3 untracked" or "clean"), `remote_state: str` ("in-sync"|"ahead"|"behind"|"diverged"|"no-upstream"|"unknown"), `pull_action: str` ("pull"|"noop"|"skip-local"|"skip-frozen"|"skip-missing")
  - method: `to_dict() -> dict` (the JSON shape)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_status_model.py`:

```python
"""Tests for the shared repo-state classifier — the single source of truth
for status presentation across CLI, web, and JSON (CLAUDE.md standard)."""

import pytest

from gitstow.core.git import RepoStatus
from gitstow.core.status_model import RepoState, classify


def _status(**kw) -> RepoStatus:
    return RepoStatus(branch=kw.pop("branch", "main"), **kw)


class TestClassify:
    def test_missing_repo(self):
        state = classify(exists=False, frozen=False, status=None)
        assert state.presence == "missing"
        assert state.pull_action == "skip-missing"

    def test_unreadable_repo(self):
        state = classify(exists=True, frozen=False, status=None)
        assert state.presence == "unreadable"
        assert state.pull_action == "skip-missing"

    def test_clean_in_sync(self):
        state = classify(exists=True, frozen=False, status=_status())
        assert state.local_summary == "clean"
        assert state.remote_state == "in-sync"
        assert state.pull_action == "noop"

    def test_staged_only_is_local_change_not_clean(self):
        # The exact web-dashboard bug from the audit: staged-only showed "clean".
        state = classify(exists=True, frozen=False, status=_status(staged=2))
        assert state.has_local_changes is True
        assert state.local_summary == "2 staged"
        assert state.blocks_pull is True

    def test_untracked_only_does_not_block_pull(self):
        # Product decision 2026-07-06: untracked files never block bulk pull.
        state = classify(exists=True, frozen=False, status=_status(untracked=3, behind=2))
        assert state.has_local_changes is True
        assert state.blocks_pull is False
        assert state.pull_action == "pull"

    def test_modified_blocks_pull(self):
        state = classify(exists=True, frozen=False, status=_status(dirty=1, behind=2))
        assert state.blocks_pull is True
        assert state.pull_action == "skip-local"

    def test_composition_summary(self):
        state = classify(exists=True, frozen=False, status=_status(dirty=2, staged=1, untracked=3))
        assert state.local_summary == "2 modified · 1 staged · 3 untracked"

    def test_remote_states(self):
        assert classify(exists=True, frozen=False, status=_status(ahead=1)).remote_state == "ahead"
        assert classify(exists=True, frozen=False, status=_status(behind=1)).remote_state == "behind"
        assert classify(exists=True, frozen=False, status=_status(ahead=1, behind=1)).remote_state == "diverged"
        assert classify(exists=True, frozen=False, status=_status(has_upstream=False)).remote_state == "no-upstream"

    def test_frozen_wins_pull_action(self):
        state = classify(exists=True, frozen=True, status=_status(behind=5))
        assert state.pull_action == "skip-frozen"

    def test_behind_pulls(self):
        state = classify(exists=True, frozen=False, status=_status(behind=5))
        assert state.pull_action == "pull"

    def test_to_dict_shape(self):
        d = classify(exists=True, frozen=False, status=_status(dirty=1, ahead=2)).to_dict()
        assert d["presence"] == "ok"
        assert d["local"] == {"modified": 1, "staged": 0, "untracked": 0, "summary": "1 modified"}
        assert d["remote"] == {"state": "ahead", "ahead": 2, "behind": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_status_model.py -v`
Expected: FAIL with `ModuleNotFoundError: gitstow.core.status_model`.

- [ ] **Step 3: Implement**

Create `src/gitstow/core/status_model.py`:

```python
"""Shared repo-state classification — the single source of truth for how every
surface (CLI, web, JSON) describes a repo's state.

Two SEPARATE dimensions (per the project standards in CLAUDE.md):
  local  — working-tree composition: modified / staged / untracked counts
  remote — relationship to upstream: in-sync / ahead / behind / diverged
Presence (missing / unreadable) and frozen are overlays, not states of either.
"""

from __future__ import annotations

from dataclasses import dataclass

from gitstow.core.git import RepoStatus


@dataclass(frozen=True)
class RepoState:
    presence: str            # "ok" | "missing" | "unreadable"
    frozen: bool = False
    branch: str = ""
    modified: int = 0
    staged: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = True

    @property
    def has_local_changes(self) -> bool:
        return (self.modified + self.staged + self.untracked) > 0

    @property
    def blocks_pull(self) -> bool:
        """Modified or staged files block bulk pull. Untracked files do not —
        an ff-only pull can't lose them, and git aborts if one would be
        overwritten (product decision 2026-07-06)."""
        return (self.modified + self.staged) > 0

    @property
    def local_summary(self) -> str:
        if self.presence != "ok":
            return self.presence
        parts = []
        if self.modified:
            parts.append(f"{self.modified} modified")
        if self.staged:
            parts.append(f"{self.staged} staged")
        if self.untracked:
            parts.append(f"{self.untracked} untracked")
        return " · ".join(parts) if parts else "clean"

    @property
    def remote_state(self) -> str:
        if self.presence != "ok":
            return "unknown"
        if not self.has_upstream:
            return "no-upstream"
        if self.ahead and self.behind:
            return "diverged"
        if self.behind:
            return "behind"
        if self.ahead:
            return "ahead"
        return "in-sync"

    @property
    def pull_action(self) -> str:
        """What a bulk pull should do with this repo."""
        if self.presence != "ok":
            return "skip-missing"
        if self.frozen:
            return "skip-frozen"
        if self.blocks_pull:
            return "skip-local"
        if self.behind:
            return "pull"
        return "noop"

    def to_dict(self) -> dict:
        return {
            "presence": self.presence,
            "frozen": self.frozen,
            "branch": self.branch,
            "local": {
                "modified": self.modified,
                "staged": self.staged,
                "untracked": self.untracked,
                "summary": self.local_summary,
            },
            "remote": {
                "state": self.remote_state,
                "ahead": self.ahead,
                "behind": self.behind,
            },
        }


def classify(*, exists: bool, frozen: bool, status: RepoStatus | None) -> RepoState:
    """Build a RepoState from a git RepoStatus (or its absence)."""
    if not exists:
        return RepoState(presence="missing", frozen=frozen)
    if status is None:
        return RepoState(presence="unreadable", frozen=frozen)
    return RepoState(
        presence="ok",
        frozen=frozen,
        branch=status.branch,
        modified=status.dirty,
        staged=status.staged,
        untracked=status.untracked,
        ahead=status.ahead,
        behind=status.behind,
        has_upstream=status.has_upstream,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_status_model.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gitstow/core/status_model.py tests/test_status_model.py
git commit -m "feat: shared repo-state classifier separating local composition from remote state

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: CLI `status` consumes the model (P4)

**Files:**
- Modify: `src/gitstow/cli/status.py` (`_get_repo_status`, table rendering, summary)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `classify`, `RepoState` from Task 2.
- Produces: human table gains a composition-aware "Local Changes" column and a separate "Remote" column; JSON keeps all legacy keys and adds `presence`, `local`, `remote` (from `RepoState.to_dict()`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestStatusModelInCli:
    def test_status_json_includes_composition(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.git import RepoStatus
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)

        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

        fake = RepoStatus(branch="main", staged=2, untracked=1, behind=3)
        with patch("gitstow.cli.status.get_status", return_value=fake):
            result = CliRunner().invoke(app, ["status", "--json"])

        payload = json.loads(result.output)
        entry = payload[0]
        # New model keys (additive)
        assert entry["local"] == {"modified": 0, "staged": 2, "untracked": 1, "summary": "2 staged · 1 untracked"}
        assert entry["remote"]["state"] == "behind"
        # Legacy keys preserved
        assert entry["staged"] == 2 and entry["behind"] == 3 and entry["clean"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestStatusModelInCli -v`
Expected: FAIL with `KeyError: 'local'`.

- [ ] **Step 3: Implement**

In `src/gitstow/cli/status.py`, add the import:

```python
from gitstow.core.status_model import classify
```

Rewrite `_get_repo_status` to build both legacy and model fields:

```python
def _get_repo_status(repo: Repo, ws: Workspace) -> dict:
    """Gather status for a single repo."""
    path = repo.get_path(ws.get_path())
    exists = path.exists() and is_git_repo(path)

    if not path.exists():
        return {"repo": repo.key, "workspace": repo.workspace, "error": "Not found on disk"}
    if not exists:
        return {"repo": repo.key, "workspace": repo.workspace, "error": "Not a git repo"}

    status = get_status(path)
    commit = get_last_commit(path)
    state = classify(exists=True, frozen=repo.frozen, status=status)

    return {
        "repo": repo.key,
        "workspace": repo.workspace,
        # Legacy keys (kept for compat)
        "branch": status.branch,
        "dirty": status.dirty,
        "staged": status.staged,
        "untracked": status.untracked,
        "ahead": status.ahead,
        "behind": status.behind,
        "clean": status.clean,
        "status_symbol": status.status_symbol,
        "ahead_behind": status.ahead_behind_str,
        "frozen": repo.frozen,
        "tags": repo.tags,
        "last_commit": commit.message,
        "last_commit_date": commit.date,
        "last_pulled": repo.last_pulled,
        # Shared status model (new)
        **state.to_dict(),
    }
```

Update the human table: replace the `Status` column pair with the two-dimension presentation. Change the column definitions:

```python
    table.add_column("Repo", style="white", min_width=20)
    table.add_column("Branch")
    table.add_column("Local Changes")
    table.add_column("Remote")
    table.add_column("Last Commit", style="dim")
```

and the row-styling block (replacing the frozen/clean/dirty ladder and ahead/behind styling):

```python
        local = s.get("local", {})
        summary = local.get("summary", "?")
        if s.get("frozen"):
            local_str = f"[cyan]❄ {summary}[/cyan]"
        elif summary == "clean":
            local_str = "[green]✓ clean[/green]"
        else:
            local_str = f"[yellow]{summary}[/yellow]"

        remote = s.get("remote", {})
        remote_state = remote.get("state", "unknown")
        remote_styles = {
            "in-sync": "[dim]—[/dim]",
            "ahead": f"[blue]↑{remote.get('ahead', 0)}[/blue]",
            "behind": f"[magenta]↓{remote.get('behind', 0)}[/magenta]",
            "diverged": f"[red]↑{remote.get('ahead', 0)} ↓{remote.get('behind', 0)}[/red]",
            "no-upstream": "[dim]no upstream[/dim]",
            "unknown": "[dim]?[/dim]",
        }
        remote_str = remote_styles.get(remote_state, remote_state)

        row = []
        if multi_ws:
            row.append(s.get("workspace", ""))
        row.extend([s["repo"], s.get("branch", ""), local_str, remote_str, commit_str])
        table.add_row(*row)
```

Update the summary counts (replace the clean/dirty/frozen/errors block) to composition-aware wording:

```python
    clean = sum(1 for s in statuses if s.get("local", {}).get("summary") == "clean" and not s.get("frozen") and "error" not in s)
    changed = sum(1 for s in statuses if "error" not in s and not s.get("frozen") and s.get("local", {}).get("summary") not in ("clean", None))
    frozen = sum(1 for s in statuses if s.get("frozen"))
    errors = sum(1 for s in statuses if "error" in s)

    summary_parts = []
    if clean:
        summary_parts.append(f"[green]{clean} clean[/green]")
    if changed:
        summary_parts.append(f"[yellow]{changed} with local changes[/yellow]")
    if frozen:
        summary_parts.append(f"[cyan]{frozen} frozen[/cyan]")
    if errors:
        summary_parts.append(f"[red]{errors} errors[/red]")
```

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py tests/test_status_model.py -v && pytest -q`

```bash
git add src/gitstow/cli/status.py tests/test_cli.py
git commit -m "feat: status shows local-change composition and separate remote column

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Web dashboard consumes the model (P4, A1)

**Files:**
- Modify: `src/gitstow/web/routes/dashboard.py` (`_classify` → adapter over `RepoState`, `_STATUS_TOOLTIPS`, `_build_repos_data`), `src/gitstow/web/routes/repos.py` (`_row_context`)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `classify`, `RepoState` from Task 2.
- Produces: `_present(state: RepoState) -> tuple[str, str, str]` returning `(status_class, status_label, pull_variant)` with the SAME css-class vocabulary as today (`clean|dirty|conflict|behind|ahead|frozen` × `primary|ghost|disabled`) but correct semantics: staged/untracked count as local changes; untracked-only + behind keeps a primary Pull. Row context gains `local_summary` used in the status tooltip.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_serve.py` (uses the existing `client`/`configured` fixtures and `_make_repo_on_disk`/`_fake_status` helpers):

```python
class TestStatusModelInWeb:
    def _seed(self, workspace_dir, repos_file_status, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        store = RepoStore()
        store.add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))
        monkeypatch.setattr(
            "gitstow.web.routes.dashboard.get_status", lambda p: repos_file_status
        )

    def test_staged_only_is_not_clean(self, client, configured, workspace_dir, monkeypatch):
        # The audit's headline web bug: staged-only rendered as "clean".
        self._seed(workspace_dir, _fake_status(staged=2), monkeypatch)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        assert "2 staged" in r.text
        assert 'status-clean' not in r.text or "clean" not in r.text.lower().split("a/one")[1][:200]

    def test_untracked_only_behind_keeps_primary_pull(self, client, configured, workspace_dir, monkeypatch):
        self._seed(workspace_dir, _fake_status(untracked=1, behind=3), monkeypatch)
        r = client.get("/dashboard/rows")
        assert "behind" in r.text
```

(The exact assertion strings should be adjusted to the row template's markup during implementation — the invariant under test: staged-only must NOT classify as `clean`, and untracked-only+behind must classify as `behind` with a live Pull.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_serve.py::TestStatusModelInWeb -v`
Expected: staged-only case FAILS (renders clean today).

- [ ] **Step 3: Implement the adapter**

In `src/gitstow/web/routes/dashboard.py`, add the import and replace `_classify` with:

```python
from gitstow.core.status_model import RepoState, classify


def _present(state: RepoState) -> tuple[str, str, str]:
    """Map a RepoState onto the dashboard's (css_class, label, pull_variant).

    Keeps the existing css-class vocabulary; semantics come from the shared
    model — staged and untracked files count as local changes (they used to
    render as 'clean'), and untracked-only repos keep a live Pull button.
    """
    if state.presence == "missing":
        return "conflict", "missing", "disabled"
    if state.presence == "unreadable":
        return "conflict", "error", "disabled"
    if state.frozen:
        return "frozen", "frozen", "disabled"
    if state.blocks_pull and state.behind:
        return "conflict", "conflict", "disabled"
    if state.blocks_pull:
        return "dirty", state.local_summary, "ghost"
    if state.behind:
        return "behind", "behind", "primary"
    if state.ahead:
        return "ahead", "ahead", "ghost"
    if state.untracked:
        return "dirty", state.local_summary, "ghost"
    return "clean", "clean", "ghost"
```

Update `_STATUS_TOOLTIPS["dirty"]` to composition-aware wording:

```python
    "dirty":    "Local changes — uncommitted work in the tree (see label for modified/staged/untracked). Untracked-only repos can still pull.",
```

In `_build_repos_data`, replace the `_classify(...)` call:

```python
        state = classify(exists=exists, frozen=repo.frozen, status=status)
        status_class, status_label, pull_variant = _present(state)
```

and enrich the status tooltip line in the row dict:

```python
            "status_tooltip": f"{_STATUS_TOOLTIPS.get(status_class, status_label)} ({state.local_summary})",
```

In `src/gitstow/web/routes/repos.py`, update the import block (`_classify` → `_present` plus `classify` from the model) and mirror the same two-line change inside `_row_context`:

```python
from gitstow.core.status_model import classify
from gitstow.web.routes.dashboard import (
    _STATUS_TOOLTIPS,
    _delta,
    _present,
    _pull_tooltip,
    _relative_time,
    _workspace_slot,
)
```
```python
    state = classify(exists=exists, frozen=repo.frozen, status=status)
    status_class, status_label, pull_variant = _present(state)
```

- [ ] **Step 4: Run web suite, fix any label-assertion drift**

Run: `pytest tests/test_serve.py -v`
Expected: new tests PASS. Any pre-existing test asserting the literal label "dirty" gets updated to the new composition label (e.g. "1 modified") — that's the standard being enforced, not a regression.

- [ ] **Step 5: Full suite + commit**

```bash
git add src/gitstow/web/routes/dashboard.py src/gitstow/web/routes/repos.py tests/test_serve.py
git commit -m "feat(web): dashboard classifies via the shared status model — staged/untracked no longer render clean

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Pull semantics aligned on both surfaces (B6)

**Files:**
- Modify: `src/gitstow/cli/pull.py` (`_pull_one_repo`), `src/gitstow/web/routes/repos.py` (`pull_all`), `src/gitstow/web/templates/partials/pull_summary.html`
- Test: `tests/test_cli.py`, `tests/test_serve.py`

**Interfaces:**
- Consumes: `RepoState.blocks_pull` / `pull_action` from Task 2.
- Produces: identical rule everywhere — bulk pull skips repos with modified/staged changes (detail = `local_summary`), pulls untracked-only repos. Web pull summary gains a `skipped_local` count.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestPullSemantics:
    def _one_repo_setup(self, tmp_path, monkeypatch):
        from gitstow.core.config import Settings, Workspace, save_config
        from gitstow.core.repo import Repo, RepoStore

        config_file = tmp_path / "config.yaml"
        repos_file = tmp_path / "repos.yaml"
        monkeypatch.setattr("gitstow.core.config.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.CONFIG_FILE", config_file)
        monkeypatch.setattr("gitstow.core.paths.REPOS_FILE", repos_file)
        ws_dir = tmp_path / "ws"
        (ws_dir / "a" / "one" / ".git").mkdir(parents=True)
        save_config(Settings(workspaces=[Workspace(path=str(ws_dir), label="ws", layout="structured")]))
        RepoStore(path=repos_file).add(Repo(owner="a", name="one", remote_url="u", workspace="ws"))

    def test_untracked_only_repo_is_pulled(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import PullResult, RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status", return_value=RepoStatus(branch="main", untracked=2)), \
             patch("gitstow.cli.pull.git_pull", return_value=PullResult(success=True, output="Updating...")) as mock_pull:
            result = CliRunner().invoke(app, ["pull", "--json"])
        assert mock_pull.called  # was skipped as "dirty" before
        payload = json.loads(result.output)
        assert payload["pulled"] == 1

    def test_modified_repo_is_skipped_with_composition_detail(self, tmp_path, monkeypatch):
        import json
        from unittest.mock import patch
        from typer.testing import CliRunner
        from gitstow.cli.main import app
        from gitstow.core.git import RepoStatus

        self._one_repo_setup(tmp_path, monkeypatch)
        with patch("gitstow.cli.pull.get_status", return_value=RepoStatus(branch="main", dirty=3)):
            result = CliRunner().invoke(app, ["pull", "--json"])
        payload = json.loads(result.output)
        row = payload["results"][0]
        assert row["status"] == "skipped"
        assert "3 modified" in row["detail"]
```

Add to `tests/test_serve.py`:

```python
class TestWebPullSkipsLocalChanges:
    def test_pull_all_skips_modified_repo(self, client, configured, workspace_dir, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        _make_repo_on_disk(workspace_dir, "a", "one")
        RepoStore().add(Repo(owner="a", name="one", remote_url="u", workspace="test-ws"))

        monkeypatch.setattr("gitstow.web.routes.repos.get_status", lambda p: _fake_status(dirty=2))
        called = []
        monkeypatch.setattr("gitstow.web.routes.repos.git_pull", lambda p: called.append(p))

        r = client.post("/repos/pull-all")
        assert r.status_code == 200
        assert called == []                      # pull never ran on the modified repo
        assert "local changes" in r.text.lower() # summary reports the skip
```

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/test_cli.py::TestPullSemantics tests/test_serve.py::TestWebPullSkipsLocalChanges -v`
Expected: `test_untracked_only_repo_is_pulled` FAILS (skipped today); web test FAILS (pull runs on dirty repos today).

- [ ] **Step 3: Implement CLI side**

In `src/gitstow/cli/pull.py`, add the import and rewrite the dirty check in `_pull_one_repo`:

```python
from gitstow.core.status_model import classify
```
```python
    status = get_status(path)
    state = classify(exists=True, frozen=repo.frozen, status=status)
    if state.blocks_pull:
        return {
            "repo": repo.key,
            "status": "skipped",
            "detail": f"Local changes ({state.local_summary})",
        }
```

- [ ] **Step 4: Implement web side**

In `src/gitstow/web/routes/repos.py`, replace the bare `git_pull` task in `pull_all` with a state-checking worker (add `get_status` to the existing git import — already imported — and `classify` from Task 4's import):

```python
def _pull_if_safe(path):
    """Bulk-pull worker: same skip rule as the CLI — modified/staged block, untracked doesn't."""
    state = classify(exists=True, frozen=False, status=get_status(path))
    if state.blocks_pull:
        return {"skipped_local": True, "detail": state.local_summary}
    return git_pull(path)
```

In `pull_all`, change the task construction and result handling:

```python
    tasks = [(gk, functools.partial(_pull_if_safe, p)) for gk, p in targets]
    task_results = await run_parallel(tasks, max_concurrent=settings.parallel_limit)

    now_iso = datetime.now().isoformat(timespec="seconds")
    ok = 0
    skipped_local: list[dict] = []
    failed: list[dict] = []
    for r in task_results:
        data = r.data if r.success else None
        if isinstance(data, dict) and data.get("skipped_local"):
            skipped_local.append({"key": r.key, "detail": data["detail"]})
            continue
        pull_result = data
        if pull_result and pull_result.success:
            ok += 1
            ws_label, _, rkey = r.key.partition(":")
            store.update(rkey, workspace=ws_label, last_pulled=now_iso)
        else:
            err = (pull_result.error if pull_result else r.error) or "unknown error"
            failed.append({"key": r.key, "error": err.strip()[:240]})

    summary = {
        "total": len(targets),
        "ok": ok,
        "failed": failed,
        "skipped_frozen": skipped_frozen,
        "skipped_local": skipped_local,
        "missing": missing,
    }
```

In `src/gitstow/web/templates/partials/pull_summary.html`, add a line in the summary section (match surrounding markup style):

```html
{% if summary.skipped_local %}
<div class="summary-line">
  {{ summary.skipped_local | length }} skipped — local changes:
  {% for s in summary.skipped_local %}<span class="mono">{{ s.key }}</span> ({{ s.detail }}){% if not loop.last %}, {% endif %}{% endfor %}
</div>
{% endif %}
```

- [ ] **Step 5: Run tests, full suite, commit**

Run: `pytest tests/test_cli.py tests/test_serve.py -v && pytest -q`

```bash
git add src/gitstow/cli/pull.py src/gitstow/web/routes/repos.py src/gitstow/web/templates/partials/pull_summary.html tests/
git commit -m "feat: one pull rule everywhere — modified/staged skip, untracked-only pulls

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Parallel, non-blocking web status (E1)

**Files:**
- Modify: `src/gitstow/web/routes/dashboard.py` (`_build_repos_data` → async, both routes)
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `run_parallel` from `core/parallel.py` (async: `await run_parallel(tasks, max_concurrent)`).
- Produces: `async def _build_repos_data(settings, store) -> tuple[list, dict]` — same return shape; `get_status` subprocesses run in the thread pool with the semaphore, not serially on the event loop. Both `dashboard` and `dashboard_rows` await it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_serve.py`:

```python
class TestParallelDashboardStatus:
    def test_statuses_gathered_concurrently(self, client, configured, workspace_dir, monkeypatch):
        import threading
        from gitstow.core.repo import Repo, RepoStore

        store = RepoStore()
        for i in range(6):
            _make_repo_on_disk(workspace_dir, "o", f"r{i}")
            store.add(Repo(owner="o", name=f"r{i}", remote_url="u", workspace="test-ws"))

        concurrent = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow_status(path):
            import time
            with lock:
                concurrent["now"] += 1
                concurrent["max"] = max(concurrent["max"], concurrent["now"])
            time.sleep(0.05)
            with lock:
                concurrent["now"] -= 1
            return _fake_status()

        monkeypatch.setattr("gitstow.web.routes.dashboard.get_status", slow_status)
        r = client.get("/dashboard/rows")
        assert r.status_code == 200
        assert concurrent["max"] >= 2  # serial implementation never exceeds 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_serve.py::TestParallelDashboardStatus -v`
Expected: FAIL with `concurrent["max"] == 1`.

- [ ] **Step 3: Implement**

In `src/gitstow/web/routes/dashboard.py`, add imports:

```python
import functools

from gitstow.core.parallel import run_parallel
```

Make `_build_repos_data` async and gather statuses up front (replace the top of the loop):

```python
async def _build_repos_data(settings, store) -> tuple[list, dict]:
    """Gather the rendered row data + aggregate counts.

    Statuses are collected in parallel through the shared semaphore — one git
    subprocess per repo, off the event loop — then rows are assembled in order.
    """
    workspaces = settings.get_workspaces()
    ws_by_label = {w.label: w for w in workspaces}
    ws_sorted = sorted(ws_by_label.keys())

    repos = [r for r in store.list_all() if r.workspace in ws_by_label]

    # Phase 1 — parallel status gathering
    probe: list[tuple[str, object]] = []
    for repo in repos:
        ws = ws_by_label[repo.workspace]
        repo_path = repo.get_path(ws.get_path())
        if repo_path.exists() and is_git_repo(repo_path):
            probe.append((repo.global_key, functools.partial(get_status, repo_path)))
    results = await run_parallel(probe, max_concurrent=settings.parallel_limit)
    status_by_key = {r.key: (r.data if r.success else None) for r in results}

    # Phase 2 — assemble rows in stable order
    repos_data = []
    counts = {"clean": 0, "dirty": 0, "conflict": 0, "behind": 0, "ahead": 0, "frozen": 0}

    for i, repo in enumerate(repos, start=1):
        ws = ws_by_label[repo.workspace]
        repo_path = repo.get_path(ws.get_path())
        exists = repo.global_key in status_by_key
        status = status_by_key.get(repo.global_key)

        state = classify(exists=exists, frozen=repo.frozen, status=status)
        status_class, status_label, pull_variant = _present(state)
        # ... rest of the existing row-assembly body unchanged ...
```

(The remainder of the loop body — counts, delta, dict append — stays exactly as it is after Task 4.)

Update both callers:

```python
    repos_data, counts = await _build_repos_data(settings, store)
```

in `dashboard(...)` and

```python
    repos_data, _ = await _build_repos_data(settings, store)
```

in `dashboard_rows(...)`.

- [ ] **Step 4: Run tests, full suite, commit**

Run: `pytest tests/test_serve.py -v && pytest -q`

```bash
git add src/gitstow/web/routes/dashboard.py tests/test_serve.py
git commit -m "perf(web): gather dashboard statuses in parallel off the event loop

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Batched store writes — `RepoStore.bulk()` (E2)

**Files:**
- Modify: `src/gitstow/core/repo.py` (`bulk()`, `_mutate()`), `src/gitstow/cli/pull.py`, `src/gitstow/cli/fetch.py`, `src/gitstow/web/routes/repos.py` (`pull_all`, `fetch_all`)
- Test: `tests/test_repo.py`

**Interfaces:**
- Consumes: Wave 1's `_mutate()`/`file_lock` plumbing.
- Produces: `RepoStore.bulk()` context manager — inside it, `add/remove/update` mutate in memory; ONE locked reload-mutate-write cycle wraps the whole block. Callers stamping N timestamps use `with store.bulk():`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_repo.py`:

```python
class TestBulkWrites:
    def test_bulk_writes_once(self, tmp_repos_file, monkeypatch):
        from gitstow.core.repo import Repo, RepoStore

        store = RepoStore(path=tmp_repos_file)
        for i in range(5):
            store.add(Repo(owner="o", name=f"r{i}", remote_url="u", workspace="oss"))

        writes = []
        original_write = RepoStore._write

        def counting_write(self):
            writes.append(1)
            original_write(self)

        monkeypatch.setattr(RepoStore, "_write", counting_write)

        with store.bulk():
            for i in range(5):
                store.update(f"o/r{i}", workspace="oss", frozen=True)

        assert len(writes) == 1  # five updates, one file write

        fresh = RepoStore(path=tmp_repos_file)
        assert all(r.frozen for r in fresh.list_all())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo.py::TestBulkWrites -v`
Expected: FAIL with `AttributeError: 'RepoStore' object has no attribute 'bulk'`.

- [ ] **Step 3: Implement**

In `src/gitstow/core/repo.py`: add `self._in_bulk = False` to `__init__`, then:

```python
    @contextlib.contextmanager
    def bulk(self):
        """Batch many mutations into ONE locked read-modify-write cycle.

        Bulk operations (pull/fetch all) stamp N timestamps; without this,
        each stamp rewrites the whole YAML file N times.
        """
        with file_lock(self._lock_path()):
            self.load()
            self._in_bulk = True
            try:
                yield self
            finally:
                self._in_bulk = False
                self._write()

    @contextlib.contextmanager
    def _mutate(self):
        """Locked read-modify-write for a single mutation — or a no-op wrapper
        when inside bulk(), which already holds the lock and defers the write."""
        if self._in_bulk:
            yield
            return
        with file_lock(self._lock_path()):
            self.load()
            yield
            self._write()
```

- [ ] **Step 4: Wire the four bulk callers**

`src/gitstow/cli/pull.py` — wrap the results-processing loop (the one calling `store.update(..., last_pulled=...)`; it lives inside the retry `for attempt` loop, so wrap the inner results loop):

```python
        failed_keys: set[str] = set()
        with store.bulk():
            for task_result in results:
                # ... existing body with store.update(...) calls unchanged ...
```

`src/gitstow/cli/fetch.py` — same wrap around its results loop.

`src/gitstow/web/routes/repos.py` `pull_all` — wrap the stamping loop:

```python
    ok = 0
    skipped_local: list[dict] = []
    failed: list[dict] = []
    with store.bulk():
        for r in task_results:
            # ... existing body unchanged ...
```

`fetch_all` — same wrap.

- [ ] **Step 5: Run tests, full suite, commit**

Run: `pytest -q && ruff check src/`

```bash
git add src/gitstow/core/repo.py src/gitstow/cli/pull.py src/gitstow/cli/fetch.py src/gitstow/web/routes/repos.py tests/test_repo.py
git commit -m "perf: batch bulk-operation store writes into a single locked file write

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Wave completion checklist

- [ ] `pytest -q` green, `ruff check src/` clean
- [ ] Manual smoke: `gitstow ui` — a repo with only a staged file shows its composition (not "clean"); Pull all reports "skipped — local changes" for a modified repo
- [ ] Manual smoke: `gitstow status` shows Local Changes + Remote columns
- [ ] Help dialog copy in `web/templates` still matches behavior (grep templates for "dirty" and update wording to "local changes")
- [ ] `docs/user/commands.md` status/pull sections updated to the new columns and pull rule
- [ ] SKILL.md decision-tree row "See dirty repos only" → wording check (`--dirty` flag still exists and now means "any local changes")
- [ ] Check off Wave 2 items in `docs/building/audit-2026-07-06.md`
- [ ] CHANGELOG `[Unreleased]`: status-model entry + TUI removal + pull-semantics change (breaking-ish behavior note)
