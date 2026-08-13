"""Address validation and safe random address generation."""

from __future__ import annotations

import ipaddress
import random
import re

MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def is_valid_mac(value: str) -> bool:
    return bool(MAC_PATTERN.fullmatch(value or ""))


def is_unicast(value: str) -> bool:
    return is_valid_mac(value) and not int(value[:2], 16) & 1


def random_mac() -> str:
    octets = bytearray(random.getrandbits(8) for _ in range(6))
    octets[0] = (octets[0] | 2) & 0xFE
    return ":".join(f"{octet:02x}" for octet in octets)


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ipaddress.AddressValueError:
        return False


def prefix_length(netmask: str | int) -> int:
    value = str(netmask)
    try:
        prefix = int(value)
        if not 0 <= prefix <= 32:
            raise ValueError
        return prefix
    except ValueError:
        try:
            return ipaddress.IPv4Network(f"0.0.0.0/{value}").prefixlen
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as error:
            raise ValueError(f"Invalid IPv4 netmask: {netmask}") from error


def is_valid_netmask(value: str | int) -> bool:
    try:
        prefix_length(value)
        return True
    except ValueError:
        return False


def random_ip_in_subnet(current_cidr: str) -> str:
    """Choose a host in the current subnet, excluding reserved/current addresses."""
    try:
        interface = ipaddress.IPv4Interface(current_cidr)
    except (ipaddress.AddressValueError, ValueError) as error:
        raise ValueError(f"Interface has no usable IPv4 subnet: {current_cidr}") from error
    network = interface.network
    first = int(network.network_address) + 1
    last = int(network.broadcast_address) - 1
    current = int(interface.ip)
    candidates = (last - first + 1) - int(first <= current <= last)
    if candidates <= 0:
        raise ValueError(f"Subnet {network} has no alternative host address")
    candidate = first + random.randrange(candidates)
    if first <= current <= last and candidate >= current:
        candidate += 1
    return str(ipaddress.IPv4Address(candidate))


def parse_duration(value: str | int) -> int:
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("Duration must be positive")
        return value
    compact = str(value).strip().lower().replace(" ", "")
    if compact.isdigit() and int(compact) > 0:
        return int(compact)
    matches = list(re.finditer(r"(\d+)([smhd])", compact))
    if not matches or "".join(match.group(0) for match in matches) != compact:
        raise ValueError(f"Invalid duration: {value}")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    total = sum(int(match.group(1)) * units[match.group(2)] for match in matches)
    if total <= 0:
        raise ValueError("Duration must be positive")
    return total


def format_duration(seconds: int) -> str:
    days, remainder = divmod(max(0, int(seconds)), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    for amount, suffix in ((days, "d"), (hours, "h"), (minutes, "m"), (secs, "s")):
        if amount:
            parts.append(f"{amount}{suffix}")
    return "".join(parts) or "0s"
