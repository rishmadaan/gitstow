# gitstow

A git repository library manager — clone, organize, and maintain collections of repos you learn from.

![gitstow demo](demo.gif)

## Why?

AI-assisted development has created a new relationship with open source. Developers increasingly maintain local clones of repositories they study and reference, not just repos they contribute to. `gitstow` manages this collection as a first-class concern.

Existing tools solve parts of this:
- **[ghq](https://github.com/x-motemen/ghq)** (3.5k stars) auto-organizes repos by `host/owner/repo` — but can't pull, status-check, or run commands across them.
- **[gita](https://github.com/nosarthur/gita)** (1.8k stars) runs bulk git operations — but doesn't organize or auto-structure anything.

`gitstow` combines the best of both: **paste a URL → auto-organized → bulk pull, freeze, tag, and manage your entire collection.** Works across multiple workspaces — open-source collections, active projects, and more.

## Quick Start

```bash
pipx install gitstow   # recommended
# or: pip install gitstow

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

### Workspaces
```bash
# Track open-source repos (structured: owner/repo layout)
gitstow workspace add ~/oss --label oss --layout structured

# Track your active projects (flat: repos directly in folder)
gitstow workspace add ~/labs/projects --label active --layout flat --auto-tag active

# Scan to discover existing repos
gitstow workspace scan active
```

### Auto-Organization
```bash
gitstow add anthropic/claude-code facebook/react torvalds/linux
```
Creates (in a structured workspace):
```
~/oss/
├── anthropic/
│   └── claude-code/
├── facebook/
│   └── react/
└── torvalds/
    └── linux/
```

### Bulk Operations
```bash
gitstow pull                    # Update all repos across all workspaces
gitstow -w oss pull             # Only update oss workspace
gitstow pull --tag ai           # Only repos tagged 'ai'
gitstow pull --exclude-tag stale # Everything except stale repos
gitstow status                  # Git status dashboard
gitstow -w active status --dirty # Dirty repos in active workspace
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

gitstow is designed to be used primarily through AI tools. The Claude Code skill is installed automatically during onboarding and auto-updates on version bumps:

```bash
gitstow install-skill   # Or: gitstow onboard (includes this)
# Then in Claude Code: "add this repo" or "update my repos"
```

> **MCP server** is also available for non-Claude-Code AI tools (Claude Desktop, Cursor, etc.) via `pip install gitstow[mcp]`. See [docs/user/configuration.md](docs/user/configuration.md#mcp-server-optional) for setup. Note: MCP tools are always loaded into context and cost tokens even when not in use — the skill has zero overhead when inactive.

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
| `gitstow list` | List repos grouped by owner/workspace |
| `gitstow status` | Git status dashboard |
| `gitstow remove <repo>` | Remove a repo from tracking |
| `gitstow migrate <path>` | Adopt existing repos into structure |
| **Workspaces** | |
| `gitstow workspace list` | List all configured workspaces |
| `gitstow workspace add <path>` | Add a new workspace |
| `gitstow workspace remove <label>` | Remove a workspace |
| `gitstow workspace scan <label>` | Discover and register repos on disk |
| **Repo Management** | |
| `gitstow repo freeze <repo>` | Skip repo during pull |
| `gitstow repo unfreeze <repo>` | Re-enable pulling |
| `gitstow repo tag <repo> <tags...>` | Add tags to a repo |
| `gitstow repo untag <repo> <tag>` | Remove a tag |
| `gitstow repo tags` | List all tags with counts |
| `gitstow repo info <repo>` | Detailed repo info |
| **Power** | |
| `gitstow exec <command>` | Run a command in every repo |
| `gitstow search <pattern>` | Grep across all repos (uses ripgrep) |
| `gitstow open <repo>` | Open in editor, browser, or Finder |
| `gitstow stats` | Collection statistics and disk usage |
| **Sharing** | |
| `gitstow collection export` | Export collection as YAML, JSON, or URLs |
| `gitstow collection import <file>` | Import a collection from file |
| **Shell** | |
| `gitstow shell setup` | Show shell integration instructions |
| `gitstow shell init` | Print shell functions to source in rc file |
| `gitstow shell pick` | fzf-powered repo picker |
| `gitstow shell completions` | Tab completion for repo names, workspaces, tags |
| `gitstow tui` | Interactive terminal dashboard |
| **Config** | |
| `gitstow config show` | Show current config and workspaces |
| `gitstow config set <key> <value>` | Change a setting |
| `gitstow onboard` | First-run setup wizard |
| `gitstow doctor` | Health check |
| `gitstow install-skill` | Install Claude Code skill |
| `gitstow setup-ai` | Auto-detect AI tools and configure integration |

All commands accept `-w <label>` to filter to a specific workspace.

## Configuration

Config lives at `~/.gitstow/config.yaml`:

```yaml
workspaces:
  - path: ~/oss
    label: oss
    layout: structured
  - path: ~/labs/projects
    label: active
    layout: flat
    auto_tags: [active]
default_host: github.com
prefer_ssh: false
parallel_limit: 6
```

Repo metadata at `~/.gitstow/repos.yaml` (nested by workspace):

```yaml
oss:
  anthropic/claude-code:
    remote_url: https://github.com/anthropic/claude-code.git
    frozen: false
    tags: [ai, tools]
    added: '2026-04-05'
active:
  gitstow:
    remote_url: https://github.com/rishmadaan/gitstow.git
    tags: [active]
    added: '2026-04-05'
```

## How It Works

1. **Workspaces** — Each workspace is a directory with a layout mode (`structured` = owner/repo, `flat` = just repo). Repos are organized across workspaces, tagged, and managed as a unified collection.
2. **Folder-as-state** — The directory structure is the primary source of truth. `repos.yaml` supplements with metadata (frozen, tags, timestamps).
3. **Error isolation** — One bad repo never stops operations on others. Failures are collected and reported in a summary.
4. **Parallel execution** — Bulk operations use `asyncio` with a semaphore (default 6 concurrent) to prevent SSH connection storms.
5. **Zero-config start** — `gitstow add owner/repo` works immediately with sensible defaults.

### A note on folder structure

Structured workspaces organize repos as `owner/repo/` (e.g., `~/oss/anthropic/claude-code/`). Unlike [ghq](https://github.com/x-motemen/ghq) which includes the host (`root/github.com/owner/repo/`), we omit it for simplicity.

Flat workspaces skip the owner directory entirely — repos are just `workspace/repo-name/`. Use flat layout for directories where you already have projects organized your own way.

## Troubleshooting

**`gitstow: command not found`** — Make sure the install location is on your PATH. With pipx this is automatic. With pip, you may need `python3 -m gitstow` or add `~/.local/bin` to your PATH.

**SSH clone fails with "Permission denied (publickey)"** — Your SSH key isn't configured for the git host. Either add your key (`ssh-add`) or use HTTPS: `gitstow config set prefer_ssh false`.

**Workspace directory doesn't exist** — Run `gitstow doctor` to check workspace health. Create missing directories or update the path with `gitstow workspace add`.

**`gitstow doctor`** — Run this first when something isn't working. It checks git installation, config files, and workspace integrity.

## Development

```bash
git clone https://github.com/rishmadaan/gitstow
cd gitstow
pip install -e ".[dev]"
pytest
ruff check src/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

## License

MIT
