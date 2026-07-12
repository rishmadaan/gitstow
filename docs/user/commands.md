---
summary: Complete reference for all gitstow commands, flags, and usage patterns.
read_when:
  - Looking up a specific command's syntax or flags
  - Want to see all available commands
  - Need JSON output or scripting examples
---

# Commands Reference

Every command supports `--help` for built-in documentation:

```bash
gitstow add --help
gitstow pull --help
```

## Global Flag: `--workspace` / `-w`

All commands accept a global `-w/--workspace` flag to scope operations to a single workspace:

```bash
gitstow -w oss pull            # Only pull repos in the 'oss' workspace
gitstow -w active list         # Only list repos in the 'active' workspace
gitstow -w work status         # Only show status for 'work' workspace
```

Without `-w`, commands operate across all workspaces. If a repo key is ambiguous (exists in multiple workspaces), gitstow will prompt you to choose or you can disambiguate with `-w`.

## Core Commands

### `gitstow add`

Clone repos into the organized `owner/repo/` structure.

```bash
gitstow add <url> [urls...]
```

**Arguments:**
- `url` — GitHub shorthand (`owner/repo`), full HTTPS URL, or SSH URL. Multiple URLs accepted.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--shallow` | `-s` | Shallow clone (`--depth 1`). Saves disk space. |
| `--branch` | `-b` | Clone a specific branch. |
| `--update` | `-u` | Pull if repo already exists (instead of skipping). |
| `--tag` | `-t` | Apply tag(s) immediately. Repeatable. |
| `--recursive` | `-r` | Initialize submodules after clone. |
| `--ssh` | | Force SSH clone URL (overrides config). |
| `--retry` | | Retry failed clones N times (e.g., `--retry 3`). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress progress messages. |

**Examples:**

```bash
# GitHub shorthand
gitstow add anthropic/claude-code

# Multiple repos at once
gitstow add facebook/react torvalds/linux golang/go

# Full URL (any git host)
gitstow add https://gitlab.com/group/subgroup/project

# SSH URL
gitstow add git@bitbucket.org:owner/repo.git

# Shallow clone with tags
gitstow add torvalds/linux --shallow --tag reference --tag kernel

# From a file (one URL per line)
cat repos.txt | gitstow add

# Update if already exists
gitstow add anthropic/claude-code --update
```

**Behavior:**
- Repos are added to the default workspace (first configured), or the workspace specified with `-w`
- If the repo is already tracked: skips (or pulls with `--update`)
- If the path exists on disk but isn't tracked: registers it automatically
- If the path exists on disk with a **different** remote than the one requested: errors with a remote-mismatch message instead of silently registering — move the directory or pick another workspace
- If the path exists but isn't a git repo: errors
- Equivalent URLs passed twice in the same invocation (e.g. HTTPS and SSH forms of the same repo) are deduplicated — only the first is cloned, the rest are reported as duplicates
- Multiple URLs are cloned concurrently (bounded by `parallel_limit`, default 6), with per-clone retry via `--retry`

---

### `gitstow pull`

Bulk update repos via `git pull --ff-only`.

```bash
gitstow pull [repos...] [flags]
```

**Arguments:**
- `repos` — Optional. Specific repos to pull (`owner/repo`). Omit to pull all.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only pull repos with this tag. Repeatable. |
| `--exclude-tag` | | Skip repos with this tag. Repeatable. |
| `--owner` | | Only pull repos from this owner. |
| `--include-frozen` | | Include frozen repos (normally skipped). |
| `--retry` | | Retry failed repos N times (e.g., `--retry 3`). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress per-repo progress. |

**Examples:**

```bash
# Pull everything (frozen repos skipped)
gitstow pull

# Only repos tagged 'ai'
gitstow pull --tag ai

# Everything except archived repos
gitstow pull --exclude-tag archived

# Specific repos
gitstow pull anthropic/claude-code facebook/react

# Force-pull frozen repos too
gitstow pull --include-frozen
```

**Behavior:**
- Frozen repos are skipped (shown as "Frozen" in summary)
- Repos with modified or staged files are skipped — the composition is shown in the summary (e.g. "2 modified · 1 staged")
- Diverged repos (local and remote both have commits the other lacks) are skipped — resolve manually (rebase or merge)
- Repos with only untracked files ARE pulled — an ff-only pull can't lose them
- Uses `--ff-only` — won't create merge commits
- Runs in parallel (configurable, default 6 concurrent)
- Updates `last_pulled` timestamp on success

---

### `gitstow fetch`

Fetch from all remotes — updates ahead/behind counts without merging.

```bash
gitstow fetch [repos...] [flags]
```

**Arguments:**
- `repos` — Optional. Specific repos to fetch. Omit for all.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only fetch repos with this tag. Repeatable. |
| `--exclude-tag` | | Skip repos with this tag. Repeatable. |
| `--owner` | | Only fetch repos from this owner. |
| `--retry` | | Retry failed repos N times. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress per-repo progress. |

**Examples:**

```bash
# Fetch everything (including frozen repos)
gitstow fetch

# Only repos tagged 'active'
gitstow fetch --tag active

# Specific repos
gitstow fetch anthropic/claude-code
```

**Behavior:**
- Frozen repos are included (fetch is non-destructive)
- Dirty repos are included (fetch doesn't touch the working tree)
- Runs `git fetch --all --prune` per repo
- Runs in parallel (configurable, default 6 concurrent)
- Updates `last_fetched` timestamp on success

---

### `gitstow list`

Show all tracked repos, grouped by owner.

```bash
gitstow list [query] [flags]
```

**Arguments:**
- `query` — Optional substring filter (e.g., `gitstow list react`).

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Filter by tag. |
| `--owner` | | Filter by owner. |
| `--frozen` | | Show only frozen repos. |
| `--paths` | `-p` | Show full filesystem paths. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Minimal output. |

**Examples:**

```bash
gitstow list                    # All repos
gitstow list react              # Search for 'react'
gitstow list --tag ai           # Filter by tag
gitstow list --owner anthropic  # Filter by owner
gitstow list --frozen           # Only frozen repos
gitstow list --paths            # Show full paths
gitstow list --json             # Machine-readable output
```

If a workspace has repos on disk that aren't tracked yet, `list` prints a hint (e.g. `⚠ 2 untracked repos in oss — run gitstow workspace scan oss`) — suppressed by `--json`/`--quiet`.

---

### `gitstow status`

Git status dashboard across all repos.

```bash
gitstow status [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Filter by tag. |
| `--owner` | | Filter by owner. |
| `--dirty` | | Show only repos with local changes. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Minimal output. |

Shows: repo name, branch, a **Local Changes** column (composition — e.g. "2 modified · 1 staged · 3 untracked", or "clean"), a separate **Remote** column (in-sync / ahead / behind / diverged / no-upstream), and last commit message and date. Local working-tree state and remote relationship are always shown as two separate dimensions — never a bare "dirty" bucket.

**JSON output** (`--json`) keeps all existing flat fields (`dirty`, `staged`, `untracked`, `ahead`, `behind`, `clean`, `status_symbol`, ...) and adds two keys from the shared status model:
- `local` — `{modified, staged, untracked, summary}`
- `remote` — `{state, ahead, behind}`, where `state` is one of `in-sync` / `ahead` / `behind` / `diverged` / `no-upstream` / `unknown`

**Status symbols** (legacy `status_symbol` JSON field):
- `✓` clean
- `*` unstaged changes
- `+` staged changes
- `?` untracked files
- `❄` frozen

Like `list`, `status` prints an untracked-repos hint per workspace when repos exist on disk but aren't tracked — suppressed by `--json`/`--quiet`.

---

### `gitstow remove`

Remove a repo from tracking.

```bash
gitstow remove <owner/repo> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--yes` | `-y` | Skip confirmation prompt. |
| `--delete` | | Also delete the repo from disk. |
| `--json` | `-j` | JSON output. |

By default, only removes from gitstow's registry. Files stay on disk. Use `--delete` to remove the directory too.

---

### `gitstow migrate`

Adopt existing repos into the gitstow structure.

```bash
gitstow migrate <path> [paths...]
```

Reads the remote URL from the repo, determines the owner/repo, moves the directory into your gitstow root, and registers it.

```bash
gitstow migrate ~/old-projects/some-repo
gitstow migrate ~/random-clones/*
```

---

## Repo Management Commands

All under the `gitstow repo` subcommand.

### `gitstow repo freeze` / `gitstow repo unfreeze`

Toggle the freeze flag. Frozen repos are skipped during `pull`.

```bash
gitstow repo freeze facebook/react
gitstow repo freeze --tag archived     # Freeze all repos with a tag
gitstow repo unfreeze facebook/react
```

### `gitstow repo tag` / `gitstow repo untag`

Manage tags on repos.

```bash
gitstow repo tag anthropic/claude-code ai tools reference
gitstow repo untag anthropic/claude-code reference
```

### `gitstow repo tags`

List all tags with repo counts.

```bash
gitstow repo tags
gitstow repo tags --quiet   # One tag per line (for scripting/completions)
```

### `gitstow repo info`

Detailed view of a single repo: remote URL, path, branch, status, frozen, tags, disk size, last commit.

```bash
gitstow repo info anthropic/claude-code
gitstow repo info anthropic/claude-code --json
```

---

## Power Commands

### `gitstow exec`

Run an arbitrary command in every repo's directory.

```bash
gitstow exec <command...>
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only run in repos with this tag. |
| `--owner` | | Only run in repos from this owner. |
| `--frozen` | | Only run in frozen repos. |
| `--sequential` | `-s` | Run one at a time (default: parallel). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Only show command output, no headers. |

**Examples:**

```bash
gitstow exec -- git log -1 --oneline
gitstow exec -- git branch --show-current
gitstow exec --tag python -- wc -l README.md
gitstow exec -- ls -la
gitstow exec --sequential -- git fetch    # One at a time
```

> Use `--` before the command to separate gitstow flags from command arguments.

---

### `gitstow search`

Grep across all repos. Uses ripgrep (`rg`) if available, falls back to `git grep`.

```bash
gitstow search <pattern> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only search in repos with this tag. |
| `--owner` | | Only search in repos from this owner. |
| `--glob` | `-g` | File glob pattern (e.g., `*.py`, `*.md`). |
| `--ignore-case` | `-i` | Case-insensitive search. |
| `--files` | `-l` | Only show file paths, not matching lines. |
| `--max` | `-m` | Max results per repo (default: 50). |
| `--json` | `-j` | JSON output. |

**Examples:**

```bash
gitstow search "TODO"
gitstow search "def main" --glob "*.py"
gitstow search "import React" --tag frontend
gitstow search "error" -i --files
```

---

### `gitstow open`

Open a repo in your editor, browser, or file manager.

```bash
gitstow open <owner/repo> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--editor` | `-e` | Open in your editor (`$VISUAL`/`$EDITOR`, then VS Code/Cursor). This is the default. |
| `--browser` | `-b` | Open the repo on GitHub/GitLab in your browser. |
| `--finder` | `-f` | Open in Finder/file manager. |
| `--path` | `-p` | Just print the path to stdout. |

With no flags, opens in an editor: `$VISUAL`, then `$EDITOR`, then a fallback of VS Code (`code`) or Cursor (`cursor`) if installed. Terminal editors (`vi`, `vim`, `nvim`, `nano`, `emacs`, `hx`, `kak`, `micro`) run in the foreground since they need the TTY; GUI editors are launched detached. The `--path` flag is useful for shell integration:

```bash
cd "$(gitstow open anthropic/claude-code -p)"
```

---

### `gitstow stats`

Collection statistics — total repos, owners, tags, frozen count, and disk usage breakdown.

```bash
gitstow stats
gitstow stats --json
```

---

## Sharing Commands

### `gitstow collection export`

Export your collection to a portable file.

```bash
gitstow collection export [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--output` | `-o` | Output file path. Defaults to stdout. |
| `--tag` | `-t` | Only export repos with this tag. |
| `--format` | `-f` | Output format: `yaml` (default), `json`, or `urls`. |

**Examples:**

```bash
gitstow collection export                          # YAML to stdout
gitstow collection export -o my-repos.yaml         # YAML to file
gitstow collection export --format urls            # Plain URL list
gitstow collection export --tag ai -o ai.yaml      # Export subset
```

### `gitstow collection import`

Import a collection from a file. Supports YAML (from export), JSON, or plain URL lists.

```bash
gitstow collection import <file> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Apply tag(s) to all imported repos. |
| `--shallow` | `-s` | Shallow clone imported repos. |
| `--dry-run` | `-n` | Show what would be imported without doing it. |

---

## Shell Integration

### `gitstow shell setup`

Show instructions for setting up shell integration (aliases and fzf picker).

### `gitstow shell init [bash|zsh|fish]`

Print shell functions to source in your rc file. Provides:
- `gs` — cd into a repo via fzf picker
- `gse` — open a repo in editor via fzf picker
- `gsp` — `gitstow pull` shorthand
- `gss` — `gitstow status` shorthand
- `gsl` — `gitstow list` shorthand
- `gsa` — `gitstow add` shorthand

### `gitstow shell pick`

Interactive repo picker (uses fzf if available, falls back to beaupy). Outputs the selected repo's path. Designed for piping:

```bash
cd "$(gitstow shell pick)"
code "$(gitstow shell pick)"
```

### `gitstow shell completions [bash|zsh|fish]`

Print shell completion script for tab-completing repo names, workspace labels, and tag names.

```bash
# Add to your ~/.zshrc or ~/.bashrc (after shell init):
eval "$(gitstow shell completions)"

# Or for fish:
gitstow shell completions fish | source
```

Completes:
- Repo keys for `remove`, `open`, `repo freeze/unfreeze/tag/untag/info`
- Workspace labels for `-w/--workspace`
- Tag names for `-t/--tag`

### `gitstow ui`

Launch a local browser dashboard — a dark-themed web UI for daily repo management. Binds to `127.0.0.1` only; auto-opens your default browser.

> Requires the `ui` extra: `pip install "gitstow[ui]"` (or `pipx install "gitstow[ui]"`). Running `gitstow ui` without it prints an install hint and exits.

```bash
gitstow ui                     # http://127.0.0.1:7853
gitstow ui --port 8080         # custom port
gitstow ui --no-browser        # don't auto-open
```

**Features:**
- Live status across every tracked repo (clean / dirty / conflict / behind / ahead / frozen), auto-refreshes every 30s
- Click a repo name to open its detail drawer (metadata, tags, freeze toggle, danger zone)
- **Pull** on a row updates in place via HTMX; **Pull all** runs the parallel semaphore and shows a summary panel with failures
- Add / remove / freeze / tag repos without leaving the tab
- Workspace CRUD + Scan; Collection export/import under Settings
- Click **Shutdown** in the footer (or Ctrl+C) to stop

**Security:** binds `127.0.0.1` only — there is no `--host` flag. The server runs git operations in arbitrary workspace directories, so it must not be LAN-reachable.

#### Reading the dashboard

Every interactive element has a hover tooltip. There's also a `?` button in the hero that opens a full legend dialog.

**Statuses** (left column + metrics strip):

| | Meaning | What to do |
|---|---|---|
| clean | Working tree matches HEAD; in sync with the last fetch | Nothing |
| dirty | Local changes — the label shows the composition (e.g. "2 modified · 1 staged · 3 untracked") | Commit/stash modified or staged files before pulling; untracked-only repos can still pull |
| conflict | Local changes (modified/staged) AND behind remote, or local and remote have diverged | Resolve locally before pulling |
| behind | Remote has commits you don't | Click the orange Pull button |
| ahead | You have local commits not yet pushed | Consider `git push` |
| frozen | Intentionally skipped by "Pull all" | Unfreeze from the ⋯ menu |

**The Pull button color encodes priority**:

- **Orange** `↓ Pull 5` — repo is behind; clicking fast-forwards by the shown count
- **Ghost** (gray) — already up to date, has local changes, or ahead. The button still works; it just won't change anything useful. (Untracked-only local changes still pull fine — a bulk pull only skips modified/staged files.)
- **Disabled** — frozen, in conflict, diverged from remote, or missing on disk

**Remote Δ** (`↑ N`, `↓ N`, or `—`) reflects your *last fetch*, not live remote state. If the counts look stale, pull (or run `git fetch --all` manually) to refresh them.

**Auto-refresh every 30s** re-reads local state only:

- Your `~/.gitstow/repos.yaml` registry (catches `gitstow add` from another terminal)
- `git status` on each tracked repo (catches new local commits, branch switches, dirty files)

It does **NOT** run `git fetch`. Ahead/behind counts won't update without a pull or manual fetch.

### `gitstow update`

Upgrade gitstow itself from PyPI. Detects your install method (pipx, pip, or editable) and runs the matching upgrade.

```bash
gitstow update                 # install the latest from PyPI
gitstow update --check         # just query PyPI; don't install
gitstow update -c              # short form of --check
```

For editable installs (`pip install -e .`), runs nothing and points you at `git pull` instead. For pipx installs, runs `pipx upgrade gitstow`. For regular pip installs, runs `python -m pip install --upgrade gitstow`.

---

## Configuration Commands

### `gitstow config show`

Display current settings, file paths, and repo count.

```bash
gitstow config show
gitstow config show --json
```

### `gitstow config set`

Change a setting.

```bash
gitstow config set default_host gitlab.com
gitstow config set prefer_ssh true
gitstow config set parallel_limit 8
gitstow config set clone_timeout 900
```

Valid keys: `default_host`, `prefer_ssh`, `parallel_limit`, `clone_timeout` (clone timeout in seconds).

> **Note:** The old `root_path` setting has been replaced by workspaces. Use `gitstow workspace add` to manage where repos are stored.

### `gitstow config migrate-root`

Move one workspace's repos to a new directory and update that workspace's `path` in config.

```bash
gitstow config migrate-root ~/new-location            # Moves the default workspace
gitstow -w active config migrate-root ~/new-location  # Moves a specific workspace
gitstow config migrate-root ~/new-location --copy      # Keep originals
gitstow config migrate-root ~/new-location --yes       # Skip confirmation
```

> **Note:** `migrate-root` has no local `--workspace` flag — target a non-default workspace with the global `-w` flag *before* `config`, not after `migrate-root`.

### `gitstow config path`

Print the config file path.

---

## Utility Commands

### `gitstow onboard`

Interactive first-run setup wizard. Guides you through root path, default host, SSH preference, and scans for existing repos.

```bash
gitstow onboard
gitstow onboard --force    # Re-run even if already configured
```

### `gitstow doctor`

Health check — verifies git installation, config files, and repo integrity (tracked vs on-disk).

```bash
gitstow doctor
gitstow doctor --json
```

### `gitstow install-skill`

Install the Claude Code skill for AI-assisted repo management.

```bash
gitstow install-skill
```

---

## Workspace Commands

All under the `gitstow workspace` subcommand. See [Concepts — Workspaces](concepts.md#workspaces) for the mental model.

### `gitstow workspace list`

Show all configured workspaces with their path, layout, auto-tags, and repo count.

```bash
gitstow workspace list
gitstow workspace list --quiet   # One label per line (for scripting/completions)
```

### `gitstow workspace add`

Add a new workspace.

```bash
gitstow workspace add <path> --label <label> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--label` | `-l` | **Required.** Unique label for this workspace. |
| `--layout` | | Directory layout: `structured` (default, owner/repo) or `flat`. |
| `--auto-tag` | `-t` | Tags auto-applied to discovered repos. Repeatable. |
| `--scan/--no-scan` | | Scan for existing repos after adding (default: scan). |

**Examples:**

```bash
# Add a structured workspace for open-source repos
gitstow workspace add ~/oss --label oss

# Add a flat workspace for your own projects, auto-tagged
gitstow workspace add ~/projects --label active --layout flat --auto-tag mine

# Add without scanning for existing repos
gitstow workspace add ~/archive --label archive --no-scan
```

### `gitstow workspace remove`

Remove a workspace from the configuration. Does not delete files on disk.

```bash
gitstow workspace remove <label> [flags]
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--keep-repos/--untrack-repos` | Keep tracked repos in the store (default) or untrack them. |

You cannot remove the only remaining workspace.

### `gitstow workspace scan`

Scan a workspace directory to discover and register any repos that aren't yet tracked.

```bash
gitstow workspace scan <label>
```

This respects the workspace's layout mode — in `structured` workspaces it looks for `owner/repo/.git`, in `flat` workspaces it looks for `repo/.git`.

---

## MCP Server (Optional)

> **Most users don't need this.** If you use Claude Code, the bundled skill (`gitstow install-skill`) gives full access to all commands with zero context overhead. The MCP server is for AI tools that don't support Claude Code skills — Claude Desktop, Cursor, Windsurf, etc.
>
> **Context cost warning:** MCP tools are always loaded into your AI tool's context window, costing tokens even when you're not managing repos. Only set this up if you have a dedicated repo-management workflow.

### Setup

```bash
pip install gitstow[mcp]    # Install the optional MCP dependency
```

Then add to your AI tool's config:

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp"
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp"
    }
  }
}
```

Or use `gitstow setup-ai` to auto-detect and configure.

### MCP Tools (14)

| Tool | Description |
|------|-------------|
| `list_repos` | List repos with tag/owner/query filters |
| `add_repo` | Clone a repo (shorthand or full URL) |
| `pull_repos` | Bulk pull with tag/exclude/frozen filters |
| `fetch_repos` | Bulk fetch (updates ahead/behind, no merge; includes frozen) |
| `repo_status` | Git status dashboard across repos |
| `repo_info` | Detailed single repo info |
| `freeze_repo` | Freeze a repo (skip during pull) |
| `unfreeze_repo` | Unfreeze a repo |
| `tag_repo` | Add tags to a repo |
| `untag_repo` | Remove tags from a repo |
| `remove_repo` | Remove from tracking (optionally delete) |
| `search_repos` | Grep across repos with pattern and glob |
| `collection_stats` | Disk usage, owner breakdown, tag counts |
| `list_workspaces` | List configured workspaces with repo counts |

### MCP Resources (3)

| Resource URI | Description |
|-------------|-------------|
| `gitstow://config` | Current gitstow configuration |
| `gitstow://tags` | All tags with repo counts |
| `gitstow://owners` | All owners with repo counts |

---

## JSON Output

Every main command supports `--json` (`-j`) for machine-readable output. Combined with `--quiet` (`-q`), this suppresses human-readable progress and only outputs structured JSON.

This is designed for scripting and AI tool integration:

```bash
# Parse with jq
gitstow list --json | jq '.[].key'

# Use in scripts
REPOS=$(gitstow list --json --quiet)

# AI tools use --json --quiet for structured parsing
gitstow pull --json --quiet
```
