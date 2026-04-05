---
summary: Configure gitstow — root path, default host, SSH preference, and repo metadata.
read_when:
  - Changing where repos are stored
  - Setting up SSH cloning
  - Moving repos to a new location
  - Understanding the config and metadata files
---

# Configuration

## Quick Config

```bash
# See current settings
gitstow config show

# Change settings
gitstow config set root_path ~/labs/OSS
gitstow config set default_host gitlab.com
gitstow config set prefer_ssh true
gitstow config set parallel_limit 8
```

Or run the interactive wizard:

```bash
gitstow onboard
```

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| `root_path` | `~/opensource` | Where repos are cloned. The `owner/repo/` structure is created under this directory. |
| `default_host` | `github.com` | Assumed host when you type shorthand like `owner/repo`. |
| `prefer_ssh` | `false` | If `true`, clones via SSH (`git@host:owner/repo.git`) instead of HTTPS. |
| `parallel_limit` | `6` | Maximum concurrent git operations during `pull` and `status`. |

## File Locations

gitstow uses two files, both in `~/.gitstow/`:

### `~/.gitstow/config.yaml`

Your settings. Created on first use or by `gitstow onboard`.

```yaml
root_path: ~/labs/OSS
default_host: github.com
prefer_ssh: false
parallel_limit: 6
```

### `~/.gitstow/repos.yaml`

Per-repo metadata: frozen status, tags, timestamps. Managed automatically — you don't edit this by hand.

```yaml
anthropic/claude-code:
  remote_url: https://github.com/anthropic/claude-code.git
  frozen: false
  tags: [ai, tools]
  added: 2026-04-05
  last_pulled: 2026-04-05T15:30:00
```

## Changing Your Root Directory

**Just changing the pointer** (repos stay where they are):

```bash
gitstow config set root_path ~/new-path
```

> This only updates the config. Existing repos are NOT moved. gitstow will warn you about this.

**Moving repos to a new location:**

```bash
gitstow config migrate-root ~/new-location
```

This:
1. Moves every repo from the old root to the new root
2. Preserves the `owner/repo/` structure
3. Updates the config to point to the new root
4. Reports success/failure per repo

Options:
- `--copy` — Copy instead of move (keeps the original)
- `--yes` — Skip the confirmation prompt

## SSH vs HTTPS

By default, gitstow clones over HTTPS. To prefer SSH:

```bash
gitstow config set prefer_ssh true
```

This affects all future `gitstow add` commands. You can also override per-command:

```bash
gitstow add owner/repo --ssh     # Force SSH for this clone
```

For SSH to work, you need an SSH key registered with your git host:
- GitHub: [Adding a new SSH key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
- GitLab: [Add an SSH key](https://docs.gitlab.com/ee/user/ssh.html)

## Parallel Limit

The `parallel_limit` controls how many git operations run simultaneously during `pull` and `status`. The default of 6 is conservative — it avoids overwhelming SSH connections (a known issue with tools like gita that have no limit).

If you have many repos and a fast connection, you can increase it:

```bash
gitstow config set parallel_limit 12
```

If you see SSH connection errors during bulk pulls, lower it:

```bash
gitstow config set parallel_limit 3
```

## MCP Server Setup

The MCP server lets any MCP-compatible AI tool manage your repos. Install with:

```bash
pip install gitstow[mcp]
```

Then configure your AI tool to use `gitstow-mcp` as a stdio server.

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp"
    }
  }
}
```

**Claude Code** — add to `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp",
      "type": "stdio"
    }
  }
}
```

The server exposes 12 tools (list, add, pull, status, freeze, tag, search, stats, etc.) and 3 resources. See [Commands Reference — MCP Server](commands.md#mcp-server) for the full list.

## Health Check

Run `gitstow doctor` to verify everything is configured correctly:

```bash
gitstow doctor
```

This checks:
- git is installed
- Config and repos files exist
- Root directory exists and is accessible
- Tracked repos match what's actually on disk
- Reports orphaned repos (on disk but not tracked) and missing repos (tracked but not on disk)
