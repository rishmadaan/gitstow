"""Tests for URL parser — the most critical module to test."""

import pytest

from gitstow.core.url_parser import parse_git_url, ParsedURL


class TestGitHubShorthand:
    """owner/repo → assumes github.com"""

    def test_basic_shorthand(self):
        result = parse_git_url("anthropic/claude-code")
        assert result.host == "github.com"
        assert result.owner == "anthropic"
        assert result.repo == "claude-code"
        assert result.clone_url == "https://github.com/anthropic/claude-code.git"

    def test_shorthand_preserves_original(self):
        result = parse_git_url("facebook/react")
        assert result.original == "facebook/react"

    def test_shorthand_key(self):
        result = parse_git_url("torvalds/linux")
        assert result.key == "torvalds/linux"


class TestFullURLs:
    """Full HTTPS URLs"""

    def test_https_github(self):
        result = parse_git_url("https://github.com/anthropic/claude-code")
        assert result.host == "github.com"
        assert result.owner == "anthropic"
        assert result.repo == "claude-code"

    def test_https_with_dot_git(self):
        result = parse_git_url("https://github.com/anthropic/claude-code.git")
        assert result.repo == "claude-code"
        assert ".git.git" not in result.clone_url

    def test_https_gitlab(self):
        result = parse_git_url("https://gitlab.com/group/project")
        assert result.host == "gitlab.com"
        assert result.owner == "group"
        assert result.repo == "project"

    def test_https_bitbucket(self):
        result = parse_git_url("https://bitbucket.org/owner/repo")
        assert result.host == "bitbucket.org"
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_https_codeberg(self):
        result = parse_git_url("https://codeberg.org/owner/repo")
        assert result.host == "codeberg.org"

    def test_trailing_slash(self):
        result = parse_git_url("https://github.com/owner/repo/")
        assert result.repo == "repo"


class TestSSHURLs:
    """SSH and SCP-style URLs"""

    def test_scp_style(self):
        result = parse_git_url("git@github.com:anthropic/claude-code.git")
        assert result.host == "github.com"
        assert result.owner == "anthropic"
        assert result.repo == "claude-code"

    def test_scp_without_dot_git(self):
        result = parse_git_url("git@github.com:owner/repo")
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_scp_gitlab(self):
        result = parse_git_url("git@gitlab.com:group/project.git")
        assert result.host == "gitlab.com"
        assert result.owner == "group"
        assert result.repo == "project"

    def test_ssh_scheme(self):
        result = parse_git_url("ssh://git@github.com/owner/repo")
        assert result.host == "github.com"
        assert result.owner == "owner"
        assert result.repo == "repo"


class TestHostPrefixed:
    """github.com/owner/repo (no scheme)"""

    def test_github_no_scheme(self):
        result = parse_git_url("github.com/owner/repo")
        assert result.host == "github.com"
        assert result.owner == "owner"
        assert result.repo == "repo"

    def test_gitlab_no_scheme(self):
        result = parse_git_url("gitlab.com/group/project")
        assert result.host == "gitlab.com"


class TestNestedGroups:
    """GitLab nested groups: gitlab.com/group/subgroup/repo"""

    def test_nested_gitlab(self):
        result = parse_git_url("https://gitlab.com/group/subgroup/repo")
        assert result.owner == "group/subgroup"
        assert result.repo == "repo"

    def test_deeply_nested(self):
        result = parse_git_url("https://gitlab.com/a/b/c/repo")
        assert result.owner == "a/b/c"
        assert result.repo == "repo"


class TestAzureDevOps:
    """Azure DevOps URLs"""

    def test_azure_devops(self):
        result = parse_git_url("https://dev.azure.com/org/project/_git/repo")
        assert result.host == "dev.azure.com"
        assert result.owner == "org/project"
        assert result.repo == "repo"


class TestPreferSSH:
    """SSH preference"""

    def test_prefer_ssh_converts_https(self):
        result = parse_git_url("anthropic/claude-code", prefer_ssh=True)
        assert result.clone_url == "git@github.com:anthropic/claude-code.git"

    def test_prefer_ssh_with_full_url(self):
        result = parse_git_url("https://github.com/owner/repo", prefer_ssh=True)
        assert result.clone_url == "git@github.com:owner/repo.git"


class TestCustomDefaultHost:
    """Custom default host"""

    def test_custom_host(self):
        result = parse_git_url("owner/repo", default_host="gitlab.com")
        assert result.host == "gitlab.com"
        assert result.clone_url == "https://gitlab.com/owner/repo.git"


class TestEdgeCases:
    """Edge cases and error handling"""

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty URL"):
            parse_git_url("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty URL"):
            parse_git_url("   ")

    def test_single_word_raises(self):
        with pytest.raises(ValueError, match="expected owner/repo"):
            parse_git_url("linux")

    def test_strips_whitespace(self):
        result = parse_git_url("  anthropic/claude-code  ")
        assert result.owner == "anthropic"
        assert result.repo == "claude-code"
