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
pip install gitstow
```

Or with [pipx](https://pipx.pypa.io/) (recommended — keeps it isolated):

```bash
pipx install gitstow
```

Verify it worked:

```bash
gitstow --version
# gitstow v0.2.1
```

> **Command not found?** On some systems, pip installs to a directory not on your PATH. Try `python3 -m gitstow --version` instead, or use pipx which handles PATH automatically.

## 2. Add Your First Repo

```bash
gitstow add anthropic/claude-code
```

That's it. gitstow:
1. Recognizes `anthropic/claude-code` as GitHub shorthand
2. Clones to `~/oss/anthropic/claude-code/` (your default workspace)
3. Registers it in your collection

> **Default workspace:** Repos go to `~/oss/` by default (in a workspace labeled `oss`). Change it with `gitstow onboard` for the interactive setup wizard, or add additional workspaces with `gitstow workspace add`.

## 3. Add More Repos

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

## 4. See Your Collection

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

## 5. Update Everything

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

## 6. Check Status

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

This walks you through choosing a root directory, default git host, SSH vs HTTPS preference, and scans for existing repos to register.

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

Opens a local dark-themed dashboard at `http://127.0.0.1:7853` — a tab you leave open. Shows dirty state across every repo at a glance, pulls single repos or all-of-them in parallel, adds/removes repos, edits tags. Auto-refreshes every 30 seconds. Click **Shutdown** in the footer (or Ctrl+C) when done. See [commands.md — `gitstow ui`](commands.md#gitstow-ui) for full details.

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

**"gitstow doctor" for diagnostics**
```bash
gitstow doctor
```
This checks git installation, config files, and whether tracked repos match what's on disk.
