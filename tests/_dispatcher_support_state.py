from __future__ import annotations

import contextlib
import json
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import Posture
from alan.handover import Handover
from alan.queue import Queue
from alan.router import TEAM_TRANSITION_POLICY
from alan.teams._base import Team, TeamEvent
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot
from tests._dispatcher_support_teams import _RecordingTeam


def make_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def make_db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


def make_queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def suppress_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _make_collector() -> tuple[list[TeamEvent], Callable[[TeamEvent], None]]:
    events: list[TeamEvent] = []
    lock = threading.Lock()

    def collect(event: TeamEvent) -> None:
        with lock:
            events.append(event)

    return events, collect


def _pr_merge_handover(task_id: str, pr_number: int | None = 99) -> Handover:
    from alan.handover import Decision

    return Handover(
        handover_id=f"handover_{task_id}_pr",
        handover_type="forward",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["pr_merge"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="verif-approve",
                statement=f"judge approved PR #{pr_number or 0}",
                rationale="ok",
                confidence=1.0,
                reversibility="low",
            )
        ],
        full_synthesis="ok",
        pr_number=pr_number,
    )


def _build_dispatcher_with_pr_merge(
    queue: Queue, root: Path
) -> tuple[Dispatcher, _RecordingTeam]:
    from alan.teams import TEAMS as REAL_TEAMS

    recording = _RecordingTeam()
    teams: dict[str, Team] = dict(REAL_TEAMS)
    teams["pr_merge"] = recording
    return Dispatcher(queue, teams, workspaces_root=root), recording


_FLAKY_GATE_NAME = "Frontend E2E (Playwright, blocking)"


def _monkeypatch_transient_flake_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths

    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="CLEAN",
            review_decision="APPROVED",
            failing_checks=(_FLAKY_GATE_NAME,),
            failed_reasons=(f"failing checks: {_FLAKY_GATE_NAME}",),
        ),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_kw: 1)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)


def _monkeypatch_unready_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.teams import _pr_ready

    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=False,
            merge_state_status="BLOCKED",
            review_decision="APPROVED",
            failed_reasons=("mergeStateStatus=BLOCKED",),
        ),
    )


def _write_multi_task_meta(ws: Workspace, tasks: list[dict[str, Any]]) -> Workspace:
    meta_path = ws.path / "meta.json"
    data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = tasks
    meta_path.write_text(json.dumps(data), encoding="utf-8")
    return workspace_mod.read(ws.task_id, root=ws.path.parent.parent.parent)


def _ws_with_dod(
    root: Path,
    *,
    covers: list[str],
    evidence: str | None = None,
    requirements: list[object] | None = None,
    task_id: str = "sub",
) -> Workspace:
    ws = workspace_mod.create(
        "stamp", "d", root=root, requirements=requirements  # type: ignore[arg-type]
    )
    task = workspace_mod.Task(
        id=task_id,
        status=workspace_mod.TaskStatus.COMPLETED,
        dod_clauses=[
            workspace_mod.TaskDoDClause(text="x", covers=covers, evidence=evidence)
        ],
    )
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    workspace_mod._write_meta(ws.path, new_meta)  # noqa: SLF001
    return workspace_mod.read(ws.task_id, root=root)


def _make_injected_task(
    local_id: str = "subtask-a",
    team: str = "research",
    instructions: str | None = None,
    depends_on: list[str] | None = None,
) -> Any:
    from alan.handover import InjectedTask
    return InjectedTask(
        id=local_id, team=team, instructions=instructions, depends_on=depends_on or []
    )


def _write_inj_task_meta(ws: Workspace, task_rows: list[dict[str, Any]]) -> Workspace:
    meta_path = ws.path / "meta.json"
    data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tasks"] = task_rows
    meta_path.write_text(json.dumps(data), encoding="utf-8")
    return workspace_mod.read(ws.task_id, root=ws.path.parent.parent.parent)


def _read_merge_gate_audit(tmp_path: Path) -> list[dict[str, object]]:
    audit_file = tmp_path / "logs" / "merge_gate_audit.jsonl"
    if not audit_file.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in audit_file.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _ws_with_partial_tasks(
    ws: Workspace,
    completed_evidence: list[str | None],
    *,
    extra_failed: bool = True,
) -> Workspace:
    tasks: list[dict[str, Any]] = []
    for i, ev in enumerate(completed_evidence):
        dod: list[dict[str, Any]] = [{"text": "shipped", "evidence": ev}] if ev else []
        tasks.append(
            {"id": f"t{i}", "status": "completed", "dod_clauses": dod, "depends_on": []}
        )
    if extra_failed:
        tasks.append({"id": "failed-leaf", "status": "failed", "depends_on": []})
    return _write_multi_task_meta(ws, tasks)


def _ws_with_snapshot_and_engine_key(
    root: Path, *, snapshot_posture: Posture, engine_key: str
) -> Workspace:
    ws = workspace_mod.create("live-floor", "d", root=root, engine_key=engine_key)
    snapshot = EngineSnapshot(
        posture=snapshot_posture,
        transition_graph={k: list(v) for k, v in TEAM_TRANSITION_POLICY.items()},
    )
    return stamp_engine_snapshot(ws, snapshot)


def suppress_proactive_rebase_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "alan.proactive_rebase.maybe_trigger_proactive_rebase", lambda **_kw: None
    )


def isolate_pr_state_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", tempfile.mkdtemp(prefix="alan-test-pr-state-"))
