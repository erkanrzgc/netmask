"""Transactional network changes with immediate best-effort rollback."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .interfaces import InterfaceSnapshot
from .storage import BackupManager
from .system import RuntimeFailure

Operation = Callable[[], None]


def apply_transaction(
    snapshot: InterfaceSnapshot,
    changer,
    operations: Iterable[Operation],
    *,
    backups: BackupManager | None = None,
) -> None:
    """Apply operations and restore the pre-command snapshot on any failure."""
    backup_created = backups.save_once(snapshot) if backups else False
    try:
        for operation in operations:
            operation()
    except Exception as operation_error:
        try:
            changer.restore(snapshot)
        except Exception as rollback_error:
            raise RuntimeFailure(
                f"Change failed ({operation_error}); automatic rollback also failed "
                f"({rollback_error}). The backup was preserved."
            ) from rollback_error
        if backups and backup_created:
            backups.remove(snapshot.interface)
        raise RuntimeFailure(
            f"Change failed ({operation_error}); the pre-command interface state was restored."
        ) from operation_error
