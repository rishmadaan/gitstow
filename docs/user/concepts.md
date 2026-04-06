---
summary: How gitstow organizes repos, workspaces, the folder-as-state model, tags, freeze, and design decisions.
read_when:
  - Understanding how repos are organized on disk
  - Want to know why gitstow works the way it does
  - Learning about workspaces, tags, freeze, and discovery
  - Comparing with ghq or gita
---

# Concepts

## The Repo Library Mental Model

gitstow treats your repo collection like a **library**, not a workspace. The key distinction:

| | Traditional Git tools | gitstow |
|---|---|---|
| **Your relationship to repos** | Contributor (commit, branch, PR) | Consumer (read, reference, stay current) |
| **Focus** | One repo at a time | All repos at once |
| **Primary action** | `git commit` | `gitstow pull` |
| **Organization** | You manage folder structure | Auto-organized by owner/repo |

You can still make changes inside gitstow-managed repos — it doesn't prevent that. But the tool is optimized for the "keep a curated collection updated" workflow.

## Workspaces

A workspace is a directory that gitstow manages repos in. You can have multiple workspaces, each with its own path, layout mode, and auto-tags. This is useful when you want to separate different kinds of repos — for example, open-source references in one directory and active projects in another.

Each workspace has:
- **label** — a unique short name (e.g., `oss`, `work`, `active`)
- **path** — the directory on disk (e.g., `~/opensource`, `~/projects`)
- **layout** — how repos are organized: `structured` (owner/repo) or `flat` (just repo name)
- **auto_tags** — tags automatically applied to repos discovered in this workspace

```yaml
# Example: two workspaces in config.yaml
workspaces:
  - path: ~/opensource
    label: oss
    layout: structured
  - path: ~/projects
    label: active
    layout: flat
    auto_tags: [mine]
```

The first workspace is the default — used when you run commands without specifying `-w/--workspace`.

If the same repo name exists in multiple workspaces (e.g., `anthropic/claude-code` in both `oss` and `work`), gitstow will prompt you to choose or you can disambiguate with `--workspace`:

```bash
gitstow pull --workspace oss
gitstow repo info anthropic/claude-code -w work
```

> **Migration from single root:** If you have a pre-workspace config with `root_path`, gitstow auto-migrates it to a single workspace labeled `oss` on first use. No action needed.

## Folder Structure

When you add a repo, gitstow places it in the default workspace (or the one you specify with `-w`).

### Structured layout (default)

Repos are organized by `owner/repo`:

```
~/opensource/              # Workspace path (structured layout)
├── anthropic/             # Owner (from the URL)
│   ├── claude-code/       # Repo
│   └── sdk-python/        # Another repo by the same owner
├── facebook/
│   └── react/
└── torvalds/
    └── linux/
```

The path is derived from the URL: `https://github.com/anthropic/claude-code` becomes `workspace_path/anthropic/claude-code/`.

### Flat layout

Repos are placed directly in the workspace directory by name, without the owner prefix:

```
~/projects/                # Workspace path (flat layout)
├── claude-code/
├── react/
└── my-app/
```

Flat layout is useful for workspaces where you own the repos and don't need the owner-based grouping.

### Why no host in the path?

Tools like [ghq](https://github.com/x-motemen/ghq) include the host: `root/github.com/owner/repo/`. gitstow omits it for simplicity — shorter paths are nicer to work with, and most repos are on GitHub anyway.

**The tradeoff:** Two repos with the same `owner/repo` on different hosts (e.g., GitHub and GitLab) would conflict. In practice this is extremely rare. If you hit it, gitstow will warn about the conflict.

## Folder-as-State

The directory tree is the primary source of truth. `repos.yaml` is supplemental metadata (frozen, tags, timestamps, workspace membership) that enriches what's on disk.

This means:
- If you `git clone` something manually into the right place, `gitstow doctor` will notice it as "untracked" and suggest registering it
- If you delete a repo from disk, `gitstow doctor` will flag it as "missing"
- If `repos.yaml` gets corrupted, your repos are still fine — just re-register them
- You can scan a workspace to auto-discover repos: `gitstow workspace scan <label>`

## Tags

Tags are semantic labels you attach to repos for filtering.

```bash
gitstow repo tag anthropic/claude-code ai tools
gitstow repo tag facebook/react frontend reference
```

Tags enable targeted operations:

```bash
gitstow pull --tag ai              # Only update AI repos
gitstow list --tag reference       # Show reference-only repos
gitstow pull --exclude-tag stale   # Update everything except stale
gitstow repo freeze --tag archived # Freeze all archived repos at once
```

Tags are stored in `repos.yaml`, not on disk. They're lowercase by convention.

Workspaces can also have **auto-tags** — tags automatically applied to any repo discovered or added in that workspace. This is useful for categorizing repos by workspace without manual tagging.

Use `gitstow repo tags` to see all tags with repo counts.

### Tags vs Owners

- **Owners** are structural — they come from the URL and determine the folder path. You don't choose them.
- **Tags** are semantic — they represent purpose, category, or status. You curate them.

A repo has one owner but can have many tags. You can filter by either.

## Freeze

Frozen repos are skipped during `gitstow pull`. Use freeze for repos that:
- Are archived or unmaintained upstream
- You want at a specific version (don't want surprise updates)
- Have gotten into a bad state and you haven't cleaned up yet
- You're studying a specific commit and don't want it to change

```bash
gitstow repo freeze facebook/react     # Freeze one repo
gitstow repo freeze --tag archived     # Freeze all repos with a tag
gitstow repo unfreeze facebook/react   # Re-enable pulling
gitstow pull --include-frozen          # Override for one pull
```

Frozen repos still appear in `gitstow list` and `gitstow status` (marked with `❄`).

## Discovery and Reconciliation

gitstow keeps its registry (`repos.yaml`) in sync with what's actually on disk. When you run `gitstow doctor`, `gitstow list`, or `gitstow status`, it reconciles:

- **Matched** — tracked in `repos.yaml` AND exists on disk (healthy)
- **Orphaned** — exists on disk but NOT in `repos.yaml` (untracked). Suggestion: `gitstow add <path>` to register
- **Missing** — tracked in `repos.yaml` but NOT on disk (deleted externally). Suggestion: `gitstow remove <key>` to clean up

Discovery respects each workspace's layout mode — in structured workspaces it checks `path/owner/repo/.git`, in flat workspaces it checks `path/repo/.git`.

You can also explicitly scan a workspace to discover and register repos on disk: `gitstow workspace scan <label>`.

## Error Isolation

One bad repo never stops operations on others. During `gitstow pull`:

- Network errors → logged, other repos keep pulling
- Dirty repos → skipped with warning, others keep pulling
- Auth failures → logged, others keep pulling
- Merge conflicts → logged (shouldn't happen with `--ff-only`), others keep pulling

Results are collected and shown in a summary table at the end.

## Parallel Execution

Bulk operations (`pull`, `status`) run concurrently using asyncio with a semaphore. The default limit is 6 concurrent operations.

This matters because:
- Too many simultaneous SSH connections crash (gita's most-reported issue — 87 repos at once kills SSH)
- Too few is slow with large collections
- 6 is a practical sweet spot (same as ghq's parallel clone limit)

Configure with: `gitstow config set parallel_limit <N>`

## AI Integration

gitstow is designed to be used primarily through AI tools — the CLI is the engine, but AI is the expected interface.

- **Claude Code skill** (primary) — `gitstow install-skill` or `gitstow onboard`. Enables conversational repo management ("add this repo", "update my repos"). Auto-updates on version bumps. Zero context cost when inactive — only loads when the task matches.
- **`--json` output** on every command — AI tools parse structured data, not terminal formatting. The skill uses `--json --quiet` behind the scenes.
- **MCP server** (optional) — for AI tools that don't support Claude Code skills (Claude Desktop, Cursor). Install with `pip install gitstow[mcp]`. Tradeoff: MCP tools are always loaded into context, costing tokens even when idle. See [Configuration](configuration.md#mcp-server-optional).

This is the core thesis: developers increasingly maintain local repo clones that AI tools reference. gitstow makes that collection a managed, queryable resource.

## Comparison with Other Tools

| Feature | gitstow | ghq | gita |
|---------|---------|-----|------|
| Auto-organize by owner/repo | ✅ | ✅ (with host prefix) | ❌ |
| Multiple workspaces | ✅ | ❌ | ❌ |
| Flat + structured layouts | ✅ | ❌ | ❌ |
| Bulk pull | ✅ | ❌ | ✅ |
| Status dashboard | ✅ | ❌ | ✅ |
| Freeze/skip repos | ✅ | ❌ | Via groups |
| Tags | ✅ | ❌ | ❌ (has groups) |
| URL shorthand | ✅ | ✅ | ❌ |
| JSON output | ✅ | ❌ | ❌ |
| AI integration | ✅ | ❌ | ❌ |
| Concurrency throttle | ✅ | ✅ | ❌ (causes SSH crashes) |
| Any git host | ✅ | ✅ | ✅ |
| Migrate existing repos | ✅ | ✅ | ❌ |
| Language | Python | Go | Python |
