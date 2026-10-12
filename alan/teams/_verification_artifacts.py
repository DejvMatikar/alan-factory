from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path
from typing import cast

from alan.handover import ArtifactRef
from alan.roots import logs_root
from alan.teams import _verdict
from alan.teams._audit import append_jsonl_audit
from alan.teams._pr_json import _JsonObject  # pyright: ignore[reportPrivateUsage]
from alan.workspace import Workspace

_AUDIT_FILE = "verification_audit.jsonl"

_REVIEW_DIR_NAME = ".alan"
_REVIEW_FILE_TEMPLATE = "verification_review_{inv_id}.md"
_REVIEW_DESCRIPTION_TEMPLATE = "full judge review (inv {inv_id})"
_COUNT_FILE_TEMPLATE = "verification_counts_{inv_id}.json"

reviewer_count_var: contextvars.ContextVar[
    _verdict.CriticalCountReport | None
] = contextvars.ContextVar("_verification_reviewer_count", default=None)

_DRY_RUN_ENV = "ALAN_VERIFICATION_DRY_RUN"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def dry_run_enabled() -> bool:
    return os.environ.get(_DRY_RUN_ENV, "").strip().lower() in _TRUTHY


def append_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE, entry, log_label="verification"
    )


def review_artifact_path(workspace: Workspace, inv_id: str) -> Path:
    return workspace.path / _REVIEW_DIR_NAME / _REVIEW_FILE_TEMPLATE.format(inv_id=inv_id)


def count_record_path(workspace: Workspace, inv_id: str) -> Path:
    return workspace.path / _REVIEW_DIR_NAME / _COUNT_FILE_TEMPLATE.format(inv_id=inv_id)


def _write_count_record(
    workspace: Workspace, inv_id: str, counts: _verdict.CriticalCountReport
) -> None:
    count_path = count_record_path(workspace, inv_id)
    count_path.parent.mkdir(parents=True, exist_ok=True)
    count_path.write_text(
        json.dumps({
            "inv_id": inv_id,
            "reviewer": counts.reviewer,
            "judge_reported": counts.judge_reported,
        }),
        encoding="utf-8",
    )


def _write_review_artifact(  # pyright: ignore[reportUnusedFunction]
    workspace: Workspace, inv_id: str, judge_output: str,
    *, counts: _verdict.CriticalCountReport | None = None,
) -> ArtifactRef:
    review_path = review_artifact_path(workspace, inv_id)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(judge_output, encoding="utf-8")
    record = counts if counts is not None else reviewer_count_var.get()
    if record is not None:
        _write_count_record(workspace, inv_id, record)
    return ArtifactRef(
        path=str(review_path),
        description=_REVIEW_DESCRIPTION_TEMPLATE.format(inv_id=inv_id),
    )


def read_review_artifact(workspace: Workspace, inv_id: str) -> str | None:
    try:
        return review_artifact_path(workspace, inv_id).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _valid_reviewer_count(value: object) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def read_count_record(
    workspace: Workspace, inv_id: str
) -> _verdict.CriticalCountReport | None:
    try:
        raw = json.loads(count_record_path(workspace, inv_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    record = cast(_JsonObject, raw)
    if record.get("inv_id") != inv_id:
        return None
    reviewer = record.get("reviewer")
    judge_reported = record.get("judge_reported")
    if not _valid_reviewer_count(reviewer) or not isinstance(judge_reported, bool):
        return None
    return _verdict.CriticalCountReport(reviewer=reviewer, judge_reported=judge_reported)
