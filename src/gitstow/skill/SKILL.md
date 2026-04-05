---
name: gitstow
description: >
  Manage a collection of git repositories — clone, organize, update, freeze,
  and tag repos. Activate when the user wants to add a repo, update repos,
  list their collection, check repo status, freeze/unfreeze repos, tag repos,
  or manage their git repository library.
allowed-tools: Bash(gitstow *), Read
---

# gitstow — Repo Library Manager Operator Guide

You are an expert operator of `gitstow`, a CLI tool that manages collections of git repositories organized by owner/repo.

## Before Running Any Command

**Pre-flight check** — on first use in a session, verify gitstow is available:

```bash
gitstow --version
```

If not installed: suggest `pip install gitstow` or `pipx install gitstow`.
If installed but not configured (no `~/.gitstow/config.yaml`): guide with `gitstow onboard`.

## Core Principle: Use --json for Machine Output

When YOU run gitstow commands, always use `--json --quiet` flags so you can parse structured output. Show the user a clean summary, not raw JSON.

When the USER wants to run commands themselves, show them the human-readable form (no --json).

## Command Decision Tree

| User wants to... | Command |
|---|---|
| Add/clone a repo | `gitstow add "owner/repo"` or `gitstow add "url"` |
| Add multiple repos | `gitstow add repo1 repo2 repo3` |
| Update all repos | `gitstow pull` |
| Update specific repos | `gitstow pull owner/repo` |
| Update repos by tag | `gitstow pull --tag ai` |
| List all repos | `gitstow list` |
| Search repos | `gitstow list searchterm` |
| Check git status | `gitstow status` |
| See dirty repos only | `gitstow status --dirty` |
| Freeze a repo | `gitstow repo freeze owner/repo` |
| Unfreeze a repo | `gitstow repo unfreeze owner/repo` |
| Tag a repo | `gitstow repo tag owner/repo tagname` |
| Remove a tag | `gitstow repo untag owner/repo tagname` |
| List all tags | `gitstow repo tags` |
| Repo details | `gitstow repo info owner/repo` |
| Remove a repo | `gitstow remove owner/repo` |
| Adopt existing repos | `gitstow migrate /path/to/repo` |
| Run a command in all repos | `gitstow exec -- git log -1 --oneline` |
| Search across all repos | `gitstow search "pattern" --glob "*.py"` |
| Open in editor | `gitstow open owner/repo` |
| Open in browser | `gitstow open owner/repo --browser` |
| Collection stats | `gitstow stats` |
| Export collection | `gitstow collection export -o repos.yaml` |
| Import collection | `gitstow collection import repos.yaml` |
| Move repos to new root | `gitstow config migrate-root ~/new-path` |
| See current config | `gitstow config show` |
| Change settings | `gitstow config set key value` |
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

### "Update everything" / "Pull all repos"
```bash
gitstow pull --json --quiet
```
Parse results and summarize: N pulled, N up to date, N skipped, N errors.
If errors, list them specifically.

### "What repos do I have?"
```bash
gitstow list --json
```
Present as a clean grouped list. Mention total count, frozen repos, and tags.

### "Which repos are dirty?"
```bash
gitstow status --dirty --json
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
Parse results and show file paths with context.

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
Then tell the user the file path.

### "Move my repos to a new folder"
```bash
gitstow config migrate-root ~/new-path --yes
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

## MCP Server

gitstow also has an MCP server (`gitstow-mcp`) that exposes 12 tools to any MCP-compatible AI tool. If the user is using Claude Desktop or another MCP client, they can configure it with:

```json
{"mcpServers": {"gitstow": {"command": "gitstow-mcp"}}}
```

The MCP server provides the same operations as the CLI but via structured JSON tool calls.

## File Locations

- Config: `~/.gitstow/config.yaml`
- Repo metadata: `<root>/.gitstow/repos.yaml` (portable, lives with repos)
- Repos: configured root path (default `~/opensource/`)
