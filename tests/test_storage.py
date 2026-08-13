from __future__ import annotations

import json
import stat

import pytest

from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.storage import BackupManager, atomic_write_json, read_json, restore_backup
from netmask_cli.system import RuntimeFailure


def snapshot(name="eth0"):
    return InterfaceSnapshot(
        name,
        "02:00:00:00:00:01",
        True,
        "192.0.2.10/24",
        "dhcp",
        [{"dst": "default", "gateway": "192.0.2.1", "metric": 100}],
    )


def test_atomic_write_is_json_and_private(tmp_path):
    target = tmp_path / "nested" / "state.json"
    atomic_write_json(target, {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_read_json_default_and_invalid(tmp_path):
    target = tmp_path / "missing.json"
    assert read_json(target, {"default": 1}) == {"default": 1}
    target.write_text("{")
    with pytest.raises(RuntimeFailure, match="Unable to read"):
        read_json(target)


def test_backup_round_trip_and_all(tmp_path):
    manager = BackupManager(tmp_path / "backup.json")
    manager.save_once(snapshot())
    assert manager.load("eth0") == snapshot()
    assert manager.all() == {"eth0": snapshot()}
    assert manager.load("missing") is None


def test_backup_never_overwrites_original(tmp_path):
    manager = BackupManager(tmp_path / "backup.json")
    manager.save_once(snapshot())
    manager.save_once(InterfaceSnapshot("eth0", "02:00:00:00:00:99", False, None))
    assert manager.load("eth0") == snapshot()


def test_backup_remove(tmp_path):
    manager = BackupManager(tmp_path / "backup.json")
    manager.save_once(snapshot())
    manager.remove("missing")
    manager.remove("eth0")
    assert manager.load("eth0") is None


class RestoreBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.restored = []

    def restore(self, value):
        self.restored.append(value)
        if self.fail:
            raise RuntimeError("restore failed")


def test_restore_removes_backup_only_after_success(tmp_path):
    manager = BackupManager(tmp_path / "backup.json")
    manager.save_once(snapshot())
    changer = RestoreBackend()
    restore_backup("eth0", changer, manager)
    assert changer.restored == [snapshot()]
    assert manager.load("eth0") is None


def test_restore_failure_preserves_backup(tmp_path):
    manager = BackupManager(tmp_path / "backup.json")
    manager.save_once(snapshot())
    with pytest.raises(RuntimeError, match="restore failed"):
        restore_backup("eth0", RestoreBackend(fail=True), manager)
    assert manager.load("eth0") == snapshot()


def test_restore_without_backup(tmp_path):
    with pytest.raises(RuntimeFailure, match="No backup"):
        restore_backup("eth0", RestoreBackend(), BackupManager(tmp_path / "backup.json"))
