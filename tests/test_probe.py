from __future__ import annotations

from types import SimpleNamespace

import pytest

from netmask_cli import probe
from netmask_cli.system import RuntimeFailure


def test_iputils_address_probe_uses_duplicate_detection(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "require_commands", lambda commands: calls.append(commands))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["arping", "-V"]:
            return SimpleNamespace(returncode=0, stdout="arping from iputils", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(probe, "run_command", run)
    assert probe.address_is_available("eth0", "192.0.2.20")
    assert calls[0] == ["arping"]
    assert calls[1][0] == ["arping", "-V"]
    assert calls[2][0] == [
        "arping",
        "-D",
        "-c",
        "2",
        "-w",
        "3",
        "-I",
        "eth0",
        "192.0.2.20",
    ]


def test_iputils_address_probe_detects_conflict(monkeypatch):
    monkeypatch.setattr(probe, "require_commands", lambda _commands: None)

    def run(command, **_kwargs):
        if command == ["arping", "-V"]:
            return SimpleNamespace(returncode=0, stdout="arping from iputils", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(probe, "run_command", run)
    assert not probe.address_is_available("eth0", "192.0.2.20")


@pytest.mark.parametrize(("returncode", "available"), [(0, False), (1, True)])
def test_habets_arping_semantics(monkeypatch, returncode, available):
    calls = []
    monkeypatch.setattr(probe, "require_commands", lambda _commands: None)

    def run(command, **_kwargs):
        calls.append(command)
        if command == ["arping", "-V"]:
            return SimpleNamespace(returncode=0, stdout="ARPing 2.25", stderr="")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(probe, "run_command", run)
    assert probe.address_is_available("eth0", "192.0.2.20") is available
    assert calls[1][1:4] == ["-q", "-c", "2"]
    assert "-i" in calls[1]
    assert "-S" in calls[1]


def test_habets_arping_runtime_error(monkeypatch):
    monkeypatch.setattr(probe, "require_commands", lambda _commands: None)

    def run(command, **_kwargs):
        if command == ["arping", "-V"]:
            return SimpleNamespace(returncode=0, stdout="ARPing 2.25", stderr="")
        return SimpleNamespace(returncode=2, stdout="", stderr="permission denied")

    monkeypatch.setattr(probe, "run_command", run)
    with pytest.raises(RuntimeFailure, match="permission denied"):
        probe.address_is_available("eth0", "192.0.2.20")


def test_find_available_ip_skips_conflicts_and_duplicates(monkeypatch):
    candidates = iter(["192.0.2.20", "192.0.2.20", "192.0.2.21"])
    checked = []
    monkeypatch.setattr(
        probe,
        "address_is_available",
        lambda _interface, address: checked.append(address) or address.endswith("21"),
    )
    result = probe.find_available_ip(
        "192.0.2.10/24", "eth0", attempts=3, generator=lambda _cidr: next(candidates)
    )
    assert result == "192.0.2.21"
    assert checked == ["192.0.2.20", "192.0.2.21"]


def test_find_available_ip_exhaustion_and_invalid_attempts(monkeypatch):
    monkeypatch.setattr(probe, "address_is_available", lambda *_args: False)
    with pytest.raises(RuntimeFailure, match="No unused IPv4"):
        probe.find_available_ip(
            "192.0.2.10/24", "eth0", attempts=2, generator=lambda _cidr: "192.0.2.20"
        )
    with pytest.raises(ValueError, match="positive"):
        probe.find_available_ip("192.0.2.10/24", "eth0", attempts=0)
