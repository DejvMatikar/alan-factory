from __future__ import annotations

from alan.slot_pool import PoolBusyError, SlotReservedError
from alan.teams._recovery_fault import RECOVERY_FAULT_TYPES, is_recovery_fault


def test_recovery_fault_types_is_slot_reserved_and_pool_busy() -> None:
    assert RECOVERY_FAULT_TYPES == (SlotReservedError, PoolBusyError)


def test_is_recovery_fault_true_for_slot_reserved_error() -> None:
    assert is_recovery_fault(SlotReservedError("x")) is True


def test_is_recovery_fault_true_for_pool_busy_error() -> None:
    assert is_recovery_fault(PoolBusyError("x")) is True


def test_is_recovery_fault_false_for_other_exception() -> None:
    assert is_recovery_fault(RuntimeError("x")) is False


def test_is_recovery_fault_false_for_no_pool_for_engine_error() -> None:
    class _NoPoolForEngineError(RuntimeError):
        pass

    assert is_recovery_fault(_NoPoolForEngineError("no pool")) is False
