from __future__ import annotations

import pytest

from netmask_cli import recovery
from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.storage import BackupManager
from netmask_cli.system import RuntimeFailure


def snapshot(name="eth0", kind="static"):
    return InterfaceSnapshot(name, "02:00:00:00:00:01", True, "192.0.2.10/24", kind)


class Changer:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def prepare_dhcp(self, interface):
        self.calls.append(("prepare", interface))

    def restore(self, value):
        self.calls.append(("restore", value.interface))
        if self.fail:
            raise RuntimeError("restore failed")


def test_recovery_rejects_active_daemon(monkeypatch):
    monkeypatch.setattr(recovery, "active_state", lambda: {"pid": 9})
    with pytest.raises(RuntimeFailure, match="still active"):
        recovery.recover()


def test_recovery_restores_all_backups_and_stale_firewall(monkeypatch):
    manager = BackupManager()
    manager.save_once(snapshot("eth0"))
    manager.save_once(snapshot("eth1", "dhcp"))
    changers = []

    def make_backend():
        value = Changer()
        changers.append(value)
        return value

    events = []
    switch = type("Switch", (), {"cleanup": lambda _self: events.append("firewall")})()
    monkeypatch.setattr(recovery, "active_state", lambda: None)
    monkeypatch.setattr(
        recovery,
        "_state",
        lambda: {"interface": "eth0", "firewall_backend": "nftables"},
    )
    monkeypatch.setattr(recovery, "create_kill_switch", lambda *_args: switch)
    monkeypatch.setattr(recovery, "backend", make_backend)
    messages = recovery.recover()
    assert events == ["firewall"]
    assert len(messages) == 3
    assert changers[0].calls == [("restore", "eth0")]
    assert changers[1].calls == [("prepare", "eth1"), ("restore", "eth1")]
    assert manager.all() == {}


def test_recovery_failure_preserves_backup(monkeypatch):
    manager = BackupManager()
    manager.save_once(snapshot())
    monkeypatch.setattr(recovery, "active_state", lambda: None)
    monkeypatch.setattr(recovery, "_state", lambda: None)
    monkeypatch.setattr(recovery, "backend", lambda: Changer(fail=True))
    with pytest.raises(RuntimeFailure, match="Recovery incomplete"):
        recovery.recover("eth0")
    assert manager.load("eth0") is not None


def test_recovery_rejects_unrelated_or_missing_state(monkeypatch):
    monkeypatch.setattr(recovery, "active_state", lambda: None)
    monkeypatch.setattr(
        recovery,
        "_state",
        lambda: {"interface": "eth0", "firewall_backend": "nftables"},
    )
    with pytest.raises(RuntimeFailure, match="No recoverable"):
        recovery.recover("eth9")
