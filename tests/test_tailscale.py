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


def test_slow_status_does_not_discard_ip(monkeypatch):
    # `status --json` times out on a large tailnet — the IP we already have stands
    monkeypatch.setattr(tailscale.shutil, "which", lambda _: "/usr/bin/tailscale")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["tailscale", "ip", "-4"]:
            return _completed(stdout="100.101.102.103\n")
        raise subprocess.TimeoutExpired(cmd, 3)

    monkeypatch.setattr(tailscale.subprocess, "run", fake_run)
    info = tailscale.detect_tailscale()
    assert info is not None
    assert info.ip == "100.101.102.103"
    assert info.dns_name == ""
