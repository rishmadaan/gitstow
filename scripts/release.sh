#!/usr/bin/env bash
# Release script - bumps version, commits, tags, and pushes to trigger PyPI publish.
#
# Usage:
#   bash scripts/release.sh 0.2.6
#   bash scripts/release.sh 0.2.6 "Short description of what changed"
#
# What it does:
#   1. Updates version in src/gitstow/__init__.py and pyproject.toml
#   2. Commits the version bump
#   3. Creates a git tag (v0.2.6)
#   4. Pushes the commit and tag, which triggers GitHub Actions to publish to PyPI

set -euo pipefail

VERSION="${1:-}"
MESSAGE="${2:-}"

if [ -z "$VERSION" ]; then
    echo "Usage: bash scripts/release.sh <version> [description]"
    echo "Example: bash scripts/release.sh 0.2.6 \"browser UI polish\""
    exit 1
fi

if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "Error: Version must be in X.Y.Z format (got: $VERSION)"
    exit 1
fi

if ! grep -q "^## \[$VERSION\]" CHANGELOG.md; then
    echo "Error: CHANGELOG.md has no '## [$VERSION]' section."
    echo "Document the release before shipping it (this is how 0.2.6 went missing)."
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: Tracked working tree is not clean. Commit or stash changes first."
    git status --short --untracked-files=no
    exit 1
fi

BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "main" ]; then
    echo "Warning: You are on '$BRANCH', not 'main'. Continue? (y/N)"
    read -r CONFIRM
    [ "$CONFIRM" = "y" ] || exit 1
fi

if git rev-parse "v$VERSION" >/dev/null 2>&1; then
    echo "Error: Tag v$VERSION already exists locally."
    exit 1
fi

REMOTE_TAGS="$(git ls-remote --tags origin "refs/tags/v$VERSION")"
if [ -n "$REMOTE_TAGS" ]; then
    echo "Error: Tag v$VERSION already exists on origin."
    exit 1
fi

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    else
        echo "Error: Python is required but was not found."
        exit 1
    fi
fi

INIT_FILE="src/gitstow/__init__.py"
TOML_FILE="pyproject.toml"

"$PYTHON_BIN" - "$VERSION" "$INIT_FILE" "$TOML_FILE" <<'PY'
import pathlib
import re
import sys
import tomllib

version, init_file, toml_file = sys.argv[1:]
init_path = pathlib.Path(init_file)
toml_path = pathlib.Path(toml_file)

with toml_path.open("rb") as f:
    old_version = tomllib.load(f)["project"]["version"]

init_text = init_path.read_text()
init_match = re.search(r'__version__ = "([^"]+)"', init_text)
if not init_match:
    raise SystemExit(f"Could not find __version__ in {init_file}")

init_version = init_match.group(1)
if init_version != old_version:
    raise SystemExit(
        f"Version mismatch before release: {init_file} has {init_version}, "
        f"{toml_file} has {old_version}"
    )

toml_text = toml_path.read_text()
new_init_text, init_count = re.subn(
    r'__version__ = "([^"]+)"',
    f'__version__ = "{version}"',
    init_text,
    count=1,
)
new_toml_text, toml_count = re.subn(
    r'(?m)^version = "([^"]+)"',
    f'version = "{version}"',
    toml_text,
    count=1,
)

if init_count != 1:
    raise SystemExit(f"Expected to update one __version__ in {init_file}")
if toml_count != 1:
    raise SystemExit(f"Expected to update one project version in {toml_file}")

init_path.write_text(new_init_text)
toml_path.write_text(new_toml_text)

print(f"Bumping {old_version} -> {version}")
PY

echo "Updated versions:"
grep '__version__' "$INIT_FILE"
grep '^version' "$TOML_FILE"

COMMIT_MSG="Bump to v$VERSION"
if [ -n "$MESSAGE" ]; then
    COMMIT_MSG="Bump to v$VERSION - $MESSAGE"
fi

git add "$INIT_FILE" "$TOML_FILE"
git commit -m "$COMMIT_MSG"
git tag "v$VERSION"
git push origin "$BRANCH"
git push origin "v$VERSION"

echo ""
echo "Released v$VERSION"
echo "GitHub Actions will publish to PyPI automatically:"
echo "https://github.com/rishmadaan/gitstow/actions"
