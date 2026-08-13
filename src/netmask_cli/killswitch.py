"""Nftables/iptables kill switches isolated to Netmask-owned objects."""

from __future__ import annotations

import hashlib
import shutil

from .system import RuntimeFailure, run_command


class IptablesKillSwitch:
    def __init__(self, interface: str):
        digest = hashlib.sha256(interface.encode()).hexdigest()[:10].upper()
        self.interface = interface
        self.chain_in = f"NETMASK_I_{digest}"
        self.chain_out = f"NETMASK_O_{digest}"
        self.active = False

    def enable(self) -> None:
        try:
            # Clear stale Netmask-owned chains from an interrupted earlier run.
            self.cleanup()
            self._create(self.chain_in)
            self._create(self.chain_out)
            run_command(["iptables", "-A", self.chain_in, "-j", "DROP"])
            run_command(["iptables", "-A", self.chain_out, "-j", "DROP"])
            run_command(["iptables", "-I", "INPUT", "1", "-i", self.interface, "-j", self.chain_in])
            run_command(
                ["iptables", "-I", "OUTPUT", "1", "-o", self.interface, "-j", self.chain_out]
            )
            self.active = True
        except Exception:
            self.cleanup()
            raise

    @staticmethod
    def _create(chain: str) -> None:
        run_command(["iptables", "-N", chain])

    def cleanup(self) -> None:
        failures = []
        commands = [
            ["iptables", "-D", "INPUT", "-i", self.interface, "-j", self.chain_in],
            ["iptables", "-D", "OUTPUT", "-o", self.interface, "-j", self.chain_out],
            ["iptables", "-F", self.chain_in],
            ["iptables", "-F", self.chain_out],
            ["iptables", "-X", self.chain_in],
            ["iptables", "-X", self.chain_out],
        ]
        for command in commands:
            result = run_command(command, check=False)
            if result.returncode not in (0, 1):
                failures.append(" ".join(command))
        self.active = False
        if failures:
            raise RuntimeFailure("Kill-switch cleanup failed: " + "; ".join(failures))

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False


class NftablesKillSwitch:
    """Interface-scoped drop rules contained in one Netmask-owned nftables table."""

    def __init__(self, interface: str):
        digest = hashlib.sha256(interface.encode()).hexdigest()[:10]
        self.interface = interface
        self.table = f"netmask_{digest}"
        self.active = False

    def enable(self) -> None:
        self.cleanup()
        try:
            run_command(["nft", "add", "table", "inet", self.table])
            run_command(
                [
                    "nft",
                    "add",
                    "chain",
                    "inet",
                    self.table,
                    "input",
                    "{ type filter hook input priority -10; policy accept; }",
                ]
            )
            run_command(
                [
                    "nft",
                    "add",
                    "chain",
                    "inet",
                    self.table,
                    "output",
                    "{ type filter hook output priority -10; policy accept; }",
                ]
            )
            run_command(
                [
                    "nft",
                    "add",
                    "rule",
                    "inet",
                    self.table,
                    "input",
                    "iifname",
                    self.interface,
                    "drop",
                ]
            )
            run_command(
                [
                    "nft",
                    "add",
                    "rule",
                    "inet",
                    self.table,
                    "output",
                    "oifname",
                    self.interface,
                    "drop",
                ]
            )
            self.active = True
        except Exception:
            self.cleanup()
            raise

    def cleanup(self) -> None:
        result = run_command(["nft", "delete", "table", "inet", self.table], check=False)
        self.active = False
        if result.returncode not in (0, 1):
            raise RuntimeFailure(f"Kill-switch cleanup failed: nft delete table inet {self.table}")


# Backward-compatible public name for callers that explicitly choose iptables.
KillSwitch = IptablesKillSwitch


def firewall_backend() -> str:
    if shutil.which("nft"):
        return "nftables"
    if shutil.which("iptables"):
        return "iptables"
    raise RuntimeFailure(
        "No supported firewall command found. Install nftables (preferred) or iptables."
    )


def create_kill_switch(interface: str, selected: str | None = None):
    selected = selected or firewall_backend()
    if selected == "nftables":
        return NftablesKillSwitch(interface)
    if selected == "iptables":
        return IptablesKillSwitch(interface)
    raise RuntimeFailure(f"Unknown firewall backend: {selected}")
