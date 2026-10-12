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

import alan.explore_chat as explore_chat_mod
import alan.explore_chat_repo as explore_chat_repo_mod
import alan.prep_chat as prep_chat_mod
import alan.project as project_mod
import alan.routes.explore_chat as ec_routes
from alan import collision as collision_mod
from alan import engine_config as engine_config_mod
from alan import roots, secrets_store, server
from alan.claude_cli import (
    DISPATCH_PROPOSAL_SENTINEL,
    EXPLORE_SUMMARIZE_SENTINEL,
    ChatTurnResult,
    ClaudeCliError,
)
from alan.dispatcher import PubSub
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity
from alan.routes import _chat_router
from alan.routes._deps import (
    explore_chat_locks_dep,
    explore_chat_sessions_root_dep,
    resolve_explore_chat_sessions_root,
)
from alan.routes.infra import _run_explore_chat_reconciler
from alan.teams._base import TeamEvent
from tests._chat_propose_support import propose_ok

_SPROCKET_CREDENTIAL_ENV = "SPROCKET_GH_TOKEN"  # noqa: S105


def _register_sprocket_engine() -> EngineConfig:
    repo = RepoIdentity(owner="sprocket-co", repo="sprocket")
    cfg = EngineConfig(
        repo=repo, forge=ForgeConfig(repo=repo, credential_env_name=_SPROCKET_CREDENTIAL_ENV)
    )
    engine_config_mod.register(cfg, validate_env=False)
    return cfg


_PROPOSE_COUNTER_CASE = {
    "against": "locks in scope before the shop has bandwidth to review it",
    "cheapest_80": "ship the smallest read-only slice first",
    "cost_estimate": "insufficient data",
}


def _ready_proposal_json(**proposal_overrides: Any) -> str:
    proposal = {
        "team": "brainstorming",
        "title": "explore-topic",
        "rationale": "go",
        "counter_case": _PROPOSE_COUNTER_CASE,
        **proposal_overrides,
    }
    return json.dumps({"status": "ready", "proposal": proposal})


@pytest.fixture
def ec_root(tmp_path):
    return tmp_path / "ec-sessions"


@pytest.fixture
def ws_root(tmp_path):
    return tmp_path / "ws"


@pytest.fixture
def ec_locks():
    return explore_chat_mod.ExploreChatSessionLocks()


@pytest.fixture
def pubsub():
    return PubSub()


@pytest.fixture
def client(ec_root, ws_root, ec_locks, pubsub):
    server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = lambda: ec_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._explore_chat_locks_dep] = lambda: ec_locks
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

        self._unsub = pubsub.subscribe(f"explore_chat:{sid}", on_event)

    def wait(self, timeout: float = 2.0) -> TeamEvent:
        try:
            if not self._ev.wait(timeout):
                raise AssertionError(
                    f"timeout waiting for {self.kind}; got {[e.kind for e in self.captured]}"
                )
            return next(e for e in self.captured if e.kind == self.kind)
        finally:
            self._unsub()


_ENGINE_KEY = "DejvMatikar/alan"
_BODY = {"consultant_key": "brainstorming", "engine_key": _ENGINE_KEY}
_BASE = "/api/explore-chat"


def test_list_sessions_empty(client):
    assert client.get(f"{_BASE}/sessions").json() == []


def test_list_sessions_returns_created_with_consultant_key(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    rows = client.get(f"{_BASE}/sessions").json()
    row = next((r for r in rows if r["session_id"] == sid), None)
    assert row is not None
    assert row["consultant_key"] == "brainstorming"
    assert row["summary_ready"] is False


def test_list_sessions_summary_ready_true_after_brief(client, stub_run):
    stub_run(lambda kw, n: ChatTurnResult(result_text="b", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/summarize")
    rows = client.get(f"{_BASE}/sessions").json()
    row = next(r for r in rows if r["session_id"] == sid)
    assert row["summary_ready"] is True


def test_list_sessions_route_project_id_filters(client):
    matching = client.post(
        f"{_BASE}/sessions", json={**_BODY, "project_id": "proj-a"}
    ).json()["session_id"]
    client.post(f"{_BASE}/sessions", json={**_BODY, "project_id": "proj-b"})
    rows = client.get(f"{_BASE}/sessions", params={"project_id": "proj-a"}).json()
    assert [r["session_id"] for r in rows] == [matching]


def test_list_sessions_route_no_project_id_unchanged(client):
    body_a = {**_BODY, "project_id": "proj-a"}
    a = client.post(f"{_BASE}/sessions", json=body_a).json()["session_id"]
    b = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    rows = client.get(f"{_BASE}/sessions").json()
    assert {r["session_id"] for r in rows} == {a, b}


def test_list_sessions_route_project_id_bounds_rejected(client):
    assert client.get(f"{_BASE}/sessions", params={"project_id": ""}).status_code == 422
    assert client.get(
        f"{_BASE}/sessions", params={"project_id": "x" * 201}
    ).status_code == 422


def test_list_consultants_returns_five(client):
    body = client.get(f"{_BASE}/consultants").json()
    assert len(body) == 5


def test_list_consultants_no_steering_text(client):
    body = client.get(f"{_BASE}/consultants").json()
    for c in body:
        assert "steering_text" not in c


def test_list_consultants_has_required_fields(client):
    body = client.get(f"{_BASE}/consultants").json()
    for c in body:
        assert "key" in c
        assert "label" in c
        assert "description" in c
        assert "team" in c


def test_create_session_201(client):
    res = client.post(f"{_BASE}/sessions", json=_BODY)
    assert res.status_code == 201
    explore_chat_mod.validate_session_id(res.json()["session_id"])


def test_create_session_422_invalid_consultant_key(client):
    res = client.post(f"{_BASE}/sessions", json={"consultant_key": "INVALID KEY!"})
    assert res.status_code == 422


def test_create_session_422_unknown_consultant_key(client):
    res = client.post(f"{_BASE}/sessions", json={"consultant_key": "bogus_thing"})
    assert res.status_code == 422


def test_create_session_all_known_keys(client):
    for key in (
        "brainstorming",
        "research",
        "architecture",
        "opportunity_scout",
        "ui_review",
    ):
        res = client.post(
            f"{_BASE}/sessions", json={"consultant_key": key, "engine_key": _ENGINE_KEY}
        )
        assert res.status_code == 201


def test_get_session_round_trips(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["session_id"] == sid
    assert body["consultant_key"] == "brainstorming"
    assert body["messages"] == []
    assert body["brief"] is None
    assert body["in_flight_turn"] is None


def test_get_session_includes_brief(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.put(f"{_BASE}/sessions/{sid}/brief", json={"content": "text"})
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["brief"]["content"] == "text"


def _register_bindable_engine(slug: str) -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    owner, repo = slug.split("/")
    register(EngineConfig(repo=RepoIdentity(owner=owner, repo=repo)))


def test_create_session_rejects_unknown_engine_key(client):
    res = client.post(f"{_BASE}/sessions", json={**_BODY, "engine_key": "no-such/engine"})
    assert res.status_code == 400
    assert "unknown engine" in res.json()["detail"]


def test_create_session_rejects_engine_key_mismatched_with_bound_project(
    client, tmp_path, monkeypatch
):
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)
    _register_bindable_engine("acme/sprocket")
    _register_bindable_engine("acme/widget")

    res = client.post(
        f"{_BASE}/sessions",
        json={**_BODY, "engine_key": "acme/widget", "project_id": project.id},
    )

    assert res.status_code == 400


def test_create_session_allows_engine_key_matching_bound_project(client, tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Sprocket Spine", root=projects_root)
    project_mod.update(project, {"engine_key": "acme/sprocket"}, root=projects_root)
    _register_bindable_engine("acme/sprocket")

    sid = client.post(
        f"{_BASE}/sessions",
        json={**_BODY, "engine_key": "acme/sprocket", "project_id": project.id},
    ).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["project_id"] == project.id
    assert body["engine_key"] == "acme/sprocket"


def test_create_session_allows_any_known_engine_key_for_unbound_project(
    client, tmp_path, monkeypatch
):
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(projects_root))
    project = project_mod.create("Fresh Project", root=projects_root)
    _register_bindable_engine("acme/widget")

    sid = client.post(
        f"{_BASE}/sessions",
        json={**_BODY, "engine_key": "acme/widget", "project_id": project.id},
    ).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["project_id"] == project.id
    assert body["engine_key"] == "acme/widget"


def test_get_session_engine_key_independent_of_dangling_project(client):
    _register_bindable_engine("acme/widget")
    sid = client.post(
        f"{_BASE}/sessions",
        json={**_BODY, "engine_key": "acme/widget", "project_id": "no-such-project"},
    ).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["project_id"] == "no-such-project"
    assert body["engine_key"] == "acme/widget"


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
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    rs = body["run_summary"]
    assert rs is not None
    assert rs["model"] == "opus"
    assert rs["effort"] == "xhigh"
    assert isinstance(rs["system_prompt"], str) and rs["system_prompt"]
    assert rs["total_cost_usd"] == 0
    assert rs["total_output_tokens"] == 0
    assert rs["total_input_tokens"] == 0
    assert rs["tool_counts"] == []


def test_run_summary_accumulates_across_message_and_summarize_turns(client, stub_run, pubsub):
    def factory(kw: dict[str, Any], n: int) -> ChatTurnResult:
        if n == 1:
            _write_fake_turn_stream(kw["stream_path"], "WebSearch", 0.001, 100, 50)
            return ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])
        _write_fake_turn_stream(kw["stream_path"], "WebSearch", 0.002, 200, 80)
        return ChatTurnResult(result_text="brief text", claude_session_id=kw["session_id"])

    stub_run(factory)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()

    summarize_res = client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert summarize_res.status_code == 200

    body = client.get(f"{_BASE}/sessions/{sid}").json()
    rs = body["run_summary"]
    assert rs is not None
    assert rs["total_cost_usd"] == pytest.approx(0.003)
    assert rs["total_output_tokens"] == 130
    assert rs["total_input_tokens"] == 300
    assert rs["tool_counts"] == [{"name": "WebSearch", "count": 2}]
    assert rs["model"] == "opus"
    assert rs["effort"] == "xhigh"
    assert isinstance(rs["system_prompt"], str) and rs["system_prompt"]


def test_get_session_404_unknown(client):
    resp = client.get(f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab")
    assert resp.status_code == 404


def test_get_session_404_garbage_id(client):
    assert client.get(f"{_BASE}/sessions/not-a-uuid").status_code == 404


def test_delete_session(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.delete(f"{_BASE}/sessions/{sid}").status_code == 204
    assert client.get(f"{_BASE}/sessions/{sid}").status_code == 404


def test_delete_session_404(client):
    resp = client.delete(f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab")
    assert resp.status_code == 404


def test_send_message_runs_turn_and_publishes_done(client, stub_run, pubsub):
    stub_run(lambda kw, n: ChatTurnResult(result_text=f"r{n}", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    resp = client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    assert resp.status_code == 202
    done = waiter.wait()
    assert done.payload["content"] == "r1"
    msgs = client.get(f"{_BASE}/sessions/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_send_message_publishes_chat_event(client, monkeypatch, pubsub):
    def fake(**kw: Any) -> ChatTurnResult:
        kw["on_event"]({"type": "assistant"})
        return ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    captured: list[TeamEvent] = []
    unsub = pubsub.subscribe(f"explore_chat:{sid}", captured.append)
    try:
        client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "q"})
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
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("WARNING", logger="alan.server"):
        client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
        detail = waiter.wait().payload["detail"]
    assert detail == _chat_router._GENERIC_CHAT_ERROR_TEXT  # noqa: SLF001
    assert "boom" not in detail
    assert any("boom" in r.message for r in caplog.records)


def test_send_message_publishes_error_on_unexpected(client, monkeypatch, caplog, pubsub):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("kaboom"))
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("ERROR", logger="alan.server"):
        client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
        assert "kaboom" in waiter.wait().payload["detail"]
    assert any("unexpected failure" in r.message for r in caplog.records)


def test_send_message_409_when_locked(client, stub_run, pubsub):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "first"})
    time.sleep(0.05)
    resp = client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "second"})
    assert resp.status_code == 409
    gate.set()


def test_send_message_404_unknown(client):
    assert client.post(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/messages",
        json={"content": "hi"},
    ).status_code == 404


def test_send_message_releases_lock_on_append_failure(client, monkeypatch, ec_locks):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    monkeypatch.setattr(
        "alan._chat_core.append_user_message_and_mark_started",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    with pytest.raises(RuntimeError, match="fail"):
        client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    ec_locks.acquire(sid)


def test_interrupt_204_when_idle(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/interrupt").status_code == 204


def test_interrupt_404_unknown(client):
    assert client.post(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/interrupt"
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
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "go"})
    gate.wait(2.0)
    assert client.post(f"{_BASE}/sessions/{sid}/interrupt").status_code == 204


def test_summarize_saves_brief(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(result_text="brief text", claude_session_id=kw["session_id"])
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"{_BASE}/sessions/{sid}/summarize").json()
    assert body["content"] == "brief text"
    assert client.get(f"{_BASE}/sessions/{sid}/brief").json()["content"] == "brief text"


def test_summarize_sends_sentinel(client, monkeypatch):
    captured: list[str] = []

    def fake(**kw: Any) -> ChatTurnResult:
        captured.append(kw.get("user_text", ""))
        return ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert captured[0] == EXPLORE_SUMMARIZE_SENTINEL


def test_summarize_does_not_persist_to_transcript(client, stub_run):
    stub_run(lambda kw, n: ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert client.get(f"{_BASE}/sessions/{sid}").json()["messages"] == []


def test_summarize_system_prompt_uses_consultant(client, monkeypatch):
    captured: list[str] = []

    def fake(**kw: Any) -> ChatTurnResult:
        captured.append(kw.get("system_prompt", ""))
        return ChatTurnResult(result_text="brief", claude_session_id=kw["session_id"])

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post(
        f"{_BASE}/sessions", json={"consultant_key": "architecture", "engine_key": _ENGINE_KEY}
    ).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert "Architecture" in captured[0]


def test_summarize_502_on_cli_failure(client, monkeypatch):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(ClaudeCliError("die"))
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/summarize").status_code == 502


def test_summarize_409_when_locked(client, stub_run):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "busy"})
    time.sleep(0.05)
    assert client.post(f"{_BASE}/sessions/{sid}/summarize").status_code == 409
    gate.set()


def test_summarize_404_unknown(client):
    assert client.post(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/summarize"
    ).status_code == 404


def test_propose_returns_ready_with_rationale_and_counter_case(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"{_BASE}/sessions/{sid}/propose").json()
    assert body["status"] == "ready"
    assert body["proposal"]["team"] == "brainstorming"
    assert body["proposal"]["rationale"] == "go"
    assert body["proposal"]["counter_case"]["cost_estimate"] == "insufficient data"


def test_propose_missing_counter_case_422(client, stub_run):
    raw = json.dumps(
        {
            "status": "ready",
            "proposal": {
                "team": "brainstorming",
                "title": "explore-topic",
                "rationale": "go",
            },
        }
    )
    stub_run(lambda kw, n: ChatTurnResult(result_text=raw, claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 422


def test_propose_returns_need_more_info(client, stub_run):
    raw = json.dumps({"status": "need_more_info", "questions": ["What should be explored?"]})
    stub_run(lambda kw, n: ChatTurnResult(result_text=raw, claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"{_BASE}/sessions/{sid}/propose").json()
    assert body["status"] == "need_more_info"
    assert body["questions"] == ["What should be explored?"]


def test_propose_sends_dispatch_proposal_sentinel(client, monkeypatch):
    captured: list[str] = []

    def fake(**kw: Any) -> ChatTurnResult:
        captured.append(kw.get("user_text", ""))
        return ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )

    monkeypatch.setattr("alan.routes._chat_router.run_chat_turn", fake)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/propose")
    assert captured[0] == DISPATCH_PROPOSAL_SENTINEL


def test_propose_does_not_save_brief(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/propose")
    assert client.get(f"{_BASE}/sessions/{sid}/brief").json() is None


def test_propose_missing_title_422(client, stub_run):
    raw = json.dumps(
        {
            "status": "ready",
            "proposal": {
                "team": "brainstorming",
                "rationale": "go",
                "counter_case": _PROPOSE_COUNTER_CASE,
            },
        }
    )
    stub_run(lambda kw, n: ChatTurnResult(result_text=raw, claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    res = client.post(f"{_BASE}/sessions/{sid}/propose")
    assert res.status_code == 422
    assert res.json()["detail"]["reason"] == "explore dispatch requires a non-empty proposal.title"


def test_propose_wrong_team_for_consultant_rejected(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(team="research"),
            claude_session_id=kw["session_id"],
        )
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 422


def test_propose_502_on_cli_failure(client, monkeypatch):
    monkeypatch.setattr(
        "alan.routes._chat_router.run_chat_turn",
        lambda **kw: (_ for _ in ()).throw(ClaudeCliError("die")),
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 502


def test_propose_422_on_parse_failure(client, stub_run):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text="status: bogus", claude_session_id=kw["session_id"]
        )
    )
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 422


def test_propose_409_when_locked(client, stub_run):
    gate = threading.Event()

    def slow(kw: dict[str, Any], n: int) -> ChatTurnResult:
        gate.wait(3.0)
        return ChatTurnResult(result_text="done", claude_session_id=kw["session_id"])

    stub_run(slow)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "busy"})
    time.sleep(0.05)
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 409
    gate.set()


def test_propose_404_unknown(client):
    assert client.post(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/propose"
    ).status_code == 404


def test_propose_scan_uses_brief_content_and_no_exclude(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    captured: dict[str, Any] = {}

    def fake_scan(title: str, description: str, **kw: Any) -> list[Any]:
        captured["title"] = title
        captured["description"] = description
        captured.update(kw)
        return []

    monkeypatch.setattr("alan.routes.explore_chat.scan_predispatch", fake_scan)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.put(f"{_BASE}/sessions/{sid}/brief", json={"content": "the explore brief"})
    propose_ok(client, f"{_BASE}/sessions/{sid}/propose")
    assert captured["description"] == "the explore brief"
    assert captured["title"] == "the explore brief"
    assert captured["exclude_task_id"] is None


def test_propose_response_carries_collision_verdict(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    match = collision_mod.CollisionMatch(
        label="dup ws", kind="workspace", deep_link="/workspaces/dup"
    )
    red = collision_mod.CollisionResult(state="red", sentence="Live overlap.", match=match)
    monkeypatch.setattr("alan.collision.check_collision", lambda *a, **kw: red)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"{_BASE}/sessions/{sid}/propose").json()
    assert body["collision"]["state"] == "red"
    assert body["collision"]["sentence"] == "Live overlap."


def test_propose_collision_check_failure_falls_open_to_green(client, stub_run, monkeypatch):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )

    def boom(*a: Any, **kw: Any) -> None:
        raise RuntimeError("collision closure boom")

    monkeypatch.setattr("alan.collision.check_collision", boom)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.post(f"{_BASE}/sessions/{sid}/propose").json()
    assert body["collision"]["state"] == "green"


def test_propose_all_consultants_use_their_own_team(client, stub_run):
    for key in ("brainstorming", "research", "architecture", "opportunity_scout", "ui_review"):
        stub_run(
            lambda kw, n, key=key: ChatTurnResult(
                result_text=_ready_proposal_json(team=key), claude_session_id=kw["session_id"]
            )
        )
        sid = client.post(
            f"{_BASE}/sessions", json={"consultant_key": key, "engine_key": _ENGINE_KEY}
        ).json()["session_id"]
        body = client.post(f"{_BASE}/sessions/{sid}/propose").json()
        assert body["status"] == "ready"
        assert body["proposal"]["team"] == key


def test_upsert_brief_before_summarize(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.put(
        f"{_BASE}/sessions/{sid}/brief", json={"content": "draft"}
    ).json()
    assert body["content"] == "draft"


def test_upsert_brief_overwrites(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    client.put(f"{_BASE}/sessions/{sid}/brief", json={"content": "v1"})
    client.put(f"{_BASE}/sessions/{sid}/brief", json={"content": "v2"})
    assert client.get(f"{_BASE}/sessions/{sid}/brief").json()["content"] == "v2"


def test_upsert_brief_404_unknown(client):
    assert client.put(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/brief",
        json={"content": "x"},
    ).status_code == 404


def test_get_brief_null_when_absent(client):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.get(f"{_BASE}/sessions/{sid}/brief").json() is None


def test_get_brief_404_unknown(client):
    assert client.get(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/brief"
    ).status_code == 404


def test_events_404_unknown(client):
    assert client.get(
        f"{_BASE}/sessions/12345678-1234-1234-1234-1234567890ab/events"
    ).status_code == 404


async def test_events_returns_streaming_response(ec_root, pubsub):
    session = explore_chat_mod.create_session("brainstorming", root=ec_root)
    response = await ec_routes.explore_chat_events(
        session.session_id, root=ec_root, pubsub=pubsub
    )
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"


async def test_event_stream_yields_published_events(pubsub):
    sid = "12345678-1234-1234-1234-1234567890ab"
    gen = ec_routes._event_stream(sid, pubsub)
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)
    pubsub.publish(
        f"explore_chat:{sid}",
        TeamEvent("assistant_done", {"content": "yo", "usage": "{}"}),
    )
    frame = await asyncio.wait_for(task, timeout=2)
    assert "assistant_done" in frame
    assert "yo" in frame
    await gen.aclose()


def test_clear_best_effort_logs_exception(ec_root, monkeypatch, caplog):
    s = explore_chat_mod.create_session("brainstorming", root=ec_root)
    monkeypatch.setattr(
        "alan._chat_core.clear_in_flight_turn",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk")),
    )
    with caplog.at_level("ERROR", logger="alan.server"):
        ec_routes._clear_best_effort(s.session_id, ec_root)
    assert any("failed to clear" in r.message for r in caplog.records)


def test_explore_chat_sessions_root_dep(tmp_path):
    req = MagicMock()
    req.app.state.explore_chat_sessions_root = tmp_path / "ec"
    assert explore_chat_sessions_root_dep(req) == tmp_path / "ec"


def test_explore_chat_locks_dep():
    locks = explore_chat_mod.ExploreChatSessionLocks()
    req = MagicMock()
    req.app.state.explore_chat_locks = locks
    assert explore_chat_locks_dep(req) is locks


def test_resolve_explore_chat_sessions_root_default(monkeypatch):
    monkeypatch.delenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", raising=False)
    assert resolve_explore_chat_sessions_root() == roots.explore_chat_sessions_root()


def test_resolve_explore_chat_sessions_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", str(tmp_path / "ec"))
    assert resolve_explore_chat_sessions_root() == tmp_path / "ec"


def test_run_explore_chat_reconciler_logs_cleared(tmp_path, caplog):
    root = tmp_path / "ec"
    s = explore_chat_mod.create_session("brainstorming", root=root)
    msg = explore_chat_mod.ExploreChatMessage(role="user", content="q", ts=datetime.now(UTC))
    explore_chat_mod.append_user_message_and_mark_started(s, msg, root=root)
    with caplog.at_level("INFO", logger="alan.server"):
        _run_explore_chat_reconciler(root)
    assert any("cleared" in r.message for r in caplog.records)


def test_run_explore_chat_reconciler_no_log_when_nothing(tmp_path, caplog):
    root = tmp_path / "ec"
    root.mkdir(parents=True)
    with caplog.at_level("INFO", logger="alan.server"):
        _run_explore_chat_reconciler(root)
    assert not any("cleared" in r.message for r in caplog.records)


def test_run_explore_chat_reconciler_backfills_legacy_engine_key(tmp_path, caplog):
    root = tmp_path / "ec"
    s = explore_chat_mod.create_session("brainstorming", root=root)
    target = explore_chat_mod.session_dir(s.session_id, root=root) / "session.json"
    raw = json.loads(target.read_text())
    del raw["engine_key"]
    target.write_text(json.dumps(raw), encoding="utf-8")
    with caplog.at_level("INFO", logger="alan.server"):
        _run_explore_chat_reconciler(root)
    assert any("backfilled engine_key" in r.message for r in caplog.records)
    reloaded = explore_chat_mod.load_session(s.session_id, root=root)
    assert reloaded.engine_key is not None


class TestExplorePreTurn:
    def test_no_engine_key_transitions_to_not_needed_with_no_checkout(self, ec_root):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        ec_routes._explore_pre_turn(session, ec_root)
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.NOT_NEEDED
        assert not explore_chat_mod.repo_dir(session.session_id, root=ec_root).exists()

    def test_alan_bound_engine_key_transitions_to_not_needed_with_no_checkout(
        self, ec_root, monkeypatch
    ):
        default = engine_config_mod.default_engine_config()
        engine_config_mod.register(default, validate_env=False)
        session = explore_chat_mod.create_session(
            "brainstorming", root=ec_root, engine_key=default.repo.slug
        )
        ec_routes._explore_pre_turn(session, ec_root)
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.NOT_NEEDED
        assert not explore_chat_mod.repo_dir(session.session_id, root=ec_root).exists()

    def test_unregistered_engine_transitions_to_not_needed_with_no_checkout(self, ec_root):
        session = explore_chat_mod.create_session(
            "brainstorming", root=ec_root, engine_key="ghost/repo"
        )
        ec_routes._explore_pre_turn(session, ec_root)
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.NOT_NEEDED
        assert not explore_chat_mod.repo_dir(session.session_id, root=ec_root).exists()

    def test_non_alan_engine_key_clones_and_transitions_to_ready(
        self, ec_root, tmp_path, monkeypatch
    ):
        _register_sprocket_engine()
        session = explore_chat_mod.create_session(
            "brainstorming", root=ec_root, engine_key="sprocket-co/sprocket"
        )

        def fake_provision(engine_key, dest):
            assert engine_key == "sprocket-co/sprocket"
            dest.mkdir(parents=True)

        monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
        ec_routes._explore_pre_turn(session, ec_root)
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.READY
        assert explore_chat_mod.repo_dir(session.session_id, root=ec_root).is_dir()

    def test_provision_failure_transitions_to_error_and_raises_claude_cli_error(
        self, ec_root, tmp_path, monkeypatch
    ):
        _register_sprocket_engine()
        session = explore_chat_mod.create_session(
            "brainstorming", root=ec_root, engine_key="sprocket-co/sprocket"
        )

        def fake_provision(engine_key, dest):
            raise explore_chat_repo_mod.RepoProvisionError("no credential")

        monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
        with pytest.raises(ClaudeCliError, match="no credential"):
            ec_routes._explore_pre_turn(session, ec_root)
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.ERROR
        assert not explore_chat_mod.repo_dir(session.session_id, root=ec_root).exists()

    def test_ready_status_is_frozen_and_never_reprovisions(self, ec_root, monkeypatch):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.READY, root=ec_root
        )
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)

        def boom_needs_repo(engine_key):
            raise AssertionError("needs_repo must not be called once frozen READY")

        monkeypatch.setattr(explore_chat_repo_mod, "needs_repo", boom_needs_repo)
        ec_routes._explore_pre_turn(reloaded, ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.READY

    def test_not_needed_status_is_frozen_and_never_reprovisions(self, ec_root, monkeypatch):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=ec_root
        )
        reloaded = explore_chat_mod.load_session(session.session_id, root=ec_root)

        def boom_needs_repo(engine_key):
            raise AssertionError("needs_repo must not be called once frozen NOT_NEEDED")

        monkeypatch.setattr(explore_chat_repo_mod, "needs_repo", boom_needs_repo)
        ec_routes._explore_pre_turn(reloaded, ec_root)
        assert reloaded.repo_provision == explore_chat_mod.RepoProvisionStatus.NOT_NEEDED


class TestExploreResolveCwd:
    def test_ready_returns_repo_dir(self, ec_root):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.READY, root=ec_root
        )
        assert ec_routes._explore_resolve_cwd(
            session.session_id, ec_root
        ) == explore_chat_mod.repo_dir(session.session_id, root=ec_root)

    def test_not_needed_returns_none(self, ec_root):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        explore_chat_mod.set_repo_provision(
            session.session_id, explore_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=ec_root
        )
        assert ec_routes._explore_resolve_cwd(session.session_id, ec_root) is None

    def test_unchecked_returns_none(self, ec_root):
        session = explore_chat_mod.create_session("brainstorming", root=ec_root)
        assert ec_routes._explore_resolve_cwd(session.session_id, ec_root) is None


def test_send_message_alan_session_creates_no_checkout(client, stub_run, pubsub, ec_root):
    stub_run(lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    assert not (explore_chat_mod.session_dir(sid, root=ec_root) / "repo").exists()
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "not_needed"
    assert body["project_id"] is None


_SPROCKET_BODY = {**_BODY, "engine_key": "sprocket-co/sprocket"}


def test_send_message_non_alan_engine_key_clones_and_passes_repo_cwd(
    client, stub_run, pubsub, ec_root, monkeypatch
):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        dest.mkdir(parents=True)

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    calls = stub_run(
        lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])
    )
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    assert calls[0]["cwd"] == explore_chat_mod.repo_dir(sid, root=ec_root)
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "ready"
    assert body["engine_key"] == "sprocket-co/sprocket"


def test_send_message_provision_failure_yields_turn_error_not_500(
    client, pubsub, ec_root, monkeypatch, caplog
):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        raise explore_chat_repo_mod.RepoProvisionError("no credential for repo")

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("WARNING", logger="alan.server"):
        resp = client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
        assert resp.status_code == 202
        detail = waiter.wait().payload["detail"]
    assert detail == _chat_router._GENERIC_CHAT_ERROR_TEXT  # noqa: SLF001
    assert "no credential for repo" not in detail
    assert any("no credential for repo" in r.message for r in caplog.records)
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "error"


def test_send_message_provision_timeout_yields_turn_error_not_500(
    client, pubsub, ec_root, monkeypatch
):
    _register_sprocket_engine()
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")

    def fake_run_subprocess(cmd, *, cwd, timeout, env=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(explore_chat_repo_mod, "run_subprocess", fake_run_subprocess)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    resp = client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    assert resp.status_code == 202
    waiter.wait()
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "error"


def test_summarize_provision_timeout_yields_502_not_500(client, ec_root, monkeypatch):
    _register_sprocket_engine()
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")

    def fake_run_subprocess(cmd, *, cwd, timeout, env=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(explore_chat_repo_mod, "run_subprocess", fake_run_subprocess)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    resp = client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert resp.status_code == 502


def test_send_message_error_status_is_retried_on_next_turn(
    client, stub_run, pubsub, ec_root, monkeypatch
):
    _register_sprocket_engine()
    attempts = {"n": 0}

    def fake_provision(engine_key, dest):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise explore_chat_repo_mod.RepoProvisionError("transient")
        dest.mkdir(parents=True)

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    stub_run(lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]

    err_waiter = _EventWaiter(pubsub, sid, "error")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "first"})
    err_waiter.wait()
    assert client.get(f"{_BASE}/sessions/{sid}").json()["repo_provision"] == "error"

    done_waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "second"})
    done_waiter.wait()
    assert attempts["n"] == 2
    assert client.get(f"{_BASE}/sessions/{sid}").json()["repo_provision"] == "ready"


def test_send_message_ready_status_is_not_reprovisioned_on_second_turn(
    client, stub_run, pubsub, ec_root, monkeypatch
):
    _register_sprocket_engine()
    clone_calls = {"n": 0}

    def fake_provision(engine_key, dest):
        clone_calls["n"] += 1
        dest.mkdir(parents=True)

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    stub_run(lambda kw, n: ChatTurnResult(result_text=f"r{n}", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]

    first_done = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "one"})
    first_done.wait()

    second_done = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "two"})
    second_done.wait()

    assert clone_calls["n"] == 1
    assert client.get(f"{_BASE}/sessions/{sid}").json()["repo_provision"] == "ready"


def test_propose_scan_and_collision_use_bound_engine_key_scope(
    client, stub_run, ec_root, monkeypatch
):
    _register_sprocket_engine()
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    scan_captured: dict[str, Any] = {}
    collision_captured: dict[str, Any] = {}

    def fake_scan(title, description, **kw):
        scan_captured.update(kw)
        return []

    def fake_check_collision(brief, **kw):
        collision_captured.update(kw)
        return collision_mod.SAFE_RESULT

    def fake_provision(engine_key, dest):
        dest.mkdir(parents=True)

    monkeypatch.setattr(explore_chat_repo_mod, "provision", fake_provision)
    monkeypatch.setattr("alan.routes.explore_chat.scan_predispatch", fake_scan)
    monkeypatch.setattr("alan.collision.check_collision", fake_check_collision)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 200
    assert scan_captured["scope_engine_key"] == "sprocket-co/sprocket"
    assert collision_captured["scope_engine_key"] == "sprocket-co/sprocket"


def test_propose_scan_and_collision_scope_reflects_engine_key_without_project(
    client, stub_run, monkeypatch
):
    stub_run(
        lambda kw, n: ChatTurnResult(
            result_text=_ready_proposal_json(), claude_session_id=kw["session_id"]
        )
    )
    scan_captured: dict[str, Any] = {}
    collision_captured: dict[str, Any] = {}

    def fake_scan(title, description, **kw):
        scan_captured.update(kw)
        return []

    def fake_check_collision(brief, **kw):
        collision_captured.update(kw)
        return collision_mod.SAFE_RESULT

    monkeypatch.setattr("alan.routes.explore_chat.scan_predispatch", fake_scan)
    monkeypatch.setattr("alan.collision.check_collision", fake_check_collision)
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/propose").status_code == 200
    assert scan_captured["scope_engine_key"] == _ENGINE_KEY
    assert collision_captured["scope_engine_key"] == _ENGINE_KEY


def test_archived_session_absent_from_list(client, ec_root, ws_root):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, sid, ws_root / "some-task", title="t", root=ec_root
    )
    assert client.get(f"{_BASE}/sessions").json() == []


def test_archived_session_get_404s(client, ec_root, ws_root):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, sid, ws_root / "some-task", title="t", root=ec_root
    )
    assert client.get(f"{_BASE}/sessions/{sid}").status_code == 404


def test_archived_session_delete_404s(client, ec_root, ws_root):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    prep_chat_mod.archive(
        prep_chat_mod.PrepChatKind.EXPLORE, sid, ws_root / "some-task", title="t", root=ec_root
    )
    assert client.delete(f"{_BASE}/sessions/{sid}").status_code == 404
