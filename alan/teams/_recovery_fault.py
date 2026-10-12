from __future__ import annotations

from alan.slot_pool import PoolBusyError, SlotReservedError

RECOVERY_FAULT_TYPES: tuple[type[Exception], ...] = (SlotReservedError, PoolBusyError)


def is_recovery_fault(exc: Exception) -> bool:
    return isinstance(exc, RECOVERY_FAULT_TYPES)


__all__ = ["RECOVERY_FAULT_TYPES", "is_recovery_fault"]
