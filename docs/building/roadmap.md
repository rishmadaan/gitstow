# Roadmap

Features noted for future releases, in rough priority order. Not commitments.

## Incoming-changes viewer ("what would a pull bring?") — done

**Noted:** 2026-07-19 (right after shipping the 0.6.0 diff viewer)
**Done:** 2026-11-20, released in 0.10.0 (see CHANGELOG `[0.10.0]`): the repo page's Incoming
tab, the library row's `↓ N incoming ›` chip, the exact Pull, and `gitstow diff <repo> --incoming`.

Today gitstow shows *that* a repo is behind (`↓3` after a fetch) but not *what's
inside* those incoming commits. The feature: click the "behind" badge → see the
incoming commits and their file-level diff (local HEAD vs fetched upstream,
e.g. `git diff HEAD..@{upstream}` after fetch) — before deciding to pull.

- Reuses the 0.6.0 diff viewer wholesale: same file groups, same expandable
  line-by-line panels, same parser (`core/diff.py`) and guardrails
  (binary/truncation/conflict notes).
- New plumbing needed: commit list + diff against the remote-tracking ref in
  `core/git.py`; a second tab or section ("Incoming") beside Changes on the
  repo page; CLI counterpart (`gitstow diff <repo> --incoming` or similar).
- The decision it serves: "do I want this update?" on tracked open-source
  repos — currently answered blind.
