---
summary: Install gitstow and manage your first repos in about 5 minutes.
read_when:
  - First time setting up gitstow
  - You want the fastest path to a working setup
  - You're new to multi-repo management
---

# Getting Started

Get gitstow installed and managing repos in about 5 minutes.

## What You Need

- **Python 3.10+** — check with `python3 --version`
- **git** — check with `git --version`
- **Internet** — for cloning repos

## 1. Install

```bash
pipx install gitstow   # recommended — keeps it isolated
# or: pip install gitstow
```

Every install includes the full product: all CLI commands plus the browser dashboard (`gitstow ui`).

No pipx? Most Linux distros don't ship it by default (and recent Debian/Ubuntu block plain `pip install` into the system Python):

```bash
sudo apt install pipx   # Debian/Ubuntu
brew install pipx       # macOS
```

Verify it worked:

```bash
gitstow --version
# gitstow v0.2.1
```

> **Command not found?** On some systems, pip installs to a directory not on your PATH. Try `python3 -m gitstow --version` instead, or use pipx which handles PATH automatically.

## 2. Create a Workspace

A fresh install has **no workspaces**. A workspace is the directory gitstow clones
into, and gitstow never invents one for you — so this is the first step.

The interactive wizard walks you through it (and suggests `~/opensource` as a path):

```bash
gitstow onboard
```

Or do it in one line:

```bash
gitstow workspace add ~/oss --label oss --layout structured
```

Until a workspace exists, commands that need one (`add`, `pull`, `list`, `status`, …)
stop with:

```
Error: No workspaces configured. Add one with `gitstow workspace add <path> --label <name>` or run `gitstow onboard`.
```

## 3. Add Your First Repo

```bash
gitstow add anthropic/claude-code
```

That's it. gitstow:
1. Recognizes `anthropic/claude-code` as GitHub shorthand
2. Clones to `~/oss/anthropic/claude-code/` (your first configured workspace)
3. Registers it in your collection

> **Which workspace?** With more than one configured, `gitstow add` uses the first
> one listed in `config.yaml`. Target another with `-w`, e.g. `gitstow -w active add owner/repo`.

## 4. Add More Repos

```bash
# GitHub shorthand (most common)
gitstow add facebook/react torvalds/linux

# Full URL (any git host)
gitstow add https://gitlab.com/group/project

# SSH URL
gitstow add git@bitbucket.org:owner/repo.git

# Shallow clone (saves disk space for large repos)
gitstow add torvalds/linux --shallow
```

## 5. See Your Collection

```bash
gitstow list
```

Output:
```
  gitstow — 3 repos across 3 owners

  anthropic/ (1 repo)
    claude-code              [ai, tools]      just now

  facebook/ (1 repo)
    react                                     just now

  torvalds/ (1 repo)
    linux                                     just now
```

## 6. Update Everything

```bash
gitstow pull
```

Output:
```
  Pulling 3 repos...

  Repo                Status          Details
  anthropic/claude    ✓ Pulled        3 commits pulled
  facebook/react      ○ Up to date    Already up to date
  torvalds/linux      ✓ Pulled        12 commits pulled

  2 pulled | 1 up to date
```

Every repo is pulled in parallel (up to 6 at once). If one repo fails, the others still update — you get a summary at the end.

## 7. Check Status

```bash
gitstow status
```

Shows branch, clean/dirty state, ahead/behind counts, and last commit across all repos in one dashboard.

## What's Next

- **[Commands Reference](commands.md)** — full list of commands and flags
- **[Configuration](configuration.md)** — customize workspaces, default host, SSH preference
- **[Concepts](concepts.md)** — how gitstow organizes repos, workspaces, folder structure, tags and freeze

## Optional: Interactive Setup

For a guided first-run experience:

```bash
gitstow onboard
```

This walks you through creating your first workspace (suggesting `~/opensource` as its path), choosing a default git host and SSH vs HTTPS preference, then scans for existing repos to register.

## Multiple Workspaces

If you want to manage repos in separate directories (e.g., open-source references vs active projects), add more workspaces:

```bash
gitstow workspace add ~/projects --label active --layout flat
```

Use `-w` to target a specific workspace:

```bash
gitstow add my-app -w active       # Clones to ~/projects/my-app/
gitstow pull -w oss                 # Only pull the oss workspace
gitstow list                        # Lists all workspaces by default
```

See [Concepts — Workspaces](concepts.md#workspaces) for more details.

## AI Integration (Recommended)

gitstow is built to be used primarily through AI tools. If you use [Claude Code](https://claude.ai/claude-code), install the skill:

```bash
gitstow install-skill
```

This is also done automatically during `gitstow onboard` and auto-updates when you upgrade gitstow. Once installed, you can say things like "add this repo" or "update my repos" conversationally.

> For non-Claude-Code AI tools (Claude Desktop, Cursor), an optional MCP server is available. See [Configuration — MCP Server](configuration.md#mcp-server-optional).

## Prefer a Browser?

```bash
gitstow ui
```

Opens a local dark-themed dashboard at `http://127.0.0.1:7853` — a tab you leave open. Shows dirty state across every repo at a glance, pulls single repos or all-of-them in parallel, adds/removes repos, edits tags. Auto-refreshes every 30 seconds. Click **Shutdown** in the footer (or Ctrl+C) when done. Running gitstow on a VPS? `gitstow ui --tailscale` (or the Tailscale toggle in the dashboard's Settings) also serves it on your tailnet so you can open it from your other devices — see [configuration.md](configuration.md#web-dashboard-over-tailscale). See [commands.md — `gitstow ui`](commands.md#gitstow-ui) for full details.

## Keep gitstow Up to Date

```bash
gitstow update --check    # just look; don't install
gitstow update            # upgrade to the latest PyPI version
```

## Troubleshooting

**"command not found: gitstow"**
- Try `python3 -m gitstow` as a fallback
- Or install with `pipx install gitstow` which manages PATH for you

**"Cannot parse URL"**
- Use `owner/repo` format for GitHub
- Use full URLs for other hosts: `https://gitlab.com/group/project`
- Always wrap URLs in quotes when they contain special characters

**Clone fails with authentication error**
- For HTTPS: check your git credential helper (`git config credential.helper`)
- For SSH: check your SSH key is added (`ssh -T git@github.com`)
- Set SSH as default: `gitstow config set prefer_ssh true`

**"No workspaces configured"**
- Expected on a fresh install, and after removing your last workspace — zero workspaces is a valid state, not a broken one.
- Fix it with `gitstow workspace add <path> --label <name>` or the `gitstow onboard` wizard. The same message appears in the web dashboard, with a link to the workspaces page.

**"gitstow doctor" for diagnostics**
```bash
gitstow doctor
```
This checks git installation, config files, and whether tracked repos match what's on disk.
