"""Command-line routing for Netmask."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence

from . import __version__
from .backends import backend
from .config import DEFAULT_INTERVAL, DEFAULT_NETMASK, MIN_INTERVAL
from .daemon import (
    DaemonRunner,
    active_state,
    daemon_status,
    run_foreground,
    start_daemon,
    stop_daemon,
)
from .interfaces import interface_provider, subnet_netmask
from .killswitch import firewall_backend
from .presentation import as_json, completion_script, format_snapshot, systemd_unit
from .probe import find_available_ip
from .recovery import recover as recover_state
from .storage import BackupManager, restore_backup
from .system import RuntimeFailure, platform_name, require_admin, require_commands
from .transaction import apply_transaction
from .validation import (
    is_unicast,
    is_valid_ip,
    is_valid_mac,
    is_valid_netmask,
    parse_duration,
    random_ip_in_subnet,
    random_mac,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netmask-cli",
        description="Safely change, randomize, and restore MAC/IP settings.",
        epilog=(
            "Modern commands: inspect, change, restore, recover, daemon, completion, "
            "systemd-unit. Legacy flags remain supported."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-i", "--interface", help="network interface name")
    parser.add_argument("--list-interfaces", action="store_true", help="list available interfaces")
    parser.add_argument("--inspect", action="store_true", help="inspect an interface without root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="show a change plan without mutation"
    )
    parser.add_argument("--recover", action="store_true", help="recover stale state and backups")
    parser.add_argument(
        "--completion", choices=("bash", "zsh"), help="print a shell completion script"
    )
    parser.add_argument(
        "--systemd-unit", action="store_true", help="print the systemd unit template"
    )

    mac_group = parser.add_mutually_exclusive_group()
    mac_group.add_argument("-m", "--mac", help="set a MAC address")
    mac_group.add_argument("-rm", "--random-mac", action="store_true", help="set a random MAC")

    ip_group = parser.add_mutually_exclusive_group()
    ip_group.add_argument("--ip", help="set a static IPv4 address")
    ip_group.add_argument(
        "-ri", "--random-ip", action="store_true", help="choose an address in the current subnet"
    )
    ip_group.add_argument("--dhcp", action="store_true", help="release and renew DHCP")
    parser.add_argument("-n", "--netmask", default=DEFAULT_NETMASK, help="IPv4 mask or prefix")
    parser.add_argument("--reset", action="store_true", help="restore the saved interface snapshot")

    parser.add_argument(
        "--daemon", action="store_true", help="start scheduled rotation (Linux only)"
    )
    parser.add_argument(
        "--foreground", action="store_true", help="run rotation in the foreground (Linux only)"
    )
    parser.add_argument("-t", "--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("-d", "--duration", help="run duration such as 30s, 5m, or 2h")
    parser.add_argument("--status", action="store_true", help="show daemon status")
    parser.add_argument("--stop", action="store_true", help="stop daemon and restore settings")
    parser.add_argument(
        "-ks", "--kill-switch", action="store_true", help="block traffic only while rotating"
    )
    parser.add_argument(
        "--network-hygiene",
        action="store_true",
        help="flush interface ARP and supported DNS caches after rotation",
    )
    parser.add_argument("--_internal-daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lock-token", help=argparse.SUPPRESS)
    parser.add_argument(
        "--dhcp-manager",
        choices=("networkmanager", "networkd", "dhclient"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--firewall-backend",
        choices=("nftables", "iptables"),
        help=argparse.SUPPRESS,
    )
    return parser


def _argument_error(parser: argparse.ArgumentParser, message: str) -> None:
    parser.error(message)


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    output_only = [args.completion is not None, args.systemd_unit]
    if sum(output_only) > 1:
        _argument_error(parser, "--completion and --systemd-unit are mutually exclusive")
    management = [args.list_interfaces, args.inspect, args.status, args.stop, args.recover]
    if sum(management) > 1:
        _argument_error(parser, "list, inspect, status, stop, and recover are mutually exclusive")
    mutations = [
        args.mac is not None,
        args.random_mac,
        args.ip is not None,
        args.random_ip,
        args.dhcp,
    ]
    if args.reset and any(mutations):
        _argument_error(parser, "--reset cannot be combined with MAC, IP, or DHCP actions")
    daemon_mode = args.daemon or args.foreground or args._internal_daemon
    if sum([args.daemon, args.foreground, args._internal_daemon]) > 1:
        _argument_error(parser, "daemon start and foreground modes are mutually exclusive")
    if daemon_mode and (args.reset or any(mutations)):
        _argument_error(parser, "daemon modes cannot be combined with one-shot actions")
    if any(management) and (daemon_mode or args.reset or any(mutations)):
        _argument_error(parser, "management commands cannot be combined with change actions")
    if any(output_only) and (any(management) or daemon_mode or args.reset or any(mutations)):
        _argument_error(parser, "output commands cannot be combined with network actions")
    if (args.kill_switch or args.network_hygiene or args.duration) and not (daemon_mode):
        _argument_error(parser, "daemon options require start or foreground mode")
    if args.dry_run and not any(mutations):
        _argument_error(parser, "--dry-run requires a MAC, IP, or DHCP action")
    if args.dry_run and (daemon_mode or args.reset or any(management)):
        _argument_error(parser, "--dry-run is only available for one-shot changes")
    if args.json and not (args.inspect or args.dry_run):
        _argument_error(parser, "--json requires inspect or --dry-run")
    if args.interval < MIN_INTERVAL:
        _argument_error(parser, f"--interval must be at least {MIN_INTERVAL} seconds")
    action = any(management) or any(output_only) or daemon_mode or args.reset or any(mutations)
    if not action:
        _argument_error(parser, "no action requested; use --help for available actions")
    interface_optional = (
        args.list_interfaces or args.status or args.stop or args.recover or any(output_only)
    )
    if action and not interface_optional and not args.interface:
        _argument_error(parser, "--interface is required for this action")
    if args.interface:
        if platform_name() == "linux" and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,15}", args.interface
        ):
            _argument_error(parser, f"invalid Linux interface name: {args.interface}")
        if any(character.isspace() or ord(character) < 32 for character in args.interface):
            _argument_error(
                parser, "interface names cannot contain whitespace or control characters"
            )
    if args.mac and not is_valid_mac(args.mac):
        _argument_error(parser, f"invalid MAC address: {args.mac}")
    if args.mac and not is_unicast(args.mac):
        _argument_error(parser, f"MAC address must be unicast: {args.mac}")
    if args.ip and not is_valid_ip(args.ip):
        _argument_error(parser, f"invalid IPv4 address: {args.ip}")
    if (args.ip or args.random_ip) and not is_valid_netmask(args.netmask):
        _argument_error(parser, f"invalid IPv4 netmask: {args.netmask}")
    if args.duration:
        try:
            args.duration_seconds = parse_duration(args.duration)
        except ValueError as error:
            _argument_error(parser, str(error))
    else:
        args.duration_seconds = 0
    if platform_name() == "windows" and (daemon_mode or args.kill_switch or args.recover):
        _argument_error(
            parser, "daemon and kill-switch modes are Linux-only; Windows is experimental"
        )


def _dependencies(args: argparse.Namespace) -> list[str]:
    if platform_name() == "windows":
        commands = ["netsh", "getmac"]
        if not args.dry_run and (args.mac or args.random_mac or args.reset):
            commands.append("reg")
        if args.dhcp and not args.dry_run:
            commands.append("ipconfig")
        return commands
    commands = ["ip"]
    if args.random_ip and not args.dry_run:
        commands.append("arping")
    return commands


def _modern_arguments(arguments: list[str]) -> list[str]:
    """Translate the modern command surface to the stable flag router."""
    if not arguments:
        return arguments
    command = arguments[0]
    rest = arguments[1:]
    if command == "list":
        return ["--list-interfaces", *rest]
    if command in {"inspect", "change", "restore"}:
        flag = {"inspect": "--inspect", "restore": "--reset"}.get(command)
        if not rest or rest[0].startswith("-"):
            return ([flag] if flag else []) + rest
        prefix = ([flag] if flag else []) + ["--interface", rest[0]]
        return [*prefix, *rest[1:]]
    if command == "recover":
        if rest and not rest[0].startswith("-"):
            return ["--recover", "--interface", rest[0], *rest[1:]]
        return ["--recover", *rest]
    if command == "completion":
        return ["--completion", *rest]
    if command == "systemd-unit":
        return ["--systemd-unit", *rest]
    if command == "daemon" and rest:
        action, *tail = rest
        if action == "status":
            return ["--status", *tail]
        if action == "stop":
            return ["--stop", *tail]
        if action in {"start", "foreground"}:
            mode = "--daemon" if action == "start" else "--foreground"
            if tail and not tail[0].startswith("-"):
                return [mode, "--interface", tail[0], *tail[1:]]
            return [mode, *tail]
    return arguments


def execute(args: argparse.Namespace) -> int:
    if args.completion:
        print(completion_script(args.completion), end="")
        return 0
    if args.systemd_unit:
        print(systemd_unit(), end="")
        return 0
    if args.list_interfaces:
        require_commands(["ip"] if platform_name() == "linux" else ["netsh"])
        for name in interface_provider().list_interfaces():
            print(name)
        return 0
    if args.status:
        print(daemon_status())
        return 0
    if args.stop:
        require_admin()
        stop_daemon()
        print("Daemon stopped; restore completed.")
        return 0

    require_commands(_dependencies(args))
    provider = interface_provider()
    if args.inspect:
        if args.interface not in provider.list_interfaces():
            raise RuntimeFailure(f"Interface not found: {args.interface}")
        snapshot = provider.snapshot(args.interface)
        print(as_json(snapshot.to_dict()) if args.json else format_snapshot(snapshot))
        return 0
    if args.recover:
        require_admin()
        for message in recover_state(args.interface):
            print(message)
        return 0
    if args.interface not in provider.list_interfaces():
        raise RuntimeFailure(f"Interface not found: {args.interface}")
    changer = backend()
    selected_dhcp = args.dhcp_manager
    selected_firewall = args.firewall_backend
    if platform_name() == "windows" and args.dhcp:
        selected_dhcp = "ipconfig"
    if platform_name() == "linux" and (
        args.dhcp or args.daemon or args.foreground or args._internal_daemon
    ):
        if selected_dhcp and hasattr(changer, "dhcp_manager"):
            changer.dhcp_manager = selected_dhcp
        selected_dhcp = changer.prepare_dhcp(args.interface)
    if args.kill_switch:
        selected_firewall = selected_firewall or firewall_backend()

    snapshot = None
    random_ip_value = None
    random_ip_netmask = None
    if not (args.daemon or args.foreground or args._internal_daemon or args.reset):
        snapshot = provider.snapshot(args.interface)
        if args.random_ip:
            if not snapshot.primary_ipv4_cidr:
                raise RuntimeFailure(f"{args.interface} has no IPv4 subnet for --random-ip")
            try:
                random_ip_value = (
                    random_ip_in_subnet(snapshot.primary_ipv4_cidr)
                    if args.dry_run
                    else find_available_ip(snapshot.primary_ipv4_cidr, args.interface)
                )
            except ValueError as error:
                raise RuntimeFailure(str(error)) from error
            random_ip_netmask = subnet_netmask(snapshot.primary_ipv4_cidr)

    if args.dry_run:
        actions = []
        if args.mac or args.random_mac:
            actions.append(
                {
                    "action": "set_mac",
                    "value": args.mac or random_mac(),
                }
            )
        if args.dhcp:
            actions.append({"action": "renew_dhcp", "backend": selected_dhcp})
        elif args.ip or args.random_ip:
            actions.append(
                {
                    "action": "set_ipv4",
                    "value": random_ip_value if args.random_ip else args.ip,
                    "netmask": random_ip_netmask if args.random_ip else args.netmask,
                    "collision_check": "not-run-dry-run" if args.random_ip else "not-applicable",
                }
            )
        plan = {"dry_run": True, "interface": args.interface, "actions": actions}
        if args.json:
            print(as_json(plan))
        else:
            print(f"Dry run for {args.interface}; no changes or probes were performed.")
            for action in actions:
                detail = ", ".join(f"{key}={value}" for key, value in action.items())
                print(f"- {detail}")
        return 0

    require_admin()

    if args._internal_daemon:
        runner = DaemonRunner(
            args.interface,
            args.interval,
            args.duration_seconds,
            args.kill_switch,
            args.network_hygiene,
            args.lock_token,
            selected_dhcp,
            selected_firewall,
        )
        return runner.run()
    if args.foreground:
        return run_foreground(
            args.interface,
            args.interval,
            args.duration_seconds,
            args.kill_switch,
            args.network_hygiene,
            selected_dhcp,
            selected_firewall,
        )
    if args.daemon:
        pid = start_daemon(
            args.interface,
            args.interval,
            args.duration_seconds,
            args.kill_switch,
            args.network_hygiene,
            selected_dhcp,
            selected_firewall,
        )
        print(f"Daemon started with PID {pid} on {args.interface}.")
        return 0

    running = active_state()
    if running:
        raise RuntimeFailure(
            f"Daemon PID {running['pid']} is active; stop it before a one-shot change or restore."
        )
    backups = BackupManager()
    if args.reset:
        saved = backups.load(args.interface)
        if (
            platform_name() == "linux"
            and saved
            and any(address.get("address_type") == "dhcp" for address in saved.ipv4_addresses)
        ):
            changer.prepare_dhcp(args.interface)
        restore_backup(args.interface, changer, backups)
        print(f"Restored {args.interface}.")
        return 0

    operations = []
    messages = []
    if args.mac or args.random_mac:
        value = args.mac or random_mac()
        operations.append(lambda value=value: changer.change_mac(args.interface, value))
        messages.append(f"MAC set to {value} on {args.interface}.")
    if args.dhcp:
        operations.append(lambda: changer.dhcp_renew(args.interface))
        messages.append(f"DHCP renewed on {args.interface} via {selected_dhcp}.")
    elif args.ip or args.random_ip:
        if args.random_ip:
            value = random_ip_value
            netmask = random_ip_netmask
        else:
            value = args.ip
            netmask = args.netmask
        operations.append(
            lambda value=value, netmask=netmask: changer.change_ip(args.interface, value, netmask)
        )
        messages.append(f"IPv4 set to {value}/{netmask} on {args.interface}.")
    apply_transaction(snapshot, changer, operations, backups=backups)
    for message in messages:
        print(message)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments:
        parser.print_help()
        return 0
    try:
        arguments = _modern_arguments(arguments)
        args = parser.parse_args(arguments)
        _validate(parser, args)
        return execute(args)
    except RuntimeFailure as error:
        print(f"netmask-cli: error: {error}", file=sys.stderr)
        return 1
