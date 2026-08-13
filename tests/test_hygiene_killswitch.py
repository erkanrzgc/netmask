from __future__ import annotations

from types import SimpleNamespace

import pytest

from netmask_cli import hygiene, killswitch
from netmask_cli.killswitch import KillSwitch, NftablesKillSwitch
from netmask_cli.system import RuntimeFailure


def test_network_hygiene_scoped_and_optional(monkeypatch):
    calls = []
    monkeypatch.setattr(hygiene, "run_command", lambda command: calls.append(command))
    monkeypatch.setattr(hygiene.shutil, "which", lambda _name: "/usr/bin/resolvectl")
    hygiene.run_network_hygiene("eth0")
    assert calls == [
        ["ip", "neigh", "flush", "dev", "eth0"],
        ["resolvectl", "flush-caches"],
    ]


def test_dns_absent_is_safe(monkeypatch):
    monkeypatch.setattr(hygiene.shutil, "which", lambda _name: None)
    assert hygiene.flush_dns() is False


def test_kill_switch_owns_named_chains_and_cleans(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(killswitch, "run_command", run)
    switch = KillSwitch("eth0")
    switch.enable()
    assert switch.active
    assert switch.chain_in.startswith("NETMASK_I_")
    switch.cleanup()
    assert not switch.active
    assert ["iptables", "-F", switch.chain_in] in calls
    assert ["iptables", "-X", switch.chain_out] in calls
    assert not any(command[:2] == ["iptables", "-F"] and len(command) == 2 for command in calls)


def test_kill_switch_enable_failure_attempts_cleanup(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["-A", switch.chain_in]:
            raise RuntimeFailure("enable failed")
        return SimpleNamespace(returncode=0)

    switch = KillSwitch("eth0")
    monkeypatch.setattr(killswitch, "run_command", run)
    with pytest.raises(RuntimeFailure, match="enable failed"):
        switch.enable()
    assert any(command[1] == "-X" for command in calls)


def test_kill_switch_cleanup_reports_real_failures(monkeypatch):
    monkeypatch.setattr(
        killswitch,
        "run_command",
        lambda _command, **_kwargs: SimpleNamespace(returncode=2),
    )
    with pytest.raises(RuntimeFailure, match="cleanup failed"):
        KillSwitch("eth0").cleanup()


def test_nftables_switch_uses_only_its_own_table(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(killswitch, "run_command", run)
    switch = NftablesKillSwitch("eth0")
    switch.enable()
    assert switch.active
    assert ["nft", "add", "table", "inet", switch.table] in calls
    assert any("iifname" in command and "eth0" in command for command in calls)
    assert any("oifname" in command and "eth0" in command for command in calls)
    switch.cleanup()
    assert calls[-1] == ["nft", "delete", "table", "inet", switch.table]
    assert not any(command[:3] == ["nft", "flush", "ruleset"] for command in calls)


def test_nftables_cleanup_failure_is_visible(monkeypatch):
    monkeypatch.setattr(
        killswitch,
        "run_command",
        lambda _command, **_kwargs: SimpleNamespace(returncode=2),
    )
    with pytest.raises(RuntimeFailure, match="cleanup failed"):
        NftablesKillSwitch("eth0").cleanup()


def test_firewall_factory_prefers_nftables_and_falls_back(monkeypatch):
    monkeypatch.setattr(
        killswitch.shutil,
        "which",
        lambda command: "/usr/sbin/nft" if command == "nft" else None,
    )
    assert killswitch.firewall_backend() == "nftables"
    assert isinstance(killswitch.create_kill_switch("eth0"), NftablesKillSwitch)
    monkeypatch.setattr(
        killswitch.shutil,
        "which",
        lambda command: "/usr/sbin/iptables" if command == "iptables" else None,
    )
    assert killswitch.firewall_backend() == "iptables"
    assert isinstance(killswitch.create_kill_switch("eth0"), KillSwitch)


def test_firewall_factory_reports_missing_or_unknown_backend(monkeypatch):
    monkeypatch.setattr(killswitch.shutil, "which", lambda _command: None)
    with pytest.raises(RuntimeFailure, match="nftables"):
        killswitch.firewall_backend()
    with pytest.raises(RuntimeFailure, match="Unknown firewall"):
        killswitch.create_kill_switch("eth0", "pf")
