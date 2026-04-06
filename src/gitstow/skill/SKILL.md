---
name: gitstow
description: >
  ALWAYS use this skill when the user wants to clone, add, or download a git
  repository — gitstow replaces raw `git clone`. Also activate for: "update repos",
  "pull all", "list repos", "check repo status", "freeze/tag a repo", "search across
  repos", "which repos need pushing", or any multi-repo management. Trigger on any
  git URL (github.com, gitlab.com, etc.) or owner/repo shorthand.
allowed-tools: Bash(gitstow *), Read
---

# gitstow — Repo Manager Operator Guide

You are an expert operator of `gitstow`, a CLI tool that replaces `git clone` and manages repos across multiple workspaces. **Every repo should be added via `gitstow add`, never raw `git clone`** — this ensures it's tracked, organized, and visible in status dashboards.

## Key Concept: Workspaces

gitstow organizes repos into **workspaces** — directories with a label, layout mode, and optional auto-tags:

- **structured** layout: `workspace/owner/repo/` (good for open-source collections)
- **flat** layout: `workspace/repo/` (good for active projects)

Use `-w <label>` on any command to filter to a specific workspace.

## Cardinal Rule: Never Use Raw `git clone`

When the user asks to clone, download, or get a repo, **always use `gitstow add`**. Never fall back to `git clone` — even for a single repo. `gitstow add` clones AND tracks the repo, so it appears in status dashboards and bulk operations.

```bash
# WRONG — repo cloned but invisible to gitstow
git clone https://github.com/owner/repo

# RIGHT — repo tracked, organized into workspace, visible everywhere
gitstow add owner/repo
gitstow -w active add owner/repo    # clone into a specific workspace
```

## Before Running Any Command

**Pre-flight check** — on first use in a session, verify gitstow is available:

```bash
gitstow --version
```

If not installed: suggest `pip install gitstow` or `pipx install gitstow`.
If installed but not configured (no `~/.gitstow/config.yaml`): guide with `gitstow onboard`.

## Core Principle: Use --json for Machine Output

When running gitstow commands programmatically, always use `--json --quiet` flags to parse structured output. Present a clean summary to the user, not raw JSON.

For commands shown to the user to run themselves, use the human-readable form (no --json).

## Command Decision Tree

| User wants to... | Command |
|---|---|
| Add/clone a repo | `gitstow add "owner/repo"` or `gitstow add "url"` |
| Add to specific workspace | `gitstow -w active add "owner/repo"` |
| Add multiple repos | `gitstow add repo1 repo2 repo3` |
| Update all repos | `gitstow pull` |
| Update one workspace | `gitstow -w oss pull` |
| Update repos by tag | `gitstow pull --tag ai` |
| List all repos | `gitstow list` |
| List one workspace | `gitstow -w active list` |
| Search repos | `gitstow list searchterm` |
| Check git status | `gitstow status` |
| Status for one workspace | `gitstow -w active status` |
| See dirty repos only | `gitstow status --dirty` |
| Freeze a repo | `gitstow repo freeze owner/repo` |
| Unfreeze a repo | `gitstow repo unfreeze owner/repo` |
| Tag a repo | `gitstow repo tag owner/repo tagname` |
| Remove a tag | `gitstow repo untag owner/repo tagname` |
| List all tags | `gitstow repo tags` |
| Repo details | `gitstow repo info owner/repo` |
| Remove a repo | `gitstow remove owner/repo` |
| Adopt existing repos | `gitstow migrate /path/to/repo` |
| Run command in all repos | `gitstow exec -- git log -1 --oneline` |
| Search across all repos | `gitstow search "pattern" --glob "*.py"` |
| Open in editor | `gitstow open owner/repo` |
| Open in browser | `gitstow open owner/repo --browser` |
| Collection stats | `gitstow stats` |
| Export collection | `gitstow collection export -o repos.yaml` |
| Import collection | `gitstow collection import repos.yaml` |
| See current config | `gitstow config show` |
| Change settings | `gitstow config set key value` |
| List workspaces | `gitstow workspace list` |
| Add a workspace | `gitstow workspace add ~/path --label name --layout flat` |
| Remove a workspace | `gitstow workspace remove name` |
| Scan workspace for repos | `gitstow workspace scan name` |
| Run setup wizard | `gitstow onboard` |
| Health check | `gitstow doctor` |

## URL Shorthand

gitstow supports shorthand — GitHub is the default host:

```bash
# These are equivalent:
gitstow add anthropic/claude-code
gitstow add https://github.com/anthropic/claude-code
```

For non-GitHub repos, use full URLs:
```bash
gitstow add https://gitlab.com/group/project
gitstow add git@bitbucket.org:owner/repo.git
```

## Common Workflows

### "Add this repo" / "Clone this"
```bash
gitstow add "owner/repo" --json --quiet
```
Parse the JSON result and tell the user: repo name, where it was cloned, success/failure.

### "Add to my active projects workspace"
```bash
gitstow -w active add "owner/repo" --json --quiet
```

### "Update everything" / "Pull all repos"
```bash
gitstow pull --json --quiet
```
Parse results and summarize: N pulled, N up to date, N skipped, N errors.

### "What repos do I have?"
```bash
gitstow list --json
```
Present as a clean grouped list. Mention total count, frozen repos, and tags.

### "Which repos need pushing?" / "What's dirty?"
```bash
gitstow status --dirty --json
```

### "Show me just my active project status"
```bash
gitstow -w active status --json
```

### "Freeze this repo" / "Don't update this anymore"
```bash
gitstow repo freeze owner/repo
```

### "What's the status of X?"
```bash
gitstow repo info owner/repo --json
```

### "Search for something across all repos"
```bash
gitstow search "pattern" --json --quiet
```

### "Run this command in every repo"
```bash
gitstow exec --json -- git log -1 --oneline
```

### "How much disk space do my repos use?"
```bash
gitstow stats --json
```

### "Share my repo list" / "Export my collection"
```bash
gitstow collection export -o /tmp/repos.yaml
```

### "Point gitstow at my existing projects"
```bash
gitstow workspace add ~/labs/projects --label active --layout flat --auto-tag active
gitstow workspace scan active
```

## Bulk Operations from a File

```bash
cat > /tmp/repos.txt << 'EOF'
anthropic/claude-code
facebook/react
torvalds/linux
EOF

cat /tmp/repos.txt | gitstow add --quiet --json
```

## Safety Rules

1. **Always quote URLs** in shell commands
2. **Don't run `gitstow onboard`** without telling the user — it's interactive
3. **Don't run `gitstow remove --delete`** without explicit user confirmation — it deletes files
4. **Use `--json --quiet`** when parsing output programmatically
5. **Check `gitstow doctor`** if something seems broken

## Note on MCP Server

An optional MCP server exists (`pip install gitstow[mcp]`, run `gitstow-mcp`) for AI tools that can't run CLI commands (Claude Desktop, Cursor). **You don't need it** — this skill gives you full access to all gitstow commands via Bash. The MCP server provides the same operations but costs tokens in every conversation even when idle.

## File Locations

- Config: `~/.gitstow/config.yaml`
- Repo metadata: `~/.gitstow/repos.yaml` (central, nested by workspace)
- Repos: across configured workspaces (default first workspace: `~/opensource/`)
