# SPDX-FileCopyrightText: 2026 rishmadaan
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for URL parser — the most critical module to test."""

import pytest

from gitstow.core.url_parser import parse_git_url


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


class TestAzureAndMultiDotHosts:
    """Schemeless multi-dot hosts parse as hosts; Azure clone URLs use Azure's own forms."""

    AZ_HTTPS = "https://dev.azure.com/org/project/_git/repo"
    AZ_SSH = "git@ssh.dev.azure.com:v3/org/project/repo"
    VS_HTTPS = "https://org.visualstudio.com/project/_git/repo"

    @pytest.mark.parametrize("url, prefer_ssh, host, owner, repo, clone_url", [
        ("dev.azure.com/org/project/_git/repo", False, "dev.azure.com", "org/project", "repo", AZ_HTTPS),
        (AZ_HTTPS, False, "dev.azure.com", "org/project", "repo", AZ_HTTPS),
        (AZ_HTTPS + ".git", False, "dev.azure.com", "org/project", "repo", AZ_HTTPS),
        ("https://org@dev.azure.com/org/project/_git/repo", False, "dev.azure.com", "org/project", "repo", AZ_HTTPS),
        (AZ_HTTPS, True, "dev.azure.com", "org/project", "repo", AZ_SSH),
        ("dev.azure.com/org/project/_git/repo", True, "dev.azure.com", "org/project", "repo", AZ_SSH),
        (AZ_SSH, False, "dev.azure.com", "org/project", "repo", AZ_SSH),
        (AZ_SSH, True, "dev.azure.com", "org/project", "repo", AZ_SSH),
        (VS_HTTPS, False, "org.visualstudio.com", "project", "repo", VS_HTTPS),
        ("org.visualstudio.com/project/_git/repo", False, "org.visualstudio.com", "project", "repo", VS_HTTPS),
        (VS_HTTPS, True, "org.visualstudio.com", "project", "repo", VS_HTTPS),
        ("git.example.co.uk/group/repo", False, "git.example.co.uk", "group", "repo",
         "https://git.example.co.uk/group/repo.git"),
        ("git.example.co.uk/group/sub/repo", False, "git.example.co.uk", "group/sub", "repo",
         "https://git.example.co.uk/group/sub/repo.git"),
        ("owner/repo", False, "github.com", "owner", "repo", "https://github.com/owner/repo.git"),
        ("owner/repo", True, "github.com", "owner", "repo", "git@github.com:owner/repo.git"),
        ("my-org/my.repo", False, "github.com", "my-org", "my.repo", "https://github.com/my-org/my.repo.git"),
    ])
    def test_table(self, url, prefer_ssh, host, owner, repo, clone_url):
        result = parse_git_url(url, prefer_ssh=prefer_ssh)
        assert (result.host, result.owner, result.repo, result.clone_url) == (host, owner, repo, clone_url)

    def test_azure_ssh_and_https_are_the_same_repo(self):
        from gitstow.core.url_parser import same_repo
        assert same_repo(self.AZ_SSH, self.AZ_HTTPS, "github.com")
        assert parse_git_url(self.AZ_SSH).key == parse_git_url(self.AZ_HTTPS).key == "org/project/repo"


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


class TestDeepLinks:
    """Pasted browse URLs (tree/blob/pull/...) must resolve to the repo root."""

    @pytest.mark.parametrize("url,owner,repo", [
        ("https://github.com/anthropics/claude-code/tree/main/src", "anthropics", "claude-code"),
        ("https://github.com/owner/repo/blob/main/README.md", "owner", "repo"),
        ("https://github.com/owner/repo/pull/123", "owner", "repo"),
        ("https://github.com/owner/repo/issues", "owner", "repo"),
        ("https://github.com/owner/repo/releases/tag/v1.0", "owner", "repo"),
        ("github.com/owner/repo/actions", "owner", "repo"),
        ("https://gitlab.com/group/subgroup/repo/-/tree/main", "group/subgroup", "repo"),
        ("https://gitlab.example.com/group/sub/repo/-/blob/main/x.py", "group/sub", "repo"),
    ])
    def test_deep_url_resolves_to_repo_root(self, url, owner, repo):
        parsed = parse_git_url(url)
        assert parsed.owner == owner
        assert parsed.repo == repo
        assert parsed.clone_url.endswith(f"{owner}/{repo}.git")

    def test_gitlab_nested_groups_still_work(self):
        parsed = parse_git_url("https://gitlab.com/group/subgroup/repo")
        assert parsed.owner == "group/subgroup"
        assert parsed.repo == "repo"

    @pytest.mark.parametrize("url,owner,repo", [
        ("https://gitlab.com/mygroup/mysubgroup/src", "mygroup/mysubgroup", "src"),
        ("https://gitlab.com/mygroup/mysubgroup/wiki", "mygroup/mysubgroup", "wiki"),
        ("https://gitlab.com/group/tree/-/blob/main/x.py", "group", "tree"),
    ])
    def test_marker_words_are_valid_repo_names_on_nested_hosts(self, url, owner, repo):
        parsed = parse_git_url(url)
        assert parsed.owner == owner
        assert parsed.repo == repo
