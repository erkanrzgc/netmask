from __future__ import annotations

import pytest

from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.system import RuntimeFailure
from netmask_cli.transaction import apply_transaction


def snapshot():
    return InterfaceSnapshot("eth0", "02:00:00:00:00:01", True, "192.0.2.10/24")


class Changer:
    def __init__(self, rollback_fails=False):
        self.restored = []
        self.rollback_fails = rollback_fails

    def restore(self, value):
        self.restored.append(value)
        if self.rollback_fails:
            raise RuntimeError("rollback broke")


class Backups:
    def __init__(self, created=True):
        self.created = created
        self.saved = []
        self.removed = []

    def save_once(self, value):
        self.saved.append(value)
        return self.created

    def remove(self, interface):
        self.removed.append(interface)


def fail():
    raise RuntimeError("operation broke")


def test_success_keeps_backup_for_manual_restore():
    backups = Backups()
    changer = Changer()
    calls = []
    apply_transaction(snapshot(), changer, [lambda: calls.append("ok")], backups=backups)
    assert calls == ["ok"]
    assert backups.saved == [snapshot()]
    assert backups.removed == []
    assert changer.restored == []


def test_failure_rolls_back_and_removes_new_backup():
    backups = Backups(created=True)
    changer = Changer()
    with pytest.raises(RuntimeFailure, match="pre-command interface state was restored"):
        apply_transaction(snapshot(), changer, [fail], backups=backups)
    assert changer.restored == [snapshot()]
    assert backups.removed == ["eth0"]


def test_failure_preserves_preexisting_backup():
    backups = Backups(created=False)
    with pytest.raises(RuntimeFailure):
        apply_transaction(snapshot(), Changer(), [fail], backups=backups)
    assert backups.removed == []


def test_rollback_failure_preserves_backup_and_reports_both_errors():
    backups = Backups()
    with pytest.raises(RuntimeFailure, match="automatic rollback also failed") as error:
        apply_transaction(snapshot(), Changer(rollback_fails=True), [fail], backups=backups)
    assert "operation broke" in str(error.value)
    assert "rollback broke" in str(error.value)
    assert backups.removed == []
