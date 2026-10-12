from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatch import assert_team_dispatchable
from alan.dispatcher import Dispatcher
from alan.engine_config import Posture
from alan.handover import Decision, Handover
from alan.queue import Queue
from alan.teams import TEAMS
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot

_ENGINE_SLUG = "acme/widget"


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    engines_root: Path, secrets_root: Path, workspaces_root: Path
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _create_engine(client: TestClient) -> None:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}},
        headers=_control_headers(),
    )
    assert res.status_code == 201


def _set_posture(client, posture, *, confirm=True):
    body: dict[str, object] = {"posture": posture}
    if confirm is not None:
        body["confirm"] = confirm
    return client.post(
        f"/api/engines/{_ENGINE_SLUG}/posture",
        json=body,
        headers=_control_headers(),
    )


def _pinned_workspace(root: Path, *, snapshot_posture: Posture) -> Workspace:
    ws = workspace_mod.create("dial-test", "d", root=root, engine_key=_ENGINE_SLUG)
    snapshot = EngineSnapshot(posture=snapshot_posture, transition_graph={})
    return stamp_engine_snapshot(ws, snapshot)


def _pr_merge_handover(task_id: str) -> Handover:
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
                statement="judge approved PR #99",
                rationale="ok",
                confidence=1.0,
                reversibility="low",
            )
        ],
        full_synthesis="ok",
        pr_number=99,
    )


class _RecordingPrMergeTeam:
    name = "pr_merge"
    supports_resume = False

    def __init__(self) -> None:
        self.called = 0

    def plan_workflow(
        self, _workspace: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        workspace: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *, inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.called += 1
        return _pr_merge_handover(workspace.task_id)


def _dispatcher_with_pr_merge(root: Path) -> tuple[Dispatcher, _RecordingPrMergeTeam, Queue]:
    queue = Queue(db_path=root / "queue.db")
    recording = _RecordingPrMergeTeam()
    teams: dict[str, Team] = dict(TEAMS)
    teams["pr_merge"] = recording
    return Dispatcher(queue, teams, workspaces_root=root), recording, queue


def test_downgrade_via_endpoint_floors_next_route_admitted_set(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    assert _set_posture(client, "self_merge").status_code == 200

    ws = _pinned_workspace(workspaces_root, snapshot_posture=Posture.self_merge)
    d, _recording, queue = _dispatcher_with_pr_merge(workspaces_root)
    try:
        assert "pr_merge" in d._effective_teams(ws)  # noqa: SLF001

        assert _set_posture(client, "observe").status_code == 200

        assert "pr_merge" not in d._effective_teams(ws)  # noqa: SLF001
    finally:
        queue.close()


def test_downgrade_via_endpoint_yanks_merge_rights_mid_flight(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    assert _set_posture(client, "self_merge").status_code == 200

    ws = _pinned_workspace(workspaces_root, snapshot_posture=Posture.self_merge)
    d, recording, queue = _dispatcher_with_pr_merge(workspaces_root)
    try:
        assert _set_posture(client, "observe").status_code == 200

        d._apply_router_decision(ws, _pr_merge_handover(ws.task_id))  # noqa: SLF001

        assert recording.called == 0
        enqueued_teams = [j.team for j in queue.list_jobs()]
        assert "pr_merge" not in enqueued_teams
    finally:
        queue.close()


def test_upgrade_via_endpoint_holds_pr_merge_absent_until_restamp(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    ws = _pinned_workspace(workspaces_root, snapshot_posture=Posture.observe)
    d, _recording, queue = _dispatcher_with_pr_merge(workspaces_root)
    try:
        assert not d._effective_teams(ws)  # noqa: SLF001

        assert _set_posture(client, "autonomous").status_code == 200

        effective = d._effective_teams(ws)  # noqa: SLF001
        assert "pr_merge" not in effective
        assert not effective
    finally:
        queue.close()


def test_manual_dispatch_into_downgraded_engine_422s(
    client: TestClient, workspaces_root: Path
) -> None:
    _create_engine(client)
    assert _set_posture(client, "self_merge").status_code == 200
    ws = _pinned_workspace(workspaces_root, snapshot_posture=Posture.self_merge)

    assert _set_posture(client, "observe").status_code == 200

    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - HTTPException, imported lazily
        assert_team_dispatchable(ws, ws.task_id, "pr_merge")
    assert getattr(exc_info.value, "status_code", None) == 422


def test_post_posture_without_confirm_true_422s(client: TestClient) -> None:
    _create_engine(client)
    assert _set_posture(client, "propose", confirm=None).status_code == 422
    assert _set_posture(client, "propose", confirm=False).status_code == 422


def test_post_posture_without_control_token_401s(client: TestClient) -> None:
    _create_engine(client)
    res = client.post(
        f"/api/engines/{_ENGINE_SLUG}/posture",
        json={"posture": "propose", "confirm": True},
    )
    assert res.status_code == 401


def test_posture_changes_are_visible_in_the_audit_trail(
    client: TestClient, engines_root: Path
) -> None:
    _create_engine(client)
    assert _set_posture(client, "propose").status_code == 200
    assert _set_posture(client, "observe").status_code == 200

    audit_path = engines_root / "posture_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [e["to"] for e in entries] == ["propose", "observe"]
    assert all(e["repo"] == _ENGINE_SLUG for e in entries)
    assert all(e["source"] == "api" for e in entries)
    assert entries[0]["from"] == "observe"
    assert entries[1]["from"] == "propose"
