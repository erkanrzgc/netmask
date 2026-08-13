from __future__ import annotations

import subprocess

import pytest

from netmask_cli import system
from netmask_cli.system import RuntimeFailure


def test_run_command_success():
    result = system.run_command(["printf", "hello"])
    assert result.stdout == "hello"


def test_run_command_missing():
    with pytest.raises(RuntimeFailure, match="Required command"):
        system.run_command(["command-that-does-not-exist-netmask"])


def test_run_command_failure():
    with pytest.raises(RuntimeFailure, match="Command failed"):
        system.run_command(["sh", "-c", "echo detail >&2; exit 3"])
    result = system.run_command(["sh", "-c", "exit 3"], check=False)
    assert result.returncode == 3


def test_run_command_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["x"], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeFailure, match="timed out"):
        system.run_command(["x"])


def test_require_commands(monkeypatch):
    monkeypatch.setattr(system.shutil, "which", lambda name: "/bin/ip" if name == "ip" else None)
    system.require_commands(["ip"])
    with pytest.raises(RuntimeFailure, match="isc-dhcp-client"):
        system.require_commands(["dhclient"])


def test_process_identity_current_process():
    import os

    token = system.process_start_token(os.getpid())
    assert token
    assert system.process_matches(os.getpid(), token)
    assert not system.process_matches(os.getpid(), "wrong")
    assert not system.process_matches(-1, token)
