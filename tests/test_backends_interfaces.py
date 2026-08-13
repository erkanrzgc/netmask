from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from netmask_cli import backends, interfaces
from netmask_cli.backends import LinuxBackend, WindowsBackend
from netmask_cli.interfaces import (
    InterfaceSnapshot,
    LinuxInterfaceProvider,
    WindowsInterfaceProvider,
)
from netmask_cli.system import RuntimeFailure


def recorder(fail_at=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if fail_at and fail_at in command:
            raise RuntimeFailure("failed")
        return SimpleNamespace(returncode=0, stdout="")

    return calls, run


def test_linux_change_mac_recovers_link(monkeypatch):
    calls, run = recorder()
    monkeypatch.setattr(backends, "run_command", run)
    LinuxBackend().change_mac("eth0", "02:00:00:00:00:02")
    assert [call[0] for call in calls] == [
        ["ip", "link", "set", "dev", "eth0", "down"],
        ["ip", "link", "set", "dev", "eth0", "address", "02:00:00:00:00:02"],
        ["ip", "link", "set", "dev", "eth0", "up"],
    ]


def test_linux_change_mac_recovers_on_failure(monkeypatch):
    calls, run = recorder(fail_at="address")
    monkeypatch.setattr(backends, "run_command", run)
    with pytest.raises(RuntimeFailure):
        LinuxBackend().change_mac("eth0", "02:00:00:00:00:02")
    assert calls[-1][0][-1] == "up"


def test_linux_change_ip_and_dhcp(monkeypatch):
    calls, run = recorder()
    monkeypatch.setattr(backends, "run_command", run)
    monkeypatch.setattr(backends.shutil, "which", lambda _command: None)
    monkeypatch.setattr(backends, "require_commands", lambda _commands: None)
    changer = LinuxBackend()
    changer.change_ip("eth0", "192.0.2.20", "255.255.255.0")
    changer.dhcp_renew("eth0")
    commands = [call[0] for call in calls]
    assert ["ip", "addr", "add", "192.0.2.20/24", "dev", "eth0"] in commands
    assert ["dhclient", "-r", "eth0"] in commands
    assert ["dhclient", "eth0"] in commands


@pytest.mark.parametrize("link_up", [True, False])
def test_linux_restore_snapshot_routes_and_link(monkeypatch, link_up):
    calls, run = recorder()
    monkeypatch.setattr(backends, "run_command", run)
    value = InterfaceSnapshot(
        "eth0",
        "02:00:00:00:00:01",
        link_up,
        "192.0.2.10/24",
        "static",
        [{"dst": "default", "gateway": "192.0.2.1", "metric": 12}],
    )
    LinuxBackend().restore(value)
    commands = [call[0] for call in calls]
    assert ["ip", "addr", "add", "192.0.2.10/24", "dev", "eth0"] in commands
    assert [
        "ip",
        "route",
        "replace",
        "default",
        "via",
        "192.0.2.1",
        "dev",
        "eth0",
        "metric",
        "12",
    ] in commands
    if link_up:
        assert ["ip", "link", "set", "dev", "eth0", "up"] in commands
    else:
        assert commands[-1][-1] == "down"


def test_linux_restores_multiple_addresses_rules_and_dns(monkeypatch):
    calls, run = recorder()
    monkeypatch.setattr(backends, "run_command", run)
    monkeypatch.setattr(backends.shutil, "which", lambda command: f"/usr/bin/{command}")
    value = InterfaceSnapshot(
        "eth0",
        "02:00:00:00:00:01",
        True,
        "192.0.2.10/24",
        "static",
        [
            {"dst": "192.0.2.0/24", "protocol": "kernel"},
            {"dst": "default", "gateway": "192.0.2.1", "table": 100},
        ],
        [
            {"local": "192.0.2.10", "prefixlen": 24, "address_type": "static"},
            {
                "local": "192.0.2.11",
                "prefixlen": 24,
                "broadcast": "192.0.2.255",
                "label": "eth0:secondary",
                "address_type": "static",
            },
        ],
        [{"priority": 1000, "from": "192.0.2.0/24", "table": 100}],
        {
            "servers": ["192.0.2.53"],
            "domains": ["example.test"],
            "default_route": True,
        },
    )
    LinuxBackend().restore(value)
    commands = [call[0] for call in calls]
    assert [
        "ip",
        "addr",
        "add",
        "192.0.2.11/24",
        "broadcast",
        "192.0.2.255",
        "dev",
        "eth0",
        "label",
        "eth0:secondary",
    ] in commands
    assert [
        "ip",
        "route",
        "replace",
        "default",
        "via",
        "192.0.2.1",
        "dev",
        "eth0",
        "table",
        "100",
    ] in commands
    assert [
        "ip",
        "rule",
        "add",
        "priority",
        "1000",
        "from",
        "192.0.2.0/24",
        "table",
        "100",
    ] in commands
    assert ["resolvectl", "dns", "eth0", "192.0.2.53"] in commands
    assert ["resolvectl", "domain", "eth0", "example.test"] in commands
    assert ["resolvectl", "default-route", "eth0", "yes"] in commands


@pytest.mark.parametrize(
    ("available", "manager", "expected"),
    [
        ({"nmcli"}, "networkmanager", ["nmcli", "device", "disconnect", "eth0"]),
        ({"networkctl"}, "networkd", ["networkctl", "renew", "eth0"]),
        (set(), "dhclient", ["dhclient", "-r", "eth0"]),
    ],
)
def test_dhcp_manager_detection(monkeypatch, available, manager, expected):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[0] == "nmcli":
            return SimpleNamespace(returncode=0, stdout="100 (connected)\n")
        if command[:2] == ["networkctl", "status"]:
            return SimpleNamespace(returncode=0, stdout="State: routable\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(backends, "run_command", run)
    monkeypatch.setattr(
        backends.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command in available else None,
    )
    monkeypatch.setattr(backends, "require_commands", lambda _commands: None)
    changer = LinuxBackend()
    changer.dhcp_renew("eth0")
    assert changer.dhcp_manager == manager
    assert expected in calls


def test_windows_backend_is_mockable(monkeypatch):
    calls, run = recorder()
    macs = []
    monkeypatch.setattr(backends, "run_command", run)
    changer = WindowsBackend(lambda interface, mac: macs.append((interface, mac)))
    changer.change_mac("Ethernet", "02:00:00:00:00:02")
    changer.change_ip("Ethernet", "192.0.2.20", "24")
    changer.dhcp_renew("Ethernet")
    assert macs == [("Ethernet", "020000000002")]
    assert any(command[:4] == ["netsh", "interface", "ipv4", "set"] for command, _ in calls)
    assert any("mask=255.255.255.0" in command for command, _ in calls)
    assert any(command[0] == "ipconfig" for command, _ in calls)


def test_linux_interface_snapshot(monkeypatch):
    outputs = iter(
        [
            [{"address": "02:00:00:00:00:01", "flags": ["BROADCAST", "UP"]}],
            [
                {
                    "addr_info": [
                        {
                            "family": "inet",
                            "local": "192.0.2.10",
                            "prefixlen": 24,
                            "protocol": "dhcp",
                        }
                    ]
                }
            ],
            [{"dst": "default", "gateway": "192.0.2.1"}],
            [],
        ]
    )
    monkeypatch.setattr(LinuxInterfaceProvider, "list_interfaces", lambda _self: ["eth0"])
    monkeypatch.setattr(interfaces.shutil, "which", lambda _command: None)
    monkeypatch.setattr(
        interfaces,
        "run_command",
        lambda _command: SimpleNamespace(stdout=json.dumps(next(outputs))),
    )
    value = LinuxInterfaceProvider().snapshot("eth0")
    assert value.mac == "02:00:00:00:00:01"
    assert value.ipv4_cidr == "192.0.2.10/24"
    assert value.ipv4_addresses == [
        {"local": "192.0.2.10", "prefixlen": 24, "address_type": "dhcp"}
    ]
    assert value.routes == [{"dst": "default", "gateway": "192.0.2.1"}]


def test_linux_snapshot_captures_multiple_addresses_rules_and_dns(monkeypatch):
    responses = {
        "link show dev": [{"address": "02:00:00:00:00:01", "flags": ["UP"]}],
        "addr show dev": [
            {
                "addr_info": [
                    {"family": "inet", "local": "192.0.2.10", "prefixlen": 24},
                    {
                        "family": "inet",
                        "local": "192.0.2.11",
                        "prefixlen": 24,
                        "label": "eth0:secondary",
                    },
                ]
            }
        ],
        "route show": [{"dst": "default", "gateway": "192.0.2.1"}],
        "rule show": [
            {"priority": 0, "src": "all", "table": "local"},
            {
                "priority": 1000,
                "src": "192.0.2.0",
                "srclen": 24,
                "table": 100,
            },
            {"priority": 1001, "iif": "eth0", "table": 101},
        ],
    }

    def run(command, **_kwargs):
        if command[0] == "resolvectl":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "DNS Servers: 192.0.2.53 192.0.2.54\n"
                    "DNS Domain: example.test\nDefaultRoute setting: yes\n"
                ),
            )
        joined = " ".join(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(next(value for key, value in responses.items() if key in joined)),
        )

    monkeypatch.setattr(LinuxInterfaceProvider, "list_interfaces", lambda _self: ["eth0"])
    monkeypatch.setattr(interfaces, "run_command", run)
    monkeypatch.setattr(interfaces.shutil, "which", lambda _command: "/usr/bin/resolvectl")
    value = LinuxInterfaceProvider().snapshot("eth0")
    assert len(value.ipv4_addresses) == 2
    assert value.ipv4_addresses[1]["label"] == "eth0:secondary"
    assert [rule["priority"] for rule in value.rules] == [1000, 1001]
    assert value.rules[0]["from"] == "192.0.2.0/24"
    assert "srclen" not in value.rules[0]
    assert value.dns == {
        "servers": ["192.0.2.53", "192.0.2.54"],
        "domains": ["example.test"],
        "default_route": True,
    }


def test_linux_normalizes_split_rule_destination_prefix():
    value = LinuxInterfaceProvider._normalize_rule(
        {"priority": 1002, "dst": "198.51.100.0", "dstlen": 24, "table": 102}
    )
    assert value == {
        "priority": 1002,
        "to": "198.51.100.0/24",
        "table": 102,
    }


def test_linux_interface_missing(monkeypatch):
    monkeypatch.setattr(LinuxInterfaceProvider, "list_interfaces", lambda _self: [])
    with pytest.raises(RuntimeFailure, match="not found"):
        LinuxInterfaceProvider().snapshot("eth0")


def test_windows_interface_provider_parses_mock(monkeypatch):
    responses = {
        "show interface": "Enabled Connected Dedicated Ethernet\n",
        "show addresses": (
            "DHCP enabled: Yes\nIP Address: 192.0.2.2\nSubnet Prefix: 192.0.2.0/24\n"
        ),
        "getmac": '"Ethernet","x","02-00-00-00-00-02"',
    }

    def run(command):
        joined = " ".join(command)
        return SimpleNamespace(
            stdout=next(value for key, value in responses.items() if key in joined)
        )

    monkeypatch.setattr(interfaces, "run_command", run)
    provider = WindowsInterfaceProvider()
    assert provider.list_interfaces() == ["Ethernet"]
    value = provider.snapshot("Ethernet")
    assert value.mac == "02:00:00:00:00:02"
    assert value.ipv4_cidr == "192.0.2.2/24"
    assert value.address_type == "dhcp"


def test_subnet_netmask():
    assert interfaces.subnet_netmask("10.0.0.2/8") == "255.0.0.0"
