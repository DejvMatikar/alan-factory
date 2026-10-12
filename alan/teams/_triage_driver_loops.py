from __future__ import annotations

from alan import driver_loop_status
from alan.driver_loop_registry import QuarantinedDriverLoop, load_registry
from alan.driver_loop_runs import RunOutcome
from alan.driver_loop_schedule import HoldReason
from alan.driver_loop_status import LoopStatusView
from alan.queue import Queue
from alan.teams.triage_pregate import ClassifiedSurface

UNHEALTHY_KIND = "driver_loop_unhealthy"
ATTENTION_KIND = "driver_loop_attention"
CONFIG_QUARANTINE_KIND = "config_quarantine"

_FAILING_OUTCOMES: frozenset[RunOutcome] = frozenset({RunOutcome.FAILED, RunOutcome.TIMED_OUT})
_SUPPRESSING_HOLDS: frozenset[HoldReason] = frozenset(
    {HoldReason.NOT_SUPERVISED, HoldReason.DISABLED, HoldReason.PAUSED}
)


def _is_suppressed(view: LoopStatusView) -> bool:
    decision = view.decision
    return decision.action == "hold" and decision.reason in _SUPPRESSING_HOLDS


def _unhealthy_reason(view: LoopStatusView) -> str | None:
    if _is_suppressed(view):
        return None
    decision = view.decision
    if decision.action == "spawn":
        return "overdue"
    if decision.action == "hold":
        if decision.reason == HoldReason.BACKOFF:
            return "backing_off"
        if decision.reason == HoldReason.FAST_FORWARD_FAILED:
            return "fast_forward_failed"
    if view.last_outcome in _FAILING_OUTCOMES:
        return "failing"
    return None


def _attention_reason(view: LoopStatusView) -> str | None:
    if view.decision.action == "hold" and view.decision.reason == HoldReason.INTERACTIVE_LOCK:
        return "interactive_lock"
    if _is_suppressed(view):
        return None
    if view.last_outcome == RunOutcome.RAN_WITH_DENIALS:
        return "ran_with_denials"
    return None


def _surface(kind: str, view: LoopStatusView, reason: str) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"{kind}:{view.name}",
        kind=kind,
        is_blocked=True,
        blocker_reason=reason,
        obs={"name": view.name, "reason": reason},
    )


def _surfaces_for(view: LoopStatusView) -> list[ClassifiedSurface]:
    unhealthy_reason = _unhealthy_reason(view)
    if unhealthy_reason is not None:
        return [_surface(UNHEALTHY_KIND, view, unhealthy_reason)]
    attention_reason = _attention_reason(view)
    if attention_reason is not None:
        return [_surface(ATTENTION_KIND, view, attention_reason)]
    return []


def driver_loop_surfaces(*, queue: Queue) -> list[ClassifiedSurface]:
    surfaces: list[ClassifiedSurface] = []
    for view in driver_loop_status.snapshot(queue=queue):
        surfaces.extend(_surfaces_for(view))
    return surfaces


def _quarantine_surface(record: QuarantinedDriverLoop) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"{CONFIG_QUARANTINE_KIND}:{record.path}",
        kind=CONFIG_QUARANTINE_KIND,
        is_blocked=True,
        blocker_reason=CONFIG_QUARANTINE_KIND,
        obs={"path": str(record.path), "error": record.error, "repo": None},
    )


def driver_loop_config_quarantine_surfaces() -> list[ClassifiedSurface]:
    _entries, quarantined = load_registry()
    return [_quarantine_surface(record) for record in quarantined]


__all__ = [
    "ATTENTION_KIND",
    "CONFIG_QUARANTINE_KIND",
    "UNHEALTHY_KIND",
    "driver_loop_config_quarantine_surfaces",
    "driver_loop_surfaces",
]
