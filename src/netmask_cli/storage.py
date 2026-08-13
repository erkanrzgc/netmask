"""Permission-restricted, atomic JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import backup_file
from .interfaces import InterfaceSnapshot
from .system import RuntimeFailure


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with suppress(OSError):
        path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        with suppress(OSError):
            path.chmod(0o600)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeFailure(f"Unable to read state file {path}: {error}") from error


class BackupManager:
    def __init__(self, path: Path | None = None):
        self.path = path or backup_file()

    def _read(self) -> dict[str, dict]:
        return read_json(self.path, {})

    def save_once(self, snapshot: InterfaceSnapshot) -> bool:
        data = self._read()
        if snapshot.interface not in data:
            data[snapshot.interface] = snapshot.to_dict()
            atomic_write_json(self.path, data)
            return True
        return False

    def load(self, interface: str) -> InterfaceSnapshot | None:
        value = self._read().get(interface)
        return InterfaceSnapshot.from_dict(value) if value else None

    def remove(self, interface: str) -> None:
        data = self._read()
        if interface in data:
            del data[interface]
            atomic_write_json(self.path, data)

    def all(self) -> dict[str, InterfaceSnapshot]:
        return {name: InterfaceSnapshot.from_dict(value) for name, value in self._read().items()}


def restore_backup(interface: str, changer, backups: BackupManager) -> None:
    snapshot = backups.load(interface)
    if snapshot is None:
        raise RuntimeFailure(f"No backup found for interface {interface}")
    changer.restore(snapshot)
    backups.remove(interface)
