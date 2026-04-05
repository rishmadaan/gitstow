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
