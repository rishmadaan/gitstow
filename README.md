# gitstow

A git repository library manager — clone, organize, and maintain collections of repos you learn from.

## Why?

AI-assisted development has created a new relationship with open source. Developers increasingly maintain local clones of repositories they study and reference, not just repos they contribute to. `gitstow` manages this collection as a first-class concern.

Existing tools solve parts of this:
- **[ghq](https://github.com/x-motemen/ghq)** (3.5k stars) auto-organizes repos by `host/owner/repo` — but can't pull, status-check, or run commands across them.
- **[gita](https://github.com/nosarthur/gita)** (1.8k stars) runs bulk git operations — but doesn't organize or auto-structure anything.

`gitstow` combines the best of both: **paste a URL → auto-organized into `owner/repo/` → bulk pull, freeze, tag, and manage your entire collection.**

## Quick Start

```bash
pip install gitstow

# First-run setup (optional — works without it)
gitstow onboard

# Add repos (GitHub shorthand or full URLs)
gitstow add anthropic/claude-code
gitstow add https://gitlab.com/group/project

# Update everything
gitstow pull

# See your collection
gitstow list

# Check git status across all repos
gitstow status
```

## Features

### Auto-Organization
```bash
gitstow add anthropic/claude-code facebook/react torvalds/linux
```
Creates:
```
~/opensource/
├── anthropic/
│   └── claude-code/
├── facebook/
│   └── react/
└── torvalds/
    └── linux/
```

### Bulk Operations
```bash
gitstow pull                    # Update all repos
gitstow pull --tag ai           # Only repos tagged 'ai'
gitstow pull --exclude-tag stale # Everything except stale repos
gitstow status                  # Git status dashboard
gitstow status --dirty          # Only dirty repos
```

### Freeze & Tags
```bash
gitstow repo freeze facebook/react   # Skip during pull
gitstow repo tag anthropic/claude-code ai tools
gitstow pull --tag ai                # Update only AI repos
gitstow repo tags                    # List all tags
```

### Migrate Existing Repos
```bash
gitstow migrate ~/old-projects/some-repo   # Auto-detects owner/repo from remote
```

### Any Git Host
```bash
gitstow add anthropic/claude-code              # GitHub (default)
gitstow add https://gitlab.com/group/project   # GitLab (nested groups work)
gitstow add git@bitbucket.org:owner/repo.git   # Bitbucket
gitstow add https://codeberg.org/owner/repo    # Codeberg
```

### AI Integration
```bash
gitstow install-skill   # Install Claude Code skill
# Then in Claude Code: "add this repo" or "update my repos"
```

### Run Commands Across Repos
```bash
gitstow exec -- git log -1 --oneline      # Last commit in each repo
gitstow exec --tag python -- wc -l README.md  # Line count in Python repos
```

### Search Across Repos
```bash
gitstow search "TODO"                     # Grep everything
gitstow search "def main" --glob "*.py"   # Only Python files
gitstow search "error" --tag ai -i        # Case-insensitive, AI repos only
```

### Share Your Collection
```bash
gitstow collection export -o my-repos.yaml   # Export as portable YAML
gitstow collection import my-repos.yaml      # Import on another machine
gitstow collection export --format urls      # Plain URL list
```

### Shell Integration
```bash
eval "$(gitstow shell init)"    # Add to ~/.zshrc or ~/.bashrc
gs                               # cd into a repo (fzf picker)
gse                              # Open repo in editor (fzf picker)
gsp                              # gitstow pull shorthand
```

### Interactive Dashboard
```bash
gitstow tui    # Keyboard-driven dashboard with filter, pull, freeze
```

### JSON Output
Every command supports `--json` for scripting and AI consumption:
```bash
gitstow list --json
gitstow pull --json --quiet
gitstow status --json
```

## Commands

| Command | Description |
|---------|-------------|
| **Core** | |
| `gitstow add <url> [urls...]` | Clone repos into organized structure |
| `gitstow pull` | Bulk update all (or filtered) repos |
| `gitstow list` | List repos grouped by owner |
| `gitstow status` | Git status dashboard |
| `gitstow remove <owner/repo>` | Remove a repo from tracking |
| `gitstow migrate <path>` | Adopt existing repos into structure |
| **Repo Management** | |
| `gitstow repo freeze <owner/repo>` | Skip repo during pull |
| `gitstow repo unfreeze <owner/repo>` | Re-enable pulling |
| `gitstow repo tag <owner/repo> <tags...>` | Add tags to a repo |
| `gitstow repo untag <owner/repo> <tag>` | Remove a tag |
| `gitstow repo tags` | List all tags with counts |
| `gitstow repo info <owner/repo>` | Detailed repo info |
| **Power** | |
| `gitstow exec <command>` | Run a command in every repo |
| `gitstow search <pattern>` | Grep across all repos (uses ripgrep) |
| `gitstow open <owner/repo>` | Open in editor, browser, or Finder |
| `gitstow stats` | Collection statistics and disk usage |
| **Sharing** | |
| `gitstow collection export` | Export collection as YAML, JSON, or URLs |
| `gitstow collection import <file>` | Import a collection from file |
| **Shell** | |
| `gitstow shell setup` | Show shell integration instructions |
| `gitstow shell pick` | fzf-powered repo picker |
| `gitstow tui` | Interactive terminal dashboard |
| **Config** | |
| `gitstow config show` | Show current config |
| `gitstow config set <key> <value>` | Change a setting |
| `gitstow config migrate-root <path>` | Move all repos to a new root |
| `gitstow onboard` | First-run setup wizard |
| `gitstow doctor` | Health check |
| `gitstow install-skill` | Install Claude Code skill |

## Changing Your Root Directory

To move your entire collection to a new location:

```bash
gitstow config migrate-root ~/new-location        # Move all repos
gitstow config migrate-root ~/new-location --copy  # Copy instead (keeps original)
```

This moves every repo, preserves the `owner/repo/` structure, and updates the config automatically. Don't use `config set root_path` — that only changes the pointer without moving files.

## Configuration

Config lives at `~/.gitstow/config.yaml`:

```yaml
root_path: ~/opensource       # Where repos are cloned
default_host: github.com      # For shorthand URLs (owner/repo)
prefer_ssh: false             # SSH vs HTTPS for cloning
parallel_limit: 6             # Max concurrent git operations
```

Repo metadata at `~/.gitstow/repos.yaml`:

```yaml
anthropic/claude-code:
  remote_url: https://github.com/anthropic/claude-code.git
  frozen: false
  tags: [ai, tools]
  added: 2026-04-05
  last_pulled: 2026-04-05T15:30:00
```

## How It Works

1. **Folder-as-state** — The directory structure (`root/owner/repo/`) is the primary source of truth. `repos.yaml` supplements with metadata (frozen, tags, timestamps).
2. **Error isolation** — One bad repo never stops operations on others. Failures are collected and reported in a summary.
3. **Parallel execution** — Bulk operations use `asyncio` with a semaphore (default 6 concurrent) to prevent SSH connection storms.
4. **Zero-config start** — `gitstow add owner/repo` works immediately with sensible defaults.

### A note on folder structure

gitstow organizes repos as `root/owner/repo/` (e.g., `~/opensource/anthropic/claude-code/`). Unlike [ghq](https://github.com/x-motemen/ghq) which includes the host (`root/github.com/owner/repo/`), we omit it for simplicity — most repos are on GitHub, and shorter paths are nicer to work with.

The tradeoff: if you have two repos with the same `owner/repo` on different hosts (e.g., GitHub and GitLab), they'd conflict. In practice this is extremely rare. If you hit it, use the full URL and gitstow will warn about the conflict.

## Development

```bash
git clone https://github.com/rishmadaan/gitstow
cd gitstow
pip install -e ".[dev]"
pytest
ruff check src/
```

## License

MIT
