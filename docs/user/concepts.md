---
summary: How gitstow organizes repos, the folder-as-state model, tags, freeze, and design decisions.
read_when:
  - Understanding how repos are organized on disk
  - Want to know why gitstow works the way it does
  - Learning about tags, freeze, and discovery
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

## Folder Structure

When you add a repo, gitstow creates:

```
~/opensource/              # Your root (configurable)
├── anthropic/             # Owner (from the URL)
│   ├── claude-code/       # Repo
│   └── sdk-python/        # Another repo by the same owner
├── facebook/
│   └── react/
└── torvalds/
    └── linux/
```

The path is derived from the URL: `https://github.com/anthropic/claude-code` becomes `root/anthropic/claude-code/`.

### Why no host in the path?

Tools like [ghq](https://github.com/x-motemen/ghq) include the host: `root/github.com/owner/repo/`. gitstow omits it for simplicity — shorter paths are nicer to work with, and most repos are on GitHub anyway.

**The tradeoff:** Two repos with the same `owner/repo` on different hosts (e.g., GitHub and GitLab) would conflict. In practice this is extremely rare. If you hit it, gitstow will warn about the conflict.

## Folder-as-State

The directory tree is the primary source of truth. `repos.yaml` is supplemental metadata (frozen, tags, timestamps) that enriches what's on disk.

This means:
- If you `git clone` something manually into the right place, `gitstow doctor` will notice it as "untracked" and suggest registering it
- If you delete a repo from disk, `gitstow doctor` will flag it as "missing"
- If `repos.yaml` gets corrupted, your repos are still fine — just re-register them

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

This two-level walk only checks `root/owner/repo/.git` — it doesn't descend deeper.

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

gitstow is designed to work with AI tools:

- **`--json` output** on every command — AI tools parse structured data, not terminal formatting
- **Claude Code skill** — `gitstow install-skill` enables conversational repo management ("add this repo", "update my repos", "what repos do I have?")
- **MCP server** — `gitstow-mcp` exposes 12 tools and 3 resources via the Model Context Protocol, so any MCP-compatible tool (Claude Desktop, Cursor, Windsurf, etc.) can manage your repos. See [Configuration — MCP Server Setup](configuration.md#mcp-server-setup).

This is the core thesis: developers increasingly maintain local repo clones that AI tools reference. gitstow makes that collection a managed, queryable resource.

## Comparison with Other Tools

| Feature | gitstow | ghq | gita |
|---------|---------|-----|------|
| Auto-organize by owner/repo | ✅ | ✅ (with host prefix) | ❌ |
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
