# gitstow — Implementation Plan

**Version:** 0.1.0 (Stage 1)
**Created:** 2026-04-05
**Status:** Planning

## Vision

A git repository library manager for developers and AI tools. Clone, organize, and maintain collections of repositories you learn from and reference — not repos you contribute to.

**Core thesis:** AI-assisted development has created a new relationship with open source. Developers increasingly maintain local clones of repos they study, not just contribute to. `gitstow` manages this collection as a first-class concern, with AI integration built in from day one.

## Architecture Overview

```
gitstow (CLI)
    │
    ├── cli/           Command layer (Typer) — user-facing commands
    │     │
    │     └── delegates to ──┐
    │                        │
    ├── core/          Business logic — git ops, URL parsing, repo store, config
    │                        │
    └── skill/         Claude Code skill — SKILL.md bundled with the package
```

**Design principles:**
1. **Folder-as-state** — the directory tree (`root/owner/repo/`) is the primary source of truth. Metadata in `repos.yaml` supplements but never contradicts what's on disk.
2. **Error isolation** — one bad repo never stops operations on the other 49.
3. **JSON-first** — every command supports `--json` for machine consumption. AI tools use JSON; humans see Rich-formatted output.
4. **Zero-config start** — `gitstow add <url>` works immediately with sensible defaults. Onboarding is optional polish.

## Directory Structure

### Project layout

```
~/labs/projects/gitstow/
├── src/gitstow/
│   ├── __init__.py              # __version__ = "0.1.0"
│   ├── __main__.py              # python -m gitstow
│   │
│   ├── cli/                     # Command layer
│   │   ├── __init__.py
│   │   ├── main.py              # Typer app, version callback, command registration
│   │   ├── add.py               # gitstow add <url> [urls...]
│   │   ├── pull.py              # gitstow pull [--tag] [--include-frozen]
│   │   ├── list_cmd.py          # gitstow list [--tag] [--owner] [--json]
│   │   ├── status.py            # gitstow status [--json]
│   │   ├── remove.py            # gitstow remove <owner/repo> [--yes]
│   │   ├── manage.py            # gitstow freeze/unfreeze/tag/untag/info
│   │   ├── migrate.py           # gitstow migrate <path>
│   │   ├── config_cmd.py        # gitstow config show/set/path
│   │   ├── onboard.py           # First-run setup wizard
│   │   ├── skill_cmd.py         # gitstow install-skill
│   │   └── doctor.py            # gitstow doctor
│   │
│   ├── core/                    # Business logic
│   │   ├── __init__.py
│   │   ├── paths.py             # Path constants (~/.gitstow/, skill paths)
│   │   ├── config.py            # Settings dataclass, load/save config.yaml
│   │   ├── repo.py              # Repo dataclass, RepoStore (repos.yaml CRUD)
│   │   ├── git.py               # Git operations (clone, pull, status, remote info)
│   │   ├── url_parser.py        # URL → (host, owner, repo) extraction
│   │   ├── discovery.py         # Walk directory tree to find/reconcile repos
│   │   └── parallel.py          # Async execution with semaphore + error collection
│   │
│   └── skill/                   # Claude Code skill (bundled)
│       └── SKILL.md             # Skill definition
│
├── pyproject.toml               # Hatchling build, dependencies, entry point
├── README.md                    # User-facing docs
├── CLAUDE.md                    # AI developer instructions
├── LICENSE                      # MIT
├── docs/
│   ├── user/
│   │   └── getting-started.md   # Quick start guide
│   └── building/
│       └── implementation-plan.md  # This file
└── tests/
    ├── __init__.py
    ├── test_url_parser.py       # URL parsing is critical — test heavily
    ├── test_repo.py             # RepoStore CRUD tests
    └── test_git.py              # Git operation tests (with mocks)
```

### User-facing paths

```
~/.gitstow/                      # App home (hidden, config + metadata)
├── config.yaml                  # Settings (root_path, default_host, etc.)
└── repos.yaml                   # Per-repo metadata (frozen, tags, timestamps)

~/opensource/                     # Default repo root (configurable)
├── anthropic/
│   ├── claude-code/
│   └── anthropic-sdk-python/
├── facebook/
│   └── react/
└── torvalds/
    └── linux/
```

---

## Data Models

### config.yaml

```yaml
# ~/.gitstow/config.yaml
root_path: ~/opensource           # Where repos are cloned
default_host: github.com          # Assumed when URL has no host
prefer_ssh: false                 # true = clone via SSH, false = HTTPS
parallel_limit: 6                 # Max concurrent git operations
```

### repos.yaml

```yaml
# ~/.gitstow/repos.yaml
# Key = owner/repo (matches folder structure under root)
anthropic/claude-code:
  remote_url: https://github.com/anthropic/claude-code.git
  frozen: false
  tags: [ai, tools]
  added: 2026-04-05
  last_pulled: 2026-04-05T15:30:00

facebook/react:
  remote_url: https://github.com/facebook/react.git
  frozen: true
  tags: [frontend, reference]
  added: 2026-03-15
  last_pulled: 2026-03-20T10:00:00
```

### Repo dataclass

```python
@dataclass
class Repo:
    owner: str                    # "anthropic"
    name: str                     # "claude-code"
    remote_url: str               # "https://github.com/anthropic/claude-code.git"
    path: Path                    # Absolute path on disk
    frozen: bool = False
    tags: list[str] = field(default_factory=list)
    added: str = ""               # ISO date
    last_pulled: str = ""         # ISO datetime

    @property
    def key(self) -> str:
        """owner/repo — the unique identifier."""
        return f"{self.owner}/{self.name}"
```

### Settings dataclass

```python
@dataclass
class Settings:
    root_path: str = "~/opensource"
    default_host: str = "github.com"
    prefer_ssh: bool = False
    parallel_limit: int = 6
```

---

## URL Parser — Design

The URL parser is the most critical piece. Adopted from ghq's approach with improvements.

### Supported input formats

| Input | Parsed as |
|-------|-----------|
| `owner/repo` | `https://github.com/owner/repo` (shorthand) |
| `github.com/owner/repo` | `https://github.com/owner/repo` (has dots = host) |
| `https://github.com/owner/repo` | Used as-is |
| `https://github.com/owner/repo.git` | Strip `.git` suffix |
| `git@github.com:owner/repo.git` | SCP → `ssh://git@github.com/owner/repo` |
| `ssh://git@github.com/owner/repo` | Used as-is |
| `https://gitlab.com/group/subgroup/repo` | Nested groups: owner=`group/subgroup` |
| `https://bitbucket.org/owner/repo` | Any host works |
| `https://dev.azure.com/org/project/_git/repo` | Azure DevOps special handling |

### Resolution algorithm

```
1. If URL starts with git@ or user@host: → convert SCP to ssh://
2. If URL has :// scheme → parse with urlparse
3. If first segment has a dot (looks like hostname) → prepend https://
4. Otherwise → shorthand: prepend https://{default_host}/
5. Extract path segments:
   - Last segment (minus .git) = repo name
   - Everything between host and repo = owner (handles nested groups)
6. If prefer_ssh → convert https:// to ssh://
```

### Return value

```python
@dataclass
class ParsedURL:
    host: str        # "github.com"
    owner: str       # "anthropic" or "group/subgroup" for GitLab
    repo: str        # "claude-code"
    clone_url: str   # The actual URL to pass to git clone
    original: str    # What the user typed
```

---

## Commands — Detailed Specification

### `gitstow add <url> [urls...]`

**Purpose:** Clone repos into the organized structure.

**Flow:**
1. Parse each URL → `ParsedURL`
2. Compute target path: `{root}/{owner}/{repo}/`
3. If path exists:
   - If it's a git repo with matching remote → treat as already added, pull if `--update`
   - If it's a git repo with different remote → error, explain conflict
   - If it's not a git repo → error, path occupied
4. If path doesn't exist:
   - `mkdir -p {root}/{owner}/`
   - `git clone [--depth 1 if --shallow] <clone_url> <path>`
5. Register in `repos.yaml` (frozen=false, tags=[], added=today)
6. Print success summary

**Flags:**
| Flag | Effect |
|------|--------|
| `--shallow` / `-s` | `git clone --depth 1` (saves disk) |
| `--branch` / `-b` | Clone specific branch |
| `--update` / `-u` | Pull if already exists (like ghq) |
| `--tag` / `-t` | Apply tag(s) immediately on add |
| `--json` / `-j` | JSON output |
| `--quiet` / `-q` | Suppress progress |

**Parallel:** Multiple URLs are cloned concurrently (semaphore = `parallel_limit`).

**Stdin:** If no args and stdin is not a TTY, read URLs from stdin (one per line). Enables: `cat repos.txt | gitstow add`

---

### `gitstow pull [repos...]`

**Purpose:** Bulk update repos via `git pull --ff-only`.

**Flow:**
1. Load all repos from `repos.yaml` + reconcile with filesystem (discovery)
2. Apply filters: `--tag`, `--owner`, `--exclude-tag`
3. Skip frozen repos (unless `--include-frozen`)
4. For each repo (parallel, semaphore-limited):
   a. Check if repo is dirty → skip with warning
   b. Run `git pull --ff-only`
   c. Record result (success / error / skipped-frozen / skipped-dirty)
   d. Update `last_pulled` timestamp on success
5. Print summary table:
   ```
   Pull Summary
   ┌─────────────────────┬──────────┬──────────────────────┐
   │ Repo                │ Status   │ Details              │
   ├─────────────────────┼──────────┼──────────────────────┤
   │ anthropic/claude    │ ✓ Updated│ 3 commits pulled     │
   │ facebook/react      │ ○ Frozen │ Skipped (frozen)     │
   │ torvalds/linux      │ ✓ Clean  │ Already up to date   │
   │ user/broken-repo    │ ✗ Error  │ Network unreachable  │
   └─────────────────────┴──────────┴──────────────────────┘
   Updated: 1  |  Up to date: 1  |  Frozen: 1  |  Errors: 1
   ```

**Flags:**
| Flag | Effect |
|------|--------|
| `--tag` / `-t` | Only pull repos with this tag (repeatable) |
| `--exclude-tag` | Skip repos with this tag |
| `--owner` | Only pull repos from this owner |
| `--include-frozen` | Pull frozen repos too |
| `--json` / `-j` | JSON output (structured result per repo) |
| `--quiet` / `-q` | Suppress per-repo progress, only show summary |

---

### `gitstow list [query]`

**Purpose:** Show all repos, grouped by owner.

**Output (human):**
```
gitstow — 4 repos across 3 owners

anthropic/ (2 repos)
  claude-code          main    ✓ clean     [ai, tools]
  sdk-python           main    ✓ clean     [ai]

facebook/ (1 repo)
  react                main    ❄ frozen    [frontend, reference]

torvalds/ (1 repo)
  linux                master  * dirty     []
```

**Flags:**
| Flag | Effect |
|------|--------|
| `--tag` / `-t` | Filter by tag |
| `--owner` | Filter by owner |
| `--frozen` | Show only frozen repos |
| `--paths` / `-p` | Show full paths instead of relative names |
| `--json` / `-j` | JSON output |

**Query:** Optional positional arg for substring match (like ghq): `gitstow list react` finds `facebook/react`.

---

### `gitstow status [repos...]`

**Purpose:** Detailed status dashboard for all repos.

**Output:** Similar to `list` but with git status details — branch, ahead/behind counts, dirty file counts, last commit date, last pulled date.

```
gitstow status — 4 repos

  Repo                Branch   Status      Ahead/Behind  Last Commit    Last Pulled
  anthropic/claude    main     ✓ clean     —             2 hours ago    5 min ago
  anthropic/sdk       main     * dirty(3)  —             1 day ago      1 day ago
  facebook/react      main     ❄ frozen    2↓            3 days ago     1 week ago
  torvalds/linux      master   ✓ clean     14↓           1 hour ago     2 days ago
```

**Flags:** `--json`, `--tag`, `--owner`, `--dirty-only`

---

### `gitstow remove <owner/repo> [--yes]`

**Purpose:** Remove a repo from the collection.

**Flow:**
1. Confirm with user (unless `--yes`)
2. Remove directory (`shutil.rmtree`)
3. Remove from `repos.yaml`
4. Clean up empty owner directory if no other repos remain

---

### `gitstow freeze <owner/repo>` / `gitstow unfreeze <owner/repo>`

**Purpose:** Toggle freeze flag. Frozen repos are skipped during `pull`.

**Implementation:** Update `frozen` field in `repos.yaml`. Print confirmation.

**Bulk freeze:** `gitstow freeze --tag archived` freezes all repos with that tag.

---

### `gitstow tag <owner/repo> <tag> [tags...]` / `gitstow untag <owner/repo> <tag>`

**Purpose:** Manage tags on repos.

**Also:** `gitstow tag --list` shows all tags with repo counts.

---

### `gitstow info <owner/repo>`

**Purpose:** Detailed view of a single repo.

**Output:**
```
anthropic/claude-code

  Remote:       https://github.com/anthropic/claude-code.git
  Path:         /Users/rish/labs/OSS/anthropic/claude-code
  Branch:       main
  Status:       clean
  Frozen:       no
  Tags:         ai, tools
  Added:        2026-04-05
  Last pulled:  2026-04-05 15:30
  Disk size:    142 MB
  Last commit:  fix: resolve edge case in parser (2 hours ago)
```

---

### `gitstow migrate <path> [paths...]`

**Purpose:** Adopt an existing local repo into the gitstow structure.

**Flow:**
1. Verify path is a git repo
2. Read remote URL → parse to get owner/repo
3. Compute target path: `{root}/{owner}/{repo}/`
4. Move (or copy + delete) the repo to target
5. Register in `repos.yaml`

**Inspiration:** ghq's `migrate` command. Critical for adoption — users have existing repos they want to organize.

---

### `gitstow config show` / `gitstow config set <key> <value>`

**Purpose:** View and modify settings.

**`config show` output:**
```
gitstow config

  root_path:       ~/labs/OSS
  default_host:    github.com
  prefer_ssh:      false
  parallel_limit:  6

  Config file:     ~/.gitstow/config.yaml
  Repos file:      ~/.gitstow/repos.yaml
  Repos tracked:   12
```

---

### `gitstow onboard`

**Purpose:** Interactive first-run setup.

**Flow:**
1. Welcome message + what gitstow does
2. Set root path (beaupy input, default `~/opensource`)
3. Set default host (selector: github.com, gitlab.com, custom)
4. Set protocol preference (HTTPS vs SSH)
5. Offer to scan for existing repos in root path
6. Offer to install Claude Code skill
7. Write config, print summary

---

### `gitstow doctor`

**Purpose:** Health check.

**Checks:**
1. Config file exists and is valid
2. Root path exists and is accessible
3. `git` is installed and version is adequate
4. Repos on disk match `repos.yaml` (orphans? missing?)
5. Any repos with broken remotes
6. Report: N repos tracked, N frozen, N tags used, disk usage

---

### `gitstow install-skill`

**Purpose:** Copy bundled SKILL.md to `~/.claude/skills/gitstow/`.

---

## Core Modules — Implementation Details

### `core/paths.py`

```python
APP_HOME = Path.home() / ".gitstow"
CONFIG_FILE = APP_HOME / "config.yaml"
REPOS_FILE = APP_HOME / "repos.yaml"
DEFAULT_ROOT = Path.home() / "opensource"

CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
SKILL_TARGET = CLAUDE_SKILLS_DIR / "gitstow"
```

### `core/git.py`

Thin wrappers around `subprocess.run(["git", ...])`. No library dependencies — shell out to git directly (like ghq and gita both do).

**Key functions:**
```python
def clone(url: str, target: Path, shallow: bool = False, branch: str | None = None) -> bool
def pull(repo_path: Path) -> PullResult  # PullResult: success, already_up_to_date, error_msg, commits_pulled
def get_status(repo_path: Path) -> RepoStatus  # branch, dirty_count, staged_count, untracked_count, ahead, behind
def get_remote_url(repo_path: Path) -> str | None
def get_last_commit(repo_path: Path) -> CommitInfo  # message, date, hash
def is_git_repo(path: Path) -> bool
def get_branch(repo_path: Path) -> str
def get_disk_size(repo_path: Path) -> int  # bytes
```

**Status detection:** Use `git status --porcelain=v2 --branch` — one subprocess call gets everything (dirty files, staged, untracked, ahead/behind). This is the improvement over gita's 4-5 separate calls.

### `core/parallel.py`

Async execution with semaphore, adopted from gita's pattern but with concurrency throttling (gita's biggest complaint).

```python
async def run_parallel(
    tasks: list[Callable],
    max_concurrent: int = 6,
    on_progress: Callable | None = None,
) -> list[TaskResult]:
    """Run tasks concurrently with a semaphore.
    
    Returns results in order. Each result has:
    - repo_key: str
    - success: bool
    - output: str
    - error: str | None
    """
```

### `core/discovery.py`

Walk the root directory to find git repos and reconcile with `repos.yaml`.

```python
def discover_repos(root: Path) -> list[DiscoveredRepo]
    """Walk root/*/  looking for .git directories.
    
    Returns repos found on disk. Doesn't touch repos.yaml.
    Two-level walk only: root/owner/repo/.git
    """

def reconcile(on_disk: list[DiscoveredRepo], in_store: dict[str, Repo]) -> ReconcileResult
    """Compare disk vs store.
    
    Returns:
    - matched: repos in both
    - orphaned_on_disk: on disk but not in store (suggest registering)
    - missing_from_disk: in store but not on disk (suggest removing)
    """
```

### `core/repo.py` — RepoStore

```python
class RepoStore:
    """CRUD for repos.yaml."""
    
    def __init__(self, path: Path = REPOS_FILE):
        self._path = path
        self._repos: dict[str, Repo] = {}  # key = "owner/repo"
    
    def load(self) -> None
    def save(self) -> None
    def add(self, repo: Repo) -> None
    def remove(self, key: str) -> None
    def get(self, key: str) -> Repo | None
    def list_all(self) -> list[Repo]
    def list_by_tag(self, tag: str) -> list[Repo]
    def list_by_owner(self, owner: str) -> list[Repo]
    def list_frozen(self) -> list[Repo]
    def list_unfrozen(self) -> list[Repo]
    def update(self, key: str, **kwargs) -> None
    def all_tags(self) -> dict[str, int]  # tag → count
    def all_owners(self) -> dict[str, int]  # owner → count
```

---

## Dependencies

```toml
[project]
dependencies = [
    "typer[all]>=0.9",     # CLI framework + rich integration
    "pyyaml>=6.0",         # Config/repos YAML serialization
    "rich>=13.0",          # Terminal UI (tables, colors, panels)
    "beaupy>=3.0",         # Arrow-key selectors (onboarding)
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.4",
]
```

**Notably absent:** No `giturlparse` — we'll write our own URL parser (~50 lines) based on ghq's approach. It's too small to justify a dependency, and `giturlparse` has Azure DevOps bugs.

---

## Build Stages

### Stage 1 — The Complete Useful Tool (this build)

**Milestone:** `gitstow add <url>` → `gitstow pull` → `gitstow list` workflow works end-to-end.

| Priority | Component | Files |
|----------|-----------|-------|
| P0 | Project scaffolding | `pyproject.toml`, `__init__`, `__main__`, `cli/main.py` |
| P0 | Path constants + config | `core/paths.py`, `core/config.py` |
| P0 | URL parser | `core/url_parser.py`, `tests/test_url_parser.py` |
| P0 | Git operations | `core/git.py` |
| P0 | Repo store | `core/repo.py` |
| P0 | `add` command | `cli/add.py` |
| P0 | `pull` command | `cli/pull.py`, `core/parallel.py` |
| P0 | `list` command | `cli/list_cmd.py` |
| P1 | `status` command | `cli/status.py` |
| P1 | `remove` command | `cli/remove.py` |
| P1 | `freeze`/`unfreeze`/`tag`/`untag`/`info` | `cli/manage.py` |
| P1 | `migrate` command | `cli/migrate.py` |
| P1 | `config` subcommands | `cli/config_cmd.py` |
| P1 | Onboarding wizard | `cli/onboard.py` |
| P1 | Discovery + reconciliation | `core/discovery.py` |
| P2 | `doctor` command | `cli/doctor.py` |
| P2 | `install-skill` + SKILL.md | `cli/skill_cmd.py`, `skill/SKILL.md` |
| P2 | README | `README.md` |

**Build order (sequential, each depends on previous):**

1. **Scaffolding** — `pyproject.toml`, `__init__`, `__main__`, `cli/main.py` with version command
2. **Core foundation** — `paths.py`, `config.py`, `url_parser.py` (+ tests), `git.py`, `repo.py`
3. **`add` command** — the first working feature, end-to-end
4. **`pull` command** — with `parallel.py`, error isolation, summary table
5. **`list` command** — grouped-by-owner display
6. **`status` command** — enriched dashboard with git status
7. **Management commands** — `remove`, `freeze/unfreeze`, `tag/untag`, `info`
8. **`migrate` command** — adopt existing repos
9. **`config` + `onboard`** — settings management and first-run wizard
10. **`discovery`** — reconcile disk vs store
11. **`doctor`** — health checks
12. **Skill** — SKILL.md + install-skill command
13. **README** — user-facing documentation

### Stage 2 — Polish & Power (future)

| Component | Description |
|-----------|-------------|
| TUI | Interactive dashboard via Textual (`gitstow tui`) |
| MCP server | Expose tools to any AI tool via Model Context Protocol |
| `search` | Grep across all repos |
| `open` | Open repo in editor/browser/Finder |
| `exec` | Run arbitrary commands across repos |
| `stats` | Collection statistics (disk, activity, staleness) |
| `export`/`import` | Share collections as portable lists |
| Shell integration | fzf helpers, shell completions, cd shortcut |
| GitHub Actions | Automated PyPI publishing on tag |

---

## Patterns Adopted

### From anyscribecli
- Hatchling build system with `src/` layout
- Typer + Rich for CLI
- `--json` / `--quiet` flags on all commands
- Console/err_console separation
- Beaupy for interactive onboarding
- `__main__.py` for `python -m` fallback
- SKILL.md bundled in package with `install-skill` command
- `doctor` command for diagnostics

### From ghq
- URL shorthand resolution (owner/repo → assumes GitHub)
- `looksLikeAuthority` heuristic (has dots = hostname)
- Parallel operations with semaphore cap
- `migrate` command for adopting existing repos
- `--shallow`, `--branch` clone flags
- Stdin support for piping URL lists

### From gita
- Async subprocess execution with sync fallback
- Color-coded status output
- Tag/group-based filtering
- `freeze` concept for skipping repos
- Per-repo error isolation in bulk operations

### Improvements over both
- `git status --porcelain=v2 --branch` (one call vs gita's 4-5)
- Concurrency throttling with semaphore (gita has none → SSH crashes)
- Configurable default host (ghq hardcodes github.com)
- Configurable SSH preference (ghq requires `-p` every time)
- Tags as first-class metadata (gita has groups, not tags)
- `--json` on everything (gita has no JSON output)
- Folder-as-state + supplemental metadata (vs gita's registry-only or ghq's filesystem-only)

---

## Testing Strategy

### Unit tests (always)
- **URL parser** — test all ~12 URL formats, edge cases (trailing slashes, `.git` suffix, no path, Azure DevOps)
- **RepoStore** — CRUD operations, YAML serialization/deserialization
- **Config** — load/save, defaults, missing file handling

### Integration tests (CI)
- **Clone + list** — clone a small public repo, verify it appears in list
- **Pull** — verify pull on a clean repo succeeds
- **Freeze** — verify frozen repo is skipped during pull
- **Migrate** — move a repo and verify structure

### Manual testing (pre-release)
- Add 10+ repos from different platforms
- Bulk pull with mix of frozen/clean/dirty
- Error handling: bad URL, no network, auth failure, merge conflict
- Onboarding on fresh machine

---

## Open Design Questions

1. **Host in folder structure?** ghq uses `root/host/owner/repo`. We use `root/owner/repo` (no host). This is simpler but means two repos with the same owner/name on different hosts would conflict. For now: no host prefix (99% of repos are GitHub). Revisit if users report conflicts.

2. **Reconciliation timing:** When should we reconcile disk vs store? Options:
   - On every `list`/`status` (always fresh, slight overhead)
   - On `doctor` only (fast commands, but can be stale)
   - On `pull` (reconcile before pulling)
   - **Decision:** Reconcile on `list`, `status`, `pull`, and `doctor`. Not on `add`/`remove` (those already update the store). Cache discovery results within a single command invocation.

3. **What if a repo is on disk but not in repos.yaml?** (e.g., user manually `git clone`d into the root). Options:
   - Auto-register silently
   - Show as "untracked" in list/status
   - Ignore
   - **Decision:** Show as "untracked" with a hint to run `gitstow add <path>` or `gitstow migrate <path>`. Never silently mutate state.
