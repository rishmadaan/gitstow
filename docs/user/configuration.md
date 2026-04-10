---
summary: Configure gitstow — workspaces, default host, SSH preference, and repo metadata.
read_when:
  - Changing where repos are stored
  - Setting up workspaces
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
gitstow config set default_host gitlab.com
gitstow config set prefer_ssh true
gitstow config set parallel_limit 8

# Manage workspaces
gitstow workspace list
gitstow workspace add ~/projects --label active --layout flat
```

Or run the interactive wizard:

```bash
gitstow onboard
```

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| `workspaces` | Single workspace at `~/oss` | List of workspace directories. Each has a path, label, layout, and optional auto-tags. |
| `default_host` | `github.com` | Assumed host when you type shorthand like `owner/repo`. |
| `prefer_ssh` | `false` | If `true`, clones via SSH (`git@host:owner/repo.git`) instead of HTTPS. |
| `parallel_limit` | `6` | Maximum concurrent git operations during `pull` and `status`. |

## File Locations

gitstow uses two files, both in `~/.gitstow/`:

### `~/.gitstow/config.yaml`

Your settings. Created on first use or by `gitstow onboard`.

```yaml
workspaces:
  - path: ~/oss
    label: oss
    layout: structured
  - path: ~/projects
    label: active
    layout: flat
    auto_tags: [mine]
default_host: github.com
prefer_ssh: false
parallel_limit: 6
```

Each workspace entry has:
- `path` — directory on disk where repos are stored
- `label` — unique short name used in commands and repos.yaml
- `layout` — `structured` (owner/repo subdirectories) or `flat` (repos directly in workspace)
- `auto_tags` — (optional) tags automatically applied when repos are added or discovered

### `~/.gitstow/repos.yaml`

Per-repo metadata: frozen status, tags, timestamps. Managed automatically — you don't edit this by hand. Repos are nested under their workspace label:

```yaml
oss:
  anthropic/claude-code:
    remote_url: https://github.com/anthropic/claude-code.git
    frozen: false
    tags: [ai, tools]
    added: 2026-04-05
    last_pulled: 2026-04-05T15:30:00
active:
  my-app:
    remote_url: https://github.com/me/my-app.git
    frozen: false
    tags: [mine]
    added: 2026-04-05
    last_pulled: 2026-04-05T16:00:00
```

> **Legacy migration:** If you have a pre-workspace `repos.yaml` with flat keys (no workspace nesting), gitstow auto-migrates it to the new format under the `oss` workspace label on first load.

## Managing Workspaces

Workspaces replace the old single `root_path` setting. See [Concepts — Workspaces](concepts.md#workspaces) for the mental model.

```bash
# List all workspaces
gitstow workspace list

# Add a new workspace
gitstow workspace add ~/oss --label oss
gitstow workspace add ~/projects --label active --layout flat --auto-tag mine

# Remove a workspace (files stay on disk)
gitstow workspace remove old-workspace

# Scan a workspace to discover repos on disk
gitstow workspace scan oss
```

## Changing Your Root Directory

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

## MCP Server (Optional)

> **Most users don't need this.** The Claude Code skill is the recommended AI integration — it has zero context overhead and auto-updates on version bumps. The MCP server is only for AI tools that don't support Claude Code skills (Claude Desktop, Cursor, Windsurf).

**Context cost tradeoff:** MCP tools are always loaded into the AI's context window and cost tokens in every conversation, even when you're not managing repos. The Claude Code skill has zero cost when inactive — it only activates when the task matches.

If you still want MCP:

```bash
pip install gitstow[mcp]    # Install the optional dependency
gitstow setup-ai             # Auto-detect AI tools and configure
```

Or configure manually — see [Commands Reference — MCP Server](commands.md#mcp-server-optional).

## Health Check

Run `gitstow doctor` to verify everything is configured correctly:

```bash
gitstow doctor
```

This checks:
- git is installed
- Config and repos files exist
- All workspace directories exist and are accessible
- Tracked repos match what's actually on disk (per workspace)
- Reports orphaned repos (on disk but not tracked) and missing repos (tracked but not on disk)
