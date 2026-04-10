# Security Policy

## Scope

gitstow is a CLI tool that manages local git repositories. It delegates all git operations (clone, pull, fetch) to git itself and does not handle credentials, tokens, or authentication directly.

## Reporting a Vulnerability

If you discover a security issue, please report it through [GitHub Security Advisories](https://github.com/rishmadaan/gitstow/security/advisories/new) rather than opening a public issue.

You should receive a response within 7 days.

## What Qualifies

- Command injection via crafted repository URLs or names
- Path traversal that writes outside the intended workspace directory
- Unintended execution of code during clone/pull operations
- Information disclosure through error messages or logs

## What Doesn't Qualify

- Git authentication issues (these are handled by git, not gitstow)
- Risks from cloning untrusted repositories (inherent to git itself)
