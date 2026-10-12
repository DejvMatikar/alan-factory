from __future__ import annotations

from pathlib import Path
from typing import Literal

from alan.teams._audit import append_jsonl_audit

ResumeOutcome = Literal[
    "attached",
    "attach_lost",
    "requeued_for_resume",
    "interrupted_failed",
    "resume_succeeded",
    "resume_failed",
    "rerun_never_started",
    "rerun_cap_exhausted",
]


def record_resume_outcome(
    path: Path,
    *,
    task_id: str,
    team: str,
    inv_id: str,
    outcome: ResumeOutcome,
    node_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    entry: dict[str, object] = {
        "task_id": task_id,
        "team": team,
        "inv_id": inv_id,
        "outcome": outcome,
    }
    if node_id is not None:
        entry["node_id"] = node_id
    if extra:
        entry.update(extra)
    append_jsonl_audit(path, entry, log_label="resume")


__all__ = ["ResumeOutcome", "record_resume_outcome"]
