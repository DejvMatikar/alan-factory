from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, Posture, RepoIdentity
from alan.events import PubSub
from alan.handover import Decision, Handover, save_handover
from alan.queue import Queue
from alan.workspace import (
    EngineSnapshot,
    InterventionAction,
    Requirement,
    Status,
    Task,
    TaskStatus,
    TestScenario,
    Workspace,
    create,
    read,
    stamp_engine_snapshot,
    transition,
)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def bc_root(tmp_path: Path) -> Path:
    return tmp_path / "bc"


@pytest.fixture
def ec_root(tmp_path: Path) -> Path:
    return tmp_path / "ec"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


@pytest.fixture
def client(
    ws_root: Path,
    bc_root: Path,
    ec_root: Path,
    queue: Queue,
    pubsub: PubSub,
    dispatcher: Dispatcher,
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher  # noqa: SLF001
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (  # noqa: SLF001
        lambda: bc_root
    )
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (  # noqa: SLF001
        lambda: ec_root
    )
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _scenario(scenario_id: str, when: str = "uv run pytest -k x") -> TestScenario:
    return TestScenario(id=scenario_id, description="d", given="g", when=when, then="ok")


def _seed_handover(ws: Workspace, scenarios: list[TestScenario]) -> Handover:
    handover = Handover(
        handover_id=f"handover_{ws.task_id}_001",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_plan",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(id="d1", statement="s", rationale="r", confidence=0.5, reversibility="low")
        ],
        full_synthesis="ok",
        test_scenarios=scenarios,
    )
    save_handover(ws, handover)
    return handover


def _zero_subtask_ws(ws_root: Path, title: str, scenario_id: str = "s1") -> Workspace:
    ws = create(title, "d", root=ws_root)
    _seed_handover(ws, [_scenario(scenario_id)])
    return ws


def _put(
    client: TestClient,
    ws: Workspace,
    scenario_id: str,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.put(
            f"/api/workspaces/{ws.task_id}/scenarios/{scenario_id}/binding",
            json=body,
            headers=_control_headers() if headers is None else headers,
        ),
    )


def _delete(
    client: TestClient,
    ws: Workspace,
    scenario_id: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.delete(
            f"/api/workspaces/{ws.task_id}/scenarios/{scenario_id}/binding",
            headers=_control_headers() if headers is None else headers,
        ),
    )


def test_get_scenarios_zero_subtask_workspace(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Zero Subtask GET")
    res = client.get(f"/api/workspaces/{ws.task_id}/scenarios")
    assert res.status_code == 200
    data = res.json()
    assert [s["id"] for s in data["scenarios"]] == ["s1"]
    assert data["waived_scenario_ids"] == []
    assert data["mode"] == "RUN"


def test_get_scenarios_unknown_workspace_404(client: TestClient) -> None:
    res = client.get("/api/workspaces/ghost/scenarios")
    assert res.status_code == 404


def test_get_scenarios_n_subtask_workspace_uses_latest_handover(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "N Subtask GET", "d", root=ws_root,
        tasks=[Task(id="sub-a", status=TaskStatus.PENDING)],
    )
    _seed_handover(ws, [_scenario("s1")])
    res = client.get(f"/api/workspaces/{ws.task_id}/scenarios")
    assert res.status_code == 200
    assert [s["id"] for s in res.json()["scenarios"]] == ["s1"]


def test_put_binding_repoints_stale_when_on_zero_subtask_workspace(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Repoint WS")
    res = _put(
        client, ws, "s1",
        {"when": "uv run pytest tests/test_new_location.py::test_it", "reason": "test moved"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["scenarios"][0]["when"] == "uv run pytest tests/test_new_location.py::test_it"
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings["s1"].when == (
        "uv run pytest tests/test_new_location.py::test_it"
    )


def test_put_binding_waives_scenario(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Waive WS")
    res = _put(client, ws, "s1", {"waived": True, "reason": "known broken, tracked in #1"})
    assert res.status_code == 200
    data = res.json()
    assert data["waived_scenario_ids"] == ["s1"]
    assert data["mode"] == "WAIVED"


def test_put_binding_records_intervention(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Intervention WS")
    _put(client, ws, "s1", {"waived": True, "reason": "r"})
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [
        InterventionAction.SCENARIO_OVERRIDE
    ]


def test_put_binding_writes_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "Audit WS")
    _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "test moved"})
    entry = json.loads(
        (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()[0]
    )
    assert entry["kind"] == "binding"
    assert entry["action"] == "put"
    assert entry["workspace_id"] == ws.task_id
    assert entry["scenario_id"] == "s1"
    assert entry["when"] == "uv run pytest -k y"
    assert entry["reason"] == "test moved"


def test_put_binding_repeated_call_replaces_not_merges(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Replace WS")
    first = _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r1"})
    assert first.status_code == 200
    second = _put(client, ws, "s1", {"waived": True, "reason": "r2"})
    assert second.status_code == 200
    data = second.json()
    assert data["waived_scenario_ids"] == ["s1"]
    assert data["scenarios"][0]["when"] == "uv run pytest -k x"


def test_put_binding_n_subtask_workspace(client: TestClient, ws_root: Path) -> None:
    ws = create(
        "N Subtask PUT", "d", root=ws_root,
        tasks=[Task(id="sub-a", status=TaskStatus.PENDING)],
    )
    _seed_handover(ws, [_scenario("s1")])
    res = _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r"})
    assert res.status_code == 200
    assert res.json()["scenarios"][0]["when"] == "uv run pytest -k y"


def test_put_binding_without_control_token_401(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "401 WS")
    res = _put(client, ws, "s1", {"waived": True, "reason": "r"}, headers={})
    assert res.status_code == 401


def test_put_binding_wrong_control_token_403(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "403 WS")
    res = _put(
        client, ws, "s1", {"waived": True, "reason": "r"},
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_put_binding_unknown_workspace_404(client: TestClient) -> None:
    res = client.put(
        "/api/workspaces/ghost/scenarios/s1/binding",
        json={"waived": True, "reason": "r"},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_put_binding_unknown_scenario_id_404(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "404 Scenario WS")
    res = _put(client, ws, "ghost-scenario", {"waived": True, "reason": "r"})
    assert res.status_code == 404


def test_put_binding_archived_workspace_409(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Archived WS")
    transition(ws, Status.ARCHIVED)
    res = _put(client, ws, "s1", {"waived": True, "reason": "r"})
    assert res.status_code == 409


def test_put_binding_prose_when_422(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Prose WS")
    res = _put(
        client, ws, "s1",
        {"when": "the user manually verifies in the browser", "reason": "r"},
    )
    assert res.status_code == 422


def test_put_binding_uses_workspace_governing_config_allowlist(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Custom Engine WS")
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        gate_commands=[["make", "test"]],
    )
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.propose, transition_graph={}, config=cfg)
    )
    res = _put(client, ws, "s1", {"when": "make test -- -k x", "reason": "test moved"})
    assert res.status_code == 200
    assert res.json()["scenarios"][0]["when"] == "make test -- -k x"


def test_put_binding_empty_body_422(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Empty Body WS")
    res = _put(client, ws, "s1", {"reason": "r"})
    assert res.status_code == 422


def test_put_binding_waive_blocked_by_live_requirement_test_ref_409(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Requirement Bound WS", "d", root=ws_root,
        requirements=[Requirement(text="acceptance clause", test_ref="s1")],
    )
    _seed_handover(ws, [_scenario("s1")])
    res = _put(client, ws, "s1", {"waived": True, "reason": "r"})
    assert res.status_code == 409


def test_put_binding_test_ref_only_changes_resolved_command(
    client: TestClient, ws_root: Path
) -> None:
    from alan.teams._scenario_runner import resolved_command

    ws = _zero_subtask_ws(ws_root, "Test Ref Only WS")
    res = _put(
        client, ws, "s1",
        {"test_ref": "tests/test_new_location.py::test_it", "reason": "test moved"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["scenarios"][0]["test_ref"] == "tests/test_new_location.py::test_it"
    assert data["scenarios"][0]["when"] == ""
    resolved = TestScenario.model_validate(data["scenarios"][0])
    assert resolved_command(resolved) == (
        "uv run --no-active pytest tests/test_new_location.py::test_it"
    )


def test_put_binding_repoint_allowed_even_when_requirement_bound(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Requirement Bound Repoint WS", "d", root=ws_root,
        requirements=[Requirement(text="acceptance clause", test_ref="s1")],
    )
    _seed_handover(ws, [_scenario("s1")])
    res = _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r"})
    assert res.status_code == 200


def test_delete_binding_reverts_to_base(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete WS")
    _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r"})
    res = _delete(client, ws, "s1")
    assert res.status_code == 200
    assert res.json()["scenarios"][0]["when"] == "uv run pytest -k x"
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings == {}


def test_delete_binding_records_intervention_and_audit(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = _zero_subtask_ws(ws_root, "Delete Audit WS")
    _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r"})
    _delete(client, ws, "s1")
    lines = (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()
    last = json.loads(lines[-1])
    assert last["kind"] == "binding"
    assert last["action"] == "delete"
    reloaded = read(ws.task_id, root=ws_root)
    assert [i.action for i in reloaded.meta.interventions] == [
        InterventionAction.SCENARIO_OVERRIDE,
        InterventionAction.SCENARIO_OVERRIDE,
    ]


def test_delete_binding_without_prior_put_is_noop_200(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete Noop WS")
    res = _delete(client, ws, "s1")
    assert res.status_code == 200
    assert res.json()["scenarios"][0]["when"] == "uv run pytest -k x"


def test_delete_binding_without_control_token_401(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete 401 WS")
    res = _delete(client, ws, "s1", headers={})
    assert res.status_code == 401


def test_delete_binding_wrong_control_token_403(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete 403 WS")
    res = _delete(
        client, ws, "s1", headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_delete_binding_unknown_workspace_404(client: TestClient) -> None:
    res = client.delete(
        "/api/workspaces/ghost/scenarios/s1/binding", headers=_control_headers()
    )
    assert res.status_code == 404


def test_delete_binding_unknown_scenario_id_404(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete 404 Scenario WS")
    res = _delete(client, ws, "ghost-scenario")
    assert res.status_code == 404


def test_delete_binding_archived_workspace_409(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Delete Archived WS")
    transition(ws, Status.ARCHIVED)
    res = _delete(client, ws, "s1")
    assert res.status_code == 409


def test_get_scenarios_spans_subtasks_not_just_root_task_id(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Multi Subtask Scenarios", "d", root=ws_root,
        tasks=[
            Task(id="sub-a", status=TaskStatus.PENDING, scenarios=[_scenario("s1")]),
            Task(id="sub-b", status=TaskStatus.PENDING, scenarios=[_scenario("s2")]),
        ],
    )
    res = client.get(f"/api/workspaces/{ws.task_id}/scenarios")
    assert res.status_code == 200
    assert {s["id"] for s in res.json()["scenarios"]} == {"s1", "s2"}


def test_put_binding_resolves_a_subtask_owned_scenario_instead_of_404(
    client: TestClient, ws_root: Path
) -> None:
    ws = create(
        "Subtask Owned Scenario", "d", root=ws_root,
        tasks=[Task(id="sub-a", status=TaskStatus.PENDING, scenarios=[_scenario("sub-s1")])],
    )
    res = _put(
        client, ws, "sub-s1", {"when": "uv run pytest -k y", "reason": "test moved"}
    )
    assert res.status_code == 200
    assert res.json()["scenarios"][0]["when"] == "uv run pytest -k y"


def test_put_binding_always_writes_written_by_operator(
    client: TestClient, ws_root: Path
) -> None:
    ws = _zero_subtask_ws(ws_root, "Written By WS")
    res = _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "test moved"})
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings["s1"].written_by == "operator"


def test_delete_binding_cleans_up_orphaned_binding(client: TestClient, ws_root: Path) -> None:
    ws = _zero_subtask_ws(ws_root, "Orphan WS", scenario_id="s1")
    put_res = _put(client, ws, "s1", {"when": "uv run pytest -k y", "reason": "r"})
    assert put_res.status_code == 200
    ws = read(ws.task_id, root=ws_root)
    _seed_handover(ws, [_scenario("s2")])
    res = _delete(client, ws, "s1")
    assert res.status_code == 200
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.scenario_bindings == {}
