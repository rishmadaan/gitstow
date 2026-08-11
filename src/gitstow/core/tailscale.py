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
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
        if ip_proc.returncode != 0 or not ip_proc.stdout.strip():
            return None
        ip = ip_proc.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        return None

    # MagicDNS name is a bonus: `status --json` can be slow on a large tailnet,
    # and a timeout there must not discard the IP we already have.
    dns_name = ""
    try:
        status_proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
        if status_proc.returncode == 0:
            self_info = json.loads(status_proc.stdout).get("Self") or {}
            dns_name = (self_info.get("DNSName") or "").rstrip(".")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError):
        dns_name = ""
    return TailscaleInfo(ip=ip, dns_name=dns_name)
