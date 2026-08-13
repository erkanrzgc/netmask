"""Platform, privilege, process, and subprocess helpers."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


class RuntimeFailure(RuntimeError):
    """A user-facing runtime failure."""


def platform_name() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    raise RuntimeFailure(f"Unsupported platform: {sys.platform}")


def require_admin() -> None:
    if platform_name() == "linux" and os.geteuid() != 0:
        raise RuntimeFailure("Root privileges are required; run this command with sudo.")
    if platform_name() == "windows":
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                raise RuntimeFailure("Administrator privileges are required.")
        except AttributeError:
            return


def run_command(
    command: Sequence[str], *, check: bool = True, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command), capture_output=True, text=True, check=check, timeout=timeout
        )
    except FileNotFoundError as error:
        raise RuntimeFailure(f"Required command not found: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeFailure(f"Command timed out: {' '.join(command)}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "unknown error").strip()
        raise RuntimeFailure(f"Command failed ({' '.join(command)}): {detail}") from error


_INSTALL_HINTS = {
    "ip": "Install iproute2 (Debian/Ubuntu: apt install iproute2; Fedora: dnf install iproute).",
    "dhclient": (
        "Install a DHCP client (Debian/Ubuntu: apt install isc-dhcp-client; "
        "Fedora: dnf install dhcp-client)."
    ),
    "nmcli": (
        "Install NetworkManager (Debian/Ubuntu: apt install network-manager; "
        "Fedora: dnf install NetworkManager)."
    ),
    "networkctl": (
        "Install systemd-networkd/systemd utilities or use another supported DHCP manager."
    ),
    "nft": "Install nftables (Debian/Ubuntu: apt install nftables; Fedora: dnf install nftables).",
    "arping": (
        "Install arping (Debian/Ubuntu: apt install iputils-arping; Fedora: dnf install iputils)."
    ),
    "iptables": (
        "Install iptables (Debian/Ubuntu: apt install iptables; Fedora: dnf install iptables)."
    ),
    "resolvectl": "Optional DNS cache clearing needs systemd-resolved/resolvectl.",
}


def require_commands(commands: Sequence[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        hints = " ".join(_INSTALL_HINTS.get(command, "") for command in missing).strip()
        raise RuntimeFailure(f"Missing required command(s): {', '.join(missing)}. {hints}".strip())


def process_start_token(pid: int) -> str | None:
    """Return Linux process start ticks, which disambiguate reused PIDs."""
    if platform_name() != "linux":
        return None
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields_after_name = value[value.rfind(")") + 2 :].split()
        return fields_after_name[19]
    except (OSError, IndexError):
        return None


def process_matches(pid: int, token: str | None) -> bool:
    if pid <= 0:
        return False
    if platform_name() == "linux":
        actual = process_start_token(pid)
        return actual is not None and actual == token
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
