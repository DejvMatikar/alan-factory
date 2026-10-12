from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast


@dataclass(frozen=True)
class DeployRun:
    run_id: str
    created_at: datetime
    status: str
    conclusion: str | None


DeployArm = Literal["restart_required", "restart_free"]


@dataclass(frozen=True)
class DeployObservation:
    arm: DeployArm
    checkout_contains_merge: bool
    slot_contains_merge: bool
    served_contains_merge: bool
    health_ok: bool


def is_live(observation: DeployObservation) -> bool:
    if observation.arm == "restart_required":
        return observation.served_contains_merge and observation.health_ok
    return observation.checkout_contains_merge and observation.slot_contains_merge


GatePath = Literal["restart_free", "signal", "ceiling", "awaiting_restart", "awaiting_live"]

_GATE_RECORD_KIND = "deploy_gate_v1"
_GATE_PATHS: frozenset[str] = frozenset(
    {"restart_free", "signal", "ceiling", "awaiting_restart", "awaiting_live"}
)
_LEGACY_PATH_ALIASES: dict[str, GatePath] = {"awaiting_deploy_run": "awaiting_live"}


def _resolve_path(data: dict[str, Any]) -> GatePath | None:
    path = data.get("path")
    if isinstance(path, str) and path in _LEGACY_PATH_ALIASES:
        path = _LEGACY_PATH_ALIASES[path]
    if path not in _GATE_PATHS:
        return None
    return cast(GatePath, path)


@dataclass(frozen=True)
class GateRecord:
    merge_sha: str | None
    path: GatePath
    deferred_at: float | None
    deadline: float | None = None
    race_deadline: float | None = None
    merged_at: float | None = None
    repo: str | None = None

    def to_json(self) -> str:
        return json.dumps({
            "kind": _GATE_RECORD_KIND,
            "merge_sha": self.merge_sha,
            "path": self.path,
            "deferred_at": self.deferred_at,
            "deadline": self.deadline,
            "race_deadline": self.race_deadline,
            "merged_at": self.merged_at,
            "repo": self.repo,
        })

    @staticmethod
    def from_json(raw: str | None) -> GateRecord | None:
        data = _parse_gate_dict(raw)
        if data is None:
            return None
        path = _resolve_path(data)
        if path is None:
            return None
        merge_sha = data.get("merge_sha")
        if not _is_optional_str(merge_sha):
            return None
        repo = data.get("repo")
        if not _is_optional_str(repo):
            return None
        floats = _optional_float_fields(
            data, ("deferred_at", "deadline", "race_deadline", "merged_at")
        )
        if floats is None:
            return None
        deferred_at, deadline, race_deadline, merged_at = floats
        return GateRecord(
            merge_sha=cast("str | None", merge_sha),
            path=path,
            deferred_at=deferred_at,
            deadline=deadline,
            race_deadline=race_deadline,
            merged_at=merged_at,
            repo=cast("str | None", repo),
        )


def _parse_gate_dict(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        raw_data: object = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw_data, dict):
        return None
    data = cast(dict[str, Any], raw_data)
    if data.get("kind") != _GATE_RECORD_KIND:
        return None
    return data


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _optional_float_field(value: object) -> tuple[bool, float | None]:
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    return True, float(value)


def _optional_float_fields(
    data: dict[str, Any], keys: tuple[str, ...]
) -> list[float | None] | None:
    values: list[float | None] = []
    for key in keys:
        ok, value = _optional_float_field(data.get(key))
        if not ok:
            return None
        values.append(value)
    return values


def waited_s(record: GateRecord | None, *, now: float) -> float:
    if record is None or record.deferred_at is None:
        return 0.0
    return max(0.0, now - record.deferred_at)


__all__ = [
    "DeployArm",
    "DeployObservation",
    "DeployRun",
    "GatePath",
    "GateRecord",
    "is_live",
    "waited_s",
]
