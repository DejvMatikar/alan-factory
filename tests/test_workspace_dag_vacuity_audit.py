from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan import workspace_dag
from alan.workspace import Requirement, Status


def _ws(root: Path, *, requirements: list[Requirement] | None) -> workspace_mod.Workspace:
    return workspace_mod.create(
        "vacuity-ws", "d", root=root, requirements=requirements
    )


def _audit_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / "acceptance_vacuity_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_vacuous_workspace_still_completes_and_leaves_an_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(tmp_path / "ws", requirements=[Requirement(text="unbound clause")])

    status = workspace_dag.workspace_terminal_status(ws)

    assert status is Status.COMPLETED
    rows = _audit_rows(logs)
    assert len(rows) == 1
    assert rows[0]["task_id"] == ws.task_id
    assert rows[0]["workspace_id"] == ws.workspace_id


def test_legacy_no_requirements_workspace_completes_without_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(tmp_path / "ws", requirements=None)

    status = workspace_dag.workspace_terminal_status(ws)

    assert status is Status.COMPLETED
    assert _audit_rows(logs) == []


def test_test_ref_bound_requirement_completes_without_audit_row_once_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(
        tmp_path / "ws",
        requirements=[Requirement(text="bound clause", test_ref="t1")],
    )
    assert ws.meta.requirements is not None
    ws = workspace_mod.update_requirements(
        ws, [ws.meta.requirements[0].model_copy(update={"status": "PASS"})]
    )

    status = workspace_dag.workspace_terminal_status(ws)

    assert status is Status.COMPLETED
    assert _audit_rows(logs) == []
