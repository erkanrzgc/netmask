"""Platform-specific mutating network operations."""

from __future__ import annotations

import ipaddress
import re
import shutil
from collections.abc import Callable

from .interfaces import InterfaceSnapshot
from .system import RuntimeFailure, platform_name, require_commands, run_command
from .validation import prefix_length


class LinuxBackend:
    def __init__(self, dhcp_manager: str | None = None):
        self.dhcp_manager = dhcp_manager

    def set_link(self, interface: str, up: bool) -> None:
        run_command(["ip", "link", "set", "dev", interface, "up" if up else "down"])

    def change_mac(self, interface: str, mac: str) -> None:
        self.set_link(interface, False)
        try:
            run_command(["ip", "link", "set", "dev", interface, "address", mac])
        finally:
            self.set_link(interface, True)

    def change_ip(self, interface: str, ip: str, netmask: str | int) -> None:
        prefix = prefix_length(netmask)
        run_command(["ip", "-4", "addr", "flush", "dev", interface])
        run_command(["ip", "addr", "add", f"{ip}/{prefix}", "dev", interface])
        self.set_link(interface, True)

    def dhcp_renew(self, interface: str) -> None:
        manager = self.prepare_dhcp(interface)
        if manager == "networkmanager":
            run_command(["nmcli", "device", "disconnect", interface])
            run_command(["nmcli", "device", "connect", interface], timeout=90)
        elif manager == "networkd":
            run_command(["networkctl", "renew", interface], timeout=90)
        else:
            run_command(["dhclient", "-r", interface], check=False)
            run_command(["dhclient", interface], timeout=90)

    def prepare_dhcp(self, interface: str) -> str:
        if self.dhcp_manager:
            require_commands([self._manager_command(self.dhcp_manager)])
            return self.dhcp_manager
        self.dhcp_manager = self._detect_dhcp_manager(interface)
        require_commands([self._manager_command(self.dhcp_manager)])
        return self.dhcp_manager

    @staticmethod
    def _manager_command(manager: str) -> str:
        return {
            "networkmanager": "nmcli",
            "networkd": "networkctl",
            "dhclient": "dhclient",
        }[manager]

    @staticmethod
    def _detect_dhcp_manager(interface: str) -> str:
        if shutil.which("nmcli"):
            result = run_command(
                ["nmcli", "-g", "GENERAL.STATE", "device", "show", interface], check=False
            )
            state = result.stdout.strip().split(maxsplit=1)[0] if result.stdout.strip() else ""
            if result.returncode == 0 and state.isdigit() and int(state) > 10:
                return "networkmanager"
        if shutil.which("networkctl"):
            result = run_command(["networkctl", "status", interface, "--no-pager"], check=False)
            if result.returncode == 0 and "unmanaged" not in result.stdout.casefold():
                return "networkd"
        return "dhclient"

    def restore(self, snapshot: InterfaceSnapshot) -> None:
        interface = snapshot.interface
        self.set_link(interface, False)
        try:
            run_command(["ip", "link", "set", "dev", interface, "address", snapshot.mac])
            run_command(["ip", "-4", "addr", "flush", "dev", interface])
            self.set_link(interface, True)
            addresses = snapshot.ipv4_addresses or self._legacy_addresses(snapshot)
            if any(address.get("address_type") == "dhcp" for address in addresses):
                self.dhcp_renew(interface)
            for address in addresses:
                if address.get("address_type") != "dhcp":
                    self._restore_address(interface, address)
            self._restore_routes(interface, snapshot.routes)
            self._restore_rules(snapshot.rules)
            self._restore_dns(interface, snapshot.dns)
        except Exception:
            # Link recovery is best-effort, while the original exception remains visible.
            run_command(["ip", "link", "set", "dev", interface, "up"], check=False)
            raise
        if not snapshot.link_up:
            self.set_link(interface, False)

    @staticmethod
    def _legacy_addresses(snapshot: InterfaceSnapshot) -> list[dict]:
        if not snapshot.ipv4_cidr:
            return []
        local, prefix = snapshot.ipv4_cidr.split("/", 1)
        return [
            {
                "local": local,
                "prefixlen": int(prefix),
                "address_type": snapshot.address_type or "static",
            }
        ]

    @staticmethod
    def _restore_address(interface: str, address: dict) -> None:
        command = [
            "ip",
            "addr",
            "add",
            f"{address['local']}/{address['prefixlen']}",
        ]
        if address.get("broadcast"):
            command += ["broadcast", str(address["broadcast"])]
        command += ["dev", interface]
        if address.get("label") and address["label"] != interface:
            command += ["label", str(address["label"])]
        run_command(command)

    def _restore_routes(self, interface: str, routes: list[dict]) -> None:
        for route in routes:
            if route.get("table") == "local" or route.get("protocol") in {
                "kernel",
                "dhcp",
                "ra",
            }:
                continue
            command = ["ip", "route", "replace", str(route.get("dst", "default"))]
            if route.get("gateway"):
                command += ["via", str(route["gateway"])]
            command += ["dev", interface]
            if route.get("metric") is not None:
                command += ["metric", str(route["metric"])]
            if route.get("table") and route["table"] != "main":
                command += ["table", str(route["table"])]
            if route.get("prefsrc"):
                command += ["src", str(route["prefsrc"])]
            run_command(command)

    @staticmethod
    def _rule_command(action: str, rule: dict) -> list[str]:
        command = ["ip", "rule", action]
        mappings = (
            ("priority", "priority"),
            ("from", "from"),
            ("to", "to"),
            ("iif", "iif"),
            ("oif", "oif"),
            ("fwmark", "fwmark"),
            ("table", "table"),
            ("suppress_prefixlength", "suppress_prefixlength"),
        )
        for key, option in mappings:
            if rule.get(key) is not None:
                command += [option, str(rule[key])]
        return command

    def _restore_rules(self, rules: list[dict]) -> None:
        for rule in rules:
            run_command(self._rule_command("del", rule), check=False)
            run_command(self._rule_command("add", rule))

    @staticmethod
    def _restore_dns(interface: str, dns: dict) -> None:
        if not dns or shutil.which("resolvectl") is None:
            return
        run_command(["resolvectl", "revert", interface], check=False)
        if dns.get("servers"):
            run_command(["resolvectl", "dns", interface, *dns["servers"]])
        if dns.get("domains"):
            run_command(["resolvectl", "domain", interface, *dns["domains"]])
        if dns.get("default_route") is not None:
            setting = "yes" if dns["default_route"] else "no"
            run_command(["resolvectl", "default-route", interface, setting])


class WindowsBackend:
    """Experimental backend. Drivers may reject MAC overrides."""

    def __init__(self, registry_setter: Callable[[str, str], None] | None = None):
        self._registry_setter = registry_setter or self._set_registry_mac

    def set_link(self, interface: str, up: bool) -> None:
        state = "enabled" if up else "disabled"
        run_command(["netsh", "interface", "set", "interface", interface, f"admin={state}"])

    def _adapter_key(self, interface: str) -> str:
        base = (
            r"HKLM\SYSTEM\CurrentControlSet\Control\Class"
            r"\{4D36E972-E325-11CE-BFC1-08002BE10318}"
        )
        result = run_command(["reg", "query", base, "/s", "/f", interface, "/d"])
        for line in result.stdout.splitlines():
            candidate = line.strip()
            if candidate.upper().startswith("HKEY_LOCAL_MACHINE") and re.search(
                r"\\\d{4}$", candidate
            ):
                return candidate
        raise RuntimeFailure(f"Windows adapter registry key not found: {interface}")

    def _set_registry_mac(self, interface: str, mac: str) -> None:
        key = self._adapter_key(interface)
        run_command(["reg", "add", key, "/v", "NetworkAddress", "/t", "REG_SZ", "/d", mac, "/f"])

    def change_mac(self, interface: str, mac: str) -> None:
        self.set_link(interface, False)
        try:
            self._registry_setter(interface, mac.replace(":", "").replace("-", ""))
        finally:
            self.set_link(interface, True)

    def change_ip(self, interface: str, ip: str, netmask: str | int) -> None:
        prefix = prefix_length(netmask)
        mask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
        run_command(
            [
                "netsh",
                "interface",
                "ipv4",
                "set",
                "address",
                f"name={interface}",
                "source=static",
                f"address={ip}",
                f"mask={mask}",
                "gateway=none",
            ]
        )

    def dhcp_renew(self, interface: str) -> None:
        run_command(["ipconfig", "/release", interface], check=False)
        run_command(["ipconfig", "/renew", interface], timeout=90)

    def restore(self, snapshot: InterfaceSnapshot) -> None:
        self.change_mac(snapshot.interface, snapshot.mac)
        if snapshot.ipv4_cidr:
            ip, prefix = snapshot.ipv4_cidr.split("/", 1)
            self.change_ip(snapshot.interface, ip, prefix)
        else:
            self.dhcp_renew(snapshot.interface)
        self.set_link(snapshot.interface, snapshot.link_up)


def backend():
    return LinuxBackend() if platform_name() == "linux" else WindowsBackend()
