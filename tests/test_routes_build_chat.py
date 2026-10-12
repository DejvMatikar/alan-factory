from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.explore_chat_repo as explore_chat_repo_mod
import alan.prep_chat as prep_chat_mod
import alan.routes.build_chat as bc_routes
from alan import _chat_core, roots, server
from alan import collision as collision_mod
from alan.claude_cli import (
    BUILD_SUMMARIZE_SENTINEL,
    DISPATCH_PROPOSAL_SENTINEL,
    ChatTurnResult,
    ClaudeCliError,
)
from alan.dispatcher import PubSub
from alan.health import save_learning_metrics
from alan.learning_metrics import LearningMetrics
from alan.routes import _chat_router
from alan.routes._deps import (
    build_chat_locks_dep,
    build_chat_sessions_root_dep,
    resolve_build_chat_sessions_root,
)
from alan.routes.infra import _run_build_chat_reconciler
from alan.teams._base import TeamEvent
from tests._chat_propose_support import propose_ok

_COUNTER_CASE = {
    "against": "locks in scope before the shop has bandwidth to review it",
    "cheapest_80": "ship the smallest read-only slice first",
    "cost_estimate": "insufficient data",
}


def _ready_json(**proposal_overrides: Any) -> str:
    proposal = {
        "team": "implementation_plan",
        "title": "t",
        "rationale": "go",
        "counter_case": _COUNTER_CASE,
        **proposal_overrides,
    }
    return json.dumps({"status": "ready", "proposal": proposal})


_ENGINE_KEY = "DejvMatikar/alan"
_BODY = {"engine_key": _ENGINE_KEY}


def _register_bindable_engine(slug: str) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    owner, repo = slug.split("/")
    register(EngineConfig(repo=RepoIdentity(owner=owner, repo=repo)))


@pytest.fixture
def bc_root(tmp_path):
    return tmp_path / "bc-sessions"


@pytest.fixture
def ws_root(tmp_path):
    return tmp_path / "ws"


@pytest.fixture
def bc_locks():
    return build_chat_mod.BuildChatSessionLocks()


@pytest.fixture
def pubsub():
    return PubSub()


@pytest.fixture
def client(bc_root, ws_root, bc_locks, pubsub):
    server.app.dependency_overrides[server._build_chat_sessions_root_dep] = lambda: bc_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._build_chat_locks_dep] = lambda: bc_locks
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def stub_run(monkeypatch):
    calls: list[dict[str, Any]] = []

    def _set(factory: Callable[[dict[str, Any], int], ChatTurnResult]) -> list[dict[str, Any]]:
        def fake(**kwargs: Any) -> ChatTurnResult:
            calls.append(kwargs)
            return factory(kwargs, len(calls))

        monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
        return calls

    return _set


class _EventWaiter:
    def __init__(self, pubsub: PubSub, sid: str, kind: str):
        self.kind = kind
        self.captured: list[TeamEvent] = []
        self._ev = threading.Event()

        def on_event(e: TeamEvent) -> None:
            self.captured.append(e)
            if e.kind == kind:
                self._ev.set()

        self._unsub = pubsub.subscribe(f"build_chat:{sid}", on_event)

    def wait(self, timeout: float = 2.0) -> TeamEvent:
        try:
            if not self._ev.wait(timeout):
                raise AssertionError(
                    f"timeout waiting for {self.kind}; got {[e.kind for e in self.captured]}"
                )
            return next(e for e in self.captured if e.kind == self.kind)
        finally:
            self._unsub()


def test_list_sessions_empty(client):
    assert client.get("/api/build-chat/sessions").json() == []


def test_list_sessions_returns_created(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    ids = [s["session_id"] for s in client.get("/api/build-chat/sessions").json()]
    assert sid in ids


def test_list_sessions_summary_ready_false_when_no_brief(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    rows = client.get("/api/build-chat/sessions").json()
    row = next(r for r in rows if r["session_id"] == sid)
    assert row["summary_ready"] is False


def test_list_sessions_summary_ready_true_after_brief(client, stub_run):
    stub_run(lambda kw, n: ChatTurnResult(result_text="b", claude_session_id=kw["session_id"]))
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/summarize")
    rows = client.get("/api/build-chat/sessions").json()
    row = next(r for r in rows if r["session_id"] == sid)
    assert row["summary_ready"] is True


def test_list_sessions_route_project_id_filters(client):
    matching = client.post(
        "/api/build-chat/sessions", json={**_BODY, "project_id": "proj-a"}
    ).json()["session_id"]
    client.post("/api/build-chat/sessions", json={**_BODY, "project_id": "proj-b"})
    rows = client.get("/api/build-chat/sessions", params={"project_id": "proj-a"}).json()
    assert [r["session_id"] for r in rows] == [matching]


def test_list_sessions_route_no_project_id_unchanged(client):
    a = client.post(
        "/api/build-chat/sessions", json={**_BODY, "project_id": "proj-a"}
    ).json()["session_id"]
    b = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    rows = client.get("/api/build-chat/sessions").json()
    assert {r["session_id"] for r in rows} == {a, b}


def test_list_sessions_route_project_id_bounds_rejected(client):
    assert client.get(
        "/api/build-chat/sessions", params={"project_id": ""}
    ).status_code == 422
    assert client.get(
        "/api/build-chat/sessions", params={"project_id": "x" * 201}
    ).status_code == 422


def test_create_session_201(client):
    res = client.post("/api/build-chat/sessions", json=_BODY)
    assert res.status_code == 201
    build_chat_mod.validate_session_id(res.json()["session_id"])


def test_get_session_round_trips(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["session_id"] == sid
    assert body["messages"] == []
    assert body["brief"] is None
    assert body["in_flight_turn"] is None
    assert "origin" not in body


def test_get_session_includes_brief(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "text"})
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["brief"]["content"] == "text"


def test_get_session_project_id_none_and_engine_key_echoes_binding_by_default(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["project_id"] is None
    assert body["engine_key"] == _ENGINE_KEY


def test_get_session_surfaces_project_id_from_creation(client):
    sid = client.post(
        "/api/build-chat/sessions", json={**_BODY, "project_id": "proj-a"}
    ).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["project_id"] == "proj-a"


def test_create_session_rejects_engine_key_mismatched_with_bound_project(
    client, tmp_path, monkeypatch
):
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)
    _register_bindable_engine("acme/sprocket")
    _register_bindable_engine("acme/widget")

    res = client.post(
        "/api/build-chat/sessions",
        json={"engine_key": "acme/widget", "project_id": project.id},
    )

    assert res.status_code == 400


def test_create_session_allows_engine_key_matching_bound_project(
    client, tmp_path, monkeypatch
):
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)
    _register_bindable_engine("acme/sprocket")

    sid = client.post(
        "/api/build-chat/sessions",
        json={"engine_key": "acme/sprocket", "project_id": project.id},
    ).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["project_id"] == project.id
    assert body["engine_key"] == "acme/sprocket"


def test_create_session_allows_any_known_engine_key_for_unbound_project(
    client, tmp_path, monkeypatch
):
    import alan.project as project_mod

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Fresh Project", root=projects_root)
    _register_bindable_engine("acme/widget")

    sid = client.post(
        "/api/build-chat/sessions",
        json={"engine_key": "acme/widget", "project_id": project.id},
    ).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["project_id"] == project.id
    assert body["engine_key"] == "acme/widget"


def test_get_session_engine_key_independent_of_dangling_project(client):
    _register_bindable_engine("acme/widget")
    sid = client.post(
        "/api/build-chat/sessions",
        json={"engine_key": "acme/widget", "project_id": "no-such-project"},
    ).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["project_id"] == "no-such-project"
    assert body["engine_key"] == "acme/widget"


def test_create_session_rejects_unknown_engine_key(client):
    res = client.post("/api/build-chat/sessions", json={"engine_key": "no-such/engine"})
    assert res.status_code == 400
    assert "unknown engine" in res.json()["detail"]


def _write_fake_turn_stream(
    stream_path: Path, tool_name: str, cost: float, input_tokens: int, output_tokens: int
) -> None:
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    with stream_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "system", "subtype": "init"}) + "\n")
        fh.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": tool_name, "input": {}}]
                    },
                }
            )
            + "\n"
        )
        fh.write(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": cost,
                    "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                }
            )
            + "\n"
        )


def test_get_session_run_summary_placeholder_when_no_turns(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    rs = body["run_summary"]
    assert rs is not None
    assert rs["model"] == "opus"
    assert rs["effort"] == "xhigh"
    assert isinstance(rs["system_prompt"], str) and rs["system_prompt"]
    assert rs["total_cost_usd"] == 0
    assert rs["total_output_tokens"] == 0
    assert rs["total_input_tokens"] == 0
    assert rs["tool_counts"] == []


def test_run_summary_accumulates_across_message_and_propose_turns(client, stub_run, pubsub):
    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        if n == 1:
            _write_fake_turn_stream(kw["stream_path"], "Read", 0.001, 100, 50)
            return ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])
        _write_fake_turn_stream(kw["stream_path"], "Read", 0.002, 200, 80)
        return ChatTurnResult(
            result_text=_ready_json(),
            claude_session_id=kw["session_id"],
        )

    stub_run(factory)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()

    propose_res = client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert propose_res.status_code == 200

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    rs = body["run_summary"]
    assert rs is not None
    assert rs["total_cost_usd"] == pytest.approx(0.003)
    assert rs["total_output_tokens"] == 130
    assert rs["total_input_tokens"] == 300
    assert rs["tool_counts"] == [{"name": "Read", "count": 2}]
    assert rs["model"] == "opus"
    assert rs["effort"] == "xhigh"
    assert isinstance(rs["system_prompt"], str) and rs["system_prompt"]


def test_get_session_system_prompt_cites_measured_shop_stats_when_no_data(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    prompt = body["run_summary"]["system_prompt"]
    assert "## Measured shop stats" in prompt
    assert "not enough data yet" in prompt
    assert "None" not in prompt


def test_send_message_system_prompt_includes_measured_learning_metrics(client, stub_run, pubsub):
    save_learning_metrics(
        server.app.state.queue,
        LearningMetrics(
            first_pass_approval_rate=0.75, mean_fix_cycles_to_merge=0.2, sample_size=12
        ),
        datetime.now(UTC),
    )
    captured: list[str] = []

    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        captured.append(kw["system_prompt"])
        return ChatTurnResult(result_text=f"r{n}", claude_session_id=kw["session_id"])

    stub_run(factory)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    assert "75.0%" in captured[0]


def test_propose_system_prompt_includes_measured_shop_stats(client, stub_run):
    save_learning_metrics(
        server.app.state.queue,
        LearningMetrics(
            first_pass_approval_rate=0.4, mean_fix_cycles_to_merge=0.3, sample_size=7
        ),
        datetime.now(UTC),
    )
    captured: list[str] = []

    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        captured.append(kw["system_prompt"])
        return ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])

    stub_run(factory)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert "40.0%" in captured[0]


def test_summarize_system_prompt_includes_measured_shop_stats(client, stub_run):
    captured: list[str] = []

    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        captured.append(kw["system_prompt"])
        return ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"])

    stub_run(factory)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/summarize")
    assert "## Measured shop stats" in captured[0]


def _write_fake_collision_stream(
    stream_path: Path, cost: float, input_tokens: int, output_tokens: int
) -> None:
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    with stream_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": cost,
                    "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                }
            )
            + "\n"
        )


def test_get_session_collision_run_summary_none_when_no_collision_run(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    assert body["collision_run_summary"] is None


def test_get_session_collision_run_summary_populated_after_session_scoped_check(
    client, bc_root
):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    collision_stream = _chat_core.collision_stream_path(
        build_chat_mod.CONFIG, sid, root=bc_root
    )
    _write_fake_collision_stream(collision_stream, 0.003, 40, 20)

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    cs = body["collision_run_summary"]
    assert cs is not None
    assert cs["model"] == "haiku"
    assert cs["total_cost_usd"] == pytest.approx(0.003)
    assert cs["total_input_tokens"] == 40
    assert cs["total_output_tokens"] == 20
    assert cs["tool_counts"] == []


def test_collision_run_summary_is_independent_of_run_summary(client, bc_root, stub_run):
    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        _write_fake_turn_stream(kw["stream_path"], "Read", 0.001, 100, 50)
        return ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])

    stub_run(factory)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/summarize")

    collision_stream = _chat_core.collision_stream_path(
        build_chat_mod.CONFIG, sid, root=bc_root
    )
    _write_fake_collision_stream(collision_stream, 0.009, 900, 900)

    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    rs = body["run_summary"]
    cs = body["collision_run_summary"]
    assert rs["total_cost_usd"] == pytest.approx(0.001)
    assert cs["total_cost_usd"] == pytest.approx(0.009)


def test_get_session_404_unknown(client):
    resp = client.get("/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab")
    assert resp.status_code == 404


def test_get_session_404_garbage_id(client):
    assert client.get("/api/build-chat/sessions/not-a-uuid").status_code == 404


def test_delete_session(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.delete(f"/api/build-chat/sessions/{sid}").status_code == 204
    assert client.get(f"/api/build-chat/sessions/{sid}").status_code == 404


def test_delete_session_404(client):
    resp = client.delete("/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab")
    assert resp.status_code == 404


def test_send_message_runs_turn_and_publishes_done(client, stub_run, pubsub):
    stub_run(lambda kw, n: ChatTurnResult(result_text=f"r{n}", claude_session_id=kw["session_id"]))
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    resp = client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
    assert resp.status_code == 202
    done = waiter.wait()
    assert done.payload["content"] == "r1"
    msgs = client.get(f"/api/build-chat/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_send_message_publishes_chat_event(client, monkeypatch, pubsub):
    def fake(**kw: Any) -> ChatTurnResult:
        kw["on_event"]({"type": "assistant"})
        return ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    captured: list[TeamEvent] = []
    unsub = pubsub.subscribe(f"build_chat:{sid}", captured.append)
    try:
        client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "q"})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.kind == "assistant_done" for e in captured):
                break
            time.sleep(0.01)
    finally:
        unsub()
    assert any(e.kind == "chat_event" for e in captured)
    assert any(e.kind == "assistant_done" for e in captured)


def test_send_message_publishes_error_on_cli_failure(client, monkeypatch, pubsub, caplog):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(ClaudeCliError("boom"))
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("WARNING", logger="alan.server"):
        client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
        detail = waiter.wait().payload["detail"]
    assert detail == _chat_router._GENERIC_CHAT_ERROR_TEXT  # noqa: SLF001
    assert "boom" not in detail
    assert any("boom" in r.message for r in caplog.records)


def test_send_message_publishes_error_on_unexpected(client, monkeypatch, caplog, pubsub):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("kaboom"))
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("ERROR", logger="alan.server"):
        client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
        assert "kaboom" in waiter.wait().payload["detail"]
    assert any("unexpected failure" in r.message for r in caplog.records)


def test_send_message_409_when_locked(client, stub_run, pubsub):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "first"})
    time.sleep(0.05)
    resp = client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "second"})
    assert resp.status_code == 409
    gate.set()


def test_send_message_404_unknown(client):
    assert client.post(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/messages",
        json={"content": "hi"},
    ).status_code == 404


def test_send_message_releases_lock_on_append_failure(client, monkeypatch, bc_locks):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    monkeypatch.setattr("alan._chat_core.append_user_message_and_mark_started",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")))
    with pytest.raises(RuntimeError, match="fail"):
        client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "hi"})
    bc_locks.acquire(sid)


def test_interrupt_204_when_idle(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"/api/build-chat/sessions/{sid}/interrupt").status_code == 204


def test_interrupt_404_unknown(client):
    assert client.post(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/interrupt"
    ).status_code == 404


def test_interrupt_cancels_in_flight(client, monkeypatch, pubsub):
    gate = threading.Event()

    def blocking(**kw: Any) -> ChatTurnResult:
        proc_cb = kw.get("on_proc_started")
        p = subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if callable(proc_cb):
            proc_cb(p)
        gate.set()
        p.wait()
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", blocking)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "go"})
    gate.wait(2.0)
    assert client.post(f"/api/build-chat/sessions/{sid}/interrupt").status_code == 204


def test_summarize_saves_brief(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text="brief text", claude_session_id=kw["session_id"])
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/summarize").json()
    assert body["content"] == "brief text"
    assert client.get(f"/api/build-chat/sessions/{sid}/brief").json()["content"] == "brief text"


def test_summarize_sends_sentinel(client, monkeypatch):
    captured: list[str] = []

    def fake(**kw: Any) -> ChatTurnResult:
        captured.append(kw.get("user_text", ""))
        return ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/summarize")
    assert captured[0] == BUILD_SUMMARIZE_SENTINEL


def test_summarize_does_not_persist_to_transcript(client, stub_run):
    stub_run(lambda kw, n: ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"]))
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/summarize")
    assert client.get(f"/api/build-chat/sessions/{sid}").json()["messages"] == []


def test_summarize_502_on_cli_failure(client, monkeypatch):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(ClaudeCliError("die"))
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"/api/build-chat/sessions/{sid}/summarize").status_code == 502


def test_summarize_409_when_locked(client, stub_run):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "busy"})
    time.sleep(0.05)
    assert client.post(f"/api/build-chat/sessions/{sid}/summarize").status_code == 409
    gate.set()


def test_summarize_404_unknown(client):
    assert client.post(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/summarize"
    ).status_code == 404


def test_propose_returns_ready(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(title="my-build"),
            claude_session_id=kw["session_id"],
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["status"] == "ready"
    assert body["proposal"]["team"] == "implementation_plan"
    assert body["proposal"]["title"] == "my-build"
    assert body["proposal"]["counter_case"]["cost_estimate"] == "insufficient data"


def test_propose_ready_carries_acceptance_clauses(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(
                title="my-build", acceptance_clauses=["clause one", "clause two"]
            ),
            claude_session_id=kw["session_id"],
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["status"] == "ready"
    assert body["proposal"]["acceptance_clauses"] == ["clause one", "clause two"]


def test_propose_ready_without_acceptance_clauses_defaults_empty(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(), claude_session_id=kw["session_id"]
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["proposal"]["acceptance_clauses"] == []


def test_propose_ready_missing_title_422(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(title=None), claude_session_id=kw["session_id"]
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    res = client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert res.status_code == 422
    assert res.json()["detail"]["reason"] == "build dispatch requires a non-empty proposal.title"


def test_propose_ready_missing_counter_case_422(client, stub_run):
    raw = json.dumps(
        {
            "status": "ready",
            "proposal": {
                "team": "implementation_plan",
                "title": "my-build",
                "rationale": "go",
            },
        }
    )
    stub_run(lambda kw, n: ChatTurnResult(result_text=raw, claude_session_id=kw["session_id"]))
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"/api/build-chat/sessions/{sid}/propose").status_code == 422


def test_propose_returns_need_more_info(client, stub_run):
    raw = json.dumps({"status": "need_more_info", "questions": ["Which DB?"]})
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=raw, claude_session_id=kw["session_id"])
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["status"] == "need_more_info"
    assert body["questions"] == ["Which DB?"]


def test_propose_sends_sentinel(client, monkeypatch):
    captured: list[str] = []

    def fake(**kw: Any) -> ChatTurnResult:
        captured.append(kw.get("user_text", ""))
        return ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert captured[0] == DISPATCH_PROPOSAL_SENTINEL


def test_propose_does_not_save_brief(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert client.get(f"/api/build-chat/sessions/{sid}/brief").json() is None


def test_propose_502_on_cli_failure(client, monkeypatch):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(ClaudeCliError("die"))
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"/api/build-chat/sessions/{sid}/propose").status_code == 502


def test_propose_422_on_parse_failure(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text="status: bogus", claude_session_id=kw["session_id"]
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"/api/build-chat/sessions/{sid}/propose").status_code == 422


def test_propose_422_on_wrong_team_with_title(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(team="research"), claude_session_id=kw["session_id"]
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    res = client.post(f"/api/build-chat/sessions/{sid}/propose")
    assert res.status_code == 422


def test_propose_409_when_locked(client, stub_run):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.post(f"/api/build-chat/sessions/{sid}/messages", json={"content": "busy"})
    time.sleep(0.05)
    assert client.post(f"/api/build-chat/sessions/{sid}/propose").status_code == 409
    gate.set()


def test_propose_404_unknown(client):
    assert client.post(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/propose"
    ).status_code == 404


def test_propose_recovers_colon_in_acceptance_clause(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_json(acceptance_clauses=[{"Clause text": "with detail"}]),
            claude_session_id=kw["session_id"],
        )
    )
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["proposal"]["acceptance_clauses"] == ["Clause text: with detail"]


def test_propose_merges_predispatch_scan_findings(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )

    from alan.scope_audit import ScopeGapFinding

    finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="Open PR overlaps",
        references=["PR #99"],
        suggested_action="needs_user_call",
    )
    monkeypatch.setattr("alan.routes.build_chat.scan_predispatch", lambda *a, **kw: [finding])
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert len(body["proposal"]["scope_audit"]) == 1
    assert body["proposal"]["scope_audit"][0]["kind"] == "in_flight_pr"


def test_propose_scan_uses_brief_content_as_description(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    captured: dict[str, Any] = {}

    def fake_scan(title: str, description: str, **kw: Any) -> list[Any]:
        captured["title"] = title
        captured["description"] = description
        captured.update(kw)
        return []

    monkeypatch.setattr("alan.routes.build_chat.scan_predispatch", fake_scan)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "the build brief"})
    propose_ok(client, f"/api/build-chat/sessions/{sid}/propose")
    assert captured["description"] == "the build brief"
    assert captured["title"] == "the build brief"
    assert captured["exclude_task_id"] is None
    assert captured["scope_engine_key"] == _ENGINE_KEY


def test_propose_response_carries_collision_verdict(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    match = collision_mod.CollisionMatch(
        label="dup ws", kind="workspace", deep_link="/workspaces/dup"
    )
    red = collision_mod.CollisionResult(state="red", sentence="Live overlap.", match=match)
    monkeypatch.setattr("alan.collision.check_collision", lambda *a, **kw: red)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["collision"]["state"] == "red"
    assert body["collision"]["sentence"] == "Live overlap."
    assert body["collision"]["match"] == {
        "label": "dup ws",
        "kind": "workspace",
        "deep_link": "/workspaces/dup",
    }


def test_propose_collision_check_uses_brief_content_and_no_exclude(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    captured: dict[str, Any] = {}

    def fake_check_collision(
        brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None
    ):
        captured["brief"] = brief
        captured["exclude_task_id"] = exclude_task_id
        return collision_mod.SAFE_RESULT

    monkeypatch.setattr("alan.collision.check_collision", fake_check_collision)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "the build brief"})
    propose_ok(client, f"/api/build-chat/sessions/{sid}/propose")
    assert captured["brief"] == "the build brief"
    assert captured["exclude_task_id"] is None


def test_propose_collision_check_empty_description_when_no_brief(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    captured: dict[str, Any] = {}

    def fake_check_collision(brief: str, **kw: Any) -> collision_mod.CollisionResult:
        captured["brief"] = brief
        return collision_mod.SAFE_RESULT

    monkeypatch.setattr("alan.collision.check_collision", fake_check_collision)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    propose_ok(client, f"/api/build-chat/sessions/{sid}/propose")
    assert captured["brief"] == ""


def test_propose_writes_session_scoped_collision_stream(client, stub_run, bc_root, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )

    def fake_check_collision(
        brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None
    ):
        assert stream_path is not None
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_path.write_text(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.002,
                    "usage": {"input_tokens": 5, "output_tokens": 9},
                }
            )
            + "\n"
        )
        return collision_mod.SAFE_RESULT

    monkeypatch.setattr("alan.collision.check_collision", fake_check_collision)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    propose_ok(client, f"/api/build-chat/sessions/{sid}/propose")

    expected_path = bc_root / sid / "collision_stream.jsonl"
    assert expected_path.exists()
    body = client.get(f"/api/build-chat/sessions/{sid}").json()
    cs = body["collision_run_summary"]
    assert cs is not None
    assert cs["total_cost_usd"] == pytest.approx(0.002)


def test_propose_collision_check_failure_falls_open_to_green(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )

    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("collision closure boom")

    monkeypatch.setattr(build_chat_mod, "load_brief", boom)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["collision"]["state"] == "green"


def test_derive_scan_title_uses_first_nonblank_line():
    assert bc_routes._derive_scan_title("Add a widget\nmore text") == "Add a widget"


def test_derive_scan_title_strips_heading_and_blank_lines():
    assert bc_routes._derive_scan_title("\n\n#  My Build\nbody") == "My Build"


def test_derive_scan_title_defaults_to_build_when_blank():
    assert bc_routes._derive_scan_title("   \n  ") == "build"


def test_derive_scan_title_caps_length():
    long = "x" * 80
    assert len(bc_routes._derive_scan_title(long)) == 50


def test_propose_scan_failure_falls_back_to_empty(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )

    def boom(*a: Any, **kw: Any) -> list[Any]:
        raise RuntimeError("scan boom")

    monkeypatch.setattr("alan.routes.build_chat.scan_predispatch", boom)
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["proposal"]["scope_audit"] == []


def test_propose_resolves_anti_scope_from_project(client, stub_run, tmp_path, monkeypatch):
    import alan.project as project_mod
    from alan.project_doc import apply_vision_scope_amendment, ensure_doc

    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Guarded", root=projects_root)
    project, _ = ensure_doc(project, root=projects_root)
    apply_vision_scope_amendment(
        project,
        vision="v",
        scope="Anti-scope: do not touch the billing module.",
        decision_entry="d",
        root=projects_root,
    )

    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    sid = client.post(
        "/api/build-chat/sessions", json={**_BODY, "project_id": project.id}
    ).json()["session_id"]
    client.put(
        f"/api/build-chat/sessions/{sid}/brief",
        json={"content": "Refactor the billing module payment flow"},
    )
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    kinds = [f["kind"] for f in body["proposal"]["scope_audit"]]
    assert "anti_scope" in kinds


def test_propose_scan_uses_session_engine_key_as_scope(client, stub_run, monkeypatch):
    _register_bindable_engine("sprocket-co/sprocket")
    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )
    captured: dict[str, Any] = {}

    def fake_scan(title: str, description: str, **kw: Any) -> list[Any]:
        captured.update(kw)
        return []

    def fake_provision(engine_key: str, dest: Path) -> None:
        dest.mkdir(parents=True)

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    monkeypatch.setattr("alan.routes.build_chat.scan_predispatch", fake_scan)
    sid = client.post(
        "/api/build-chat/sessions", json={"engine_key": "sprocket-co/sprocket"}
    ).json()["session_id"]
    propose_ok(client, f"/api/build-chat/sessions/{sid}/propose")
    assert captured["scope_engine_key"] == "sprocket-co/sprocket"


def test_propose_anti_scope_resolution_raises_falls_back(client, stub_run, monkeypatch):
    import alan.project_doc as project_doc_mod

    stub_run(
        lambda kw, n: ChatTurnResult(result_text=_ready_json(), claude_session_id=kw["session_id"])
    )

    def boom(*a: Any, **kw: Any) -> str | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(project_doc_mod, "read_scope_section", boom)
    sid = client.post(
        "/api/build-chat/sessions", json={**_BODY, "project_id": "some-project"}
    ).json()["session_id"]
    body = client.post(f"/api/build-chat/sessions/{sid}/propose").json()
    assert body["proposal"]["scope_audit"] == []


def test_upsert_brief_before_summarize(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    body = client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "draft"}).json()
    assert body["content"] == "draft"


def test_upsert_brief_overwrites(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "v1"})
    client.put(f"/api/build-chat/sessions/{sid}/brief", json={"content": "v2"})
    assert client.get(f"/api/build-chat/sessions/{sid}/brief").json()["content"] == "v2"


def test_upsert_brief_404_unknown(client):
    assert client.put(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/brief",
        json={"content": "x"},
    ).status_code == 404


def test_get_brief_null_when_absent(client):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    assert client.get(f"/api/build-chat/sessions/{sid}/brief").json() is None


def test_get_brief_404_unknown(client):
    assert client.get(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/brief"
    ).status_code == 404


def test_events_404_unknown(client):
    assert client.get(
        "/api/build-chat/sessions/12345678-1234-1234-1234-1234567890ab/events"
    ).status_code == 404


async def test_events_returns_streaming_response(bc_root, pubsub):
    session = build_chat_mod.create_session(root=bc_root)
    response = await bc_routes.build_chat_events(
        session.session_id, root=bc_root, pubsub=pubsub
    )
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"


async def test_event_stream_yields_published_events(pubsub):
    sid = "12345678-1234-1234-1234-1234567890ab"
    gen = bc_routes._event_stream(sid, pubsub)
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)
    pubsub.publish(
        f"build_chat:{sid}",
        TeamEvent("assistant_done", {"content": "yo", "usage": "{}"}),
    )
    frame = await asyncio.wait_for(task, timeout=2)
    assert "assistant_done" in frame
    assert "yo" in frame
    await gen.aclose()


def test_clear_best_effort_logs_exception(bc_root, monkeypatch, caplog):
    s = build_chat_mod.create_session(root=bc_root)
    monkeypatch.setattr("alan._chat_core.clear_in_flight_turn",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk")))
    with caplog.at_level("ERROR", logger="alan.server"):
        bc_routes._clear_best_effort(s.session_id, bc_root)
    assert any("failed to clear" in r.message for r in caplog.records)


def test_archived_session_absent_from_list(client, bc_root, ws_root):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, sid, ws_root / "some-task", title="t", root=bc_root
    )
    assert client.get("/api/build-chat/sessions").json() == []


def test_archived_session_get_404s(client, bc_root, ws_root):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, sid, ws_root / "some-task", title="t", root=bc_root
    )
    assert client.get(f"/api/build-chat/sessions/{sid}").status_code == 404


def test_archived_session_delete_404s(client, bc_root, ws_root):
    sid = client.post("/api/build-chat/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.BUILD, sid, ws_root / "some-task", title="t", root=bc_root
    )
    assert client.delete(f"/api/build-chat/sessions/{sid}").status_code == 404


def test_build_chat_sessions_root_dep(tmp_path):
    req = MagicMock()
    req.app.state.build_chat_sessions_root = tmp_path / "bc"
    assert build_chat_sessions_root_dep(req) == tmp_path / "bc"


def test_build_chat_locks_dep():
    locks = build_chat_mod.BuildChatSessionLocks()
    req = MagicMock()
    req.app.state.build_chat_locks = locks
    assert build_chat_locks_dep(req) is locks


def test_resolve_build_chat_sessions_root_default(monkeypatch):
    monkeypatch.delenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", raising=False)
    assert resolve_build_chat_sessions_root() == roots.build_chat_sessions_root()


def test_resolve_build_chat_sessions_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", str(tmp_path / "bc"))
    assert resolve_build_chat_sessions_root() == tmp_path / "bc"


def test_run_build_chat_reconciler_logs_cleared(tmp_path, caplog):
    root = tmp_path / "bc"
    s = build_chat_mod.create_session(root=root)
    msg = build_chat_mod.BuildChatMessage(role="user", content="q", ts=datetime.now(UTC))
    build_chat_mod.append_user_message_and_mark_started(s, msg, root=root)
    with caplog.at_level("INFO", logger="alan.server"):
        _run_build_chat_reconciler(root)
    assert any("cleared" in r.message for r in caplog.records)


def test_run_build_chat_reconciler_no_log_when_nothing(tmp_path, caplog):
    root = tmp_path / "bc"
    root.mkdir(parents=True)
    with caplog.at_level("INFO", logger="alan.server"):
        _run_build_chat_reconciler(root)
    assert not any("cleared" in r.message for r in caplog.records)


def test_run_build_chat_reconciler_backfills_legacy_engine_key(tmp_path, caplog):
    root = tmp_path / "bc"
    s = build_chat_mod.create_session(root=root)
    target = build_chat_mod.session_dir(s.session_id, root=root) / "session.json"
    raw = json.loads(target.read_text())
    del raw["engine_key"]
    target.write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level("INFO", logger="alan.server"):
        _run_build_chat_reconciler(root)
    assert any("backfilled engine_key" in r.message for r in caplog.records)
    reloaded = build_chat_mod.load_session(s.session_id, root=root)
    assert reloaded.engine_key is not None
