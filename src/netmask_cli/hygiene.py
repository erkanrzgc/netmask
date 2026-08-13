"""Narrow, network-scoped cache hygiene operations."""

from __future__ import annotations

import shutil

from .system import run_command


def flush_arp(interface: str) -> None:
    run_command(["ip", "neigh", "flush", "dev", interface])


def flush_dns() -> bool:
    """Flush systemd-resolved when available; absence is safe and non-fatal."""
    if shutil.which("resolvectl") is None:
        return False
    run_command(["resolvectl", "flush-caches"])
    return True


def run_network_hygiene(interface: str) -> None:
    flush_arp(interface)
    flush_dns()
