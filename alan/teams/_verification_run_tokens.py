from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from alan.engine_config import ForgeTarget
from alan.teams import _clause_audit, _review_context
from alan.teams._verification_artifacts import reviewer_count_var
from alan.workspace import Workspace


@contextmanager
def scope(
    workspace: Workspace,
    pr_number: int,
    effective_task_id: str,
    forge_target: ForgeTarget,
    grounded_path: Path,
    *,
    grounded_var: contextvars.ContextVar[Path | None],
    task_id_var: contextvars.ContextVar[str | None],
    review_context_var: contextvars.ContextVar[str | None],
) -> Generator[None, None, None]:
    grounded_token = grounded_var.set(grounded_path)
    tid_token = task_id_var.set(effective_task_id)
    rc_token = review_context_var.set(
        _review_context.diff_reviewer_prefix(
            pr_number, workspace, effective_task_id, checkout=grounded_path
        )
    )
    rv_token = reviewer_count_var.set(None)
    ca_token = _clause_audit.begin(workspace, pr_number, effective_task_id, forge_target)
    try:
        yield
    finally:
        _clause_audit.end(ca_token)
        task_id_var.reset(tid_token)
        grounded_var.reset(grounded_token)
        review_context_var.reset(rc_token)
        reviewer_count_var.reset(rv_token)


__all__ = ["scope"]
