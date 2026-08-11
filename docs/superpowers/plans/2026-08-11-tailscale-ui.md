# Tailscale Access for `gitstow ui` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `gitstow ui` optionally serves the dashboard on this machine's Tailscale address simultaneously with localhost, enabled by a saved config key or a per-run flag, never binding `0.0.0.0`.

**Architecture:** A new `core/tailscale.py` helper shells out to the `tailscale` CLI for the machine's tailnet IPv4 + MagicDNS name. `web/server.py` gains an `extra_allowed_hostnames` parameter on `create_app()` (widening the anti-DNS-rebinding Host guard) and an `extra_host` parameter on `run()` (dual listening sockets passed to uvicorn). `cli/serve.py` wires them together from a `--tailscale/--no-tailscale` flag falling back to a new `ui_tailscale` Settings field, which `onboard` offers to set when Tailscale is installed. README, CHANGELOG, and the landing page bill it as a core feature.

**Tech Stack:** Python 3, Typer, FastAPI/uvicorn, stdlib `subprocess`/`socket`/`json`/`shutil`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-11-tailscale-ui-design.md`

## Global Constraints

- Never bind `0.0.0.0` — only `127.0.0.1` plus the specific Tailscale IP.
- Tailscale failure (binary missing, daemon down, timeout) must degrade to localhost-only with a warning, never crash.
- All `tailscale` subprocess calls use `timeout=3`.
- Config key is flat snake_case: `ui_tailscale` (matches `prefer_ssh` style).
- The Host-header guard stays on for all requests; it only learns the specific Tailscale IP and MagicDNS name.
- Run tests from the main checkout: `cd ~/labs/projects/gitstow && pytest -q` (worktree gotcha: editable install points at the primary clone; from a worktree use `PYTHONPATH=<worktree>/src .venv/bin/python -m pytest -q`).
- `ruff check src/` must stay clean.
- Keep the full suite green at every commit.

---

### Task 1: Tailscale detection helper

**Files:**
- Create: `src/gitstow/core/tailscale.py`
- Test: `tests/test_tailscale.py`

**Interfaces:**
- Produces: `tailscale_available() -> bool`; `detect_tailscale() -> TailscaleInfo | None` where `TailscaleInfo` is a dataclass with `ip: str` (e.g. `"100.101.102.103"`) and `dns_name: str` (e.g. `"vps.tail1234.ts.net"`, `""` when MagicDNS is off). Tasks 5 and 6 import these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tailscale.py`:

```python
"""Tests for core/tailscale.py — all subprocess calls mocked, no live tailnet."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from gitstow.core import tailscale


def _completed(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_available_true_when_on_path(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")
    assert tailscale.tailscale_available() is True


def test_available_false_when_missing(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: None)
    assert tailscale.tailscale_available() is False


def test_detect_returns_ip_and_dns_name(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["tailscale", "ip", "-4"]:
            return _completed(stdout="100.101.102.103\n")
        if cmd[:2] == ["tailscale", "status"]:
            return _completed(stdout='{"Self": {"DNSName": "vps.tail1234.ts.net."}}')
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)
    info = tailscale.detect_tailscale()
    assert info is not None
    assert info.ip == "100.101.102.103"
    assert info.dns_name == "vps.tail1234.ts.net"  # trailing dot stripped


def test_detect_none_when_binary_missing(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: None)
    assert tailscale.detect_tailscale() is None


def test_detect_none_when_daemon_down(monkeypatch):
    # `tailscale ip` exits non-zero when tailscaled isn't running
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")
    monkeypatch.setattr(
        tailscale.subprocess, "run", lambda cmd, **kw: _completed(returncode=1)
    )
    assert tailscale.detect_tailscale() is None


def test_detect_none_on_timeout(monkeypatch):
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 3)

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)
    assert tailscale.detect_tailscale() is None


def test_detect_survives_bad_status_json(monkeypatch):
    # IP works, status --json returns garbage → ip with empty dns_name
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["tailscale", "ip", "-4"]:
            return _completed(stdout="100.101.102.103\n")
        return _completed(stdout="not json")

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)
    info = tailscale.detect_tailscale()
    assert info is not None
    assert info.ip == "100.101.102.103"
    assert info.dns_name == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tailscale.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gitstow.core.tailscale'`

- [ ] **Step 3: Write the implementation**

Create `src/gitstow/core/tailscale.py`:

```python
"""Tailscale detection — ask the local `tailscale` CLI for this machine's tailnet identity.

Used by `gitstow ui --tailscale` to bind the dashboard on the tailnet address.
Everything degrades to None on failure: callers fall back to localhost-only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

_TIMEOUT = 3  # seconds; the local CLI answers instantly or tailscaled is down


@dataclass
class TailscaleInfo:
    ip: str        # tailnet IPv4, e.g. "100.101.102.103"
    dns_name: str  # MagicDNS name, e.g. "vps.tail1234.ts.net" ("" if MagicDNS off)


def tailscale_available() -> bool:
    """True if the tailscale CLI is installed (says nothing about the daemon)."""
    return shutil.which("tailscale") is not None


def detect_tailscale() -> TailscaleInfo | None:
    """This machine's tailnet identity, or None if Tailscale is unusable."""
    if not tailscale_available():
        return None
    try:
        ip_proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
        if ip_proc.returncode != 0 or not ip_proc.stdout.strip():
            return None
        ip = ip_proc.stdout.strip().splitlines()[0].strip()

        dns_name = ""
        status_proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
        if status_proc.returncode == 0:
            try:
                self_info = json.loads(status_proc.stdout).get("Self") or {}
                dns_name = (self_info.get("DNSName") or "").rstrip(".")
            except (json.JSONDecodeError, AttributeError):
                dns_name = ""
        return TailscaleInfo(ip=ip, dns_name=dns_name)
    except (OSError, subprocess.TimeoutExpired):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tailscale.py -v`
Expected: 7 PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/ && pytest tests/test_tailscale.py -q
git add src/gitstow/core/tailscale.py tests/test_tailscale.py
git commit -m "feat: Tailscale detection helper (core/tailscale.py)"
```

---

### Task 2: `ui_tailscale` Settings field + `config set/show`

**Files:**
- Modify: `src/gitstow/core/config.py` (Settings dataclass, `to_dict`, `from_dict`)
- Modify: `src/gitstow/cli/config_cmd.py` (`config_show` rows, `config_set` valid keys + bool parsing)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings.ui_tailscale: bool` (default `False`), round-tripped through YAML. `gitstow config set ui_tailscale true|false`. Tasks 5 and 6 read/write this field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
class TestUiTailscaleSetting:
    def test_default_false(self):
        from gitstow.core.config import Settings
        assert Settings().ui_tailscale is False

    def test_round_trips_through_dict(self):
        from gitstow.core.config import Settings
        s = Settings(ui_tailscale=True)
        assert s.to_dict()["ui_tailscale"] is True
        assert Settings.from_dict(s.to_dict()).ui_tailscale is True

    def test_from_dict_missing_key_defaults_false(self):
        from gitstow.core.config import Settings
        assert Settings.from_dict({}).ui_tailscale is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k UiTailscale -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'ui_tailscale'` (or AttributeError/KeyError variants)

- [ ] **Step 3: Implement the Settings field**

In `src/gitstow/core/config.py`:

Add to the `Settings` dataclass after `clone_timeout: int = 300`:

```python
    ui_tailscale: bool = False  # serve `gitstow ui` on the tailnet address too
```

In `to_dict()`, add after `"clone_timeout": self.clone_timeout,`:

```python
            "ui_tailscale": self.ui_tailscale,
```

In `from_dict()`, add after `clone_timeout=data.get("clone_timeout", 300),`:

```python
            ui_tailscale=data.get("ui_tailscale", False),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: all PASS (new + existing)

- [ ] **Step 5: Wire `config set` and `config show`**

In `src/gitstow/cli/config_cmd.py`:

`config_show` — add to the `rows` list after the `clone_timeout` row:

```python
        ("ui_tailscale", str(settings.ui_tailscale).lower()),
```

`config_set` — three edits:

1. Argument help text: `key: str = typer.Argument(help="Setting key (default_host, prefer_ssh, parallel_limit, clone_timeout, ui_tailscale).")`
2. `valid_keys = {"default_host", "prefer_ssh", "parallel_limit", "clone_timeout", "ui_tailscale"}`
3. Reuse the existing bool branch — change `if key == "prefer_ssh":` to:

```python
    if key in ("prefer_ssh", "ui_tailscale"):
        if value.lower() in ("true", "yes", "1"):
            setattr(settings, key, True)
        elif value.lower() in ("false", "no", "0"):
            setattr(settings, key, False)
        else:
            err_console.print(f"[red]Error:[/red] {key} must be true or false.")
            raise typer.Exit(code=1)
```

(The old branch's error message said `prefer_ssh must be true or false.` — the f-string keeps that exact text for prefer_ssh.)

Also add an example line to the `config_set` docstring examples block: `gitstow config set ui_tailscale true`.

- [ ] **Step 6: Add a CLI-level test**

Append to `tests/test_config.py` (match the file's existing CliRunner/monkeypatch conventions — if it has no CLI tests, use this standalone form, monkeypatching load/save):

```python
class TestConfigSetUiTailscale:
    def test_set_true(self, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli import config_cmd
        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        saved = []
        monkeypatch.setattr(config_cmd, "load_config", lambda: Settings())
        monkeypatch.setattr(config_cmd, "save_config", saved.append)

        result = CliRunner().invoke(app, ["config", "set", "ui_tailscale", "true"])
        assert result.exit_code == 0
        assert saved[0].ui_tailscale is True

    def test_set_garbage_rejected(self, monkeypatch):
        from typer.testing import CliRunner

        from gitstow.cli import config_cmd
        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        monkeypatch.setattr(config_cmd, "load_config", lambda: Settings())
        result = CliRunner().invoke(app, ["config", "set", "ui_tailscale", "maybe"])
        assert result.exit_code == 1
```

- [ ] **Step 7: Run full config tests, lint, commit**

```bash
pytest tests/test_config.py -q && ruff check src/
git add src/gitstow/core/config.py src/gitstow/cli/config_cmd.py tests/test_config.py
git commit -m "feat: ui_tailscale setting — config set/show support"
```

---

### Task 3: Host guard learns extra hostnames

**Files:**
- Modify: `src/gitstow/web/server.py` (`create_app` signature + middleware)
- Test: `tests/test_serve.py` (append a new test class)

**Interfaces:**
- Consumes: nothing new.
- Produces: `create_app(extra_allowed_hostnames: set[str] | None = None) -> FastAPI`. Task 4's `run()` passes the set through; Task 5 builds it from `TailscaleInfo`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_serve.py` (reuse the file's existing `configured` fixture; build clients locally since the guard set varies per test):

```python
class TestTailscaleHostGuard:
    """The anti-rebinding guard must accept exactly the configured tailnet names."""

    TS_IP = "100.101.102.103"
    TS_DNS = "vps.tail1234.ts.net"

    def _client(self, extra=None):
        app = create_app(extra_allowed_hostnames=extra)
        return TestClient(app, base_url="http://127.0.0.1")

    def test_tailscale_host_rejected_by_default(self, configured):
        client = self._client()
        r = client.get("/", headers={"host": f"{self.TS_IP}:7853"})
        assert r.status_code == 403

    def test_tailscale_ip_accepted_when_configured(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": f"{self.TS_IP}:7853"})
        assert r.status_code == 200

    def test_magicdns_host_accepted_when_configured(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": f"{self.TS_DNS}:7853"})
        assert r.status_code == 200

    def test_unknown_host_still_rejected(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": "evil.example.com"})
        assert r.status_code == 403

    def test_localhost_still_accepted(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.get("/", headers={"host": "127.0.0.1:7853"})
        assert r.status_code == 200

    def test_cross_origin_post_from_tailnet_origin_accepted(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        # POST to a route that exists; guard runs before routing, but use a real
        # one anyway — /shutdown flips should_exit on a stashed server.
        r = client.post(
            "/repos/fetch-all",
            headers={"host": f"{self.TS_DNS}:7853", "origin": f"http://{self.TS_DNS}:7853"},
        )
        assert r.status_code != 403

    def test_cross_origin_post_from_unknown_origin_rejected(self, configured):
        client = self._client(extra={self.TS_IP, self.TS_DNS})
        r = client.post(
            "/repos/fetch-all",
            headers={"host": f"{self.TS_IP}:7853", "origin": "http://evil.example.com"},
        )
        assert r.status_code == 403
```

Note: if the `configured` fixture's dashboard renders a non-200 for some other reason, assert `!= 403` instead — the guard verdict is what's under test. Check what the existing `test_dashboard_empty` asserts and match it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_serve.py -k TailscaleHostGuard -v`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'extra_allowed_hostnames'`

- [ ] **Step 3: Implement**

In `src/gitstow/web/server.py`, change `create_app`:

```python
def create_app(extra_allowed_hostnames: set[str] | None = None) -> FastAPI:
    """Construct the FastAPI app and register routes + static files.

    extra_allowed_hostnames widens the Host/Origin guard — used for the
    machine's own Tailscale IP and MagicDNS name. Everything else still 403s.
    """
```

Inside, before the middleware definition, add:

```python
    allowed_hostnames = _ALLOWED_HOSTNAMES | (extra_allowed_hostnames or set())
```

and replace both `_ALLOWED_HOSTNAMES` references inside `_reject_rebind_and_cross_origin` with `allowed_hostnames`. The module-level `_ALLOWED_HOSTNAMES` constant stays as the base set.

Update the comment block above `_ALLOWED_HOSTNAMES` (lines 50–57) — append one line:

```python
# When Tailscale serving is enabled, the machine's own tailnet IP/MagicDNS
# name are added per-app via create_app(extra_allowed_hostnames=...).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_serve.py -v`
Expected: all PASS (new class + all existing serve tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/ && pytest tests/test_serve.py -q
git add src/gitstow/web/server.py tests/test_serve.py
git commit -m "feat: host guard accepts configured tailnet hostnames"
```

---

### Task 4: Dual-socket `run()`

**Files:**
- Modify: `src/gitstow/web/server.py` (`run()` + new `_bind_socket` helper, `socket` import)
- Test: `tests/test_serve.py` (append)

**Interfaces:**
- Consumes: `create_app(extra_allowed_hostnames=...)` from Task 3.
- Produces: `run(host="127.0.0.1", port=7853, open_browser=True, extra_host: str | None = None, extra_allowed_hostnames: set[str] | None = None)`. When `extra_host` is set, the server listens on both `host` and `extra_host` on the same port. Task 5 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_serve.py`:

```python
class TestDualSocketRun:
    def test_bind_socket_binds_requested_host(self):
        from gitstow.web import server as server_mod

        sock = server_mod._bind_socket("127.0.0.1", 0)
        try:
            addr, port = sock.getsockname()
            assert addr == "127.0.0.1"
            assert port > 0
        finally:
            sock.close()

    def test_run_with_extra_host_passes_two_sockets(self, monkeypatch):
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {}

        def fake_run(self, sockets=None):
            captured["sockets"] = sockets

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        # extra_host=127.0.0.1 with port 0: two distinct ephemeral binds — fine
        # for asserting socket plumbing without a live tailnet.
        server_mod.run(port=0, open_browser=False, extra_host="127.0.0.1")
        assert captured["sockets"] is not None
        assert len(captured["sockets"]) == 2
        for s in captured["sockets"]:
            s.close()

    def test_run_without_extra_host_passes_no_sockets(self, monkeypatch):
        import uvicorn

        from gitstow.web import server as server_mod

        captured = {"called": False}

        def fake_run(self, sockets=None):
            captured["called"] = True
            captured["sockets"] = sockets

        monkeypatch.setattr(uvicorn.Server, "run", fake_run)
        server_mod.run(port=0, open_browser=False)
        assert captured["called"] is True
        assert captured["sockets"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_serve.py -k DualSocket -v`
Expected: FAIL — `AttributeError: module 'gitstow.web.server' has no attribute '_bind_socket'`

- [ ] **Step 3: Implement**

In `src/gitstow/web/server.py`:

Add `import socket` to the stdlib imports.

Add above `run()`:

```python
def _bind_socket(host: str, port: int) -> socket.socket:
    """Create a bound TCP socket the way uvicorn's own Config.bind_socket does.

    uvicorn accepts pre-bound sockets via Server.run(sockets=...); asyncio
    calls listen() itself when the server starts.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock
```

Change `run()`:

```python
def run(
    host: str = "127.0.0.1",
    port: int = 7853,
    open_browser: bool = True,
    extra_host: str | None = None,
    extra_allowed_hostnames: set[str] | None = None,
) -> None:
    """Run the gitstow server. Blocks until Ctrl+C or /shutdown.

    Constructs uvicorn.Config + Server explicitly (not uvicorn.run) so the
    Server instance can be stashed on app.state for the /shutdown route.
    With extra_host set (Tailscale), listens on host AND extra_host on the
    same port via two pre-bound sockets.
    """
    app = create_app(extra_allowed_hostnames=extra_allowed_hostnames)

    if open_browser:
        @app.on_event("startup")
        async def _open_browser_on_start() -> None:
            webbrowser.open(f"http://{host}:{port}")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
    )
    server = uvicorn.Server(config)
    app.state.server = server
    if extra_host:
        sockets = [_bind_socket(host, port), _bind_socket(extra_host, port)]
        server.run(sockets=sockets)
    else:
        server.run()
```

Also update the module docstring (lines 1–6): it currently says "Binds to 127.0.0.1 only" — change that sentence to "Binds 127.0.0.1, plus optionally the machine's own Tailscale address (never 0.0.0.0)."

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_serve.py -q`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/
git add src/gitstow/web/server.py tests/test_serve.py
git commit -m "feat: run() serves localhost + an extra host via dual sockets"
```

---

### Task 5: `gitstow ui --tailscale` flag + wiring

**Files:**
- Modify: `src/gitstow/cli/serve.py`
- Test: `tests/test_serve.py` (append)

**Interfaces:**
- Consumes: `detect_tailscale()` / `TailscaleInfo` (Task 1), `Settings.ui_tailscale` (Task 2), `run(..., extra_host=, extra_allowed_hostnames=)` (Task 4).
- Produces: user-facing `--tailscale/--no-tailscale` flag; precedence flag > config > off.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_serve.py`:

```python
class TestUiTailscaleFlag:
    """CLI wiring: flag > config > off; graceful fallback when detection fails."""

    def _invoke(self, monkeypatch, args, ui_tailscale_cfg=False, detect_result="ok"):
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings
        from gitstow.core.tailscale import TailscaleInfo

        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("gitstow.web.server.run", fake_run)
        monkeypatch.setattr(
            "gitstow.cli.serve.load_config",
            lambda: Settings(ui_tailscale=ui_tailscale_cfg),
        )
        info = (
            TailscaleInfo(ip="100.101.102.103", dns_name="vps.tail1234.ts.net")
            if detect_result == "ok"
            else None
        )
        monkeypatch.setattr("gitstow.cli.serve.detect_tailscale", lambda: info)
        result = CliRunner().invoke(app, ["ui", "--no-browser", *args])
        return result, captured

    def test_flag_enables_tailscale(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, ["--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_host"] == "100.101.102.103"
        assert captured["extra_allowed_hostnames"] == {
            "100.101.102.103", "vps.tail1234.ts.net",
        }

    def test_config_enables_tailscale_without_flag(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, [], ui_tailscale_cfg=True)
        assert result.exit_code == 0
        assert captured["extra_host"] == "100.101.102.103"

    def test_no_tailscale_flag_overrides_config(self, monkeypatch):
        result, captured = self._invoke(
            monkeypatch, ["--no-tailscale"], ui_tailscale_cfg=True
        )
        assert result.exit_code == 0
        assert captured["extra_host"] is None

    def test_default_is_localhost_only(self, monkeypatch):
        result, captured = self._invoke(monkeypatch, [])
        assert result.exit_code == 0
        assert captured["extra_host"] is None

    def test_detection_failure_falls_back_to_localhost(self, monkeypatch):
        result, captured = self._invoke(
            monkeypatch, ["--tailscale"], detect_result="fail"
        )
        assert result.exit_code == 0  # must NOT crash
        assert captured["extra_host"] is None

    def test_tailscale_url_printed(self, monkeypatch):
        result, _ = self._invoke(monkeypatch, ["--tailscale"])
        assert "vps.tail1234.ts.net" in result.output

    def test_dns_nameless_tailnet_uses_ip(self, monkeypatch):
        from gitstow.core.tailscale import TailscaleInfo

        monkeypatch_info = TailscaleInfo(ip="100.101.102.103", dns_name="")
        from typer.testing import CliRunner

        from gitstow.cli.main import app
        from gitstow.core.config import Settings

        captured = {}
        monkeypatch.setattr("gitstow.web.server.run", lambda **kw: captured.update(kw))
        monkeypatch.setattr(
            "gitstow.cli.serve.load_config", lambda: Settings()
        )
        monkeypatch.setattr(
            "gitstow.cli.serve.detect_tailscale", lambda: monkeypatch_info
        )
        result = CliRunner().invoke(app, ["ui", "--no-browser", "--tailscale"])
        assert result.exit_code == 0
        assert captured["extra_allowed_hostnames"] == {"100.101.102.103"}
        assert "100.101.102.103" in result.output
```

Note: these tests monkeypatch `gitstow.cli.serve.load_config` and `gitstow.cli.serve.detect_tailscale`, so the implementation must import them at module top level (`from gitstow.core.config import load_config`, `from gitstow.core.tailscale import detect_tailscale`) — not inside the function. The `run` import stays late (inside `ui()`) for the existing ImportError message, so it is patched at its source: `gitstow.web.server.run`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_serve.py -k UiTailscaleFlag -v`
Expected: FAIL — `Error: No such option: --tailscale` (exit code 2) or AttributeError on `gitstow.cli.serve.load_config`

- [ ] **Step 3: Implement**

Rewrite `src/gitstow/cli/serve.py`:

```python
"""gitstow ui — launch the local web dashboard."""

from __future__ import annotations

import typer
from rich.console import Console

from gitstow.core.config import load_config
from gitstow.core.tailscale import detect_tailscale

console = Console()
err_console = Console(stderr=True)


def ui(
    port: int = typer.Option(7853, "--port", "-p", help="Port to bind."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't auto-open a browser window."
    ),
    tailscale: bool | None = typer.Option(
        None,
        "--tailscale/--no-tailscale",
        help="Also serve on this machine's Tailscale address "
        "(default: the ui_tailscale config setting).",
    ),
) -> None:
    """[bold cyan]ui[/bold cyan] — launch the gitstow web dashboard.

    Runs an HTTP server at [bold]http://127.0.0.1:PORT[/bold] — and, with
    [bold]--tailscale[/bold] (or [bold]config set ui_tailscale true[/bold]),
    simultaneously on this machine's Tailscale address so other devices on
    your tailnet can reach it. Never binds 0.0.0.0.
    Press [bold]Ctrl+C[/bold] to stop, or click [bold]Shutdown[/bold] in
    the UI footer. The browser opens automatically unless
    [bold]--no-browser[/bold] is given.
    """
    try:
        from gitstow.web import server as web_server
    except ImportError as exc:
        err_console.print(
            f"[red]Error:[/red] Web dependencies not installed: {exc}\n"
            "The dashboard ships with gitstow by default, so this usually means a\n"
            "partial or outdated install. Reinstall with:\n"
            "  [bold]pip install --upgrade gitstow[/bold]   (or: pipx upgrade gitstow)"
        )
        raise typer.Exit(code=1)

    want_tailscale = tailscale if tailscale is not None else load_config().ui_tailscale
    extra_host: str | None = None
    extra_allowed: set[str] | None = None
    ts_url: str | None = None
    if want_tailscale:
        info = detect_tailscale()
        if info is None:
            err_console.print(
                "[yellow]⚠ Tailscale not reachable[/yellow] — is tailscaled running? "
                "Serving localhost only."
            )
        else:
            extra_host = info.ip
            extra_allowed = {info.ip} | ({info.dns_name} if info.dns_name else set())
            ts_url = f"http://{info.dns_name or info.ip}:{port}"

    console.print(
        f"[dim]starting[/dim] [bold]http://127.0.0.1:{port}[/bold] "
        + (f"[dim]+[/dim] [bold]{ts_url}[/bold] " if ts_url else "")
        + "[dim]— Ctrl+C to stop[/dim]"
    )
    try:
        web_server.run(
            port=port,
            open_browser=not no_browser,
            extra_host=extra_host,
            extra_allowed_hostnames=extra_allowed,
        )
    except OSError as exc:
        if "Address already in use" in str(exc) or "address already in use" in str(exc).lower():
            err_console.print(
                f"[red]Error:[/red] Port {port} is already in use.\n"
                f"Try another port: [bold]gitstow ui --port {port + 1}[/bold]"
            )
            raise typer.Exit(code=1)
        raise
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/dim]")
```

(Note the import change: `from gitstow.web import server as web_server` + `web_server.run(...)` — this keeps the late-import error message AND makes `monkeypatch.setattr("gitstow.web.server.run", ...)` effective, since the function is now resolved at call time through the module object.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_serve.py -q`
Expected: all PASS

- [ ] **Step 5: Lint, full suite, commit**

```bash
ruff check src/ && pytest -q
git add src/gitstow/cli/serve.py tests/test_serve.py
git commit -m "feat: gitstow ui --tailscale — serve the dashboard on the tailnet"
```

---

### Task 6: Onboard prompt

**Files:**
- Modify: `src/gitstow/cli/onboard.py`
- Test: `tests/test_onboard.py` (append)

**Interfaces:**
- Consumes: `tailscale_available()` (Task 1), `Settings.ui_tailscale` (Task 2).
- Produces: `_maybe_prompt_tailscale(settings: Settings) -> None` — mutates `settings.ui_tailscale`; called from `onboard()` between the SSH step and `save_config`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_onboard.py` (this file already monkeypatches `onboard_module.bconfirm` — same pattern):

```python
def test_tailscale_prompt_shown_and_saved_when_available(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: True)
    monkeypatch.setattr(onboard_module, "bconfirm", lambda *a, **kw: True)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is True


def test_tailscale_prompt_declined(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: True)
    monkeypatch.setattr(onboard_module, "bconfirm", lambda *a, **kw: False)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is False


def test_tailscale_prompt_skipped_when_not_installed(monkeypatch):
    from gitstow.cli import onboard as onboard_module
    from gitstow.core.config import Settings

    monkeypatch.setattr(onboard_module, "tailscale_available", lambda: False)

    def boom(*a, **kw):
        raise AssertionError("prompt must not be shown without tailscale installed")

    monkeypatch.setattr(onboard_module, "bconfirm", boom)
    settings = Settings()
    onboard_module._maybe_prompt_tailscale(settings)
    assert settings.ui_tailscale is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_onboard.py -k tailscale -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'tailscale_available'` (or `_maybe_prompt_tailscale`)

- [ ] **Step 3: Implement**

In `src/gitstow/cli/onboard.py`:

Add to imports:

```python
from gitstow.core.tailscale import tailscale_available
```

Add this function after `_setup_workspace`:

```python
def _maybe_prompt_tailscale(settings: Settings) -> None:
    """Offer tailnet dashboard access — only when the tailscale CLI is installed."""
    if not tailscale_available():
        return
    console.print("  [bold]4. Web dashboard over Tailscale[/bold]")
    console.print(
        "     Make [cyan]gitstow ui[/cyan] reachable from other devices on your "
        "tailnet (never the public internet)."
    )
    console.print()
    enable = bconfirm(
        "     Enable Tailscale access for the web dashboard?", default_is_yes=False
    )
    settings.ui_tailscale = bool(enable)
    console.print(f"     → {'enabled' if settings.ui_tailscale else 'disabled'}\n")
```

In `onboard()`, call it between the SSH step and the save. After the line `console.print(f"     → {proto}\n")` and before `# Save config`, insert:

```python
    # 4. Tailscale dashboard access (skipped silently if tailscale isn't installed)
    _maybe_prompt_tailscale(settings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_onboard.py -v`
Expected: all PASS (new + existing — the existing full-flow test monkeypatches `bconfirm` already; if it asserts an exact number of confirm calls, update it to account for the possible extra prompt, or monkeypatch `tailscale_available` to `False` in that test to keep it deterministic across machines. Prefer the latter — machine-independent.)

- [ ] **Step 5: Lint, commit**

```bash
ruff check src/ && pytest tests/test_onboard.py -q
git add src/gitstow/cli/onboard.py tests/test_onboard.py
git commit -m "feat: onboard offers Tailscale dashboard access when tailscale is installed"
```

---

### Task 7: README + CHANGELOG

**Files:**
- Modify: `README.md` (`### Browser Dashboard` section, `## Configuration` section)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

**Interfaces:** none — documentation only. No tests; verification is reading the rendered diff.

- [ ] **Step 1: Update README `### Browser Dashboard`**

Replace the section's code block and the sentence about binding. Current code block gains one line; the prose sentence "Binds `127.0.0.1` only (arbitrary git execution must not be LAN-reachable)." is replaced. New section content:

````markdown
### Browser Dashboard
```bash
gitstow ui                  # opens http://127.0.0.1:7853 in your browser
gitstow ui --port 8080
gitstow ui --no-browser
gitstow ui --tailscale      # also serve on your Tailscale address (VPS-friendly)
```
Persistent local web dashboard for daily repo management — a tab you leave open. Shows dirty state across your library, pulls single repos or all-of-them with a parallel summary panel, adds/removes repos, edits tags, freezes stale ones. The repo detail page has a Changes section — staged/unstaged/untracked groups, click a file to expand its colored line diff. Auto-refreshes row status every 30 seconds. Click **Shutdown** in the footer — or Ctrl+C — to stop.

Private by default: binds `127.0.0.1` (arbitrary git execution must not be LAN-reachable). Running gitstow on a VPS or home server? Enable **Tailscale access** and the dashboard is also served on your tailnet address — open it from any of your devices, encrypted by WireGuard, invisible to the public internet. It never binds `0.0.0.0`:
```bash
gitstow config set ui_tailscale true   # every gitstow ui from now on
gitstow ui --tailscale                 # or per-run
```
If Tailscale isn't running, `gitstow ui` warns and serves localhost only.
````

- [ ] **Step 2: Update README `## Configuration`**

In the `config.yaml` example block, add after `parallel_limit: 6`:

```yaml
ui_tailscale: false   # serve `gitstow ui` on your Tailscale address too
```

- [ ] **Step 3: CHANGELOG entry**

Under `## [Unreleased]`, add an `### Added` section above the existing `### Changed` (Keep a Changelog order: Added before Changed):

```markdown
### Added

- **Tailscale access for the dashboard** — `gitstow ui --tailscale` (or `gitstow config set ui_tailscale true`, also offered during `gitstow onboard` when Tailscale is installed) serves the dashboard on the machine's Tailscale address alongside localhost. VPS-friendly: reachable from any device on your tailnet, never bound to `0.0.0.0`, and the anti-rebinding Host guard only admits the machine's own tailnet IP and MagicDNS name. Falls back to localhost-only with a warning when Tailscale isn't running.
```

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: Tailscale dashboard access in README + changelog"
```

---

### Task 8: Landing page — core-feature billing

**Files:**
- Modify: `site/index.html` (`#dashboard` section copy, `#features` grid)

**Interfaces:** none — static site. Note: pushing this to `main` deploys gitstow.com automatically (`site/**` triggers Pages). That is expected — the feature ships in the same merge.

- [ ] **Step 1: Update the `#dashboard` section copy**

Current paragraph (in `<section id="dashboard">`):

```html
<p>A local-first browser dashboard, and the primary way to live with your library. Dirty state at a glance, one-click pulls, tags, freezes, and per-file diffs. It auto-refreshes every 30 seconds and never phones home: <code class="code-chip">127.0.0.1</code> only.</p>
```

Replace with:

```html
<p>A local-first browser dashboard, and the primary way to live with your library. Dirty state at a glance, one-click pulls, tags, freezes, and per-file diffs. It auto-refreshes every 30 seconds and never phones home: <code class="code-chip">127.0.0.1</code> by default — or flip on Tailscale access and open it from any device on your tailnet. Never the public internet.</p>
```

- [ ] **Step 2: Add the feature card**

In the `#features` grid (`<div class="features">`), add a ninth `fcard` after the "Shell sugar" card, matching the existing one-line format exactly:

```html
      <div class="fcard"><div class="glyph">⇶</div><div class="title">Tailscale access</div><div class="body">Run gitstow on a VPS, open the dashboard from your laptop. Your tailnet only — never 0.0.0.0.</div><code>gitstow ui --tailscale</code></div>
```

- [ ] **Step 3: Verify locally**

Run: `open site/index.html` (or `python3 -m http.server -d site 8000` and browse) — confirm the new card renders in the grid without breaking the layout (the grid is CSS-auto-flowing; 9 cards leaves one row uneven, which matches how 8 cards already flow at narrow widths — acceptable; flag to the user if it looks off at desktop width).

- [ ] **Step 4: Commit**

```bash
git add site/index.html
git commit -m "site: Tailscale access — dashboard copy + feature card"
```

---

### Task 9: Full verification

**Files:** none new.

- [ ] **Step 1: Full suite + lint**

```bash
cd ~/labs/projects/gitstow && pytest -q && ruff check src/
```
Expected: all tests pass, lint clean.

- [ ] **Step 2: Live smoke test (this machine has Tailscale? check first)**

```bash
tailscale ip -4 2>/dev/null || echo "no tailscale here — skip live check, note it in the report"
```

If Tailscale is present: run `gitstow ui --tailscale --no-browser --port 7899`, then from another terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://$(tailscale ip -4 | head -1):7899/"   # expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7899/                        # expect 200
curl -s -o /dev/null -w "%{http_code}\n" -H "Host: evil.example.com" http://127.0.0.1:7899/  # expect 403
```

Kill the server afterward. If no Tailscale on the dev machine, state that clearly in the final report — per project standards the real-tailnet browser check happens on the VPS and must be listed as "needs the user".

- [ ] **Step 3: Report**

Report with the three buckets: what is done, what is left, what needs the user (e.g., the on-VPS live check; deploying is merging to main).
