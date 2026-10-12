from __future__ import annotations

from datetime import UTC, datetime

from alan import default_profile
from alan.self_update._state import UpdaterState, load_state
from alan.teams._never_live_budget import stale_threshold_s
from alan.teams.triage_pregate import ClassifiedSurface

_KIND = "auto_update"
_BLOCKED_VERDICT = "blocked"
_RED_VERDICT = "red"
_MAIN_RED_FLAG = "main_red"
_ROLLED_BACK_PHASE = "rolled_back"
_ROLLBACK_FAILED_PHASE = "rollback_failed"
_STALE_FLAG = "stale"
_JOURNAL_FLAGS = frozenset({_ROLLED_BACK_PHASE, _ROLLBACK_FAILED_PHASE})


def _surface(flag: str, obs: dict[str, object]) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"{_KIND}:{flag}",
        kind=_KIND,
        is_blocked=True,
        blocker_reason=flag,
        obs=obs,
    )


def _blocked_or_red_surface(
    verdict: str, reason: str, checked_at: str
) -> ClassifiedSurface | None:
    if verdict == _BLOCKED_VERDICT:
        return _surface(reason, {"reason": reason, "checked_at": checked_at})
    if verdict == _RED_VERDICT:
        return _surface(_MAIN_RED_FLAG, {"reason": _MAIN_RED_FLAG, "checked_at": checked_at})
    return None


def _rollback_surface(state: UpdaterState) -> ClassifiedSurface | None:
    journal = state.journal
    if journal is None:
        return None
    phase = journal.phase.value
    if phase not in _JOURNAL_FLAGS:
        return None
    return _surface(phase, {"reason": phase, "target_sha": journal.target_sha})


def _parse_checked_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _stale_surface(checked_at: str, now: datetime) -> ClassifiedSurface | None:
    parsed = _parse_checked_at(checked_at)
    if parsed is None or (now - parsed).total_seconds() <= stale_threshold_s():
        return None
    return _surface(_STALE_FLAG, {"reason": _STALE_FLAG, "checked_at": checked_at})


def auto_update_surfaces(*, now: datetime | None = None) -> list[ClassifiedSurface]:
    if default_profile.resolved().source is not default_profile.Source.profile:
        return []
    state = load_state()
    status = state.status
    if status is None:
        return []
    moment = now if now is not None else datetime.now(UTC)
    return [
        surface
        for surface in (
            _blocked_or_red_surface(status.verdict.value, status.reason.value, status.checked_at),
            _rollback_surface(state),
            _stale_surface(status.checked_at, moment),
        )
        if surface is not None
    ]
