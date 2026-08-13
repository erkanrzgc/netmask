from __future__ import annotations

import os

import pytest

from netmask_cli import daemon
from netmask_cli.daemon import DaemonRunner, InstanceLock
from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.storage import atomic_write_json
from netmask_cli.system import RuntimeFailure


def sample_snapshot():
    return InterfaceSnapshot("eth0", "02:00:00:00:00:01", True, "192.0.2.10/24")


def test_instance_lock_rejects_active_daemon(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    path.write_text("old")
    monkeypatch.setattr(daemon, "active_state", lambda: {"pid": 42})
    with pytest.raises(RuntimeFailure, match="already running"):
        InstanceLock(path).acquire()


def test_instance_lock_replaces_stale_and_releases(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    path.write_text("stale")
    monkeypatch.setattr(daemon, "active_state", lambda: None)
    lock = InstanceLock(path)
    lock.acquire()
    assert path.read_text() == lock.token
    lock.release()
    assert not path.exists()


def test_active_state_validates_pid_identity(tmp_path, monkeypatch):
    path = tmp_path / "daemon.json"
    atomic_write_json(path, {"pid": 22, "process_start": "abc"})
    monkeypatch.setattr(
        daemon, "daemon_process_matches", lambda pid, token: (pid, token) == (22, "abc")
    )
    assert daemon.active_state(path)["pid"] == 22
    monkeypatch.setattr(daemon, "daemon_process_matches", lambda _pid, _token: False)
    assert daemon.active_state(path) is None


def test_start_daemon_detaches_and_writes_state(monkeypatch, tmp_path):
    class Process:
        pid = 4321

    captured = {}

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(daemon.subprocess, "Popen", popen)
    monkeypatch.setattr(daemon, "process_start_token", lambda _pid: "ticks")
    pid = daemon.start_daemon("eth0", 30, 60, True, True, "networkmanager", "nftables")
    assert pid == 4321
    assert captured["kwargs"]["start_new_session"] is True
    assert "--_internal-daemon" in captured["command"]
    assert captured["command"][-4:] == [
        "--dhcp-manager",
        "networkmanager",
        "--firewall-backend",
        "nftables",
    ]
    assert daemon._state()["process_start"] == "ticks"
    assert daemon._state()["dhcp_manager"] == "networkmanager"
    assert daemon._state()["firewall_backend"] == "nftables"


class Provider:
    def snapshot(self, _interface):
        return sample_snapshot()


class Changer:
    def __init__(self, fail_rotation=False, fail_restore=False):
        self.calls = []
        self.fail_rotation = fail_rotation
        self.fail_restore = fail_restore

    def change_mac(self, interface, mac):
        self.calls.append(("mac", interface, mac))
        if self.fail_rotation:
            raise RuntimeError("rotation")

    def dhcp_renew(self, interface):
        self.calls.append(("dhcp", interface))

    def restore(self, value):
        self.calls.append(("restore", value))
        if self.fail_restore:
            raise RuntimeError("restore")


def runner(monkeypatch, changer=None, duration=1):
    value = DaemonRunner("eth0", 10, duration)
    value.provider = Provider()
    value.changer = changer or Changer()
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(daemon, "process_start_token", lambda _pid: "ticks")
    daemon.daemon_lock_file().parent.mkdir(parents=True, exist_ok=True)
    daemon.daemon_lock_file().write_text("test-lock")
    return value


def test_daemon_rotation_is_mac_then_dhcp(monkeypatch):
    value = runner(monkeypatch)
    monkeypatch.setattr(daemon, "random_mac", lambda: "02:00:00:00:00:02")
    value.rotate()
    assert value.changer.calls == [
        ("mac", "eth0", "02:00:00:00:00:02"),
        ("dhcp", "eth0"),
    ]


def test_daemon_kill_switch_always_cleans(monkeypatch):
    events = []

    class Switch:
        def __init__(self, _interface):
            pass

        def enable(self):
            events.append("enable")

        def cleanup(self):
            events.append("cleanup")

    value = runner(monkeypatch, Changer(fail_rotation=True))
    value.kill_switch = True
    monkeypatch.setattr(
        daemon, "create_kill_switch", lambda _interface, _selected: Switch(_interface)
    )
    with pytest.raises(RuntimeFailure, match="pre-command interface state was restored"):
        value.rotate()
    assert events == ["enable", "cleanup"]


def test_daemon_cleanup_failure_sets_nonzero(monkeypatch):
    class Switch:
        def __init__(self, _interface):
            pass

        def enable(self):
            pass

        def cleanup(self):
            raise RuntimeFailure("rules remain")

    value = runner(monkeypatch)
    value.kill_switch = True
    monkeypatch.setattr(
        daemon, "create_kill_switch", lambda _interface, _selected: Switch(_interface)
    )
    with pytest.raises(RuntimeFailure):
        value.rotate()
    assert value.cleanup_failed


def test_daemon_duration_runs_rotation_and_restores(monkeypatch):
    value = runner(monkeypatch, duration=1)
    monotonic = iter([0.0, 0.0, 2.0, 2.0])
    monkeypatch.setattr(daemon.time, "monotonic", lambda: next(monotonic))
    assert value.run() == 0
    assert value.changer.calls[-1] == ("restore", sample_snapshot())
    assert not daemon.daemon_file().exists()
    assert not daemon.daemon_lock_file().exists()


def test_daemon_restore_failure_is_nonzero_and_backup_remains(monkeypatch):
    value = runner(monkeypatch, Changer(fail_restore=True), duration=1)
    monotonic = iter([0.0, 0.0, 2.0, 2.0])
    monkeypatch.setattr(daemon.time, "monotonic", lambda: next(monotonic))
    assert value.run() == 1
    assert value.backups.load("eth0") == sample_snapshot()


def test_signal_requests_stop(monkeypatch):
    value = runner(monkeypatch)
    value._signal(15, None)
    assert value.stop_requested


def test_daemon_status_running_and_stale(monkeypatch):
    state = {
        "pid": 5,
        "interface": "eth0",
        "started_at": daemon.utc_now(),
        "rotations": 3,
        "duration": 60,
        "status": "running",
        "dhcp_manager": "networkmanager",
        "firewall_backend": "nftables",
    }
    monkeypatch.setattr(daemon, "active_state", lambda: state)
    text = daemon.daemon_status()
    assert "PID 5" in text
    assert "Rotations: 3" in text
    assert "DHCP backend: networkmanager" in text
    assert "Firewall backend: nftables" in text
    monkeypatch.setattr(daemon, "active_state", lambda: None)
    monkeypatch.setattr(daemon, "_state", lambda: None)
    assert daemon.daemon_status() == "Daemon is not running."


def test_stop_daemon_sends_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(
        daemon,
        "active_state",
        lambda: {"pid": 5, "process_start": "ticks"},
    )
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(daemon, "daemon_process_matches", lambda _pid, _token: False)
    daemon.stop_daemon()
    assert sent[0][0] == 5


def test_stop_daemon_rejects_missing(monkeypatch):
    monkeypatch.setattr(daemon, "active_state", lambda: None)
    with pytest.raises(RuntimeFailure, match="not running"):
        daemon.stop_daemon()


def test_daemon_process_match_checks_command(monkeypatch):
    class ProcessPath:
        def read_bytes(self):
            return b"/usr/bin/python3\0-m\0netmask_cli\0--_internal-daemon\0"

    monkeypatch.setattr(daemon, "process_matches", lambda _pid, _token: True)
    monkeypatch.setattr(daemon, "Path", lambda _value: ProcessPath())
    assert daemon.daemon_process_matches(12, "ticks")
    monkeypatch.setattr(daemon, "process_matches", lambda _pid, _token: False)
    assert not daemon.daemon_process_matches(12, "ticks")


def test_daemon_process_match_accepts_netmask_foreground(monkeypatch):
    class ProcessPath:
        def read_bytes(self):
            return b"/usr/bin/netmask-cli\0daemon\0foreground\0eth0\0"

    monkeypatch.setattr(daemon, "process_matches", lambda _pid, _token: True)
    monkeypatch.setattr(daemon, "Path", lambda _value: ProcessPath())
    assert daemon.daemon_process_matches(12, "ticks")


def test_foreground_writes_handshake_and_runs(monkeypatch):
    events = []

    class Lock:
        token = "foreground-token"

        def acquire(self):
            events.append("lock")

        def release(self):
            events.append("release")

    class Runner:
        def __init__(self, *args):
            events.append(args)

        def run(self):
            return 4

    monkeypatch.setattr(daemon, "InstanceLock", Lock)
    monkeypatch.setattr(daemon, "DaemonRunner", Runner)
    monkeypatch.setattr(daemon, "process_start_token", lambda _pid: "ticks")
    assert daemon.run_foreground("eth0", 30, 60, True, False, "networkd", "nftables") == 4
    state = daemon._state()
    assert state["lock_token"] == "foreground-token"
    assert state["dhcp_manager"] == "networkd"
    assert events[0] == "lock"
    assert events[1][0:5] == ("eth0", 30, 60, True, False)


def test_status_preserves_cleanup_failure(monkeypatch):
    state = {"status": "cleanup_failed"}
    monkeypatch.setattr(daemon, "active_state", lambda: None)
    monkeypatch.setattr(daemon, "_state", lambda: state)
    assert "cleanup failed" in daemon.daemon_status()


def test_stop_reports_child_cleanup_failure(monkeypatch):
    monkeypatch.setattr(daemon, "active_state", lambda: {"pid": 5, "process_start": "ticks"})
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(daemon, "daemon_process_matches", lambda _pid, _token: False)
    monkeypatch.setattr(daemon, "_state", lambda: {"status": "cleanup_failed"})
    with pytest.raises(RuntimeFailure, match="cleanup failed"):
        daemon.stop_daemon()
