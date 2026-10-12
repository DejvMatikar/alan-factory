from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.build_chat as build_chat_mod
import alan.explore_chat_repo as chat_repo_mod
import alan.routes.build_chat as bc_routes
from alan import engine_config as engine_config_mod
from alan import secrets_store, server
from alan.claude_cli import ChatTurnResult, ClaudeCliError
from alan.dispatcher import PubSub
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity
from alan.teams._base import TeamEvent

_SPROCKET_CREDENTIAL_ENV = "SPROCKET_GH_TOKEN"  # noqa: S105


def _register_sprocket_engine() -> EngineConfig:
    repo = RepoIdentity(owner="sprocket-co", repo="sprocket")
    cfg = EngineConfig(
        repo=repo, forge=ForgeConfig(repo=repo, credential_env_name=_SPROCKET_CREDENTIAL_ENV)
    )
    engine_config_mod.register(cfg, validate_env=False)
    return cfg


_ENGINE_KEY = "DejvMatikar/alan"
_BODY = {"engine_key": _ENGINE_KEY}
_SPROCKET_BODY = {"engine_key": "sprocket-co/sprocket"}
_BASE = "/api/build-chat"


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


class TestBuildPreTurn:
    def test_alan_bound_transitions_to_not_needed_with_no_checkout(self, bc_root):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        bc_routes._build_pre_turn(session, bc_root)  # noqa: SLF001
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.NOT_NEEDED
        assert not build_chat_mod.repo_dir(session.session_id, root=bc_root).exists()

    def test_non_alan_engine_key_clones_and_transitions_to_ready(self, bc_root, monkeypatch):
        _register_sprocket_engine()
        session = build_chat_mod.create_session(root=bc_root, engine_key="sprocket-co/sprocket")

        def fake_provision(engine_key, dest):
            assert engine_key == "sprocket-co/sprocket"
            dest.mkdir(parents=True)

        monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
        bc_routes._build_pre_turn(session, bc_root)  # noqa: SLF001
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.READY
        assert build_chat_mod.repo_dir(session.session_id, root=bc_root).is_dir()

    def test_provision_failure_transitions_to_error_and_raises_claude_cli_error(
        self, bc_root, monkeypatch
    ):
        _register_sprocket_engine()
        session = build_chat_mod.create_session(root=bc_root, engine_key="sprocket-co/sprocket")

        def fake_provision(engine_key, dest):
            raise chat_repo_mod.RepoProvisionError("no credential")

        monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
        with pytest.raises(ClaudeCliError, match="no credential"):
            bc_routes._build_pre_turn(session, bc_root)  # noqa: SLF001
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.ERROR
        assert not build_chat_mod.repo_dir(session.session_id, root=bc_root).exists()

    def test_ready_status_is_frozen_and_never_reprovisions(self, bc_root, monkeypatch):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.READY, root=bc_root
        )
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)

        def boom_needs_repo(engine_key):
            raise AssertionError("needs_repo must not be called once frozen READY")

        monkeypatch.setattr(chat_repo_mod, "needs_repo", boom_needs_repo)
        bc_routes._build_pre_turn(reloaded, bc_root)  # noqa: SLF001
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.READY

    def test_not_needed_status_is_frozen_and_never_reprovisions(self, bc_root, monkeypatch):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=bc_root
        )
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)

        def boom_needs_repo(engine_key):
            raise AssertionError("needs_repo must not be called once frozen NOT_NEEDED")

        monkeypatch.setattr(chat_repo_mod, "needs_repo", boom_needs_repo)
        bc_routes._build_pre_turn(reloaded, bc_root)  # noqa: SLF001
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.NOT_NEEDED

    def test_registered_engine_without_forge_raises_rather_than_degrading_to_not_needed(
        self, bc_root
    ):
        cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="bare"))
        engine_config_mod.register(cfg, validate_env=False)
        session = build_chat_mod.create_session(root=bc_root, engine_key="acme/bare")
        with pytest.raises(ClaudeCliError, match="no forge configured"):
            bc_routes._build_pre_turn(session, bc_root)  # noqa: SLF001
        reloaded = build_chat_mod.load_session(session.session_id, root=bc_root)
        assert reloaded.repo_provision == build_chat_mod.RepoProvisionStatus.ERROR


class TestBuildResolveCwd:
    def test_ready_returns_repo_dir(self, bc_root):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.READY, root=bc_root
        )
        assert bc_routes._build_resolve_cwd(  # noqa: SLF001
            session.session_id, bc_root
        ) == build_chat_mod.repo_dir(session.session_id, root=bc_root)

    def test_not_needed_returns_none(self, bc_root):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        build_chat_mod.set_repo_provision(
            session.session_id, build_chat_mod.RepoProvisionStatus.NOT_NEEDED, root=bc_root
        )
        assert bc_routes._build_resolve_cwd(session.session_id, bc_root) is None  # noqa: SLF001

    def test_unchecked_returns_none(self, bc_root):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        assert bc_routes._build_resolve_cwd(session.session_id, bc_root) is None  # noqa: SLF001


class TestShopStatsWrappedPromptCarriesRepoLabel:
    def test_alan_bound_session(self, bc_root):
        session = build_chat_mod.create_session(root=bc_root, engine_key=_ENGINE_KEY)
        ctx = bc_routes._ctx_with_shop_stats("stats block")  # noqa: SLF001
        assert "alan (this repository)" in ctx.system_prompt(session)

    def test_non_alan_bound_session(self, bc_root):
        _register_sprocket_engine()
        session = build_chat_mod.create_session(root=bc_root, engine_key="sprocket-co/sprocket")
        ctx = bc_routes._ctx_with_shop_stats("stats block")  # noqa: SLF001
        assert "sprocket-co/sprocket" in ctx.system_prompt(session)


def test_send_message_alan_session_creates_no_checkout(client, stub_run, pubsub, bc_root):
    stub_run(lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    assert not (build_chat_mod.session_dir(sid, root=bc_root) / "repo").exists()
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "not_needed"


def test_send_message_non_alan_engine_key_clones_and_passes_repo_cwd(
    client, stub_run, pubsub, bc_root, monkeypatch
):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        dest.mkdir(parents=True)

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
    calls = stub_run(
        lambda kw, n: ChatTurnResult(result_text="ok", claude_session_id=kw["session_id"])
    )
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "assistant_done")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    assert calls[0]["cwd"] == build_chat_mod.repo_dir(sid, root=bc_root)
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "ready"
    assert body["engine_key"] == "sprocket-co/sprocket"


def test_send_message_provision_failure_yields_turn_error_not_500(
    client, pubsub, bc_root, monkeypatch, caplog
):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        raise chat_repo_mod.RepoProvisionError("no credential for repo")

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    with caplog.at_level("WARNING", logger="alan.server"):
        resp = client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
        assert resp.status_code == 202
        detail = waiter.wait().payload["detail"]
    assert "no credential for repo" not in detail
    assert any("no credential for repo" in r.message for r in caplog.records)
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert body["repo_provision"] == "error"


def test_provision_failure_leaves_cli_session_id_unset(client, pubsub, bc_root, monkeypatch):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        raise chat_repo_mod.RepoProvisionError("no credential")

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    waiter = _EventWaiter(pubsub, sid, "error")
    client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": "hi"})
    waiter.wait()
    reloaded = build_chat_mod.load_session(sid, root=bc_root)
    assert reloaded.cli_session_id is None


def test_summarize_provision_failure_yields_502(client, bc_root, monkeypatch):
    _register_sprocket_engine()

    def fake_provision(engine_key, dest):
        raise chat_repo_mod.RepoProvisionError("no credential for repo")

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    resp = client.post(f"{_BASE}/sessions/{sid}/summarize")
    assert resp.status_code == 502


def test_missing_secret_yields_502_on_summarize_and_propose(client, bc_root, monkeypatch):
    _register_sprocket_engine()
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: None)  # noqa: ARG005
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid}/summarize").status_code == 502
    sid2 = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]
    assert client.post(f"{_BASE}/sessions/{sid2}/propose").status_code == 502


def test_send_message_error_status_is_retried_on_next_turn(
    client, stub_run, pubsub, bc_root, monkeypatch
):
    _register_sprocket_engine()
    attempts = {"n": 0}

    def fake_provision(engine_key, dest):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise chat_repo_mod.RepoProvisionError("transient")
        dest.mkdir(parents=True)

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
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


def test_send_message_ready_status_is_not_reprovisioned_on_second_and_third_turn(
    client, stub_run, pubsub, bc_root, monkeypatch
):
    _register_sprocket_engine()
    clone_calls = {"n": 0}

    def fake_provision(engine_key, dest):
        clone_calls["n"] += 1
        dest.mkdir(parents=True)

    monkeypatch.setattr(chat_repo_mod, "provision", fake_provision)
    stub_run(lambda kw, n: ChatTurnResult(result_text=f"r{n}", claude_session_id=kw["session_id"]))
    sid = client.post(f"{_BASE}/sessions", json=_SPROCKET_BODY).json()["session_id"]

    for content in ("one", "two", "three"):
        done = _EventWaiter(pubsub, sid, "assistant_done")
        client.post(f"{_BASE}/sessions/{sid}/messages", json={"content": content})
        done.wait()

    assert clone_calls["n"] == 1
    assert client.get(f"{_BASE}/sessions/{sid}").json()["repo_provision"] == "ready"


def test_repo_provision_appears_on_get_session(client, bc_root):
    sid = client.post(f"{_BASE}/sessions", json=_BODY).json()["session_id"]
    body = client.get(f"{_BASE}/sessions/{sid}").json()
    assert "repo_provision" in body
