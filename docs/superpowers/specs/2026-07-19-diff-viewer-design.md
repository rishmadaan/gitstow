# Diff Viewer — Design

**Date:** 2026-07-19
**Status:** Approved by Rishabh (web-first, full diffs, CLI parity, view-only)

## Problem

When a repo shows local changes ("2 modified · 1 untracked"), gitstow can't
show *which* files changed or *what* changed inside them. The user drops to a
terminal and raw git. GitHub Desktop is the reference experience: a sidebar of
changed files grouped by staged/unstaged, click a file → line-by-line diff.

## Scope

- **View-only.** No staging, committing, discarding, or editing. (Commit/push
  is a possible future; this work is its foundation, not its start.)
- Working-tree changes only — staged, unstaged, untracked. Not commit history
  or upstream comparison.

## Approach (decided)

Server-side diff rendering: Python asks git for the diff, parses the unified
diff format (~50 lines of parsing), and renders it with Jinja + existing CSS.
No JS diff library, no ANSI-to-HTML recoloring. Rationale: zero new
dependencies, matches the dashboard's server-rendered htmx architecture and
dark theme.

## Components

### 1. `core/git.py` — new plumbing (all git calls stay here)

- `get_changed_files(repo_path) -> ChangedFiles`: one `git status
  --porcelain=v2` call parsed into three lists — staged, unstaged, untracked —
  each entry carrying path and change kind (modified/added/deleted/renamed).
  Per-file `+added −removed` counts come from `git diff --numstat` (and
  `--cached` for staged).
- `get_file_diff(repo_path, file, staged: bool) -> str`: raw unified diff for
  one file (`git diff [--cached] -- <file>`). Untracked files use `git diff
  --no-index /dev/null <file>` to render as all-new lines.

### 2. Diff parsing + rendering (web)

- Small parser in `core/diff.py`: unified diff text → hunks of (kind:
  add/del/context, old line-no, new line-no, text) — feeds the Jinja template.
- Guardrails:
  - Binary files → "binary file changed" row, no content.
  - Diffs over ~500 lines → truncate with "showing first 500 lines" notice.

### 3. Web UI — repo detail page (`_repo_drawer.html`)

- New **Changes** section, rendered only when the repo has local changes.
- Three groups: **Staged**, **Unstaged**, **Untracked** — each file row shows
  path, change kind, `+n −m` counts.
- Clicking a file expands its line-by-line diff inline, loaded on demand via
  htmx (`GET /repos/{ws}/{key}/diff?file=...&staged=...`) so 40 dirty files
  don't render 40 diffs upfront. Green added / red removed lines with line
  numbers, styled to the existing dark theme.
- Dashboard rows: the local-changes badge ("2 modified") links to the repo
  page's Changes section (`/repos/{ws}/{key}#changes`).

### 4. CLI — `gitstow diff`

- `gitstow diff <repo>`: resolve the repo across workspaces (existing
  `cli/helpers.py` lookup), then hand through to git's own colored diff —
  git already renders terminal diffs well; we don't repaint.
- `--staged` flag for staged changes. No file arguments = everything.
- Passthrough runs interactively (inherits TTY, color, pager) via a thin
  function in `core/git.py` — the CLI still never shells out to git itself.

## Error handling

- Repo missing on disk / not a git repo → same handling as existing status
  paths (presence overlay already models this).
- Clean repo: web shows no Changes section; CLI prints "no local changes".
- File disappears between list and diff request → empty diff, harmless.

## Testing

- Parser unit tests: add/del/context lines, hunk headers, binary, rename,
  truncation.
- `get_changed_files` against a fixture repo (tmp git repo with staged +
  unstaged + untracked files).
- Web route test: diff endpoint returns rendered hunks; drawer shows Changes
  section only when dirty.
- Per project standards: verify the drawer section in a real browser, not
  just TestClient.
