from __future__ import annotations

import contextlib
import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import agent_key, claude_cli, server
from alan import workflow as workflow_mod
from alan.dispatcher import PubSub
from alan.launch_spec import EnvEntry, LaunchSpec, ProcessSpec, PromptSegment
from alan.node_launch_record import LaunchRecord, parse_launch, record_from_spec
from alan.queue import Queue
from alan.workspace import Workspace, create


def _spec() -> LaunchSpec:
    return LaunchSpec(
        layout="agent",
        agent=agent_key.team_role("verification", "judge"),
        model="opus",
        effort="xhigh",
        max_turns=150,
        permission_mode="auto",
        prompt=(
            PromptSegment("system_prompt", "body"),
            PromptSegment("headless_execution", "\n\ntail", shared=True),
        ),
        allowed_tools=("Read",),
        add_dirs=("/work",),
        denied=("Write Edit NotebookEdit", "Bash(gh auth:*)"),
        session_mode="new",
        session_id="sid",
        process=ProcessSpec(
            cwd="/checkout",
            idle_timeout_seconds=2100.0,
            abs_timeout_seconds=14400.0,
            fleet=True,
            detached=True,
            priority_delta=10,
            env=(EnvEntry("ALAN_STATE_ROOT", "sandbox_redirect"),),
        ),
    )


def _record() -> LaunchRecord:
    return record_from_spec(_spec())


def test_record_from_spec_stores_segments_effort_and_effective_grants() -> None:
    record = _record()
    assert record["agent"] == "team_role:verification/judge"
    assert record["model"] == "opus"
    assert record["effort"] == "xhigh"
    assert record["prompt_segments"] == [
        {"label": "system_prompt", "text": "body", "shared": False},
        {"label": "headless_execution", "text": "\n\ntail", "shared": True},
    ]
    assert record["allowed_tools"] == ["Read"]
    assert record["denied_tools"] == ["Write", "Edit", "NotebookEdit", "Bash(gh auth:*)"]
    assert record["add_dirs"] == ["/work"]
    assert record["session_mode"] == "new"
    assert record["cwd"] == "/checkout"
    assert record["idle_timeout_seconds"] == 2100.0
    assert record["abs_timeout_seconds"] == 14400.0
    assert record["fleet"] is True
    assert record["detached"] is True
    assert record["priority_delta"] == 10
    assert record["env"] == [{"name": "ALAN_STATE_ROOT", "category": "sandbox_redirect"}]


def test_record_without_an_agent_or_process_values_is_still_complete() -> None:
    record = record_from_spec(LaunchSpec(layout="agent"))
    assert record["agent"] is None
    assert record["cwd"] is None
    assert record["prompt_segments"] == []
    assert parse_launch(record) == record


def test_the_record_round_trips_through_json_and_parse() -> None:
    record = _record()
    assert parse_launch(json.loads(json.dumps(record))) == record


def _broken(key: str, value: object) -> dict[str, Any]:
    return {**_record(), key: value}


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "text",
        [],
        {},
        _broken("model", 3),
        _broken("effort", ["x"]),
        _broken("max_turns", "150"),
        _broken("max_turns", True),
        _broken("idle_timeout_seconds", "fast"),
        _broken("idle_timeout_seconds", True),
        _broken("fleet", "yes"),
        _broken("session_mode", None),
        _broken("allowed_tools", "Read"),
        _broken("allowed_tools", [1]),
        _broken("prompt_segments", [{"label": "x", "text": "y"}]),
        _broken("prompt_segments", ["x"]),
        _broken("prompt_segments", "x"),
        _broken("env", [{"name": "A"}]),
        _broken("env", [{"name": "A", "category": "not_a_category"}]),
        _broken("env", [{"name": "A", "category": 3}]),
        _broken("env", [{"name": "A", "category": "allowlisted", "value": "hunter2"}]),
        _broken("session_mode", "forked"),
        _broken("session_mode", 3),
        _broken("prompt_segments", [{"label": "x", "text": "y", "shared": False, "extra": 1}]),
        {**_record(), "token": "hunter2"},
    ],
)
def test_parse_launch_rejects_malformed_records(candidate: object) -> None:
    assert parse_launch(candidate) is None


def test_parse_launch_rejects_a_record_missing_a_key() -> None:
    partial = dict(_record())
    del partial["model"]
    assert parse_launch(partial) is None


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _workspace_with_invocation(ws_root: Path) -> tuple[Workspace, str]:
    ws = create("launch record", "d", root=ws_root)
    return ws, workflow_mod.create_invocation_dir(ws)


def _save_config(ws: Workspace, inv_id: str, **extra: Any) -> Path:
    return workflow_mod.save_node_config(
        ws,
        inv_id,
        "alpha",
        system_prompt="sys",
        user_text="user",
        allowed_tools=["Read"],
        max_turns=20,
        permission_mode="auto",
        model="opus",
        turns_source="static",
        derived_max_turns=20,
        **extra,
    )


def test_save_node_config_persists_and_loads_the_launch_key(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    _save_config(ws, inv_id, launch=_record())
    loaded = workflow_mod.load_node_config(ws, inv_id, "alpha")
    assert loaded is not None
    assert loaded["launch"] == _record()


def test_legacy_config_without_launch_still_parses(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    _save_config(ws, inv_id)
    path = workflow_mod.config_path(ws, inv_id, "alpha")
    assert "launch" not in json.loads(path.read_text(encoding="utf-8"))
    loaded = workflow_mod.load_node_config(ws, inv_id, "alpha")
    assert loaded is not None
    assert "launch" not in loaded
    assert loaded["max_turns"] == 20


def test_an_invalid_launch_key_is_dropped_but_the_config_still_loads(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    path = _save_config(ws, inv_id, launch=_record())
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["launch"] = {"model": 5}
    path.write_text(json.dumps(doc), encoding="utf-8")
    loaded = workflow_mod.load_node_config(ws, inv_id, "alpha")
    assert loaded is not None
    assert "launch" not in loaded


def test_save_node_launch_adds_the_launch_to_an_existing_config(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    path = _save_config(ws, inv_id)
    written = workflow_mod.save_node_launch(ws, inv_id, "alpha", _record())
    assert written == path
    loaded = workflow_mod.load_node_config(ws, inv_id, "alpha")
    assert loaded is not None
    assert loaded["launch"] == _record()
    assert loaded["system_prompt"] == "sys"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_node_launch_rewrites_a_previous_launch(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    _save_config(ws, inv_id, launch=_record())
    resumed = {**_record(), "session_mode": "resumed"}
    workflow_mod.save_node_launch(ws, inv_id, "alpha", resumed)  # type: ignore[arg-type]
    loaded = workflow_mod.load_node_config(ws, inv_id, "alpha")
    assert loaded is not None
    assert loaded["launch"]["session_mode"] == "resumed"


def test_save_node_launch_writes_nothing_when_no_config_exists(ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    assert workflow_mod.save_node_launch(ws, inv_id, "alpha", _record()) is None
    assert not workflow_mod.config_path(ws, inv_id, "alpha").exists()


def test_effort_the_cli_receives_is_recorded_for_a_real_streaming_spec() -> None:
    sonnet = record_from_spec(claude_cli.streaming_spec("p", model="sonnet"))
    haiku = record_from_spec(claude_cli.streaming_spec("p", model="haiku"))
    assert sonnet["effort"] == "high"
    assert haiku["effort"] is None


@pytest.fixture
def client(ws_root: Path, tmp_path: Path) -> Iterator[TestClient]:
    queue = Queue(db_path=tmp_path / "queue.db")
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._pubsub_dep] = lambda: PubSub()  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()
        with contextlib.suppress(Exception):
            queue.close()


def test_the_config_route_serves_the_launch_key(client: TestClient, ws_root: Path) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    _save_config(ws, inv_id, launch=_record())
    res = client.get(f"/api/workspaces/{ws.task_id}/invocations/{inv_id}/agents/alpha/config")
    assert res.status_code == 200
    assert res.json()["launch"]["effort"] == "xhigh"
    assert res.json()["launch"]["prompt_segments"][0]["label"] == "system_prompt"


def test_the_config_route_omits_launch_for_legacy_records(
    client: TestClient, ws_root: Path
) -> None:
    ws, inv_id = _workspace_with_invocation(ws_root)
    _save_config(ws, inv_id)
    res = client.get(f"/api/workspaces/{ws.task_id}/invocations/{inv_id}/agents/alpha/config")
    assert res.status_code == 200
    assert "launch" not in res.json()
