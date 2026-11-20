---
summary: Complete reference for all gitstow commands, flags, and usage patterns.
read_when:
  - Looking up a specific command's syntax or flags
  - Want to see all available commands
  - Need JSON output or scripting examples
---

# Commands Reference

Every command supports `--help` for built-in documentation:

```bash
gitstow add --help
gitstow pull --help
```

## Global Flag: `--workspace` / `-w`

All commands accept a global `-w/--workspace` flag to scope operations to a single workspace:

```bash
gitstow -w oss pull            # Only pull repos in the 'oss' workspace
gitstow -w active list         # Only list repos in the 'active' workspace
gitstow -w work status         # Only show status for 'work' workspace
```

Without `-w`, commands operate across all workspaces. If a repo key is ambiguous (exists in multiple workspaces), gitstow will prompt you to choose or you can disambiguate with `-w`.

## Core Commands

### `gitstow add`

Clone repos into the organized `owner/repo/` structure.

```bash
gitstow add <url> [urls...]
```

**Arguments:**
- `url` — GitHub shorthand (`owner/repo`), full HTTPS URL, or SSH URL. Multiple URLs accepted.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--shallow` | `-s` | Shallow clone (`--depth 1`). Saves disk space. |
| `--branch` | `-b` | Clone a specific branch. |
| `--update` | `-u` | Pull if repo already exists (instead of skipping). |
| `--tag` | `-t` | Apply tag(s) immediately. Repeatable. |
| `--recursive` | `-r` | Initialize submodules after clone. |
| `--ssh` | | Force SSH clone URL (overrides config). |
| `--retry` | | Retry failed clones N times (e.g., `--retry 3`). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress progress messages. |

**Examples:**

```bash
# GitHub shorthand
gitstow add anthropic/claude-code

# Multiple repos at once
gitstow add facebook/react torvalds/linux golang/go

# Full URL (any git host)
gitstow add https://gitlab.com/group/subgroup/project

# SSH URL
gitstow add git@bitbucket.org:owner/repo.git

# Shallow clone with tags
gitstow add torvalds/linux --shallow --tag reference --tag kernel

# From a file (one URL per line)
cat repos.txt | gitstow add

# Update if already exists
gitstow add anthropic/claude-code --update
```

**Behavior:**
- Repos are added to the first configured workspace, or the workspace specified with `-w`. With none configured, `add` exits 1 with `No workspaces configured…` and clones nothing
- If the repo is already tracked: skips (or pulls with `--update`). If its folder is missing on disk, it errors instead and names the way out: `gitstow -w <label> remove <key>`, then add it again to re-clone (the MCP `add_repo` tool names `remove_repo` with `<label>:<key>`, the dashboard's Add page the row's ⋯ menu Remove)
- If the same repo is already tracked in that workspace under another URL form (HTTPS vs SSH, or an Azure repo first added by its SSH `v3/` URL): reports it as already tracked under its stored name and folder, never clones a second copy; `--update` pulls that existing folder
- If the path exists on disk as a git repo whose `origin` remote matches, but isn't tracked: registers it automatically
- If the path exists on disk as a git repo with **no** `origin` remote (one you `git init`ed yourself): errors and never clones over it, printing the command to run inside it (`git -C <folder> remote add origin <url>`); run the same `gitstow add` again afterwards to register it
- If the path exists on disk with a **different** remote than the one requested: errors with a remote-mismatch message instead of silently registering — move the directory or pick another workspace
- If the path exists but isn't a git repo: errors
- Equivalent URLs passed twice in the same invocation (e.g. HTTPS and SSH forms of the same repo) are deduplicated — only the first is cloned, the rest are reported as duplicates
- Multiple URLs are cloned concurrently (bounded by `parallel_limit`, default 6), with per-clone retry via `--retry`. Each clone runs in a hidden staging folder beside the target (`.gitstow-clone-…`) and is moved into place only when complete; if something appeared at the target in the meantime (another process got there first), that repo fails with `already exists … left untouched` and whatever is there is kept (on Linux and macOS an empty folder at the target is simply replaced; on Windows that clone stops with `already exists`). A clone that fails for good leaves no repo folder behind (an empty owner folder it created may stay). If gitstow is killed mid-clone, a hidden `.gitstow-clone-…` folder may be left beside the target; it is safe to delete
- A URL whose owner or repo would not be a safe folder name (a `.`, `..` or empty path segment, a segment of only dots and spaces, or a `\`, `:` or control character; a control character inside the URL, such as a pasted tab, is refused rather than dropped; whitespace around the whole URL, including a trailing newline, is trimmed first) is refused as invalid, so a repo always lands inside its workspace
- **Limits:** clones and pulls follow symlinks you place inside a workspace (a delete or move refuses a folder that resolves outside it). gitstow does not check for two repos that map to one folder on a case-insensitive file system (`Acme/Widget` and `acme/widget`) or because Windows drops a trailing dot (`widget.` and `widget`), and a Windows reserved name (`con`, `nul`) makes the clone fail

---

### `gitstow pull`

Bulk update repos: `git fetch --prune`, then a fast-forward-only merge of the upstream branch.

```bash
gitstow pull [repos...] [flags]
```

**Arguments:**
- `repos` — Optional. Specific repos to pull (`owner/repo`). Omit to pull all.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only pull repos with this tag. Repeatable. |
| `--exclude-tag` | | Skip repos with this tag. Repeatable. |
| `--owner` | | Only pull repos from this owner. |
| `--include-frozen` | | Include frozen repos (normally skipped). |
| `--force` | | Also pull the named repos when they have modified or staged files. Needs repo names: without them the command stops with an error and pulls nothing (filters do not count). Git still refuses any pull that would overwrite them. |
| `--retry` | | Retry failed repos N times (e.g., `--retry 3`). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress per-repo progress. |

**Examples:**

```bash
# Pull everything (frozen repos skipped)
gitstow pull

# Only repos tagged 'ai'
gitstow pull --tag ai

# Everything except archived repos
gitstow pull --exclude-tag archived

# Specific repos
gitstow pull anthropic/claude-code facebook/react

# Pull frozen repos too
gitstow pull --include-frozen

# Pull a repo even though it has modified files
gitstow pull anthropic/claude-code --force
```

**Behavior:**
- Frozen repos are skipped (shown as "Frozen" in summary)
- Repos with modified or staged files are skipped — the composition is shown in the summary (e.g. "2 modified · 1 staged"). With `--force` the repos you name are pulled (a bulk `--force` is refused); git's ff-only pull still refuses (reported as an error, files untouched) if the pull would overwrite a modified file
- Diverged repos (local and remote both have commits the other lacks) are skipped — resolve manually (rebase or merge). This holds with `--force`, and for frozen repos under `--include-frozen`
- Local-only repos (no upstream configured) are skipped, likewise
- Repos with only untracked files ARE pulled — an ff-only pull can't lose them
- Uses `--ff-only` — won't create merge commits
- Never overwrites your local edits or ignored files, whatever your git settings for stashing, rebasing or merge options: a pull that would replace a modified file, or an ignored local file (like a `.env`) that upstream has started tracking, is refused and reported as an error with the files left as they were. Autostash settings (`merge.autoStash`, `rebase.autoStash`), rebase settings (`pull.rebase`, `branch.<name>.rebase`), per-branch merge options (`branch.<name>.mergeOptions`) and a merge strategy set in `pull.twohead` are not used. A branch whose name contains `=` and that has merge options set is refused (pull it with git)
- gitstow only ever changes the repo's own folder: a tracked folder whose `.git` is broken or empty is reported as an error, never treated as part of a repo around it. gitstow tells git not to look above the folder, and before it fetches or merges it checks that git sees the folder as the top of its own repository, and otherwise leaves it untouched ("… is not the top of its own git repository; left untouched"). `gitstow fetch` makes the same check
- **Limits:** a pull only fast-forwards, and refuses when that would overwrite a modified, staged, untracked or ignored file. It does not stop programs you have told git to run during a pull, such as hooks (`post-merge`), clean/smudge filters (Git LFS) or helpers chosen through your environment (`GIT_EXEC_PATH`); those run as you configured them and can change files. Editing files while a pull runs can also race with it. `--prune` removes remote-tracking refs that are gone upstream, and with custom refspecs it can remove tags too
- A branch whose upstream was deleted on the remote is reported as "the upstream branch no longer exists on the remote". A detached HEAD is skipped as a local-only repo here; where a single repo is pulled directly (the dashboard's row Pull, `gitstow add --update`) it is reported as "not on a branch"
- Runs in parallel (configurable, default 6 concurrent)
- Updates `last_pulled` timestamp on success

---

### `gitstow fetch`

Fetch from all remotes — updates ahead/behind counts without merging.

```bash
gitstow fetch [repos...] [flags]
```

**Arguments:**
- `repos` — Optional. Specific repos to fetch. Omit for all.

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only fetch repos with this tag. Repeatable. |
| `--exclude-tag` | | Skip repos with this tag. Repeatable. |
| `--owner` | | Only fetch repos from this owner. |
| `--retry` | | Retry failed repos N times. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Suppress per-repo progress. |

**Examples:**

```bash
# Fetch everything (including frozen repos)
gitstow fetch

# Only repos tagged 'active'
gitstow fetch --tag active

# Specific repos
gitstow fetch anthropic/claude-code
```

**Behavior:**
- Frozen repos are included (fetch is non-destructive)
- Dirty repos are included (fetch doesn't touch the working tree)
- Runs `git fetch --all --prune` per repo
- Runs in parallel (configurable, default 6 concurrent)
- Updates `last_fetched` timestamp on success

---

### `gitstow list`

Show all tracked repos, grouped by owner.

```bash
gitstow list [query] [flags]
```

**Arguments:**
- `query` — Optional substring filter (e.g., `gitstow list react`).

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Filter by tag. |
| `--owner` | | Filter by owner. |
| `--frozen` | | Show only frozen repos. |
| `--paths` | `-p` | Show full filesystem paths. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Minimal output. |

**Examples:**

```bash
gitstow list                    # All repos
gitstow list react              # Search for 'react'
gitstow list --tag ai           # Filter by tag
gitstow list --owner anthropic  # Filter by owner
gitstow list --frozen           # Only frozen repos
gitstow list --paths            # Show full paths
gitstow list --json             # Machine-readable output
```

If a workspace has repos on disk that aren't tracked yet, `list` prints a hint (e.g. `⚠ 2 untracked repos in oss — run gitstow workspace scan oss`) — suppressed by `--json`/`--quiet`.

---

### `gitstow status`

Git status dashboard across all repos.

```bash
gitstow status [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Filter by tag. |
| `--owner` | | Filter by owner. |
| `--dirty` | | Show only repos with local changes. |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Minimal output. |

Shows: repo name, branch, a **Local Changes** column (composition — e.g. "2 modified · 1 staged · 3 untracked", or "clean"), a separate **Remote** column (in-sync / ahead / behind / diverged / no-upstream), and last commit message and date. Local working-tree state and remote relationship are always shown as two separate dimensions — never a bare "dirty" bucket.

**JSON output** (`--json`) keeps all existing flat fields (`dirty`, `staged`, `untracked`, `ahead`, `behind`, `clean`, `status_symbol`, ...) and adds two keys from the shared status model:
- `local` — `{modified, staged, untracked, summary}`
- `remote` — `{state, ahead, behind}`, where `state` is one of `in-sync` / `ahead` / `behind` / `diverged` / `no-upstream` / `unknown`

**Status symbols** (legacy `status_symbol` JSON field):
- `✓` clean
- `*` unstaged changes
- `+` staged changes
- `?` untracked files
- `❄` frozen

Like `list`, `status` prints an untracked-repos hint per workspace when repos exist on disk but aren't tracked — suppressed by `--json`/`--quiet`.

---

### `gitstow remove`

Remove a repo from tracking.

```bash
gitstow remove <owner/repo> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--yes` | `-y` | Skip confirmation prompt. |
| `--delete` | | Also delete the repo from disk. |
| `--json` | `-j` | JSON output. |

By default, only removes from gitstow's registry. Files stay on disk. Use `--delete` to remove the directory too.

---

### `gitstow migrate`

Adopt existing repos into the gitstow structure.

```bash
gitstow migrate <path> [paths...]
```

Reads the remote URL from the repo, determines the owner/repo, moves the directory into your gitstow root, and registers it.

The repo you name can live anywhere, but its destination must stay inside the workspace: if the destination resolves outside it (for example through a symlinked owner folder), the repo is left where it is and reported as an error.

```bash
gitstow migrate ~/old-projects/some-repo
gitstow migrate ~/random-clones/*
```

---

## Repo Management Commands

All under the `gitstow repo` subcommand.

### `gitstow repo freeze` / `gitstow repo unfreeze`

Toggle the freeze flag. Frozen repos are skipped during `pull`.

```bash
gitstow repo freeze facebook/react
gitstow repo freeze --tag archived     # Freeze all repos with a tag
gitstow repo unfreeze facebook/react
```

### `gitstow repo tag` / `gitstow repo untag`

Manage tags on repos.

```bash
gitstow repo tag anthropic/claude-code ai tools reference
gitstow repo untag anthropic/claude-code reference
```

### `gitstow repo tags`

List all tags with repo counts.

```bash
gitstow repo tags
gitstow repo tags --quiet   # One tag per line (for scripting/completions)
```

### `gitstow repo info`

Detailed view of a single repo: remote URL, path, branch, status, frozen, tags, disk size, last commit.

```bash
gitstow repo info anthropic/claude-code
gitstow repo info anthropic/claude-code --json
```

---

## Power Commands

### `gitstow exec`

Run an arbitrary command in every repo's directory.

```bash
gitstow exec <command...>
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only run in repos with this tag. |
| `--owner` | | Only run in repos from this owner. |
| `--frozen` | | Only run in frozen repos. |
| `--sequential` | `-s` | Run one at a time (default: parallel). |
| `--json` | `-j` | JSON output. |
| `--quiet` | `-q` | Only show command output, no headers. |

**Examples:**

```bash
gitstow exec -- git log -1 --oneline
gitstow exec -- git branch --show-current
gitstow exec --tag python -- wc -l README.md
gitstow exec -- ls -la
gitstow exec --sequential -- git fetch    # One at a time
```

> Use `--` before the command to separate gitstow flags from command arguments.

---

### `gitstow search`

Grep across all repos. Uses ripgrep (`rg`) if available, falls back to `git grep`.

```bash
gitstow search <pattern> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Only search in repos with this tag. |
| `--owner` | | Only search in repos from this owner. |
| `--glob` | `-g` | File glob pattern (e.g., `*.py`, `*.md`), or `!` in front to exclude (e.g., `'!*.md'`). Works with ripgrep and with the `git grep` fallback. |
| `--ignore-case` | `-i` | Case-insensitive search. |
| `--files` | `-l` | Only show file paths, not matching lines. |
| `--max` | `-m` | Max results per repo (default: 50). |
| `--json` | `-j` | JSON output. |

**Examples:**

```bash
gitstow search "TODO"
gitstow search "def main" --glob "*.py"
gitstow search "import React" --tag frontend
gitstow search "error" -i --files
gitstow search -- -foo                      # A pattern that starts with "-"
```

---

### `gitstow open`

Open a repo in your editor, browser, or file manager.

```bash
gitstow open <owner/repo> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--editor` | `-e` | Open in your editor (`$VISUAL`/`$EDITOR`, then VS Code/Cursor). This is the default. |
| `--browser` | `-b` | Open the repo on GitHub/GitLab in your browser. |
| `--finder` | `-f` | Open in Finder/file manager. |
| `--path` | `-p` | Just print the path to stdout. |

With no flags, opens in an editor: `$VISUAL`, then `$EDITOR`, then a fallback of VS Code (`code`) or Cursor (`cursor`) if installed. Terminal editors (`vi`, `vim`, `nvim`, `nano`, `emacs`, `hx`, `kak`, `micro`) run in the foreground since they need the TTY; GUI editors are launched detached. The `--path` flag is useful for shell integration:

```bash
cd "$(gitstow open anthropic/claude-code -p)"
```

---

### `gitstow stats`

Collection statistics — total repos, owners, tags, frozen count, and disk usage breakdown.

```bash
gitstow stats
gitstow stats --json
```

---

## Sharing Commands

### `gitstow collection export`

Export your collection to a portable file.

```bash
gitstow collection export [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--output` | `-o` | Output file path. Defaults to stdout. |
| `--tag` | `-t` | Only export repos with this tag. |
| `--format` | `-f` | Output format: `yaml` (default), `json`, or `urls`. |

**Examples:**

```bash
gitstow collection export                          # YAML to stdout
gitstow collection export -o my-repos.yaml         # YAML to file
gitstow collection export --format urls            # Plain URL list
gitstow collection export --tag ai -o ai.yaml      # Export subset
```

### `gitstow collection import`

Import a collection from a file. Supports YAML (from export), JSON, or plain URL lists.

```bash
gitstow collection import <file> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--tag` | `-t` | Apply tag(s) to all imported repos. |
| `--shallow` | `-s` | Shallow clone imported repos. |
| `--dry-run` | `-n` | Show what would be imported without doing it. |

**Behavior:**
- A repo already tracked in its target workspace (under any URL form) is skipped instead of cloning a second copy; its tags and frozen state are left as they are
- A file that lists the same repo twice imports it once, with the first entry's tags and frozen state
- An entry whose folder is already used by a different repo (another host, or another owner in a flat workspace), either earlier in the file or already tracked, is not imported: `gitstow collection import` reports it as a conflict with the reason, and the dashboard's import banner lists it with the reason
- Every entry is judged by its URL, whatever the file format: an exported YAML or JSON file is treated like a URL list (the `key` stored in an exported file is not used)
- `--dry-run` lists every entry once with what it would do: add, skip (with the reason), conflict, or cannot add
- A URL whose owner or repo would not be a safe folder name (a `.`, `..` or empty path segment, a segment of only dots and spaces, or a `\`, `:` or control character; a control character inside the URL, such as a pasted tab, is refused rather than dropped; whitespace around the whole URL, including a trailing newline, is trimmed first) is refused as invalid

---

## Shell Integration

### `gitstow shell setup`

Show instructions for setting up shell integration (aliases and fzf picker).

### `gitstow shell init [bash|zsh|fish]`

Print shell functions to source in your rc file. Provides:
- `gs` — cd into a repo via fzf picker
- `gse` — open a repo in editor via fzf picker
- `gsp` — `gitstow pull` shorthand
- `gss` — `gitstow status` shorthand
- `gsl` — `gitstow list` shorthand
- `gsa` — `gitstow add` shorthand

### `gitstow shell pick`

Interactive repo picker (uses fzf if available, falls back to beaupy). Outputs the selected repo's path. Designed for piping:

```bash
cd "$(gitstow shell pick)"
code "$(gitstow shell pick)"
```

### `gitstow shell completions [bash|zsh|fish]`

Print shell completion script for tab-completing repo names, workspace labels, and tag names.

```bash
# Add to your ~/.zshrc or ~/.bashrc (after shell init):
eval "$(gitstow shell completions)"

# Or for fish:
gitstow shell completions fish | source
```

Completes:
- Repo keys for `remove`, `open`, `repo freeze/unfreeze/tag/untag/info`
- Workspace labels for `-w/--workspace`
- Tag names for `-t/--tag`

### `gitstow ui`

Launch a local browser dashboard — a dark-themed web UI for daily repo management. Binds to `127.0.0.1`; auto-opens your default browser.

```bash
gitstow ui                     # http://127.0.0.1:7853
gitstow ui --port 8080         # custom port
gitstow ui --no-browser        # don't auto-open
gitstow ui --tailscale         # also serve on this machine's Tailscale address
```

**Features:**
- Live status across every tracked repo (clean / local changes / local edits / behind / ahead / diverged / frozen), auto-refreshes every 30s
- Click a repo name to open its repo page (Incoming and Local changes tabs, metadata, tags, freeze toggle, danger zone)
- **What a pull would bring:** a behind or diverged row's `↓ N incoming ›` chip opens the repo page's Incoming tab — the incoming commits, every changed file with `+/−` counts, and each file's line-by-line diff — and its Pull takes exactly those commits (see below)
- **Pull** on a row updates in place via HTMX; **Pull all** runs the parallel semaphore and shows a summary panel with failures
- Add / remove / freeze / tag repos without leaving the tab
- Workspace CRUD + Scan; Collection export/import under Settings
- Click **Shutdown** in the footer (or Ctrl+C) to stop

**Tailscale access:** with `--tailscale` (or `gitstow config set ui_tailscale true` to make it the default), the dashboard additionally binds this machine's own Tailscale address, so you can open it from any other device on your tailnet — useful for a VPS or headless box. `--no-tailscale` turns it off for a single run. The same setting has a checkbox on the dashboard's Settings page — saving it there applies from the next `gitstow ui` onward, and the page says so. If the Tailscale CLI isn't installed or the daemon isn't reachable, gitstow prints a warning and serves localhost-only instead of failing.

**Security:** binds `127.0.0.1` by default, and — only when Tailscale access is enabled — this machine's own Tailscale address as well. It never binds `0.0.0.0`, and there is no `--host` flag. The server runs git operations in arbitrary workspace directories, so it must not be LAN-reachable; with Tailscale access on, your trust boundary is your tailnet. Requests arriving with any other Host/Origin are rejected.

#### Reading the dashboard

Every interactive element has a hover tooltip. There's also a `?` button in the hero that opens a full legend dialog.

**Statuses** (left column + metrics strip):

| | Meaning | What to do |
|---|---|---|
| clean | Working tree matches HEAD; in sync with the last fetch | Nothing |
| dirty | Local changes — the label shows the composition (e.g. "2 modified · 1 staged · 3 untracked") | Commit/stash modified or staged files before pulling; untracked-only repos can still pull |
| local edits | Modified or staged files AND behind remote (the Remote Δ chip shows how many commits). Nothing has been checked for conflicts | Resolve the local work, then come back; the repo page names the files the update also changes |
| diverged | Local and remote both have commits the other lacks | Rebase or merge in a terminal; gitstow only fast-forwards |
| error | gitstow could not read the repo (git's message is in the tooltip). Counted under its own "error" chip in the metrics strip; "missing" is only for a folder that is not on disk | Repair in a terminal; nothing is shown as clean and nothing on disk was changed |
| behind | Remote has commits you don't | Click the orange Pull button |
| ahead | You have local commits not yet pushed | Consider `git push` |
| frozen | Intentionally skipped by "Pull all" | Unfreeze from the ⋯ menu |

**The Pull button color encodes priority**:

- **Orange** `↓ Pull 5` — repo is behind; takes exactly those 5 commits: it moves the branch to the commit the count came from and does **not** fetch first. If the branch or its upstream changed since the row was drawn, nothing is pulled and the row says so (it then shows the new count)
- **Ghost** (gray) — nothing known to be waiting (up to date, ahead, or untracked-only changes). It fetches, then fast-forwards if the remote has anything new. (Untracked-only local changes still pull fine — a bulk pull only skips modified/staged files.)
- **Disabled** — frozen, local edits while behind, diverged from remote, missing on disk or unreadable

If a single-row **Pull** or **Fetch** fails (git refuses to overwrite a local file, the network is down), git's message appears in red under the repo name and the *Last pull* stamp keeps its old value. Dismiss it with **×**.

**Remote Δ** (`↑ N`, `↓ N incoming ›`, `↑N ↓N ›`, or `—`) reflects your *last fetch*, not live remote state. If the counts look stale, use **Fetch all** (or a row's ⋯ → Fetch) to refresh them. `no upstream` means the branch does not follow a remote branch although the repo has a remote (the repo page shows the one command that sets it); `local` means the repo has no remote at all; `upstream gone` means the branch follows a remote branch that no longer exists here. Bulk pulls skip all three. `?` means the repo is missing or could not be read, so nothing is known about its upstream.

#### The repo page: Incoming and Local changes

Click a repo name, or a row's `↓ N incoming ›` chip, to open its page. Two tabs sit under the header:

- **Incoming** — what a pull would bring, as of the last fetch: which branch is compared with which upstream and at which two commits, the last fetch gitstow recorded, the incoming commits (newest first; each with author, date and that commit's own files and lines — those rows need not add up to the totals, since two commits can change the same file), and every changed file with its kind (M/A/D/R) and `+/−` counts. Click a file for its line-by-line diff. Totals always count the whole update; a huge one lists the newest 50 commits and the 200 largest files, a file's diff stops after 500 lines, and each list says so and names the terminal command that shows everything (`gitstow diff <repo> --incoming`).
  - **Pull** takes exactly the commits listed: it moves the branch to the newest one shown and nothing newer, and does not fetch first. Right before it, under a per-repo lock, it re-checks the folder, the branch and its upstream (their full ref names, byte for byte), both commit ids, a fresh status read, the frozen flag and that no file is edited or staged; if anything differs, nothing is pulled and the page offers "Show the N now waiting". Success is shown only after git finished and the new commit was read back ("main moved from 4c81d0e to 9f3e2ab"). There is no undo.
  - When gitstow's last fetch is older than 7 days (or there is none on record), **Fetch first** is the main button and "Pull these N without fetching" the second. A failed fetch quotes git and keeps showing the list as of the previous fetch.
  - Pull is off, and the panel says why, when files are edited or staged (it names the ones the update also changes), when the branches have diverged (it names files changed on both sides — conflicts are not checked), or when the repo is frozen (**Unfreeze** is offered; it does not pull). A repo that cannot be read shows "Could not check" with git's message and no counts.
- **Local changes** — staged, unstaged and untracked files in this folder; click one for its colored line diff.

**Auto-refresh every 30s** re-reads local state only:

- Your `~/.gitstow/repos.yaml` registry (catches `gitstow add` from another terminal)
- `git status` on each tracked repo (catches new local commits, branch switches, dirty files)

It does **NOT** run `git fetch`. Ahead/behind counts won't update without a pull or manual fetch. It pauses while a row on screen shows a Pull or Fetch error, so the message stays while that row is shown. Dismiss it with ×; if you filter that row out instead, refresh resumes and the next refresh clears it.

### `gitstow update`

Upgrade gitstow itself from PyPI. Detects your install method (pipx, pip, or editable) and runs the matching upgrade.

```bash
gitstow update                 # install the latest from PyPI
gitstow update --check         # just query PyPI; don't install
gitstow update -c              # short form of --check
```

For editable installs (`pip install -e .`), runs nothing and points you at `git pull` instead. For pipx installs, runs `pipx upgrade gitstow`. For regular pip installs, runs `python -m pip install --upgrade gitstow`.

---

## Configuration Commands

### `gitstow config show`

Display current settings, file paths, and repo count.

```bash
gitstow config show
gitstow config show --json
```

### `gitstow config set`

Change a setting.

```bash
gitstow config set default_host gitlab.com
gitstow config set prefer_ssh true
gitstow config set parallel_limit 8
gitstow config set clone_timeout 900
gitstow config set ui_tailscale true
```

Valid keys: `default_host`, `prefer_ssh`, `parallel_limit`, `clone_timeout` (clone timeout in seconds), `ui_tailscale` (serve `gitstow ui` on this machine's Tailscale address as well).

> **Note:** The old `root_path` setting has been replaced by workspaces. Use `gitstow workspace add` to manage where repos are stored.

### `gitstow config migrate-root`

Move one workspace's repos to a new directory and update that workspace's `path` in config.

```bash
gitstow config migrate-root ~/new-location            # Moves the first configured workspace
gitstow -w active config migrate-root ~/new-location  # Moves a specific workspace
gitstow config migrate-root ~/new-location --copy      # Keep originals
gitstow config migrate-root ~/new-location --yes       # Skip confirmation
```

> **Note:** `migrate-root` has no local `--workspace` flag — target a non-default workspace with the global `-w` flag *before* `config`, not after `migrate-root`.

### `gitstow config path`

Print the config file path.

---

## Utility Commands

### `gitstow onboard`

Interactive first-run setup wizard. Guides you through root path, default host, SSH preference, and scans for existing repos.

```bash
gitstow onboard
gitstow onboard --force    # Re-run even if already configured
```

### `gitstow doctor`

Health check — verifies git installation, config files, and repo integrity (tracked vs on-disk).

```bash
gitstow doctor
gitstow doctor --json
```

It also lists any repos.yaml entry that was set aside (`set_aside_records` in `--json`): a key that is not a safe folder path inside its workspace, such as `../outside` or an absolute path, which versions before 0.9.0 accepted. Every command ignores such an entry and gitstow keeps it unchanged in repos.yaml; delete it there, then add the repo again (`gitstow add <url>`, or `gitstow workspace scan <label>` for a folder already on disk). While any entry is set aside, `gitstow list` and `gitstow status` print a one-line reminder on stderr (not with `--json` or `--quiet`).

### `gitstow install-skill`

Install the Claude Code skill for AI-assisted repo management.

```bash
gitstow install-skill
```

---

## Workspace Commands

All under the `gitstow workspace` subcommand. See [Concepts — Workspaces](concepts.md#workspaces) for the mental model.

### `gitstow workspace list`

Show all configured workspaces with their path, layout, auto-tags, and repo count.

```bash
gitstow workspace list
gitstow workspace list --quiet   # One label per line (for scripting/completions)
```

With no workspaces configured — a fresh install, or after removing your last one —
the command exits **0** (this is a valid state, not an error) and prints:

```
No workspaces configured. Add one with:
  gitstow workspace add <path> --label <name>
or run:
  gitstow onboard
```

`--quiet` prints nothing in that case, so scripts see an empty list.

### `gitstow workspace add`

Add a new workspace.

```bash
gitstow workspace add <path> --label <label> [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--label` | `-l` | **Required.** Unique label for this workspace. |
| `--layout` | | Directory layout: `structured` (default, owner/repo) or `flat`. |
| `--auto-tag` | `-t` | Tags auto-applied to discovered repos. Repeatable. |
| `--scan/--no-scan` | | Scan for existing repos after adding (default: scan). |

**Examples:**

```bash
# Add a structured workspace for open-source repos
gitstow workspace add ~/oss --label oss

# Add a flat workspace for your own projects, auto-tagged
gitstow workspace add ~/projects --label active --layout flat --auto-tag mine

# Add without scanning for existing repos
gitstow workspace add ~/archive --label archive --no-scan
```

### `gitstow workspace remove`

Remove a workspace from the configuration. Does not delete files on disk.

```bash
gitstow workspace remove <label> [flags]
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--keep-repos/--untrack-repos` | Keep tracked repos in the store (default) or untrack them. |

**Removing your last workspace is allowed.** Zero workspaces is a valid state —
gitstow does not invent one to fill the gap. The command reports the removal and
then tells you how to get back:

```
  ✓ Workspace oss removed
  No workspaces configured. Add one with:
    gitstow workspace add <path> --label <name>
  or run:
    gitstow onboard
```

Until you add one, commands that sweep workspaces (`add`, `pull`, `fetch`, `list`, `status`, `exec`, `search`, `stats`, `migrate`,
`collection import`, `config migrate-root` and `shell pick`)
exit **1** with that same message on stderr. Run with `--json`, the same failure
is emitted as `{"success": false, "error": "..."}` on stdout instead (still exit
1), so a parsed run never sees an empty stdout — `collection import`,
`config migrate-root` and `shell pick` have no `--json` option and always use the
prose form. `workspace list`, `config show` and
`doctor` still run, and each points at the same fix. Commands that name a single
repo (`diff`, `open`, `remove`, `repo …`) report that the repo isn't tracked, which
is what actually happened.

### `gitstow workspace scan`

Scan a workspace directory to discover and register any repos that aren't yet tracked.

```bash
gitstow workspace scan <label>
```

This respects the workspace's layout mode — in `structured` workspaces it looks for `owner/repo/.git`, in `flat` workspaces it looks for `repo/.git`. A repo folder whose name gitstow cannot track (only dots and spaces, or a `\`, `:` or control character, all legal on Linux) is skipped and listed; rename it to track it.

---

## MCP Server (Optional)

> **Most users don't need this.** If you use Claude Code, the bundled skill (`gitstow install-skill`) gives full access to all commands with zero context overhead. The MCP server is for AI tools that don't support Claude Code skills — Claude Desktop, Cursor, Windsurf, etc.
>
> **Context cost warning:** MCP tools are always loaded into your AI tool's context window, costing tokens even when you're not managing repos. Only set this up if you have a dedicated repo-management workflow.

### Setup

```bash
pip install gitstow[mcp]    # Install the optional MCP dependency
```

Then add to your AI tool's config:

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp"
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "gitstow": {
      "command": "gitstow-mcp"
    }
  }
}
```

Or use `gitstow setup-ai` to auto-detect and configure.

### MCP Tools (14)

| Tool | Description |
|------|-------------|
| `list_repos` | List repos with tag/owner/query filters |
| `add_repo` | Clone a repo (shorthand or full URL) |
| `pull_repos` | Bulk pull with tag/exclude/frozen filters |
| `fetch_repos` | Bulk fetch (updates ahead/behind, no merge; includes frozen) |
| `repo_status` | Git status dashboard across repos |
| `repo_info` | Detailed single repo info |
| `freeze_repo` | Freeze a repo (skip during pull) |
| `unfreeze_repo` | Unfreeze a repo |
| `tag_repo` | Add tags to a repo |
| `untag_repo` | Remove tags from a repo |
| `remove_repo` | Remove from tracking (optionally delete) |
| `search_repos` | Grep across repos with pattern and glob |
| `collection_stats` | Disk usage, owner breakdown, tag counts |
| `list_workspaces` | List configured workspaces with repo counts |

### MCP Resources (3)

| Resource URI | Description |
|-------------|-------------|
| `gitstow://config` | Current gitstow configuration |
| `gitstow://tags` | All tags with repo counts |
| `gitstow://owners` | All owners with repo counts |

---

## JSON Output

Every main command supports `--json` (`-j`) for machine-readable output. Combined with `--quiet` (`-q`), this suppresses human-readable progress and only outputs structured JSON.

This is designed for scripting and AI tool integration:

```bash
# Parse with jq
gitstow list --json | jq '.[].key'

# Use in scripts
REPOS=$(gitstow list --json --quiet)

# AI tools use --json --quiet for structured parsing
gitstow pull --json --quiet
```
