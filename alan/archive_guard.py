from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Workspace
from alan.workspace_dag import all_leaves_done, incomplete_subtask_ids

INCOMPLETE_SUBTASKS_PREFIX = "workspace has incomplete subtasks"

_ARCHIVE_AUDIT_FILE = "archive_audit.jsonl"


class ArchiveRequest(BaseModel):
    force: bool = False
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _reject_forced_without_reason(self) -> "ArchiveRequest":
        if self.force and not self.reason.strip():
            raise ValueError("reason is required when force=true")
        return self


def refusal_detail(ws: Workspace) -> str | None:
    if all_leaves_done(ws):
        return None
    return f"{INCOMPLETE_SUBTASKS_PREFIX}: {', '.join(incomplete_subtask_ids(ws))}"


def record_forced_archive(ws: Workspace, reason: str) -> None:
    append_jsonl_audit(
        logs_root() / _ARCHIVE_AUDIT_FILE,
        {"task_id": ws.task_id, "reason": reason},
        log_label="archive_force",
    )
