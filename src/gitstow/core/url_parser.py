"""URL parser — extract host/owner/repo from any git URL.

Supports:
  - Full URLs: https://github.com/owner/repo
  - SSH URLs: git@github.com:owner/repo.git
  - Shorthand: owner/repo (assumes default_host)
  - Host-prefixed: github.com/owner/repo
  - Nested groups: gitlab.com/group/subgroup/repo
  - Azure DevOps: dev.azure.com/org/project/_git/repo

Resolution algorithm adopted from ghq (3.5k stars) with improvements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Patterns
_HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_SCP_LIKE = re.compile(r"^([^@]+@)?([^:]+):(.+)$")
_LOOKS_LIKE_HOST = re.compile(r"[A-Za-z0-9][-A-Za-z0-9]*\.[A-Za-z]{2,}(?::\d{1,5})?$")

# Hosts whose repo path is always exactly owner/repo — never nested groups.
_TWO_SEGMENT_HOSTS = {"github.com", "bitbucket.org", "codeberg.org", "gitea.com"}

# Path segments that begin a browse-UI suffix rather than the repo path.
# GitLab separates repo path from browse UI with "/-/"; GitHub-style hosts
# use these words directly after owner/repo.
_DEEP_LINK_MARKERS = {
    "-", "tree", "blob", "pull", "pulls", "issues", "commit", "commits",
    "releases", "actions", "wiki", "compare", "raw", "src",
}


@dataclass
class ParsedURL:
    """Result of parsing a git URL."""

    host: str        # "github.com"
    owner: str       # "anthropic" or "group/subgroup" for nested
    repo: str        # "claude-code"
    clone_url: str   # Normalized URL for git clone
    original: str    # What the user typed

    @property
    def key(self) -> str:
        """owner/repo — used as folder path and unique identifier."""
        return f"{self.owner}/{self.repo}"


def parse_git_url(
    raw: str,
    default_host: str = "github.com",
    prefer_ssh: bool = False,
) -> ParsedURL:
    """Parse any git URL format into structured components.

    Args:
        raw: The input — full URL, SCP-style, or shorthand (owner/repo).
        default_host: Host to assume for shorthand URLs (default: github.com).
        prefer_ssh: If True, convert HTTPS URLs to SSH for cloning.

    Returns:
        ParsedURL with host, owner, repo, and normalized clone URL.

    Raises:
        ValueError: If the input can't be parsed into a valid git URL.
    """
    raw = raw.strip().rstrip("/")
    if not raw:
        raise ValueError("Empty URL")

    original = raw

    # Step 1: Convert SCP-style (git@host:path) to ssh:// URL
    if not _HAS_SCHEME.match(raw):
        scp_match = _SCP_LIKE.match(raw)
        if scp_match and ":" in raw and "/" not in raw.split(":")[0]:
            user_part = scp_match.group(1) or ""
            host_part = scp_match.group(2)
            path_part = scp_match.group(3)
            # Ensure path doesn't start with //
            path_part = path_part.lstrip("/")
            raw = f"ssh://{user_part}{host_part}/{path_part}"

    # Step 2: If no scheme, try to detect if it starts with a hostname
    if not _HAS_SCHEME.match(raw):
        first_segment = raw.split("/")[0]
        if _LOOKS_LIKE_HOST.match(first_segment):
            # Looks like github.com/owner/repo — prepend https://
            raw = f"https://{raw}"
        elif "/" in raw:
            # Looks like owner/repo — prepend default host
            raw = f"https://{default_host}/{raw}"
        else:
            raise ValueError(
                f"Cannot parse '{original}': expected owner/repo or a full URL"
            )

    # Step 3: Parse the URL
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"Cannot extract host from '{original}'")

    # Step 4: Extract path segments
    path = parsed.path.strip("/")

    # Strip .git suffix
    if path.endswith(".git"):
        path = path[:-4]

    # Azure DevOps: dev.azure.com/org/project/_git/repo
    if "dev.azure.com" in host or "visualstudio.com" in host:
        parts = path.split("/")
        if "_git" in parts:
            git_idx = parts.index("_git")
            owner = "/".join(parts[:git_idx])
            repo = "/".join(parts[git_idx + 1 :])
        else:
            owner, repo = _extract_owner_repo(host, path)
    else:
        owner, repo = _extract_owner_repo(host, path)

    if not owner or not repo:
        raise ValueError(
            f"Cannot extract owner/repo from '{original}'. "
            f"Expected format: owner/repo or https://host/owner/repo"
        )

    # Step 5: Build the clone URL
    clone_url = _build_clone_url(host, owner, repo, parsed.scheme, prefer_ssh)

    return ParsedURL(
        host=host,
        owner=owner,
        repo=repo,
        clone_url=clone_url,
        original=original,
    )


def _extract_owner_repo(host: str, path: str) -> tuple[str, str]:
    """Split a URL path into owner and repo.

    Handles nested groups (group/subgroup/repo → owner="group/subgroup"),
    truncates browse-UI suffixes (…/repo/tree/main/… → …/repo), and caps
    known single-owner hosts at exactly two segments.
    """
    parts = [p for p in path.split("/") if p]

    # A marker at index >= 2 means everything from it onward is browse UI.
    for i, seg in enumerate(parts):
        if i >= 2 and seg in _DEEP_LINK_MARKERS:
            parts = parts[:i]
            break

    if host in _TWO_SEGMENT_HOSTS and len(parts) > 2:
        parts = parts[:2]

    if len(parts) < 2:
        return "", parts[0] if parts else ""
    return "/".join(parts[:-1]), parts[-1]


def _build_clone_url(
    host: str,
    owner: str,
    repo: str,
    scheme: str,
    prefer_ssh: bool,
) -> str:
    """Build the clone URL from components."""
    if prefer_ssh or scheme == "ssh":
        return f"git@{host}:{owner}/{repo}.git"
    else:
        return f"https://{host}/{owner}/{repo}.git"
