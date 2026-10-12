from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from alan import store
from alan.roots import state_root

logger = logging.getLogger(__name__)

_TRIAGE_SWEEP_STATUS_FILE = "triage_sweep_status.json"


class TriageSweepStatus(BaseModel, frozen=True):
    outcome: str
    duration_seconds: float
    computed_at: str


def _triage_sweep_status_path() -> Path:
    return state_root() / _TRIAGE_SWEEP_STATUS_FILE


def _write_triage_sweep_status(  # pyright: ignore[reportUnusedFunction]
    outcome: str, duration_seconds: float
) -> None:
    status = TriageSweepStatus(
        outcome=outcome,
        duration_seconds=duration_seconds,
        computed_at=datetime.now(UTC).isoformat(),
    )
    path = _triage_sweep_status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_mkstemp(
            path, status.model_dump_json(), prefix=".triage_sweep_status_"
        )
    except OSError:
        logger.warning("triage: failed to persist sweep status")


def load_triage_sweep_status() -> TriageSweepStatus | None:
    try:
        raw = _triage_sweep_status_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return TriageSweepStatus.model_validate_json(raw)
    except ValidationError:
        return None


__all__ = [
    "TriageSweepStatus",
    "load_triage_sweep_status",
]
