"""Stable human/JSON output helpers and generated integration assets."""

from __future__ import annotations

import json
from typing import Any

from .interfaces import InterfaceSnapshot


def as_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def format_snapshot(snapshot: InterfaceSnapshot) -> str:
    state = "up" if snapshot.link_up else "down"
    addresses = (
        ", ".join(
            f"{address['local']}/{address['prefixlen']} ({address.get('address_type', 'unknown')})"
            for address in snapshot.ipv4_addresses
        )
        or "none"
    )
    dns_servers = ", ".join(snapshot.dns.get("servers", [])) or "none"
    return "\n".join(
        [
            f"Interface: {snapshot.interface}",
            f"Link: {state}",
            f"MAC: {snapshot.mac}",
            f"IPv4: {addresses}",
            f"Routes: {len(snapshot.routes)}",
            f"Policy rules: {len(snapshot.rules)}",
            f"DNS servers: {dns_servers}",
        ]
    )


def completion_script(shell: str) -> str:
    commands = "inspect change restore recover daemon completion systemd-unit"
    options = (
        "--help --version --json --dry-run --random-mac --random-ip --mac --ip "
        "--netmask --dhcp --interval --duration --kill-switch --network-hygiene"
    )
    if shell == "bash":
        return f"""_netmask_cli() {{
  local current="${{COMP_WORDS[COMP_CWORD]}}"
  COMPREPLY=( $(compgen -W '{commands} {options}' -- "$current") )
}}
complete -F _netmask_cli netmask-cli netmask
"""
    if shell == "zsh":
        words = " ".join(commands.split() + options.split())
        return f"""#compdef netmask-cli netmask
_netmask_cli() {{
  local -a choices
  choices=({words})
  _describe 'command or option' choices
}}
compdef _netmask_cli netmask-cli netmask
"""
    raise ValueError(f"Unsupported shell: {shell}")


def systemd_unit() -> str:
    return """[Unit]
Description=Netmask scheduled rotation on %I
After=network.target
ConditionPathExists=/sys/class/net/%I

[Service]
Type=simple
Environment=NETMASK_CONFIG_DIR=/var/lib/netmask
StateDirectory=netmask
ExecStart=/usr/bin/env netmask-cli daemon foreground %I --interval 300
Restart=on-failure
RestartSec=10
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/netmask

[Install]
WantedBy=multi-user.target
"""
