from __future__ import annotations

from pathlib import Path

from netmask_cli import config


def test_config_override(monkeypatch, tmp_path):
    expected = tmp_path / "isolated"
    monkeypatch.setenv("NETMASK_CONFIG_DIR", str(expected))
    assert config.config_dir() == expected
    assert config.backup_file() == expected / "backup.json"
    assert config.daemon_file() == expected / "daemon.json"
    assert config.daemon_lock_file() == expected / "daemon.lock"
    assert config.log_file() == expected / "netmask.log"


def test_xdg_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("NETMASK_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.config_dir() == tmp_path / "netmask"


def test_windows_appdata_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("NETMASK_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(config.sys, "platform", "win32")
    assert config.config_dir() == Path(tmp_path) / "netmask"
