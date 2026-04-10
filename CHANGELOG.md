# Changelog

All notable changes to gitstow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
