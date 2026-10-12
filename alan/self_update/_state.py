from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from alan import self_update
from alan.self_update import _journal

_STATUS_KEYS = frozenset({
    "verdict", "reason", "fetched_sha", "head_sha", "checked_at",
    "path", "target_sha", "previous_sha", "previous_slot",
})


@dataclass(frozen=True)
class UpdaterState:
    status: self_update.StatusRecord | None
    journal: _journal.Journal | None


def _opt_str(fields: dict[str, object], key: str) -> str | None:
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _apply_path(fields: dict[str, object]) -> self_update.ApplyPath | None:
    value = fields.get("path")
    return self_update.ApplyPath(str(value)) if value is not None else None


def _parse_status(payload: object) -> self_update.StatusRecord | None:
    if not isinstance(payload, dict):
        return None
    fields = cast("dict[str, object]", payload)
    if not _STATUS_KEYS <= fields.keys():
        return None
    checked_at = fields.get("checked_at")
    if not isinstance(checked_at, str):
        return None
    try:
        return self_update.StatusRecord(
            verdict=self_update.Verdict(str(fields["verdict"])),
            reason=self_update.Reason(str(fields["reason"])),
            fetched_sha=_opt_str(fields, "fetched_sha"),
            head_sha=_opt_str(fields, "head_sha"),
            checked_at=checked_at,
            path=_apply_path(fields),
            target_sha=_opt_str(fields, "target_sha"),
            previous_sha=_opt_str(fields, "previous_sha"),
            previous_slot=_opt_str(fields, "previous_slot"),
        )
    except (ValueError, TypeError):
        return None


def _load_status() -> self_update.StatusRecord | None:
    try:
        text = self_update.status_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload: object = json.loads(text)
    except ValueError:
        return None
    return _parse_status(payload)


def load_state() -> UpdaterState:
    return UpdaterState(status=_load_status(), journal=_journal.read())
