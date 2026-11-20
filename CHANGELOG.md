# Changelog

All notable changes to gitstow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.9.0] - 2026-11-19

### Fixed

- **`gitstow add` can no longer delete a local repo** — if the target folder already held a git repo with no `origin` remote (one you `git init`ed yourself), `add` treated it as a fresh clone; the clone failed on the non-empty folder, and with `--retry 1` or more the retry cleanup deleted the folder, your commits with it, before cloning again. That folder is now never cloned over: `add` stops with an error that gives the exact command to add the remote (`git -C <folder> remote add origin <url>`), after which the same `gitstow add` registers it as already on disk.
- **A URL, a collection or an old repos.yaml entry can no longer make gitstow act on a folder outside its workspace** — a URL with a `..` path segment (like `https://github.com/../Documents`) was accepted, so `gitstow collection import` or the dashboard's import could register the folder beside the workspace as a tracked repo, and the MCP `remove_repo` tool, which unlike `gitstow remove` and the dashboard did not check where a folder was, could then delete it. URLs whose owner or repo would not be a safe folder name (a `.`, `..` or empty path segment, a segment of only dots and spaces, or a `\`, `:` or control character, so `acme//widget`, `..\Documents` and `D:` too; a control character inside the URL is refused, not silently dropped; whitespace around the whole URL is trimmed first) are now refused as invalid. Entries already in repos.yaml with such a key (accepted by older versions, or written by hand) reached further: `gitstow pull` and `gitstow exec` could act on a repo outside the workspace, `gitstow config migrate-root` could move one, and removing an entry like `./widget` could delete the workspace folder. Such an entry is now set aside when repos.yaml is read: every command ignores it, it is kept unchanged in the file, and `gitstow doctor` lists it with what to do (`gitstow list` and `gitstow status` print a one-line reminder). Every delete or move (`gitstow remove --delete`, the repo page's "Remove + delete folder", `remove_repo`, `gitstow repo move`, `gitstow config migrate-root`, and `gitstow migrate`'s destination) refuses a folder that resolves outside its workspace, also through a symlink, and `gitstow workspace scan` skips (and lists) a repo folder whose name cannot be tracked safely.
- **Clones never replace or delete a folder with anything in it** — every clone, from `gitstow add`, the MCP `add_repo` tool, the dashboard's Add page and both collection imports (`gitstow collection import` and the dashboard's import), now runs in a hidden staging folder beside the target and is moved into place in one step only when it is complete. If something appeared at the target in the meantime (another gitstow command, the dashboard, or your own `git clone`), that repo fails with "already exists … left untouched" and whatever is there is kept (on Linux and macOS an empty folder at the target is simply replaced, as there is nothing in it to lose; on Windows that clone stops with "already exists"); a retry or a failed clone only ever cleans up its own staging folder, so a failed clone leaves no repo folder behind. Before, a failing `gitstow add --retry` cleared its target before each retry, which could delete a folder another add had filled in the meantime, local edits included.
- **Pulls never overwrite your local edits or ignored files, whatever your git settings for stashing, rebasing or merge options** — a pull never auto-stashes and re-applies your edits (with `merge.autoStash`, `pull.rebase` plus `rebase.autoStash`, or `--autostash` in a branch's `branch.<name>.mergeOptions`, git did that and could leave conflict markers in a file while reporting success), never lets a branch's merge options or a `pull.twohead` strategy turn it into a squash or a merge commit (with `pull.twohead=subtree` git could even overwrite an ignored local file and report success), and never overwrites an ignored local file (like a `.env`) when upstream starts tracking that path. In every case the pull is refused and the files are left as they were. gitstow does not stop programs you have told git to run during a pull, such as hooks, clean/smudge filters (Git LFS) or helpers chosen through your environment; those run as you configured them. A branch whose name contains `=` and that has merge options set is refused, since gitstow cannot switch them off for it; pull it with git. A branch whose upstream was deleted on the remote now gets a plain message ("the upstream branch no longer exists on the remote") instead of git's raw error, and so does a detached HEAD ("not on a branch") where a single repo is pulled directly (the dashboard's row Pull, `gitstow add --update`); bulk pulls skip a detached HEAD as a local-only repo, as before. This applies to `gitstow pull`, `gitstow add --update`, the MCP `pull_repos` tool and the dashboard's Pull buttons.
- **Azure DevOps URLs work** — `dev.azure.com/org/project/_git/repo` typed without `https://` was read as GitHub shorthand, and even with the scheme the clone URL was built in a form Azure does not serve. Clone URLs now use Azure's documented forms (`https://dev.azure.com/org/project/_git/repo`, or `git@ssh.dev.azure.com:v3/org/project/repo` with SSH preferred; legacy `org.visualstudio.com` URLs keep HTTPS, and the legacy SSH form `org@vs-ssh.visualstudio.com:v3/org/project/repo` clones by the `ssh.dev.azure.com` form), and the SSH and HTTPS forms are recognised as the same repo. Any host with several dots (e.g. `git.example.co.uk/group/repo`) is now recognised without a scheme too. Repos added by an HTTPS Azure URL keep their folders. Repos first added by the SSH `v3/` URL keep their `v3/...` folder and stay tracked, and adding them again by either URL form now finds the existing record instead of cloning a second copy.
- **Adding a repo that is already tracked never clones a second copy or resets it** — `gitstow add`, the MCP `add_repo` tool and the dashboard's Add page find the existing record, also when it is tracked under another URL form, and report it under its stored name (`gitstow add --update` pulls that folder); both collection imports skip it instead of cloning a second copy. The dashboard's Add page used to re-register an already-tracked repo, resetting its tags and frozen state. A collection that lists the same repo twice (say its HTTPS and SSH URLs) now imports it once and keeps the first entry's tags and frozen state, and an entry that would land in a folder already used by a different repo (another host, or another owner in a flat workspace) is not imported instead of being registered over it: `gitstow collection import` names the conflict, and the dashboard's import banner lists it with the reason (it used to count it as failed with no reason). An exported YAML or JSON file is judged by its URLs like a URL list; it used to match entries by their stored key, which skipped such a conflict as already tracked. `gitstow collection import --dry-run` lists each entry once with what it would do. If the tracked repo's folder has been deleted, `gitstow add`, `add_repo` and the Add page now say so and name the way out (remove it from gitstow: `gitstow remove <name>`, `remove_repo`, or Remove in the row's ⋯ menu; then add it again to re-clone) instead of answering "already tracked".
- **`gitstow pull` and `gitstow fetch` never act on a repo around a tracked folder** — if a tracked repo's `.git` was broken or empty and its workspace sits inside another git repo (say a workspace under a folder that is itself a repo), git looked in the folders above, so `gitstow pull` could fast-forward that outer repo and write its files. gitstow now tells git to stop at the repo's own folder, and `gitstow pull` and `gitstow fetch` first check that git sees the folder as the top of its own repository, so such a repo is reported as an error and the outer repo is left untouched.
- **gitstow run from inside a git hook no longer touches the wrong repo** — a git hook runs with `GIT_DIR`, `GIT_WORK_TREE` and similar variables set for its own repo, and git obeys them over the folder it is run in, so a `gitstow pull` started from a hook could fast-forward the hook's repo and write its files into another repo's folder. gitstow now drops those variables from every git command it runs, so each command acts on the repo it names. Git settings passed through the environment (`GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG`) are ignored by gitstow's git commands too; put them in a git config file instead.
- **The dashboard shows why a single-row Pull or Fetch failed** — when git refused a Pull (to protect a local file) or a Fetch failed, the row came back as if nothing had happened. Git's message now appears in the row, under the repo name, and the last-pulled time keeps its old value; the 30-second auto-refresh waits while the message is on screen; dismiss it with ×, or filter its row out and the next refresh clears it.
- **`gitstow search` handles patterns that start with `-`** — `gitstow search -- -foo` searches for `-foo` instead of passing it to ripgrep or git grep as an option, and `--glob` now also filters when ripgrep is not installed (the `git grep` fallback used to ignore it). The CLI and the MCP `search_repos` tool now share one search runner with the same environment as every other git call.
- **Frozen repos that split from upstream show a clean skip** — `gitstow pull --include-frozen` on a frozen repo that has diverged from its remote (or has no upstream) now reports "skipped — Diverged from remote" (or "Local-only repo") like any other repo, instead of attempting a pull that could only fail and showing it as an error.
- **The "untracked repos" hint now appears when nothing is tracked yet** — `gitstow list` and `gitstow status` returned early on an empty result and never suggested `gitstow workspace scan`, so a workspace whose repos were all untracked never got the nudge. `gitstow list --json` with no results no longer prints a message ahead of the JSON, and `gitstow list --json --quiet` (the form the bundled Claude skill uses) now prints JSON; it used to print plain repo names whenever anything was tracked.
- **A blank `$VISUAL` or `$EDITOR` no longer crashes `gitstow open`** — a whitespace-only value counts as unset, so the next editor setting or the usual fallbacks are used.
- **Dashboard row numbers stay put after a single-row Pull, Fetch, freeze or tag** — the refreshed row used to be numbered counting records from workspaces that are no longer configured, so it could jump to a different number than the full page showed.

### Added

- **`gitstow pull owner/repo --force`** — also pulls the repos you name when they have modified or staged files, for when you have asked for a pull and mean it (the dashboard's single-row Pull already works this way). `--force` applies only to repos you name: `gitstow pull --force` with no names (filters such as `--tag` or `-w` do not count) stops with an error before pulling anything. Git's fast-forward-only pull still refuses any pull that would overwrite a modified file, and that refusal is reported as an error with your file untouched. Diverged and local-only repos are still skipped, and frozen repos still need `--include-frozen`.
- **MCP `pull_repos` rows for frozen repos now name their workspace** — `skipped_frozen` rows carry a `workspace` field, so two frozen repos with the same name in different workspaces can be told apart (the CLI's frozen rows already did this).

## [0.8.0] - 2026-09-13

### Changed

- License changed from MIT to GNU GPL v3.0 or later (`GPL-3.0-or-later`) for
  versions after 0.7.2. Previously released versions retain their MIT license.
  Commercial use remains permitted; redistribution of covered versions must
  satisfy the GPL, including corresponding-source requirements. Repositories
  managed by gitstow are unaffected. Bundled htmx and fonts retain their own
  licenses, now included in both source and wheel distributions.
- Standardized author attribution as `rishmadaan`, including a Git mailmap for
  historical author names without rewriting commits.
- The version is now `0.8.0`, distinct from the published
  MIT-licensed 0.7.2. The installed Claude skill includes its GPL notice and
  full license, including on automatic upgrades; an incomplete bundled skill
  leaves the previous installation and version marker intact. The website
  also ships its GPL terms and both font licenses.

## [0.7.2] - 2026-09-03

### Fixed

- **A fresh install no longer invents an `oss` workspace at `~/opensource`** — with nothing configured, gitstow used to conjure a workspace that lived only in memory. It could not be removed (`workspace remove oss` refused: "Cannot remove the only workspace"), it could not be replaced (`workspace add ... --label oss` refused: "already exists"), removing it from the dashboard silently did nothing because the page re-conjured it on the next render, and `gitstow add owner/repo` quietly cloned into `~/opensource` under a workspace label that appeared nowhere in your config. Zero workspaces is now a real, visible state: `get_workspaces()` returns exactly what you configured, every surface shows the same empty state with the same one-line fix (`gitstow workspace add <path> --label <name>`, or `gitstow onboard`), and you may remove your last workspace — the CLI tells you what to do next, and the dashboard's library, add-repo, and workspaces pages each show an empty-state card instead of a dead form or a phantom row. The legacy `root_path` → `oss` migration is unchanged and still runs, but only in `load_config()` where it is written to disk, so what you see is what your config holds. `~/opensource` survives solely as the path `gitstow onboard` suggests for your first workspace.
- **The empty state now reaches every surface that needed it** — `gitstow onboard` works on a config with zero workspaces (it used to refuse because the *file* existed, and `--force` silently reset `parallel_limit`, `clone_timeout` and `ui_tailscale`); `gitstow pull owner/repo` and `gitstow fetch owner/repo` stop with the hint instead of "No repos to pull"; the MCP tools `list_repos`, `pull_repos`, `fetch_repos`, `search_repos`, `collection_stats` and `repo_status` return the hint instead of a success-shaped empty result; the dashboard's Settings page shows the empty-state card in place of the collection-import form, and a POST to it answers with that page rather than raw JSON; the library page no longer renders its "Pull all" / "Fetch all" bar and repo filters with no workspace to act on, and those endpoints answer with the empty-state card instead of a zero-item summary or a wall of "workspace not found"; `gitstow doctor` prints workspace labels in its orphan reports again (Rich was parsing `[label]` as a style tag and swallowing it) and skips the SSH probe when nothing is configured; and the terminal hint prints each command on its own line so it never wraps mid-command.
- **`--json` runs now get the empty state as JSON, not silence** — with zero workspaces configured, `add`, `pull`, `fetch`, `list`, `status`, `exec`, `search`, `stats` and `migrate` exited 1 with an empty stdout and the hint as prose on stderr, so anything parsing stdout (scripts, the Claude Code skill) saw a crash rather than "nothing configured yet". With `--json` they now write `{"success": false, "error": "No workspaces configured…"}` to stdout, in the same shape as every other JSON failure, and still exit 1. Without `--json` nothing changes.
- **One orphaned repo no longer aborts a bulk pull or fetch** — `gitstow pull good orphan`, where `orphan` is tracked under a workspace that has since left your config, exited 1 while building the target list and never touched `good`. It now warns and skips that one repo — the same contract as the existing "not tracked. Skipping." path — so the valid repos named alongside it still run. In `--json` mode the warning goes to stderr and stdout stays the result payload. Naming only the orphan still ends in "No repos to pull."

### Changed

- **Upgrading from 0.7.1 or earlier adopts your implicit `oss` workspace** — installs where `gitstow add` had cloned into `~/opensource` without ever writing a config now get that workspace written to `config.yaml` once, silently, on the next command, so those repos stay visible (if `~/opensource` no longer exists, nothing is invented and `gitstow doctor` reports the leftover records). The adoption only ever looks at configs that predate this version — every config gitstow writes from now on carries a `config_version` marker, so once you remove `oss` yourself it stays removed.

## [0.7.1] - 2026-08-11

### Added

- **Tailscale toggle on the dashboard Settings page** — `ui_tailscale` was the last config field you could only set from the CLI. Because it applies when the server binds its sockets, the page never pretends the change is live: it compares what you saved against what the running server actually bound and tells you to restart `gitstow ui`. On a machine without the Tailscale CLI the row is disabled with the reason shown, and saving there leaves an existing `ui_tailscale: true` (e.g. a config synced from another machine) untouched rather than silently clearing it.

### Changed

- **`gitstow ui --tailscale` now prints the tailnet IP** instead of the MagicDNS name — the IP works from any peer, while the name depends on the other device's DNS. The MagicDNS name (and short name) still work if you type them.

## [0.7.0] - 2026-08-11

### Added

- **Tailscale access for the dashboard** — `gitstow ui --tailscale` (or `gitstow config set ui_tailscale true`, also offered during `gitstow onboard` when Tailscale is installed) serves the dashboard on the machine's Tailscale address alongside localhost. VPS-friendly: reachable from any device on your tailnet, never bound to `0.0.0.0`, and the anti-rebinding Host guard only admits the machine's own tailnet IP and MagicDNS name. Falls back to localhost-only with a warning when Tailscale isn't running.

### Changed

- **MCP server works with both mcp 1.x and 2.x** — the server imports FastMCP from whichever the environment has (2.0 renamed it to `MCPServer`); the temporary `mcp<2` pin is gone. Internal: adopted ruff 0.16's stricter default lint rules (141 fixes; deliberate patterns get configured ignores with reasons) and unpinned ruff.

## [0.6.1] - 2026-08-09

### Changed

- **The browser dashboard is now part of the default install** — `pip install gitstow` / `pipx install gitstow` includes `gitstow ui`; the `[ui]` extra is gone (kept as an empty alias so old install commands still work). Site, README, docs, and the Claude skill updated to the bracket-free command, plus a "no pipx? here's how to install it" note for Linux users on the landing page and README.
- **OS-aware install tabs on gitstow.com** — the quick-start section now shows macOS / Linux / Windows tabs with the right command for each (brew/apt + pipx, plain pip on Windows), defaulting to the visitor's own OS.

### Added

- **Marketing site at [gitstow.com](https://gitstow.com)** — static landing page in `site/` (plain HTML/CSS, no build step), auto-deployed via GitHub Pages (`.github/workflows/pages.yml`) on any push to `main` touching `site/**`. Custom domain served through Cloudflare DNS.

## [0.6.0] - 2026-07-19

### Added

- **`gitstow diff <repo> [--staged]` command** — view-only colored git diff passthrough for a single repo's uncommitted changes; a clean repo prints "no local changes". The web dashboard gains a matching Changes section on the repo detail page (staged/unstaged/untracked groups, click a file to expand its line diff, with binary/truncation guardrails), and the dashboard's dirty badge links straight to it (`#changes`).

### Fixed

- **Orphaned repo records are now self-serve clearable.** When a workspace is removed but its repo records stay in the store (the default `--keep-repos` behavior), those records previously caused a crash (`gitstow -w <label> remove` raised a raw traceback) and could only be cleared by hand-editing `repos.yaml`. Now: `gitstow workspace remove <label>` on a removed label clears its orphaned records; every repo command exits with a clean error pointing at that command instead of crashing; `doctor`'s hint names the command instead of suggesting a YAML edit; and the web dashboard's Workspaces page shows an "Orphaned records" section with a Clear button, warns when removing a workspace leaves records behind, and matches the CLI's clearing behavior. Note: freeze/tag/untag on orphaned records (previously silently succeeded on invisible repos) now exit with the same guidance.
- **Web workspace labels are validated** with the same charset rule as the CLI — previously a label like `bad/label` could be created from the dashboard, producing scan/remove buttons that 404.

## [0.5.1] - 2026-07-16

### Fixed

- **Workspace move picker reads as an action, not a state.** The dropdown opened showing the first other workspace as if the repo were already there; it now opens on a disabled "Move to…" placeholder and refuses to submit empty.
- **The Workspace section states where the repo currently lives** ("Currently in `<workspace>`" with the workspace badge) instead of relying on the page header alone.

## [0.5.0] - 2026-07-16

### Added

- **Move repos between workspaces.** From the dashboard's repo detail page (new Workspace section: pick a target, confirm, done) or the new `gitstow repo move <repo> <workspace>` command. The folder physically relocates on disk following the target workspace's layout (structured `owner/repo` vs flat), the catalog updates in the same locked operation, existing tags are kept and the target's auto-tags merge in, and repos that own linked git worktrees get `git worktree repair` run automatically after the move.
- **Move safety net.** Collisions are refused before anything is touched (catalog, disk, even dangling symlinks); sources that can't move safely are refused with actionable errors (symlinked repos, linked-worktree gitfiles, relative `.git` symlinks, non-repo directories squatting on a tracked path); catalog keys can never escape their workspace on disk; and if anything fails mid-move, the rollback restores the folder without ever deleting data it can't prove is its own.

## [0.4.0] - 2026-07-12

### Added

- **Dashboard filters that actually filter.** The search box, workspace dropdown, and Hide-frozen toggle now work — instant client-side filtering that survives auto-refresh and pull updates.
- **Settings save.** The Settings page persists changes for real (and gains Parallel limit + Clone timeout fields).
- **`local` badge.** Repos without an upstream remote show a `local` Remote Δ badge; bulk pulls skip them with a clear reason instead of failing every run.
- **Responsive dashboard.** Works down to half-monitor width — columns collapse by priority, the table scrolls within its own container, and row menus flip upward when space runs out.
- **Last-fetched visibility.** The repo detail page shows when each repo was last pulled AND last fetched (hover for exact times); Remote Δ tooltips state which fetch the counts reflect.

### Changed

- **Fully offline dashboard.** htmx and both fonts are now bundled — no CDN, no network needed for the UI itself.
- **Styled confirmations.** Native browser confirm/alert dialogs replaced with an in-app dialog (also makes the dashboard automatable).
- **Web collection import honors recorded workspaces**, sharing one implementation with the CLI.
- **Honest metrics chips.** Diverged and missing repos get their own hero chips instead of hiding inside 'conflict'; the pull summary reads 'N attempted' with exclusions stated.
- **Cleaner micro-interactions.** Styled file picker, a LIVE dot that actually goes red when the server is unreachable, keyboard-visible focus rings, Escape closes menus, add-repo shows a Cloning… state.

### Fixed

- **Pull all / Fetch all spinners stop when the operation completes** (htmx indicator/disable double-count).
- **Repo detail timestamps** no longer render raw machine format.

## [0.3.0] - 2026-07-12

### Added

- **Composition-aware status everywhere.** `gitstow status` now shows a Local Changes column (modified/staged/untracked counts) and a separate Remote column (in-sync/ahead/behind/diverged). The web dashboard rows, repo drawer, and help legend all use the same shared classifier — a repo with only staged or untracked files can no longer display as "clean". JSON output gains additive `local` and `remote` keys (all existing keys preserved).
- **`gitstow fetch_repos` MCP tool** and a `last_fetched` timestamp in all JSON outputs — MCP clients can now see fetch state, matching the CLI and web dashboard.
- **Untracked-repo hints on `list` and `status`.** Repos found on disk but not in the registry are now surfaced with a hint pointing at `workspace scan`, instead of being silently invisible.
- **`doctor` detects orphaned workspaces**, and `workspace remove` now warns when repos would be left pointing at a removed workspace.
- **`clone_timeout` setting** (`gitstow config set clone_timeout <seconds>`) — clone operations on very large repos no longer fail on a hardcoded 300s limit.
- **macOS added to the CI test matrix**, matching the packaging classifiers.
- **CHANGELOG gate in the release script** — `scripts/release.sh` now refuses to cut a release without a corresponding changelog entry.

### Changed

- **`add` clones multiple repos in parallel** and now detects remote mismatches and duplicate URLs before cloning, instead of silently registering whatever is already on disk.
- **`search` now runs across repos in parallel**, matching `exec`.
- **Pull, fetch, and the MCP server share one operations layer.** The MCP server now follows the same bulk-pull rule as the CLI and web dashboard (modified/staged skip, diverged skip, untracked-only pulls).
- **`open` prefers `$VISUAL`/`$EDITOR`** over guessing an editor, and runs terminal editors in the foreground so they actually get a usable TTY.
- **Disk sizing (`stats`, `repo info`) now shells out to `du`** instead of walking every file in Python — much faster on large repos.
- **`collection import` honors each entry's recorded workspace** instead of dumping everything into one workspace, and now fails loudly (with a printed error) on newer file versions instead of exiting silently.
- **The web dashboard's dependencies (FastAPI, uvicorn, Jinja2) moved to the optional `[ui]` extra.** Existing installs keep working; fresh CLI-only installs are ~15 packages lighter. Install with `pip install "gitstow[ui]"` to keep the dashboard.
- **Bulk pull rule unified and refined (behavior change).** CLI `pull` and the dashboard's Pull-all now follow one rule: repos with modified or staged files are skipped (with the composition shown), diverged repos are skipped (fast-forward pull cannot succeed), and repos with only untracked files ARE pulled — previously the CLI skipped them forever while the web pulled everything.

### Fixed

- **`search`, `exec`, and `status` `--json` now emit pure JSON on an empty filter match** — previously they printed a "no repos match" banner and no payload at all, even with `--json` set.

### Removed

- **The Textual TUI (`gitstow tui`).** The web dashboard (`gitstow ui`) replaced it as the visual surface in v0.2.0; the TUI had been broken and parked since. Git history preserves it.

## [0.2.8] - 2026-07-12

### Fixed

- **Pasted GitHub browse URLs now resolve correctly.** URLs like `/tree/...`, `/blob/...`, `/pull/...` now resolve to the repo root instead of being misparsed.
- **`config migrate-root`** now actually updates workspace paths, and honors the global `-w/--workspace` flag.
- **`repo freeze/unfreeze/tag/untag`** now honor `-w/--workspace` and report cross-workspace ambiguity clearly instead of guessing.
- **`pull --json` and `fetch --json`** always emit pure JSON on stdout — no more banners or progress lines interleaved with the payload.
- **Pull summary** keeps per-workspace identity for same-named frozen repos in different workspaces.
- **Bulk git operations** no longer hang on credential prompts and are now locale-independent.
- **`remove --delete`** refuses to delete paths that resolve outside the workspace root.
- **Workspace labels** are now validated (lowercase alphanumeric, dash, underscore only).

### Added

- **Atomic, cross-process-locked writes** to `repos.yaml`, safe for concurrent CLI and web UI use.
- **Test gate before PyPI publish** — releases now require a passing test suite.

### Security

- **Web dashboard CSRF protection.** POST routes now reject cross-origin and DNS-rebinding requests on the localhost UI.

## [0.2.7] - 2026-05-28

### Fixed

- **`gitstow onboard`** now uses Beaupy's `default_is_yes` confirmation parameter, fixing first-run setup crashes on confirmation prompts.
- **AI integration setup** now renders MCP warning text with balanced Rich markup during onboarding and `gitstow setup-ai`.

## [0.2.5] - 2026-04-19

### Added

- **`gitstow fetch`** — new CLI command to fetch all remotes without merging. Updates ahead/behind counts. Includes frozen repos (fetch is non-destructive). Supports `--tag`, `--exclude-tag`, `--owner`, `--retry`, `--json`, `--quiet` flags.
- **Web dashboard — Fetch all button.** New `btn-outline` button in the action bar between Refresh and Add repo. Fetches all remotes in parallel (including frozen repos), then shows a summary panel. No confirmation needed — fetch is non-destructive.
- **Web dashboard — single-repo Fetch.** New "Fetch" action in each row's three-dot menu. Updates that row's ahead/behind counts in place via HTMX.
- **`last_fetched` timestamp** tracked per repo in `repos.yaml`. Stamped on successful fetch operations. Backward-compatible — existing repos default to empty.
- Updated help dialog and tooltips to reference Fetch all instead of "fetch manually."

## [0.2.4] - 2026-04-19

### Changed

- **Renamed `gitstow serve` → `gitstow ui`** — more intuitive command name for the browser dashboard. `gitstow serve` remains as a hidden alias for backwards compatibility.
- **Removed `WEB_GUI_PLAN.md`** — shipped planning artifact retired; git history preserves it.

## [0.2.3] - 2026-04-18

### Fixed

- **Web dashboard — "Open folder" now actually opens the folder.** The row-menu and drawer buttons were previously no-ops; they now POST to a new `/repos/{ws}/{key}/open-folder` route which shells out to the platform opener (`open` on macOS, `xdg-open` on Linux, `explorer` on Windows). The server binds to `127.0.0.1` only, so no exposure beyond localhost.

### Added

- **Web dashboard — Copy URL / Copy path / Copy local path actions.** The `⋯` row menu and the repo drawer each expose three clipboard actions: Copy URL (remote clone URL), Copy path (repo key like `owner/repo`), Copy local path (absolute filesystem path). Implemented client-side with `navigator.clipboard` and a textarea fallback for non-secure contexts.
- **Toast notifications.** A new bottom-center toast surfaces success/failure for Open folder and the copy actions (reads an optional `data-toast` attr on any `hx-post` button and an error message from the JSON response body on failure).

## [0.2.2] - 2026-04-18

### Added

- **Dashboard tooltips everywhere.** Every interactive element now has a `title=` explaining what it does or what its state means — status pills (with the recommended next step), delta badges, workspace chips, tags, last-pull, Pull button (text varies by repo state), the `⋯` menu and each of its items, action-bar buttons, column headers, the nav links, the live dot, the Shutdown button, the lock icon. Hover over anything.
- **Pull button now shows the commit count when behind.** `↓ Pull 5` instead of just `Pull` — the action's payload is visible at a glance without hover.
- **"Reading the dashboard" help dialog.** New `?` button in the hero opens a native `<dialog>` modal with: statuses + what each means + what to do, the Pull button color convention, what Remote Δ reflects (last fetch, not live remote), what auto-refresh actually does (local state only; does NOT run `git fetch`), and a reference for every action. Click the backdrop or press Esc to close.
- Matching `docs/user/commands.md` "Reading the dashboard" subsection under `gitstow ui` so the legend exists in prose too, not only in the UI.

### Changed

- `_classify` and `_delta` helpers in `web/routes/dashboard.py` now also return tooltip strings; `_pull_tooltip` maps (variant × status) to the exact explanation shown on each Pull button. Partial template `partials/repo_row.html` threads these through consistently so HTMX row swaps after pull keep the tooltips accurate.

## [0.2.1] - 2026-04-17

### Added

- **`gitstow update`** — self-upgrade from PyPI. Detects the install method (pipx, pip, or editable) and runs the matching upgrade command. Use `--check` / `-c` to query PyPI without installing — shows newer-version-available or up-to-date. Editable installs get a friendly note pointing at `git pull` instead of trying to pip-upgrade.
- Smoke tests for `serve` and `update` command help.

### Fixed

- **CI**: added `httpx` to `[dev]` extras. `fastapi.testclient.TestClient` re-exports Starlette's version, which imports `httpx` at module load. Fresh CI installs were failing at test collection; local envs passed because `httpx` was already present from other packages.

## [0.2.0] - 2026-04-17

### Added

- **`gitstow ui`** — a persistent local browser dashboard for daily repo management. Launches a FastAPI + Jinja2 + HTMX server at `http://127.0.0.1:7853` and auto-opens your default browser. Dark theme (Bricolage Grotesque display, JetBrains Mono data) with ember-orange primary accent and signal-blue secondary.
  - **Dashboard** — ledger view of every tracked repo: colored-dot status (clean / dirty / conflict / behind / ahead / frozen), workspace chip, branch, remote delta, tags, last-pull time. Hero metrics strip summarizes counts. Hover reveals an accent indicator line on the row's left edge.
  - **Pull** — single-repo pull with HTMX row swap in place; **Pull all** runs every non-frozen repo in parallel via the existing `core.parallel` semaphore and renders a summary panel (`N ok · N failed · N skipped`) with per-repo failure details.
  - **Add repo** — form with URL parser, workspace selector, and optional tags; merges workspace auto-tags on clone.
  - **Remove** — registry-only from the row's ⋯ menu (HTMX row-delete); registry + disk from the repo drawer (defensive path check; refuses to rmtree outside the workspace root).
  - **Freeze / tag toggles** — row-menu freeze with HTMX label flip; drawer freeze checkbox posts on change; drawer tag editor replaces the list from a comma-separated input.
  - **Workspaces** — list, add (label + path + layout + auto-tags), remove (files untouched), **Scan** (discovers untracked repos on disk and catalogs them with auto-tags merged).
  - **Collection export / import** — under Settings; downloads YAML (canonical round-trip), JSON, or plain URL list; upload accepts any of the three.
  - **Auto-refresh** — dashboard tbody reloads every 30s via HTMX `hx-trigger="every 30s"` without touching hero, metrics, or action bar.
  - **Shutdown** — footer button POSTs `/shutdown`, which flips `uvicorn.Server.should_exit` (Ctrl+C also works).
- **New core dependencies** (shipped, not behind an extra): `fastapi>=0.110`, `uvicorn>=0.27`, `jinja2>=3.1`, `python-multipart>=0.0.9`.
- **30 FastAPI TestClient smoke tests** covering every route + mutation path. Monkeypatches isolate config/repo files to tmp; git calls are mocked (never shells out).

### Changed

- Command count 29 → 30 (`serve` added).
- `CLAUDE.md` architecture tree expanded to document the new `web/` module.

### Security

- `gitstow ui` binds `127.0.0.1` only. There is no `--host` flag — arbitrary git execution must not be LAN-reachable.
- All mutations are POST; `/shutdown` is POST so stray links can't terminate the server.
- Registry + disk delete resolves the repo path and verifies it lives under the workspace root before `rmtree`.

### Deferred

- The Textual TUI has multiple breakages and is parked in [BACKLOG.md](BACKLOG.md); `gitstow ui` is the primary visual dashboard now. TUI remains for the eventual SSH / remote use case.
- Web GUI is intentionally single-user + localhost-only. Auth, multi-user, HTTPS, remote access, and daemonization are explicitly out of scope for v0.2.

## [0.1.0] - 2026-04-10

Initial release.

### Added

- **Core commands:** `add`, `pull`, `list`, `status`, `remove`, `migrate`
- **Workspace system:** Multiple workspaces with structured (`owner/repo`) and flat (`repo`) layouts
- **Workspace commands:** `workspace list`, `workspace add`, `workspace remove`, `workspace scan`
- **Repo management:** `repo freeze`, `repo unfreeze`, `repo tag`, `repo untag`, `repo tags`, `repo info`
- **Bulk operations:** Parallel pull/status with configurable concurrency (default 6)
- **Power commands:** `exec` (run commands across repos), `search` (grep across repos via ripgrep), `open` (editor/browser/finder), `stats`
- **Collection sharing:** `collection export` (YAML/JSON/URLs) and `collection import`
- **Shell integration:** `shell pick` (fzf picker), `shell init` (aliases), `shell setup`
- **Interactive TUI:** `tui` command with Textual-based dashboard (filter, pull, freeze toggle)
- **URL parsing:** GitHub, GitLab, Bitbucket, Codeberg, Azure DevOps, custom hosts; HTTPS, SSH, and shorthand formats
- **AI integration:** Claude Code skill (auto-installed via `onboard` or `install-skill`) and optional MCP server
- **Setup:** `onboard` wizard, `doctor` health check, `config show/set`
- **Output modes:** `--json` and `--quiet` flags on all main commands
- **Global workspace filter:** `-w/--workspace` flag on all commands
- **Error isolation:** One failing repo never blocks operations on others
