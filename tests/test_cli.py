from __future__ import annotations

import json
import subprocess
import sys

import pytest

from netmask_cli import cli
from netmask_cli.interfaces import InterfaceSnapshot
from netmask_cli.system import RuntimeFailure


@pytest.mark.parametrize(
    "arguments",
    [
        ["--mac", "02:00:00:00:00:01", "--random-mac", "-i", "eth0"],
        ["--ip", "192.0.2.1", "--dhcp", "-i", "eth0"],
        ["--reset", "--random-mac", "-i", "eth0"],
        ["--daemon", "--dhcp", "-i", "eth0"],
        ["--status", "--stop"],
        ["--status", "--daemon", "-i", "eth0"],
        ["--kill-switch", "-i", "eth0"],
        ["--network-hygiene", "-i", "eth0"],
        ["--duration", "5m", "-i", "eth0"],
        ["--random-mac"],
        ["--random-mac", "-i", "bad interface"],
        ["--mac", "broken", "-i", "eth0"],
        ["--mac", "01:00:5e:00:00:01", "-i", "eth0"],
        ["--ip", "999.1.1.1", "-i", "eth0"],
        ["--ip", "192.0.2.2", "--netmask", "255.0.255.0", "-i", "eth0"],
        ["--daemon", "--interval", "9", "-i", "eth0"],
        ["--daemon", "--duration", "forever", "-i", "eth0"],
        ["--interface", "eth0"],
        ["--json", "--list-interfaces"],
        ["--dry-run", "-i", "eth0"],
        ["--inspect", "--random-mac", "-i", "eth0"],
        ["--foreground", "--daemon", "-i", "eth0"],
    ],
)
def test_argument_conflicts_exit_two(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_help_version_and_no_args(capsys):
    assert cli.main([]) == 0
    assert "Safely change" in capsys.readouterr().out
    with pytest.raises(SystemExit) as version:
        cli.main(["--version"])
    assert version.value.code == 0
    assert "0.3.0" in capsys.readouterr().out


def test_module_smoke_help():
    result = subprocess.run(
        [sys.executable, "-m", "netmask_cli", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--list-interfaces" in result.stdout
    assert "--network-hygiene" in result.stdout
    assert "randomize hostname" not in result.stdout


def test_runtime_failure_returns_one(monkeypatch, capsys):
    monkeypatch.setattr(cli, "execute", lambda _args: (_ for _ in ()).throw(RuntimeFailure("boom")))
    assert cli.main(["--list-interfaces"]) == 1
    assert "boom" in capsys.readouterr().err


class Provider:
    def __init__(self, cidr="192.0.2.10/24"):
        self.cidr = cidr

    def list_interfaces(self):
        return ["eth0", "lo"]

    def snapshot(self, name):
        return InterfaceSnapshot(name, "02:00:00:00:00:01", True, self.cidr)


class Changer:
    def __init__(self):
        self.calls = []

    def change_mac(self, *args):
        self.calls.append(("mac", *args))

    def change_ip(self, *args):
        self.calls.append(("ip", *args))

    def dhcp_renew(self, *args):
        self.calls.append(("dhcp", *args))

    def restore(self, *args):
        self.calls.append(("restore", *args))

    def prepare_dhcp(self, interface):
        self.calls.append(("prepare_dhcp", interface))
        return "dhclient"


class Backups:
    def __init__(self):
        self.saved = []

    def save_once(self, value):
        self.saved.append(value)
        return True

    def remove(self, interface):
        self.saved = [value for value in self.saved if value.interface != interface]


def patch_oneshot(monkeypatch, provider=None):
    provider = provider or Provider()
    changer = Changer()
    backups = Backups()
    monkeypatch.setattr(cli, "require_admin", lambda: None)
    monkeypatch.setattr(cli, "require_commands", lambda _commands: None)
    monkeypatch.setattr(cli, "interface_provider", lambda: provider)
    monkeypatch.setattr(cli, "backend", lambda: changer)
    monkeypatch.setattr(cli, "BackupManager", lambda: backups)
    return changer, backups


def test_list_interfaces_does_not_require_privilege(monkeypatch, capsys):
    monkeypatch.setattr(cli, "interface_provider", Provider)
    assert cli.main(["--list-interfaces"]) == 0
    assert capsys.readouterr().out.splitlines() == ["eth0", "lo"]


def test_manual_mac_and_ip(monkeypatch):
    changer, backups = patch_oneshot(monkeypatch)
    result = cli.main(
        ["-i", "eth0", "--mac", "02:00:00:00:00:02", "--ip", "192.0.2.20", "-n", "24"]
    )
    assert result == 0
    assert changer.calls == [
        ("mac", "eth0", "02:00:00:00:00:02"),
        ("ip", "eth0", "192.0.2.20", "24"),
    ]
    assert len(backups.saved) == 1


def test_random_mac_and_subnet_ip(monkeypatch):
    changer, backups = patch_oneshot(monkeypatch)
    monkeypatch.setattr(cli, "random_mac", lambda: "02:00:00:00:00:03")
    monkeypatch.setattr(cli, "find_available_ip", lambda _cidr, _interface: "192.0.2.44")
    assert cli.main(["-i", "eth0", "--random-mac", "--random-ip"]) == 0
    assert changer.calls == [
        ("mac", "eth0", "02:00:00:00:00:03"),
        ("ip", "eth0", "192.0.2.44", "255.255.255.0"),
    ]
    assert backups.saved


def test_random_ip_without_subnet_returns_one(monkeypatch, capsys):
    _, backups = patch_oneshot(monkeypatch, Provider(cidr=None))
    assert cli.main(["-i", "eth0", "--random-ip"]) == 1
    assert "no IPv4 subnet" in capsys.readouterr().err
    assert backups.saved == []


def test_dhcp(monkeypatch):
    changer, _ = patch_oneshot(monkeypatch)
    assert cli.main(["-i", "eth0", "--dhcp"]) == 0
    assert changer.calls == [("prepare_dhcp", "eth0"), ("dhcp", "eth0")]


def test_missing_interface_runtime_error(monkeypatch, capsys):
    patch_oneshot(monkeypatch)
    assert cli.main(["-i", "eno1", "--random-mac"]) == 1
    assert "Interface not found" in capsys.readouterr().err


def test_status(monkeypatch, capsys):
    monkeypatch.setattr(cli, "daemon_status", lambda: "status text")
    assert cli.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == "status text"


def test_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "require_admin", lambda: calls.append("admin"))
    monkeypatch.setattr(cli, "stop_daemon", lambda: calls.append("stop"))
    assert cli.main(["--stop"]) == 0
    assert calls == ["admin", "stop"]


def test_windows_rejects_daemon(monkeypatch):
    monkeypatch.setattr(cli, "platform_name", lambda: "windows")
    with pytest.raises(SystemExit) as error:
        cli.main(["--daemon", "-i", "Ethernet", "--interval", "10"])
    assert error.value.code == 2


def test_daemon_start_routes_validated_options(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "require_admin", lambda: None)
    monkeypatch.setattr(cli, "require_commands", lambda commands: calls.append(commands))
    monkeypatch.setattr(cli, "interface_provider", Provider)
    monkeypatch.setattr(cli, "backend", Changer)
    monkeypatch.setattr(cli, "firewall_backend", lambda: "nftables")
    monkeypatch.setattr(cli, "start_daemon", lambda *args: calls.append(args) or 42)
    assert (
        cli.main(
            [
                "--daemon",
                "-i",
                "eth0",
                "--interval",
                "10",
                "--duration",
                "1m",
                "--kill-switch",
                "--network-hygiene",
            ]
        )
        == 0
    )
    assert calls[-1] == ("eth0", 10, 60, True, True, "dhclient", "nftables")


def test_internal_daemon_returns_runner_result(monkeypatch):
    class Runner:
        def __init__(self, *args):
            self.args = args

        def run(self):
            return 7

    monkeypatch.setattr(cli, "require_admin", lambda: None)
    monkeypatch.setattr(cli, "require_commands", lambda _commands: None)
    monkeypatch.setattr(cli, "interface_provider", Provider)
    monkeypatch.setattr(cli, "backend", Changer)
    monkeypatch.setattr(cli, "DaemonRunner", Runner)
    assert (
        cli.main(
            [
                "--_internal-daemon",
                "-i",
                "eth0",
                "--interval",
                "10",
                "--lock-token",
                "token",
            ]
        )
        == 7
    )


def test_oneshot_rejected_while_daemon_is_active(monkeypatch, capsys):
    patch_oneshot(monkeypatch)
    monkeypatch.setattr(cli, "active_state", lambda: {"pid": 77})
    assert cli.main(["-i", "eth0", "--random-mac"]) == 1
    assert "Daemon PID 77 is active" in capsys.readouterr().err


def test_combined_failure_automatically_rolls_back(monkeypatch, capsys):
    changer, backups = patch_oneshot(monkeypatch)

    def fail_ip(*_args):
        raise RuntimeError("address rejected")

    changer.change_ip = fail_ip
    assert (
        cli.main(
            [
                "-i",
                "eth0",
                "--mac",
                "02:00:00:00:00:02",
                "--ip",
                "192.0.2.20",
                "--netmask",
                "24",
            ]
        )
        == 1
    )
    assert changer.calls[0] == ("mac", "eth0", "02:00:00:00:00:02")
    assert changer.calls[-1][0] == "restore"
    assert backups.saved == []
    assert "pre-command interface state was restored" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("modern", "legacy"),
    [
        (["list"], ["--list-interfaces"]),
        (["inspect", "eth0", "--json"], ["--inspect", "--interface", "eth0", "--json"]),
        (["change", "eth0", "--dhcp"], ["--interface", "eth0", "--dhcp"]),
        (["restore", "eth0"], ["--reset", "--interface", "eth0"]),
        (["recover", "eth0"], ["--recover", "--interface", "eth0"]),
        (["daemon", "status"], ["--status"]),
        (["daemon", "stop"], ["--stop"]),
        (
            ["daemon", "foreground", "eth0", "--interval", "60"],
            ["--foreground", "--interface", "eth0", "--interval", "60"],
        ),
        (["completion", "zsh"], ["--completion", "zsh"]),
        (["systemd-unit"], ["--systemd-unit"]),
    ],
)
def test_modern_command_translation(modern, legacy):
    assert cli._modern_arguments(modern) == legacy


def test_modern_inspect_json_needs_no_admin(monkeypatch, capsys):
    monkeypatch.setattr(cli, "require_commands", lambda _commands: None)
    monkeypatch.setattr(cli, "interface_provider", Provider)
    monkeypatch.setattr(
        cli,
        "require_admin",
        lambda: (_ for _ in ()).throw(AssertionError("admin requested")),
    )
    assert cli.main(["inspect", "eth0", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["interface"] == "eth0"
    assert result["ipv4_cidr"] == "192.0.2.10/24"


def test_dry_run_is_mutation_and_probe_free(monkeypatch, capsys):
    changer, backups = patch_oneshot(monkeypatch)
    monkeypatch.setattr(cli, "random_mac", lambda: "02:00:00:00:00:09")
    monkeypatch.setattr(cli, "random_ip_in_subnet", lambda _cidr: "192.0.2.99")
    monkeypatch.setattr(
        cli,
        "find_available_ip",
        lambda *_args: (_ for _ in ()).throw(AssertionError("probe performed")),
    )
    assert cli.main(["change", "eth0", "--random-mac", "--random-ip", "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["actions"][1]["value"] == "192.0.2.99"
    assert result["actions"][1]["collision_check"] == "not-run-dry-run"
    assert changer.calls == []
    assert backups.saved == []


def test_completion_and_systemd_output(capsys):
    assert cli.main(["completion", "bash"]) == 0
    assert "complete -F" in capsys.readouterr().out
    assert cli.main(["systemd-unit"]) == 0
    assert "daemon foreground %I" in capsys.readouterr().out


def test_recover_command(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "require_commands", lambda commands: calls.append(commands))
    monkeypatch.setattr(cli, "require_admin", lambda: calls.append("admin"))
    monkeypatch.setattr(cli, "recover_state", lambda interface: [f"recovered {interface}"])
    assert cli.main(["recover", "eth0"]) == 0
    assert calls == [["ip"], "admin"]
    assert capsys.readouterr().out.strip() == "recovered eth0"


def test_foreground_daemon_routing(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "require_commands", lambda _commands: None)
    monkeypatch.setattr(cli, "require_admin", lambda: None)
    monkeypatch.setattr(cli, "interface_provider", Provider)
    monkeypatch.setattr(cli, "backend", Changer)
    monkeypatch.setattr(cli, "run_foreground", lambda *args: calls.append(args) or 6)
    assert cli.main(["daemon", "foreground", "eth0", "--duration", "1m"]) == 6
    assert calls == [("eth0", 30, 60, False, False, "dhclient", None)]
