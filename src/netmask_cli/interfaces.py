"""Read-only network interface discovery and snapshot capture."""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any

from .system import RuntimeFailure, platform_name, run_command


@dataclass(frozen=True)
class InterfaceSnapshot:
    interface: str
    mac: str
    link_up: bool
    ipv4_cidr: str | None
    address_type: str | None = None
    routes: list[dict[str, Any]] = field(default_factory=list)
    ipv4_addresses: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    dns: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ipv4_cidr and not self.ipv4_addresses:
            local, prefix = self.ipv4_cidr.split("/", 1)
            object.__setattr__(
                self,
                "ipv4_addresses",
                [
                    {
                        "local": local,
                        "prefixlen": int(prefix),
                        "address_type": self.address_type or "static",
                    }
                ],
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> InterfaceSnapshot:
        addresses = list(value.get("ipv4_addresses", []))
        if not addresses and value.get("ipv4_cidr"):
            local, prefix = value["ipv4_cidr"].split("/", 1)
            addresses = [
                {
                    "local": local,
                    "prefixlen": int(prefix),
                    "address_type": value.get("address_type") or "static",
                }
            ]
        return cls(
            interface=value["interface"],
            mac=value["mac"],
            link_up=bool(value["link_up"]),
            ipv4_cidr=value.get("ipv4_cidr"),
            address_type=value.get("address_type"),
            routes=list(value.get("routes", [])),
            ipv4_addresses=addresses,
            rules=list(value.get("rules", [])),
            dns=dict(value.get("dns", {})),
        )

    @property
    def primary_ipv4_cidr(self) -> str | None:
        if self.ipv4_cidr:
            return self.ipv4_cidr
        if not self.ipv4_addresses:
            return None
        address = self.ipv4_addresses[0]
        return f"{address['local']}/{address['prefixlen']}"


class LinuxInterfaceProvider:
    def list_interfaces(self) -> list[str]:
        result = run_command(["ip", "-j", "link", "show"])
        return sorted(entry["ifname"].split("@", 1)[0] for entry in json.loads(result.stdout))

    def snapshot(self, interface: str) -> InterfaceSnapshot:
        if interface not in self.list_interfaces():
            raise RuntimeFailure(f"Interface not found: {interface}")
        link_result = run_command(["ip", "-j", "link", "show", "dev", interface])
        addr_result = run_command(["ip", "-j", "-4", "addr", "show", "dev", interface])
        route_result = run_command(
            ["ip", "-j", "-4", "route", "show", "table", "all", "dev", interface]
        )
        rule_result = run_command(["ip", "-j", "-4", "rule", "show"])
        links = json.loads(link_result.stdout)
        addresses = json.loads(addr_result.stdout)
        routes = json.loads(route_result.stdout)
        rules = [self._normalize_rule(rule) for rule in json.loads(rule_result.stdout)]
        if not links:
            raise RuntimeFailure(f"Unable to inspect interface: {interface}")
        link = links[0]
        ipv4_addresses = [
            self._normalize_address(info)
            for entry in addresses
            for info in entry.get("addr_info", [])
            if info.get("family") == "inet"
        ]
        address = ipv4_addresses[0] if ipv4_addresses else None
        cidr = None
        address_type = None
        if address:
            cidr = f"{address['local']}/{address['prefixlen']}"
            address_type = address["address_type"]
        interface_rules = [
            rule for rule in rules if self._rule_applies(rule, interface, ipv4_addresses)
        ]
        return InterfaceSnapshot(
            interface=interface,
            mac=link.get("address", "N/A"),
            link_up="UP" in link.get("flags", []),
            ipv4_cidr=cidr,
            address_type=address_type,
            routes=routes,
            ipv4_addresses=ipv4_addresses,
            rules=interface_rules,
            dns=self._dns_snapshot(interface),
        )

    @staticmethod
    def _normalize_address(address: dict[str, Any]) -> dict[str, Any]:
        dynamic = address.get("dynamic") or "dynamic" in address.get("flags", [])
        result = {
            key: address[key]
            for key in ("local", "prefixlen", "broadcast", "scope", "label", "metric")
            if key in address
        }
        result["address_type"] = (
            "dhcp" if dynamic or address.get("protocol") == "dhcp" else "static"
        )
        return result

    @staticmethod
    def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(rule)
        if "src" in normalized:
            normalized["from"] = normalized.pop("src")
        if "dst" in normalized:
            normalized["to"] = normalized.pop("dst")
        return normalized

    @staticmethod
    def _rule_applies(
        rule: dict[str, Any], interface: str, addresses: list[dict[str, Any]]
    ) -> bool:
        if rule.get("iif") == interface or rule.get("oif") == interface:
            return True
        source = rule.get("from")
        if not source or source == "all":
            return False
        try:
            source_network = ipaddress.IPv4Network(source, strict=False)
            return any(
                ipaddress.IPv4Address(address["local"]) in source_network for address in addresses
            )
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            return False

    @staticmethod
    def _dns_snapshot(interface: str) -> dict[str, Any]:
        if shutil.which("resolvectl") is None:
            return {}
        result = run_command(["resolvectl", "status", interface], check=False)
        if result.returncode:
            return {}
        servers: list[str] = []
        domains: list[str] = []
        default_route: bool | None = None
        current_key = None
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if ":" in line:
                key, value = (part.strip() for part in line.split(":", 1))
                current_key = key
            else:
                value = line
            if current_key in {"DNS Servers", "Current DNS Server"}:
                servers.extend(value.split())
            elif current_key == "DNS Domain":
                domains.extend(value.split())
            elif current_key == "DefaultRoute setting":
                default_route = value.casefold() == "yes"
        return {
            "servers": list(dict.fromkeys(servers)),
            "domains": list(dict.fromkeys(domains)),
            "default_route": default_route,
        }


class WindowsInterfaceProvider:
    """Experimental netsh-backed one-shot interface support."""

    def list_interfaces(self) -> list[str]:
        result = run_command(["netsh", "interface", "show", "interface"])
        names = []
        for line in result.stdout.splitlines():
            match = re.match(r"\s*(?:Enabled|Disabled)\s+\S+\s+\S+\s+(.+?)\s*$", line)
            if match:
                names.append(match.group(1))
        return names

    def snapshot(self, interface: str) -> InterfaceSnapshot:
        if interface not in self.list_interfaces():
            raise RuntimeFailure(f"Interface not found: {interface}")
        result = run_command(["netsh", "interface", "ipv4", "show", "addresses", interface])
        ip_match = re.search(r"IP Address:\s*([0-9.]+)", result.stdout)
        prefix_match = re.search(r"Subnet Prefix:\s*[0-9.]+/(\d+)", result.stdout)
        mac_result = run_command(["getmac", "/v", "/fo", "csv"])
        mac = "N/A"
        for line in mac_result.stdout.splitlines():
            if interface.casefold() in line.casefold():
                match = re.search(r"([0-9A-F]{2}-){5}[0-9A-F]{2}", line, re.I)
                if match:
                    mac = match.group(0).replace("-", ":").lower()
                    break
        cidr = None
        if ip_match:
            prefix = prefix_match.group(1) if prefix_match else "32"
            cidr = f"{ip_match.group(1)}/{prefix}"
        address_type = (
            "dhcp" if re.search(r"DHCP enabled:\s*Yes", result.stdout, re.I) else "static"
        )
        ipv4_addresses = []
        if cidr:
            local, prefix = cidr.split("/", 1)
            ipv4_addresses.append(
                {"local": local, "prefixlen": int(prefix), "address_type": address_type}
            )
        return InterfaceSnapshot(
            interface,
            mac,
            True,
            cidr,
            address_type,
            [],
            ipv4_addresses,
        )


def interface_provider():
    return LinuxInterfaceProvider() if platform_name() == "linux" else WindowsInterfaceProvider()


def subnet_netmask(cidr: str) -> str:
    return str(ipaddress.IPv4Interface(cidr).network.netmask)
