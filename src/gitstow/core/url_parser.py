# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""URL parser — extract host/owner/repo from any git URL.

Supports:
  - Full URLs: https://github.com/owner/repo
  - SSH URLs: git@github.com:owner/repo.git
  - Shorthand: owner/repo (assumes default_host)
  - Host-prefixed: github.com/owner/repo
  - Nested groups: gitlab.com/group/subgroup/repo
  - Azure DevOps: dev.azure.com/org/project/_git/repo,
    git@ssh.dev.azure.com:v3/org/project/repo, org.visualstudio.com/project/_git/repo

Resolution algorithm adopted from ghq (3.5k stars) with improvements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Patterns
_HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_SCP_LIKE = re.compile(r"^([^@]+@)?([^:]+):(.+)$")
_LOOKS_LIKE_HOST = re.compile(r"(?:[A-Za-z0-9][-A-Za-z0-9]*\.)+[A-Za-z]{2,}(?::\d{1,5})?$")
_UNSAFE_IN_SEGMENT = re.compile(r"[/\\:\x00-\x1f\x7f]")
_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")
_SEGMENT_RULE = (
    "owner/repo may not contain '.', '..' or empty path segments, "
    "a segment of only dots and spaces, or a \\, : or control character"
)

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


def valid_segment(segment: str) -> bool:
    """Whether one owner/repo path segment is a safe folder name on every platform.

    Non-empty; no `/`, `\\` or `:` (path separators and Windows drive letters) and no
    control character; and not only dots and spaces, because Windows drops trailing
    dots and spaces, so `...` or `.. ` would name the parent or owner folder. Every
    repo key gitstow turns into a folder passes this, wherever it came from: a URL
    (parse_git_url), repos.yaml (RepoStore.load) or a folder on disk (discovery).
    """
    return bool(segment) and not _UNSAFE_IN_SEGMENT.search(segment) and bool(segment.strip(". "))


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
    # Before urlparse, which silently deletes tabs and newlines (acme/wid<TAB>get would
    # become acme/widget), so a control character anywhere is refused, not dropped.
    if _CONTROL_CHAR.search(raw):
        raise ValueError(f"Cannot use {original!r}: {_SEGMENT_RULE}")

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
    path = path.removesuffix(".git")

    # owner/repo becomes a folder path under the workspace. Every raw segment is
    # checked before anything drops empty ones or truncates a browse link, so
    # `acme//widget`, `..`, `\\Documents` or `D:` can never turn into a path outside it.
    if path and not all(valid_segment(seg) for seg in path.split("/")):
        raise ValueError(f"Cannot use '{original}': {_SEGMENT_RULE}")

    # Azure DevOps SSH (git@ssh.dev.azure.com:v3/org/project/repo) names the
    # same repo as the HTTPS form: normalise the host and drop the "v3" segment.
    if host == "ssh.dev.azure.com":
        host = "dev.azure.com"
        path = path.removeprefix("v3/")

    # Azure DevOps: dev.azure.com/org/project/_git/repo
    if _is_azure(host):
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


def same_repo(url_a: str, url_b: str, default_host: str) -> bool:
    """Whether two remote URLs point at the same host/owner/repo (protocol-agnostic)."""
    try:
        pa = parse_git_url(url_a, default_host=default_host)
        pb = parse_git_url(url_b, default_host=default_host)
    except ValueError:
        return False
    return (pa.host, pa.owner, pa.repo) == (pb.host, pb.owner, pb.repo)


def _extract_owner_repo(host: str, path: str) -> tuple[str, str]:
    """Split a URL path into owner and repo.

    Handles nested groups (group/subgroup/repo → owner="group/subgroup"),
    truncates browse-UI suffixes (…/repo/tree/main/… → …/repo), and caps
    known single-owner hosts at exactly two segments.
    """
    parts = [p for p in path.split("/") if p]

    # Word markers only make sense on hosts with fixed owner/repo depth.
    # Nested-group hosts (GitLab-style) separate browse UI with "/-/", and a
    # real subgroup repo may legitimately be named "src" or "wiki".
    markers = _DEEP_LINK_MARKERS if host in _TWO_SEGMENT_HOSTS else {"-"}
    for i, seg in enumerate(parts):
        if i >= 2 and seg in markers:
            parts = parts[:i]
            break

    if host in _TWO_SEGMENT_HOSTS and len(parts) > 2:
        parts = parts[:2]

    if len(parts) < 2:
        return "", parts[0] if parts else ""
    return "/".join(parts[:-1]), parts[-1]


def _is_azure(host: str) -> bool:
    return host == "dev.azure.com" or host.endswith(".visualstudio.com")


def _build_clone_url(
    host: str,
    owner: str,
    repo: str,
    scheme: str,
    prefer_ssh: bool,
) -> str:
    """Build the clone URL from components."""
    if _is_azure(host):
        # Azure's documented clone forms: {org}/{project}/_git/{repo} and v3/{org}/{project}/{repo}.
        if (prefer_ssh or scheme == "ssh") and host == "dev.azure.com":
            return f"git@ssh.dev.azure.com:v3/{owner}/{repo}"
        # shortcut: visualstudio.com always clones over HTTPS — its SSH host differs
        # per organisation; build the SSH form if someone asks for it.
        return f"https://{host}/{owner}/_git/{repo}"
    if prefer_ssh or scheme == "ssh":
        return f"git@{host}:{owner}/{repo}.git"
    else:
        return f"https://{host}/{owner}/{repo}.git"
