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
| `--ssh` | | Force SSH clone URL (overrides config). |
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
- If the repo is already tracked: skips (or pulls with `--update`)
- If the path exists on disk but isn't tracked: registers it automatically
- If the path exists but isn't a git repo: errors
- Multiple URLs are cloned in parallel

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
- Dirty repos are skipped (never risks losing local changes)
- Uses `--ff-only` — won't create merge commits
- Runs in parallel (configurable, default 6 concurrent)
- Updates `last_pulled` timestamp on success

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
| `--dirty` | | Show only dirty repos. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Minimal output. |

Shows: repo name, branch, clean/dirty status (with file counts), ahead/behind remote, last commit message and date.

**Status symbols:**
- `✓` clean
- `*` unstaged changes
- `+` staged changes
- `?` untracked files
- `❄` frozen

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
| `--editor` | `-e` | Open in default editor (VS Code, Cursor, etc.). |
| `--browser` | `-b` | Open the repo on GitHub/GitLab in your browser. |
| `--finder` | `-f` | Open in Finder/file manager. |
| `--path` | `-p` | Just print the path to stdout. |

With no flags, opens in the default editor. The `--path` flag is useful for shell integration:

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

### `gitstow tui`

Interactive terminal dashboard built with [Textual](https://github.com/Textualize/textual). Requires `pip install gitstow[tui]`.

Keyboard shortcuts:
- `r` — Refresh
- `p` — Pull all unfrozen repos
- `f` — Toggle freeze on selected repo
- `Enter` — Show repo details
- `/` — Focus filter input
- `q` — Quit

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
gitstow config set root_path ~/labs/OSS
gitstow config set default_host gitlab.com
gitstow config set prefer_ssh true
gitstow config set parallel_limit 8
```

### `gitstow config migrate-root`

Move all repos to a new root directory.

```bash
gitstow config migrate-root ~/new-location
gitstow config migrate-root ~/new-location --copy    # Keep originals
gitstow config migrate-root ~/new-location --yes      # Skip confirmation
```

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

### MCP Tools (12)

| Tool | Description |
|------|-------------|
| `list_repos` | List repos with tag/owner/query filters |
| `add_repo` | Clone a repo (shorthand or full URL) |
| `pull_repos` | Bulk pull with tag/exclude/frozen filters |
| `repo_status` | Git status dashboard across repos |
| `repo_info` | Detailed single repo info |
| `freeze_repo` | Freeze a repo (skip during pull) |
| `unfreeze_repo` | Unfreeze a repo |
| `tag_repo` | Add tags to a repo |
| `untag_repo` | Remove tags from a repo |
| `remove_repo` | Remove from tracking (optionally delete) |
| `search_repos` | Grep across repos with pattern and glob |
| `collection_stats` | Disk usage, owner breakdown, tag counts |

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
