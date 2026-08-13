"""Detached Linux rotation process with atomic state and safe cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .backends import backend
from .config import daemon_file, daemon_lock_file, log_file
from .hygiene import run_network_hygiene
from .interfaces import interface_provider
from .killswitch import create_kill_switch
from .storage import BackupManager, atomic_write_json, read_json, restore_backup
from .system import RuntimeFailure, process_matches, process_start_token
from .transaction import apply_transaction
from .validation import format_duration, random_mac


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state(path: Path | None = None) -> dict[str, Any] | None:
    return read_json(path or daemon_file(), None)


def active_state(path: Path | None = None) -> dict[str, Any] | None:
    value = _state(path)
    if not value:
        return None
    try:
        return (
            value if daemon_process_matches(int(value["pid"]), value.get("process_start")) else None
        )
    except (KeyError, TypeError, ValueError):
        return None


def daemon_process_matches(pid: int, token: str | None) -> bool:
    if not process_matches(pid, token):
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    internal = b"-m" in command and b"netmask_cli" in command and b"--_internal-daemon" in command
    is_netmask = any(
        b"netmask-cli" in argument or b"netmask_cli" in argument for argument in command
    )
    foreground = is_netmask and (
        b"--foreground" in command or (b"daemon" in command and b"foreground" in command)
    )
    return internal or foreground


class InstanceLock:
    def __init__(self, path: Path | None = None):
        self.path = path or daemon_lock_file()
        self.token = uuid.uuid4().hex

    def acquire(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            existing = active_state()
            if existing:
                raise RuntimeFailure(
                    f"Daemon already running with PID {existing['pid']}"
                ) from error
            self.path.unlink(missing_ok=True)
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as second_error:
                raise RuntimeFailure(
                    "Another daemon start acquired the instance lock"
                ) from second_error
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(self.token)

    def release(self) -> None:
        try:
            if self.path.read_text(encoding="utf-8") == self.token:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass


def start_daemon(
    interface: str,
    interval: int,
    duration: int,
    kill_switch: bool,
    network_hygiene: bool,
    dhcp_manager: str | None = None,
    firewall: str | None = None,
) -> int:
    lock = InstanceLock()
    lock.acquire()
    command = [
        sys.executable,
        "-m",
        "netmask_cli",
        "--_internal-daemon",
        "--interface",
        interface,
        "--interval",
        str(interval),
        "--lock-token",
        lock.token,
    ]
    if duration:
        command += ["--duration", str(duration)]
    if kill_switch:
        command.append("--kill-switch")
    if network_hygiene:
        command.append("--network-hygiene")
    if dhcp_manager:
        command += ["--dhcp-manager", dhcp_manager]
    if firewall:
        command += ["--firewall-backend", firewall]
    try:
        log_file().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(log_file(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        # The start token is normally immediate on Linux; tolerate a short scheduler delay.
        token = None
        for _ in range(20):
            token = process_start_token(process.pid)
            if token:
                break
            time.sleep(0.01)
        state = {
            "pid": process.pid,
            "process_start": token,
            "interface": interface,
            "interval": interval,
            "duration": duration,
            "started_at": utc_now(),
            "rotations": 0,
            "status": "starting",
            "lock_token": lock.token,
            "dhcp_manager": dhcp_manager,
            "firewall_backend": firewall,
        }
        atomic_write_json(daemon_file(), state)
        return process.pid
    except Exception:
        lock.release()
        raise


def run_foreground(
    interface: str,
    interval: int,
    duration: int,
    kill_switch: bool,
    network_hygiene: bool,
    dhcp_manager: str | None = None,
    firewall: str | None = None,
) -> int:
    """Run under a service manager while retaining normal state/lock semantics."""
    lock = InstanceLock()
    lock.acquire()
    state = {
        "pid": os.getpid(),
        "process_start": process_start_token(os.getpid()),
        "interface": interface,
        "interval": interval,
        "duration": duration,
        "started_at": utc_now(),
        "rotations": 0,
        "status": "starting",
        "lock_token": lock.token,
        "dhcp_manager": dhcp_manager,
        "firewall_backend": firewall,
    }
    try:
        atomic_write_json(daemon_file(), state)
        return DaemonRunner(
            interface,
            interval,
            duration,
            kill_switch,
            network_hygiene,
            lock.token,
            dhcp_manager,
            firewall,
        ).run()
    except Exception:
        lock.release()
        raise


class DaemonRunner:
    def __init__(
        self,
        interface: str,
        interval: int,
        duration: int = 0,
        kill_switch: bool = False,
        network_hygiene: bool = False,
        lock_token: str | None = None,
        dhcp_manager: str | None = None,
        firewall: str | None = None,
    ):
        self.interface = interface
        self.interval = interval
        self.duration = duration
        self.kill_switch = kill_switch
        self.network_hygiene = network_hygiene
        self.lock_token = lock_token
        self.dhcp_manager = dhcp_manager
        self.firewall_backend = firewall
        self.stop_requested = False
        self.cleanup_failed = False
        self.provider = interface_provider()
        self.changer = backend()
        if self.dhcp_manager and hasattr(self.changer, "dhcp_manager"):
            self.changer.dhcp_manager = self.dhcp_manager
        self.backups = BackupManager()
        self.started_monotonic = 0.0
        self.rotations = 0

    def _signal(self, signum, _frame) -> None:
        self.stop_requested = True
        self._log(f"Received signal {signum}")

    def _log(self, message: str) -> None:
        log_file().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(log_file(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(f"[{utc_now()}] {message}\n")

    def _write_state(self, status: str) -> None:
        atomic_write_json(
            daemon_file(),
            {
                "pid": os.getpid(),
                "process_start": process_start_token(os.getpid()),
                "interface": self.interface,
                "interval": self.interval,
                "duration": self.duration,
                "started_at": datetime.fromtimestamp(
                    time.time() - (time.monotonic() - self.started_monotonic), timezone.utc
                ).isoformat(),
                "rotations": self.rotations,
                "status": status,
                "lock_token": self.lock_token,
                "dhcp_manager": self.dhcp_manager,
                "firewall_backend": self.firewall_backend,
            },
        )

    def rotate(self) -> None:
        switch = (
            create_kill_switch(self.interface, self.firewall_backend) if self.kill_switch else None
        )
        operation_error = None
        try:
            if switch:
                switch.enable()
            cycle_snapshot = self.provider.snapshot(self.interface)
            operations = [
                lambda: self.changer.change_mac(self.interface, random_mac()),
                lambda: self.changer.dhcp_renew(self.interface),
            ]
            if self.network_hygiene:
                operations.append(lambda: run_network_hygiene(self.interface))
            apply_transaction(cycle_snapshot, self.changer, operations)
        except Exception as error:
            operation_error = error
            raise
        finally:
            if switch:
                try:
                    switch.cleanup()
                except Exception as cleanup_error:
                    self.cleanup_failed = True
                    self._log(f"WARNING: {cleanup_error}")
                    if operation_error is None:
                        raise

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.started_monotonic = time.monotonic()
        result = 0
        snapshot_saved = False
        try:
            if hasattr(self.changer, "prepare_dhcp"):
                self.dhcp_manager = self.changer.prepare_dhcp(self.interface)
            if self.lock_token:
                try:
                    if daemon_lock_file().read_text(encoding="utf-8") != self.lock_token:
                        raise RuntimeFailure("Daemon lock token does not match the active instance")
                except OSError as error:
                    raise RuntimeFailure("Daemon instance lock is missing") from error
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    parent_state = _state()
                    if (
                        parent_state
                        and parent_state.get("pid") == os.getpid()
                        and parent_state.get("lock_token") == self.lock_token
                    ):
                        break
                    time.sleep(0.01)
                else:
                    raise RuntimeFailure("Daemon parent did not publish matching process state")
            snapshot = self.provider.snapshot(self.interface)
            self.backups.save_once(snapshot)
            snapshot_saved = True
            self._write_state("running")
            while not self.stop_requested:
                if self.duration and time.monotonic() - self.started_monotonic >= self.duration:
                    break
                self.rotate()
                self.rotations += 1
                self._write_state("running")
                deadline = time.monotonic() + self.interval
                while not self.stop_requested and time.monotonic() < deadline:
                    time.sleep(min(0.25, deadline - time.monotonic()))
        except Exception as error:
            result = 1
            self._log(f"Daemon error: {error}")
        finally:
            if snapshot_saved:
                try:
                    self._write_state("restoring")
                except Exception as error:
                    result = 1
                    self._log(f"WARNING: state update failed during cleanup: {error}")
                try:
                    restore_backup(self.interface, self.changer, self.backups)
                except Exception as error:
                    result = 1
                    self.cleanup_failed = True
                    self._log(f"WARNING: restore failed: {error}")
            try:
                lock = daemon_lock_file()
                if not self.lock_token or lock.read_text(encoding="utf-8") == self.lock_token:
                    lock.unlink(missing_ok=True)
            except OSError:
                result = 1
            if result or self.cleanup_failed:
                try:
                    self._write_state("cleanup_failed")
                except Exception as error:
                    self._log(f"WARNING: unable to persist cleanup failure: {error}")
            else:
                daemon_file().unlink(missing_ok=True)
        return 1 if self.cleanup_failed else result


def daemon_status() -> str:
    value = active_state()
    if value is None:
        stale = _state()
        if stale and stale.get("status") == "cleanup_failed":
            return (
                "Daemon is not running, but cleanup failed. "
                "The backup was preserved; review the log and run --reset."
            )
        if stale:
            daemon_file().unlink(missing_ok=True)
        return "Daemon is not running."
    started = datetime.fromisoformat(value["started_at"])
    elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    duration = int(value.get("duration", 0))
    remaining = max(0, duration - elapsed) if duration else None
    lines = [
        f"Daemon is {value.get('status', 'running')} (PID {value['pid']}).",
        f"Interface: {value['interface']}",
        f"Uptime: {format_duration(elapsed)}",
        f"Rotations: {value.get('rotations', 0)}",
    ]
    if remaining is not None:
        lines.append(f"Remaining: {format_duration(remaining)}")
    if value.get("dhcp_manager"):
        lines.append(f"DHCP backend: {value['dhcp_manager']}")
    if value.get("firewall_backend"):
        lines.append(f"Firewall backend: {value['firewall_backend']}")
    return "\n".join(lines)


def stop_daemon(timeout: float = 15.0) -> None:
    value = active_state()
    if value is None:
        raise RuntimeFailure("Daemon is not running.")
    pid = int(value["pid"])
    token = value.get("process_start")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon_process_matches(pid, token):
            final_state = _state()
            if final_state and final_state.get("status") == "cleanup_failed":
                raise RuntimeFailure(
                    "Daemon stopped, but cleanup failed; review the log and run --reset."
                )
            return
        time.sleep(0.1)
    raise RuntimeFailure(f"Daemon PID {pid} did not stop within {timeout:g}s")
