# Tailscale access for `gitstow ui` — Design

**Date:** 2026-08-11
**Status:** Approved direction (config + flag enablement), pending spec review

## Problem

`gitstow ui` binds 127.0.0.1 only. On a headless VPS the dashboard is
unreachable from the user's other machines. Users on a Tailscale network
want the dashboard reachable over the tailnet — the way OpenClaw and
Hermes expose their local UIs — without ever exposing it to the public
internet.

Two things block this today, and both must change together:

1. `cli/serve.py` hard-wires host `127.0.0.1` (the `run()` function in
   `web/server.py` already accepts a host, but the CLI never exposes it).
2. The anti-DNS-rebinding middleware in `web/server.py` rejects any
   request whose `Host` header isn't in `{127.0.0.1, localhost, ::1}` —
   so even a forced bind would 403 every Tailscale request.

## Decision

Bind the machine's Tailscale IP (100.64.0.0/10) directly, alongside
localhost, when enabled. Never bind `0.0.0.0`.

Rejected alternatives:

- **`tailscale serve` proxy** — the Host-header guard still 403s proxied
  requests, so code changes are needed anyway; adds a second component to
  configure and keep running for zero savings.
- **Bind `0.0.0.0`** — the dashboard executes git and deletes
  directories with no authentication; public exposure on a VPS is
  unacceptable.

Security model: Tailscale traffic is WireGuard-encrypted, so plain HTTP
is fine. Anyone on the user's tailnet can reach the dashboard — the same
trust model OpenClaw uses. The Host-header guard stays on for everything
else.

## Changes

### 1. `core/config.py` — Settings

New field: `ui_tailscale: bool = False`. Settable via
`gitstow config set ui_tailscale true` (flat snake_case key, matching
`default_host` / `prefer_ssh`).

### 2. Tailscale detection helper (`core/`)

Small helper that shells out to the `tailscale` CLI:

- IPv4 address: `tailscale ip -4`
- MagicDNS name: `tailscale status --json` → `Self.DNSName`
  (trailing dot stripped)

Failure (binary missing, daemon down, timeout) returns None-equivalent;
callers degrade gracefully. No third-party dependency, no interface
scanning.

### 3. `web/server.py` — guard + dual bind

- `_ALLOWED_HOSTNAMES` becomes a base set; `create_app()` accepts extra
  allowed hostnames (the Tailscale IP and MagicDNS name). The middleware
  logic is otherwise unchanged; POST Origin checks use the same set.
- `run()` accepts the extra host. When present, it builds **two listening
  sockets** (127.0.0.1 and the Tailscale IP, same port) and passes them
  to `uvicorn.Server.run(sockets=...)` — localhost and tailnet served
  simultaneously by one server. Single-host behavior is unchanged when no
  extra host is given.

### 4. `cli/serve.py` — flag + startup output

- `--tailscale/--no-tailscale` flag, default None → falls back to
  `settings.ui_tailscale`.
- When enabled and detection succeeds: startup message prints both URLs
  (localhost + `http://<magicdns>:<port>`, IP as fallback).
- When enabled but detection fails: one-line warning, serve
  localhost-only, do not crash. A VPS session must never die because
  tailscaled is down.

### 5. `cli/onboard.py` — setup prompt

Ask "Enable Tailscale access for the web dashboard?" (default No) **only
when the `tailscale` binary is found** on PATH. Writes `ui_tailscale`
into settings. Users without Tailscale never see the question.

### 6. Documentation — core-feature billing

- **README:**
  - `### Browser Dashboard` section: add Tailscale access as a headline
    capability (open the dashboard from any device on your tailnet;
    VPS-friendly), with `gitstow ui --tailscale` and
    `gitstow config set ui_tailscale true` examples.
  - `## Configuration` section: document the `ui_tailscale` key.
- **Landing page (`site/index.html`):**
  - `#dashboard` section copy currently says "never phones home:
    `127.0.0.1` only" — update to reflect the new truth: private by
    default, optionally reachable over your Tailscale network, never the
    public internet.
  - Add a feature card to the `#features` grid (glyph + title + body +
    command chip, matching the existing eight cards), e.g. title
    "Tailscale access", command `gitstow ui --tailscale`.
  - Site deploys automatically on merge to main (`site/**` push).
- **CHANGELOG:** entry under the next version.

## Error handling

| Condition | Behavior |
| --- | --- |
| Tailscale enabled, CLI missing/daemon down | Warn once, serve localhost-only |
| `tailscale` subprocess hangs | Short timeout (~3s), treat as failure |
| Port in use | Existing error message unchanged |
| Request with unknown Host header | 403, unchanged |

## Testing

- Guard unit tests: Tailscale IP/MagicDNS Host accepted when configured;
  still 403 when not configured; unknown hosts always 403.
- Detection helper: mocked subprocess for success / missing binary /
  daemon-down output.
- Dual-socket `run()`: covered by a smoke test asserting two sockets are
  built when an extra host is given (no live tailnet in CI).
- Existing dashboard TestClient suite stays green.
- Per project standards: verify the real flow in a browser over an
  actual tailnet once implemented (HTTP-level tests are necessary, not
  sufficient).

## Out of scope

- Authentication on the dashboard (tailnet trust model instead).
- HTTPS / `tailscale serve` integration.
- Funnel (public internet exposure) — explicitly never.
