"""Crash recovery for preserved backups and Netmask-owned firewall objects."""

from __future__ import annotations

from .backends import backend
from .config import daemon_file, daemon_lock_file
from .daemon import _state, active_state
from .killswitch import create_kill_switch
from .storage import BackupManager, restore_backup
from .system import RuntimeFailure, platform_name


def recover(interface: str | None = None) -> list[str]:
    running = active_state()
    if running:
        raise RuntimeFailure(f"Daemon PID {running['pid']} is still active; stop it first.")

    stale = _state()
    backups = BackupManager()
    available = backups.all()
    targets = [interface] if interface else sorted(available)
    targets = [name for name in targets if name in available]
    relevant_stale = bool(stale and (interface is None or stale.get("interface") == interface))
    if not targets and not relevant_stale:
        raise RuntimeFailure("No recoverable daemon state or interface backup was found.")

    messages: list[str] = []
    errors: list[str] = []
    if (
        stale
        and stale.get("firewall_backend")
        and stale.get("interface")
        and (interface is None or stale["interface"] == interface)
    ):
        try:
            create_kill_switch(stale["interface"], stale["firewall_backend"]).cleanup()
            messages.append(
                f"Removed stale {stale['firewall_backend']} state for {stale['interface']}."
            )
        except Exception as error:
            errors.append(f"firewall cleanup: {error}")

    for name in targets:
        snapshot = available[name]
        changer = backend()
        try:
            if platform_name() == "linux" and any(
                address.get("address_type") == "dhcp" for address in snapshot.ipv4_addresses
            ):
                changer.prepare_dhcp(name)
            restore_backup(name, changer, backups)
            messages.append(f"Restored {name} from its preserved snapshot.")
        except Exception as error:
            errors.append(f"{name}: {error}")

    if errors:
        raise RuntimeFailure("Recovery incomplete: " + "; ".join(errors))
    daemon_file().unlink(missing_ok=True)
    daemon_lock_file().unlink(missing_ok=True)
    return messages
