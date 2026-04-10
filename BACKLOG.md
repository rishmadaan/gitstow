# Backlog

Post-v0.1.0 improvements tracked here. See [GitHub Issues](https://github.com/rishmadaan/gitstow/issues) for discussion.

## Enhancements

- [x] **TUI: expand beyond read-only dashboard** (#1) — Added pull selected (P), workspace cycling (w), tag cycling (t)
- [x] **Shell completion for repo names** (#2) — `gitstow shell completions` for bash/zsh/fish; `--quiet` on list/tags/workspace list
- [x] **Network retry/resume for batch operations** (#3) — `--retry N` flag on add and pull; cleans up partial clones before retry
- [x] **Export format versioning and checksums** (#4) — Added `version: 1` to YAML/JSON exports; validates on import; backward-compatible with unversioned files
- [x] **Progress indication during long clones** (#5) — `git clone --progress`; pull shows live counter `[5/47]`
- [x] **Publish to PyPI** (#6) — Release workflow created (`.github/workflows/release.yml`); publish on GitHub release

## Documentation

- [x] **Record demo GIF for README** (#7) — Recorded with VHS, embedded in README
