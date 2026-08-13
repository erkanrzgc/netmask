"""Configuration paths and runtime defaults."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "netmask"
DEFAULT_INTERVAL = 30
MIN_INTERVAL = 10
DEFAULT_NETMASK = "255.255.255.0"


def config_dir() -> Path:
    """Return the Netmask config directory without touching legacy data."""
    override = os.environ.get("NETMASK_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def backup_file() -> Path:
    return config_dir() / "backup.json"


def daemon_file() -> Path:
    return config_dir() / "daemon.json"


def daemon_lock_file() -> Path:
    return config_dir() / "daemon.lock"


def log_file() -> Path:
    return config_dir() / "netmask.log"
