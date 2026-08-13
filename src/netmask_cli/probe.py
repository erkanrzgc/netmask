"""IPv4 duplicate-address probing for random static selections."""

from __future__ import annotations

from collections.abc import Callable

from .system import RuntimeFailure, require_commands, run_command
from .validation import random_ip_in_subnet


def address_is_available(interface: str, address: str) -> bool:
    """Return True when RFC 5227-style arping sees no duplicate response."""
    require_commands(["arping"])
    version = run_command(["arping", "-V"], check=False, timeout=5)
    identity = f"{version.stdout}\n{version.stderr}".casefold()
    if "iputils" in identity:
        result = run_command(
            ["arping", "-D", "-c", "2", "-w", "3", "-I", interface, address],
            check=False,
            timeout=5,
        )
        return result.returncode == 0
    result = run_command(
        [
            "arping",
            "-q",
            "-c",
            "2",
            "-w",
            "3",
            "-i",
            interface,
            "-S",
            "0.0.0.0",
            address,
        ],
        check=False,
        timeout=5,
    )
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or "unknown arping error").strip()
        raise RuntimeFailure(f"ARP duplicate-address probe failed: {detail}")
    return result.returncode == 1


def find_available_ip(
    current_cidr: str,
    interface: str,
    *,
    attempts: int = 16,
    generator: Callable[[str], str] = random_ip_in_subnet,
) -> str:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    tried: set[str] = set()
    for _ in range(attempts):
        candidate = generator(current_cidr)
        if candidate in tried:
            continue
        tried.add(candidate)
        if address_is_available(interface, candidate):
            return candidate
    raise RuntimeFailure(
        f"No unused IPv4 address was found in {current_cidr} after {attempts} probes."
    )
