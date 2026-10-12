from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import alan.project_amendment as project_amendment_mod
import alan.project_roadmap as project_roadmap_mod
from alan import control_plane, control_token_store, server
from alan import reconciler as _reconciler_mod
from alan.teams._base import TeamEvent


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch):
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )
    monkeypatch.setattr(_reconciler_mod, "seed_dep_sweep", lambda *, queue, workspaces_root: None)


@pytest.fixture
def client():
    with TestClient(server.app) as c:
        yield c


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["queue_paused"] is False
    assert data["paused_until"] is None
    assert "health_state" in data
    assert "last_computed_at" in data


def test_health_queue_paused():
    with TestClient(server.app) as c:
        server.app.state.queue.pause_until(time.time() + 3600.0)
        try:
            res = c.get("/api/health")
            data = res.json()
            assert data["ok"] is True
            assert data["queue_paused"] is True
            assert data["paused_until"] is not None
            assert data["paused_until"] > time.time()
        finally:
            server.app.state.queue.pause_until(0.0)


def test_build_chat_router_mounted():
    paths = set(server.app.openapi()["paths"])
    assert "/api/build-chat/sessions" in paths
    assert "/api/build-chat/sessions/{session_id}/propose" in paths


def test_cors_allows_dev_origin(client):
    res = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_mount_ui_skips_when_dist_missing(tmp_path):
    fresh = FastAPI()
    missing = tmp_path / "does-not-exist"
    assert server._mount_ui(fresh, missing) is False
    assert not any(getattr(route, "name", None) == "ui" for route in fresh.routes)


def test_mount_ui_attaches_when_dist_present(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>ok</body></html>")
    fresh = FastAPI()
    assert server._mount_ui(fresh, dist) is True
    with TestClient(fresh) as c:
        res = c.get("/")
        assert res.status_code == 200
        assert "ok" in res.text


def test_mount_ui_falls_back_to_index_for_spa_route(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa</body></html>")
    fresh = FastAPI()
    assert server._mount_ui(fresh, dist) is True
    with TestClient(fresh) as c:
        deep = c.get("/workspaces/abc/invocations/inv_001")
        assert deep.status_code == 200
        assert "spa" in deep.text
        shallow = c.get("/workspaces")
        assert shallow.status_code == 200
        assert "spa" in shallow.text


def test_mount_ui_skips_when_index_missing(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    fresh = FastAPI()
    assert server._mount_ui(fresh, dist) is False
    assert not any(getattr(route, "name", None) == "ui" for route in fresh.routes)


def test_mount_ui_404s_if_index_removed_after_mount(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    index = dist / "index.html"
    index.write_text("<html><body>spa</body></html>")
    fresh = FastAPI()
    assert server._mount_ui(fresh, dist) is True
    index.unlink()
    with TestClient(fresh) as c:
        res = c.get("/workspaces/abc")
        assert res.status_code == 404


def test_mount_ui_does_not_mask_asset_404(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa</body></html>")
    fresh = FastAPI()
    server._mount_ui(fresh, dist)
    with TestClient(fresh) as c:
        res = c.get("/assets/missing.js")
        assert res.status_code == 404


def test_spa_route_helper_rules():
    assert server._looks_like_spa_route("workspaces") is True
    assert server._looks_like_spa_route("workspaces/abc") is True
    assert server._looks_like_spa_route("api") is False
    assert server._looks_like_spa_route("api/health") is False
    assert server._looks_like_spa_route("assets/app.js") is False


def test_dist_dir_defaults_without_override(monkeypatch):
    monkeypatch.delenv("ALAN_WEB_DIST_DIR", raising=False)
    expected = Path(server.__file__).parent / "web" / "dist"
    assert server._dist_dir() == expected


def test_dist_dir_honors_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-dist"
    monkeypatch.setenv("ALAN_WEB_DIST_DIR", str(override))
    assert server._dist_dir() == override


def test_dist_dir_ignores_blank_override(monkeypatch):
    monkeypatch.setenv("ALAN_WEB_DIST_DIR", "   ")
    expected = Path(server.__file__).parent / "web" / "dist"
    assert server._dist_dir() == expected


def test_lifespan_bootstraps_control_token_at_roots_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    expected = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(expected))
    calls: list[Path] = []
    original = control_plane.bootstrap_control_token

    def _spy(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(control_plane, "bootstrap_control_token", _spy)
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass
    assert calls == [expected]


def test_lifespan_bootstraps_control_token_file_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        token_path = tmp_path / "state" / control_token_store.CONTROL_TOKEN_FILENAME
        assert token_path.is_file()
        assert (token_path.stat().st_mode & 0o777) == 0o600
        assert (
            os.environ[control_plane.CONTROL_TOKEN_ENV]
            == token_path.read_text(encoding="utf-8")
        )
    assert control_plane.CONTROL_TOKEN_ENV not in os.environ


def test_lifespan_reuses_persisted_control_token_across_a_simulated_restart(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)

    first_app = FastAPI(lifespan=server.lifespan)
    with TestClient(first_app):
        first_token = os.environ[control_plane.CONTROL_TOKEN_ENV]
    assert control_plane.CONTROL_TOKEN_ENV not in os.environ

    second_app = FastAPI(lifespan=server.lifespan)
    with TestClient(second_app):
        second_token = os.environ[control_plane.CONTROL_TOKEN_ENV]

    assert second_token == first_token


def test_lifespan_adopts_an_existing_plaintext_value_file_with_no_meta(monkeypatch, tmp_path):
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    state_root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(state_root))
    monkeypatch.delenv(control_plane.CONTROL_TOKEN_ENV, raising=False)
    state_root.mkdir(parents=True)
    (state_root / control_token_store.CONTROL_TOKEN_FILENAME).write_text(
        "pre-existing-legacy-token", encoding="utf-8"
    )

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        assert os.environ[control_plane.CONTROL_TOKEN_ENV] == "pre-existing-legacy-token"
        meta_path = state_root / control_token_store.CONTROL_TOKEN_META_FILENAME
        assert meta_path.is_file()


def test_control_token_router_is_mounted():
    paths = set(server.app.openapi()["paths"])
    assert "/api/control-token/status" in paths
    assert "/api/control-token/extend" in paths
    assert "/api/control-token/rotate" in paths
    assert "/api/control-token/revoke" in paths


def test_health_endpoint_never_returns_control_token(client):
    token = os.environ[control_plane.CONTROL_TOKEN_ENV]
    res = client.get("/api/health")
    assert token not in res.text


def test_plan_rejects_empty_body(client):
    res = client.post("/api/plan", json={"title": "", "description": ""})
    assert res.status_code == 400
    assert "title or description required" in res.json()["detail"]


def test_plan_rejects_whitespace_only_body(client):
    res = client.post("/api/plan", json={"title": "  ", "description": "\n\t"})
    assert res.status_code == 400


def test_plan_streams_role_events(monkeypatch, client):
    def fake(title: str, description: str, emit: Callable[[TeamEvent], None]) -> None:
        emit(TeamEvent("role_started", {"role": "architect"}))
        emit(TeamEvent("role_completed", {"role": "architect"}))
        emit(TeamEvent("complete", {"path": "/fake/p.md", "plan": "body"}))

    monkeypatch.setattr(server, "run_team", fake)
    with client.stream("POST", "/api/plan", json={"title": "x", "description": ""}) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers.get("x-accel-buffering") == "no"
        body = "".join(res.iter_text())

    assert "event: role_started" in body
    assert "event: role_completed" in body
    assert "event: complete" in body
    assert '"role": "architect"' in body
    assert '"plan": "body"' in body


def test_plan_streams_error_event(monkeypatch, client):
    def fake(title: str, description: str, emit: Callable[[TeamEvent], None]) -> None:
        emit(TeamEvent("role_started", {"role": "architect"}))
        emit(TeamEvent("error", {"role": "architect", "detail": "boom"}))

    monkeypatch.setattr(server, "run_team", fake)
    with client.stream("POST", "/api/plan", json={"title": "x", "description": ""}) as res:
        body = "".join(res.iter_text())

    assert "event: error" in body
    assert '"detail": "boom"' in body


def test_format_sse_frame_shape():
    frame = server._format_sse(TeamEvent("role_started", {"role": "architect"}))
    assert frame == 'event: role_started\ndata: {"role": "architect"}\n\n'


async def test_signal_done_passes_through_clean_completion():
    import asyncio

    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    async def fine() -> None:
        return None

    task = asyncio.create_task(fine())
    await task
    server._signal_done(loop, queue, task)
    await asyncio.sleep(0)
    assert await queue.get() is None
    assert queue.empty()


async def test_signal_done_emits_error_event_on_runner_exception():
    import asyncio

    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    async def boom() -> None:
        raise RuntimeError("kaboom")

    task = asyncio.create_task(boom())
    with pytest.raises(RuntimeError):
        await task
    server._signal_done(loop, queue, task)
    await asyncio.sleep(0)
    first = await queue.get()
    second = await queue.get()
    assert isinstance(first, TeamEvent)
    assert first.kind == "error"
    assert first.payload == {"role": "team", "detail": "kaboom"}
    assert second is None


async def test_make_emit_enqueues_when_open():
    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    closed = threading.Event()
    emit = server._make_emit(loop, queue, closed)
    emit(TeamEvent("role_started", {"role": "x"}))
    await asyncio.sleep(0)
    item = await queue.get()
    assert isinstance(item, TeamEvent)
    assert item.payload == {"role": "x"}


async def test_make_emit_skips_after_close():
    import asyncio
    import threading

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    closed = threading.Event()
    closed.set()
    emit = server._make_emit(loop, queue, closed)
    emit(TeamEvent("role_started", {"role": "x"}))
    await asyncio.sleep(0)
    assert queue.empty()


async def test_signal_done_swallows_cancelled_task():
    import asyncio

    queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    async def hang() -> None:
        await asyncio.sleep(10)

    task = asyncio.create_task(hang())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    server._signal_done(loop, queue, task)
    await asyncio.sleep(0)
    assert await queue.get() is None
    assert queue.empty()


def test_lifespan_skips_dispatcher_when_env_set(monkeypatch):
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as client:
        assert client.get("/").status_code in (200, 404)
        assert test_app.state.dispatcher is None


def test_lifespan_zero_value_does_not_disable(monkeypatch, tmp_path):
    from alan import queue as queue_mod

    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "0")
    monkeypatch.setattr(queue_mod, "QUEUE_DB", tmp_path / "queue.db")
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        assert test_app.state.dispatcher is not None


def test_lifespan_starts_and_stops_dispatcher(monkeypatch, tmp_path):
    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as client:
        assert client.get("/").status_code in (200, 404)
        dispatcher = test_app.state.dispatcher
        queue = test_app.state.queue
        assert dispatcher is not None
        assert queue is not None
        non_triage = [j for j in queue.list_jobs() if j.team != "triage"]
        assert non_triage == []


def test_lifespan_wires_workspace_index_into_dispatcher(monkeypatch, tmp_path):
    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        dispatcher = test_app.state.dispatcher
        indexes = test_app.state.workspace_indexes
        expected = indexes[test_app.state.workspaces_root]
        assert dispatcher._workspace_index is expected  # noqa: SLF001


def test_lifespan_wires_reconcile_incidents_into_triage_team(monkeypatch, tmp_path):
    from alan.digest_sweep import reconcile_incidents_sweep

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        triage_team = dispatcher._teams["triage"]  # pyright: ignore[reportPrivateUsage]
        assert triage_team.reconcile_incidents is reconcile_incidents_sweep


def test_lifespan_closes_queue_even_when_dispatcher_stop_raises(monkeypatch, tmp_path, caplog):
    from alan import dispatcher as dispatcher_mod
    from alan import queue as queue_mod

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setattr(queue_mod, "QUEUE_DB", tmp_path / "queue.db")

    original_stop = dispatcher_mod.Dispatcher.stop

    def boom_stop(self, *, timeout=5.0):
        original_stop(self, timeout=timeout)
        raise dispatcher_mod.DispatcherError("stop went sideways")

    monkeypatch.setattr(dispatcher_mod.Dispatcher, "stop", boom_stop)

    test_app = FastAPI(lifespan=server.lifespan)
    with caplog.at_level("ERROR", logger="alan.server"), TestClient(test_app) as client:
        assert client.get("/").status_code in (200, 404)
        queue_ref = test_app.state.queue
    import pytest as _pytest

    from alan.queue import QueueError

    with _pytest.raises(QueueError, match="queue is closed"):
        queue_ref.enqueue("x", "y")
    assert any("dispatcher.stop()" in record.message for record in caplog.records)


def test_lifespan_runs_reconciler_before_dispatcher_starts(monkeypatch, tmp_path, caplog):
    from alan import queue as queue_mod
    from alan import workflow as workflow_mod
    from alan import workspace as workspace_mod

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    pre_queue = queue_mod.Queue(db_path=tmp_path / "queue.db")
    ws = workspace_mod.create("alpha", "d", root=tmp_path / "workspaces")
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="dependency_sweep",
        tasks=[workflow_mod.PendingTaskSpec(id="w", role="w", model="sonnet")],
    )
    pre_queue.enqueue(ws.task_id, "dependency_sweep", triggered_by_invocation_id=inv_id)
    claimed = pre_queue.claim()
    assert claimed is not None
    pre_queue.close()
    test_app = FastAPI(lifespan=server.lifespan)
    with caplog.at_level("INFO", logger="alan.server"), TestClient(test_app) as _client:
        rows = test_app.state.queue.list_jobs(task_id=ws.task_id)
        assert rows[0].status.value == "failed"
        assert rows[0].interruption_count == 1
    assert any("reconciler interrupted" in record.message for record in caplog.records)


def test_lifespan_logs_reconciler_errors(monkeypatch, tmp_path, caplog):
    from alan import reconciler as reconciler_mod

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    def stub_reconcile(*, queue, workspaces_root, pubsub, replay_routing=None):
        del queue, workspaces_root, pubsub, replay_routing
        report = reconciler_mod.ReconciliationReport()
        report.errors.append((42, "boom"))
        return report

    monkeypatch.setattr(reconciler_mod, "reconcile_on_startup", stub_reconcile)
    test_app = FastAPI(lifespan=server.lifespan)
    with caplog.at_level("WARNING", logger="alan.server"), TestClient(test_app):
        pass
    assert any("reconciler errors" in record.message for record in caplog.records)


def test_resolve_dispatcher_concurrency_default_when_unset(monkeypatch):
    monkeypatch.delenv("ALAN_DISPATCHER_CONCURRENCY", raising=False)
    assert server._resolve_dispatcher_concurrency() == 6


def test_resolve_dispatcher_concurrency_valid_value(monkeypatch):
    monkeypatch.setenv("ALAN_DISPATCHER_CONCURRENCY", "3")
    assert server._resolve_dispatcher_concurrency() == 3


def test_resolve_dispatcher_concurrency_invalid_string_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("ALAN_DISPATCHER_CONCURRENCY", "abc")
    with caplog.at_level("WARNING", logger="alan.server"):
        result = server._resolve_dispatcher_concurrency()
    assert result == 6
    assert any("invalid" in r.message for r in caplog.records)


def test_resolve_dispatcher_concurrency_zero_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("ALAN_DISPATCHER_CONCURRENCY", "0")
    with caplog.at_level("WARNING", logger="alan.server"):
        result = server._resolve_dispatcher_concurrency()
    assert result == 6
    assert any("invalid" in r.message for r in caplog.records)


def test_lifespan_dispatcher_concurrency_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_DISPATCHER_CONCURRENCY", "3")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        dispatcher = test_app.state.dispatcher
        assert dispatcher._concurrency == 3  # noqa: SLF001


def test_lifespan_engines_dir_unset_registers_nothing(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod

    monkeypatch.delenv("ALAN_ENGINES_DIR", raising=False)
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines_store"))
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        assert engine_config_mod.get_registered() is None
        assert engine_config_mod.lookup_by_slug("sprocket-co/sprocket") is None


def test_lifespan_discovers_and_registers_second_engine(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod

    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    (engines_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\nposture: propose\n"
    )
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(engines_dir))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines_store"))
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        cfg = engine_config_mod.lookup_by_slug("sprocket-co/sprocket")
        assert cfg is not None
        assert cfg.posture == engine_config_mod.Posture.propose
        assert engine_config_mod.get_registered() is None


def test_lifespan_second_engine_workspace_dispatched_from_shared_queue(monkeypatch, tmp_path):
    from alan import workspace as workspace_mod
    from alan._dispatcher_base import enqueue_with_pending_workflow

    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    (engines_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\nposture: propose\n"
    )
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(engines_dir))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines_store"))
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app) as _client:
        ws = workspace_mod.create(
            "sprocket-task",
            "desc",
            root=test_app.state.workspaces_root,
            engine_key="sprocket-co/sprocket",
        )

        job, _inv_id = enqueue_with_pending_workflow(
            queue=test_app.state.queue,
            pubsub=test_app.state.pubsub,
            workspace=ws,
            team=_noop_dispatch_team(),
            prior=None,
        )

        jobs = test_app.state.queue.list_jobs()
        assert any(j.id == job.id for j in jobs)

        reloaded = workspace_mod.read(ws.task_id, root=test_app.state.workspaces_root)
        assert reloaded.meta.engine_snapshot is not None
        assert reloaded.meta.engine_snapshot.config is not None
        assert reloaded.meta.engine_snapshot.config.repo.slug == "sprocket-co/sprocket"


def _noop_dispatch_team():
    class _NoopTeam:
        name = "noop"
        supports_resume = False

        def plan_workflow(self, _ws, _prior):
            return []

    return _NoopTeam()


def test_lifespan_reloads_persisted_engine_across_restart(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod

    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    monkeypatch.delenv("ALAN_ENGINES_DIR", raising=False)
    monkeypatch.setenv("ALAN_DISABLE_DISPATCHER", "1")
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))

    cfg = engine_config_mod.EngineConfig(
        repo=engine_config_mod.RepoIdentity(owner="sprocket-co", repo="sprocket")
    )

    first_app = FastAPI(lifespan=server.lifespan)
    with TestClient(first_app):
        engine_config_mod.register_and_persist(cfg, validate_env=False)
        assert engine_config_mod.lookup_by_repo(cfg.repo) == cfg

    engine_config_mod._registry.clear()  # noqa: SLF001

    second_app = FastAPI(lifespan=server.lifespan)
    with TestClient(second_app):
        assert engine_config_mod.lookup_by_repo(cfg.repo) == cfg
        assert cfg in engine_config_mod.registered_engines()


def test_lifespan_passes_dispatcher_replay_routing_to_reconciler(monkeypatch, tmp_path):
    from alan import dispatcher as dispatcher_mod
    from alan import reconciler as reconciler_mod

    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    captured_routing: list[object] = []

    def spy_reconcile(*, queue, workspaces_root, pubsub, replay_routing=None):
        captured_routing.append(replay_routing)
        return reconciler_mod.ReconciliationReport()

    monkeypatch.setattr(reconciler_mod, "reconcile_on_startup", spy_reconcile)
    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        pass

    assert len(captured_routing) == 1
    routing = captured_routing[0]
    assert callable(routing)
    assert hasattr(routing, "__self__")
    assert isinstance(routing.__self__, dispatcher_mod.Dispatcher)


def test_create_slot_pool_returns_none_when_no_root(monkeypatch):
    from alan.routes import infra as infra_mod

    monkeypatch.delenv("ALAN_SLOT_POOL_ROOT", raising=False)
    assert infra_mod._create_slot_pool(2) is None


def test_create_slot_pool_returns_none_when_repo_root_unresolvable(monkeypatch, tmp_path, caplog):
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    monkeypatch.setattr(infra_mod, "resolve_repo_root", lambda: None)
    with caplog.at_level("WARNING", logger="alan.server"):
        result = infra_mod._create_slot_pool(2)
    assert result is None
    assert any("slot pool disabled" in r.message for r in caplog.records)


def test_create_slot_pool_returns_none_when_repo_root_not_git_checkout(
    monkeypatch, tmp_path, caplog
):
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    monkeypatch.setattr(infra_mod, "resolve_repo_root", lambda: str(tmp_path / "not-a-repo"))
    with caplog.at_level("WARNING", logger="alan.server"):
        result = infra_mod._create_slot_pool(2)
    assert result is None
    assert any("slot pool disabled" in r.message for r in caplog.records)


def test_create_slot_pool_accepts_remote_url(monkeypatch, tmp_path):
    from alan.routes import infra as infra_mod
    from alan.slot_pool import SlotPool

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    monkeypatch.setattr(
        infra_mod, "resolve_repo_root", lambda: "https://github.com/DejvMatikar/alan.git"
    )
    result = infra_mod._create_slot_pool(2)
    assert isinstance(result, SlotPool)


def test_create_slot_pool_default_pool_carries_alan_forge_target_for_remote_source(
    monkeypatch, tmp_path
):
    from alan.engine_config import DEFAULT_FORGE_TARGET
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    monkeypatch.setattr(
        infra_mod, "resolve_repo_root", lambda: "https://github.com/DejvMatikar/alan.git"
    )
    result = infra_mod._create_slot_pool(2)
    assert result is not None
    assert result._forge_target == DEFAULT_FORGE_TARGET


def test_create_slot_pool_default_pool_resolves_no_forge_target_for_local_source(
    monkeypatch, tmp_path
):
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(infra_mod, "resolve_repo_root", lambda: str(tmp_path))
    result = infra_mod._create_slot_pool(2)
    assert result is not None
    assert result._forge_target is None


def test_create_slot_pool_does_not_reset_at_construction(monkeypatch, tmp_path):
    from alan.routes import infra as infra_mod
    from alan.slot_pool import SlotPool

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(infra_mod, "resolve_repo_root", lambda: str(tmp_path))
    reset_called = []
    real_reset = SlotPool.reset_all

    def spy_reset(self, reserved=None):  # noqa: ANN001
        reset_called.append(True)
        real_reset(self, reserved)

    monkeypatch.setattr(SlotPool, "reset_all", spy_reset)
    result = infra_mod._create_slot_pool(2)
    assert isinstance(result, SlotPool)
    assert reset_called == []


def test_intra_workspace_cap_is_base_when_no_pool():
    from alan.queue import INTRA_WORKSPACE_CAP
    from alan.routes import infra as infra_mod

    assert infra_mod._intra_workspace_cap(None) == INTRA_WORKSPACE_CAP


def test_intra_workspace_cap_is_pooled_when_pool_present(tmp_path):
    from alan.queue import INTRA_WORKSPACE_CAP_POOLED
    from alan.routes import infra as infra_mod
    from alan.slot_pool import SlotPool

    pool = SlotPool(root=tmp_path / "slots", size=2, repo_root=str(tmp_path))
    assert infra_mod._intra_workspace_cap(pool) == INTRA_WORKSPACE_CAP_POOLED


def _sprocket_engine_config():
    from alan.engine_config import EngineConfig, RepoIdentity

    return EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        warmup_commands=[["cargo", "check"]],
    )


def _sprocket_engine_config_with_forge():
    from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity

    return EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
            credential_env_name="SPROCKET_GH_TOKEN",
        ),
        warmup_commands=[["cargo", "check"]],
    )


def test_create_slot_pool_for_engine_carries_engine_forge_target(tmp_path, monkeypatch):
    from alan import secrets_store
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("SPROCKET_GH_TOKEN", "sprocket-secret-token")
    cfg = _sprocket_engine_config_with_forge()
    pool = infra_mod._create_slot_pool_for_engine(tmp_path, 1, cfg)
    forge_target = pool._forge_target  # noqa: SLF001
    assert forge_target is not None
    assert forge_target.credential_env_name == "SPROCKET_GH_TOKEN"
    assert forge_target.repo.slug == "sprocket-co/sprocket"
    env = secrets_store.transport_env(forge_target)
    assert env[secrets_store.ASKPASS_TOKEN_ENV] == "sprocket-secret-token"


def test_clone_source_for_engine_builds_github_https_url():
    from alan.routes import infra as infra_mod

    cfg = _sprocket_engine_config()
    assert infra_mod._clone_source_for_engine(cfg) == "https://github.com/sprocket-co/sprocket.git"


def test_engine_pool_dirname_sanitizes_slash():
    import hashlib

    from alan.routes import infra as infra_mod

    slug = "sprocket-co/sprocket"
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
    assert infra_mod._engine_pool_dirname(slug) == f"sprocket-co_sprocket-{digest}"


def test_engine_pool_dirname_disambiguates_sanitization_collisions():
    from alan.routes import infra as infra_mod

    first = infra_mod._engine_pool_dirname("a_b/c")
    second = infra_mod._engine_pool_dirname("a/b_c")
    assert first != second


def test_create_slot_pool_for_engine_roots_under_sanitized_slug(tmp_path):
    from alan.routes import infra as infra_mod
    from alan.slot_pool import SlotPool

    cfg = _sprocket_engine_config()
    pool = infra_mod._create_slot_pool_for_engine(tmp_path, 2, cfg)
    assert isinstance(pool, SlotPool)
    dirname = infra_mod._engine_pool_dirname(cfg.repo.slug)
    assert pool.slot_path(0) == tmp_path / dirname / "slot_0"


def test_create_slot_pool_for_engine_uses_engine_warmup_commands(tmp_path):
    from alan.routes import infra as infra_mod

    cfg = _sprocket_engine_config()
    pool = infra_mod._create_slot_pool_for_engine(tmp_path, 1, cfg)
    assert pool._warmup_commands == [["cargo", "check"]]  # noqa: SLF001


def test_create_keyed_slot_pools_returns_empty_when_no_root(monkeypatch):
    from alan.routes import infra as infra_mod

    monkeypatch.delenv("ALAN_SLOT_POOL_ROOT", raising=False)
    assert infra_mod._create_keyed_slot_pools() == {}


def test_create_keyed_slot_pools_skips_default_engine(monkeypatch, tmp_path):
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    assert infra_mod._create_keyed_slot_pools() == {}


def test_create_keyed_slot_pools_includes_registered_secondary_engine(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    engine_config_mod.register(_sprocket_engine_config())
    pools = infra_mod._create_keyed_slot_pools()
    assert set(pools) == {"sprocket-co/sprocket"}
    dirname = infra_mod._engine_pool_dirname("sprocket-co/sprocket")
    assert pools["sprocket-co/sprocket"].slot_path(0) == (tmp_path / "slots" / dirname / "slot_0")


def test_create_keyed_slot_pools_defaults_secondary_size_to_small_constant(
    monkeypatch, tmp_path
):
    from alan import engine_config as engine_config_mod
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    engine_config_mod.register(_sprocket_engine_config())
    pools = infra_mod._create_keyed_slot_pools()
    assert pools["sprocket-co/sprocket"]._size == infra_mod._DEFAULT_SECONDARY_POOL_SIZE  # noqa: SLF001


def test_create_keyed_slot_pools_honors_explicit_pool_size(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod
    from alan.engine_config import RepoIdentity
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    engine_config_mod.register(
        engine_config_mod.EngineConfig(
            repo=RepoIdentity(owner="sprocket-co", repo="sprocket"), pool_size=5
        )
    )
    pools = infra_mod._create_keyed_slot_pools()
    assert pools["sprocket-co/sprocket"]._size == 5  # noqa: SLF001


def test_create_keyed_slot_pools_starts_unwarmed(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    engine_config_mod.register(_sprocket_engine_config())
    pools = infra_mod._create_keyed_slot_pools()
    assert pools["sprocket-co/sprocket"].warm is False


def test_reset_and_warm_pools_warms_keyed_pools_but_not_default(monkeypatch, tmp_path):
    from alan.routes import infra as infra_mod
    from alan.slot_pool import SlotPool

    default_pool = SlotPool(root=tmp_path / "default", size=1, repo_root=str(tmp_path))
    keyed_pool = SlotPool(root=tmp_path / "keyed", size=1, repo_root=str(tmp_path))
    infra_mod._reset_and_warm_pools(default_pool, {"sprocket-co/sprocket": keyed_pool}, {})
    assert default_pool.warm is False
    assert keyed_pool.warm is True


def test_reset_and_warm_pools_applies_reservation_to_every_pool(monkeypatch, tmp_path):
    from alan.routes import infra as infra_mod
    from alan.slot_pool import Reservation, SlotPool

    default_pool = SlotPool(root=tmp_path / "default", size=1, repo_root=str(tmp_path))
    keyed_pool = SlotPool(root=tmp_path / "keyed", size=1, repo_root=str(tmp_path))
    reserved = {
        default_pool.slot_path(0): Reservation("sess-default", adoptable=True),
        keyed_pool.slot_path(0): Reservation("sess-keyed", adoptable=True),
    }
    infra_mod._reset_and_warm_pools(default_pool, {"sprocket-co/sprocket": keyed_pool}, reserved)
    assert default_pool._reserved == {0: "sess-default"}  # noqa: SLF001
    assert keyed_pool._reserved == {0: "sess-keyed"}  # noqa: SLF001


def test_create_keyed_slot_pools_isolated_from_default_pool_root(monkeypatch, tmp_path):
    from alan import engine_config as engine_config_mod
    from alan.routes import infra as infra_mod

    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(infra_mod, "resolve_repo_root", lambda: str(tmp_path))
    engine_config_mod.register(_sprocket_engine_config())

    default_pool = infra_mod._create_slot_pool(2)
    keyed = infra_mod._create_keyed_slot_pools()

    assert default_pool is not None
    assert default_pool.slot_path(0) == tmp_path / "slots" / "slot_0"
    assert keyed["sprocket-co/sprocket"].slot_path(0) != default_pool.slot_path(0)


def test_lifespan_wires_keyed_slot_pool_for_second_engine(monkeypatch, tmp_path):
    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    (engines_dir / "sprocket.yaml").write_text(
        "repo:\n  owner: sprocket-co\n  repo: sprocket\n"
        "posture: propose\n"
        "warmup_commands:\n  - [cargo, check]\n"
    )
    monkeypatch.setenv("ALAN_ENGINES_DIR", str(engines_dir))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines_store"))
    monkeypatch.delenv("ALAN_DISABLE_DISPATCHER", raising=False)
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ALAN_SLOT_POOL_ROOT", str(tmp_path / "slots"))
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("ALAN_SLOT_REPO_ROOT", str(tmp_path))

    test_app = FastAPI(lifespan=server.lifespan)
    with TestClient(test_app):
        dispatcher = test_app.state.dispatcher
        assert dispatcher._concurrency == 6  # noqa: SLF001  — shared, single cap unchanged
        keyed = dispatcher._slot_pools_by_engine  # noqa: SLF001
        assert "sprocket-co/sprocket" in keyed
        from alan.routes import infra as infra_mod

        dirname = infra_mod._engine_pool_dirname("sprocket-co/sprocket")
        expected_slot = tmp_path / "slots" / dirname / "slot_0"
        assert keyed["sprocket-co/sprocket"].slot_path(0) == expected_slot
        assert dispatcher._slot_pool.slot_path(0) == tmp_path / "slots" / "slot_0"  # noqa: SLF001
        assert dispatcher._pool_factory is not None  # noqa: SLF001 — runtime registration wired


def test_roadmap_reorder_ids_extracts_string_list():
    assert server._roadmap_reorder_ids({"ordered_ids": ["a", "b"]}) == ["a", "b"]  # noqa: SLF001


def test_roadmap_reorder_ids_raises_on_missing_key():
    with pytest.raises(project_roadmap_mod.RoadmapError):
        server._roadmap_reorder_ids({})  # noqa: SLF001


def test_roadmap_reorder_ids_raises_on_non_list_value():
    with pytest.raises(project_roadmap_mod.RoadmapError):
        server._roadmap_reorder_ids({"ordered_ids": "not-a-list"})  # noqa: SLF001


def test_roadmap_edit_fields_extracts_item_id_title_text():
    fields = server._roadmap_edit_fields(  # noqa: SLF001
        {"item_id": "itm_1", "title": "T", "text": "B"}
    )
    assert fields == ("itm_1", "T", "B")


def test_roadmap_edit_fields_defaults_title_and_text_to_none():
    fields = server._roadmap_edit_fields({"item_id": "itm_1"})  # noqa: SLF001
    assert fields == ("itm_1", None, None)


def test_roadmap_edit_fields_raises_on_missing_item_id():
    with pytest.raises(project_roadmap_mod.RoadmapError):
        server._roadmap_edit_fields({"title": "T"})  # noqa: SLF001


@pytest.fixture
def amendment_ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def amendment_projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def amendment_client(
    amendment_ws_root: Path, amendment_projects_root: Path
) -> Iterator[TestClient]:
    _ = amendment_projects_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: amendment_ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _create_project(client: TestClient, title: str = "Spine") -> str:
    res = client.post("/api/projects", json={"title": title})
    assert res.status_code == 201
    project_id: str = res.json()["id"]
    return project_id


def _import_roadmap(
    client: TestClient, project_id: str, markdown: str
) -> list[dict[str, Any]]:
    res = client.post(f"/api/projects/{project_id}/roadmap/import", json={"markdown": markdown})
    assert res.status_code == 201
    items: list[dict[str, Any]] = res.json()["items"]
    return items


def test_approve_roadmap_reorder_changes_order_only(amendment_client: TestClient) -> None:
    project_id = _create_project(amendment_client)
    items = _import_roadmap(
        amendment_client, project_id, "## First\nf\n\n## Second\ns\n"
    )
    ids = [i["id"] for i in items]

    propose_res = amendment_client.post(
        f"/api/projects/{project_id}/roadmap/reorder",
        json={"ordered_ids": [ids[1], ids[0]]},
    )
    assert propose_res.status_code == 201
    fingerprint = propose_res.json()["fingerprint"]

    approve_res = amendment_client.post(
        f"/api/projects/{project_id}/amendments/{fingerprint}/approve"
    )
    assert approve_res.status_code == 200

    roadmap = amendment_client.get(f"/api/projects/{project_id}").json()["roadmap"]
    ordered = sorted(roadmap, key=lambda r: r["order"])
    assert [r["id"] for r in ordered] == [ids[1], ids[0]]
    assert {r["id"] for r in ordered} == set(ids)


def test_approve_roadmap_edit_changes_title_and_text(amendment_client: TestClient) -> None:
    project_id = _create_project(amendment_client)
    items = _import_roadmap(amendment_client, project_id, "## Old Title\nold body\n")
    item_id = items[0]["id"]

    propose_res = amendment_client.post(
        f"/api/projects/{project_id}/roadmap/items/{item_id}",
        json={"title": "New Title", "text": "new body"},
    )
    assert propose_res.status_code == 201
    fingerprint = propose_res.json()["fingerprint"]

    approve_res = amendment_client.post(
        f"/api/projects/{project_id}/amendments/{fingerprint}/approve"
    )
    assert approve_res.status_code == 200

    roadmap = amendment_client.get(f"/api/projects/{project_id}").json()["roadmap"]
    entry = next(r for r in roadmap if r["id"] == item_id)
    assert entry["title"] == "New Title"
    assert entry["text"] == "new body"


def test_approve_roadmap_reorder_409_on_malformed_payload(amendment_client: TestClient) -> None:
    project_id = _create_project(amendment_client)
    _import_roadmap(amendment_client, project_id, "## Only\nbody\n")
    queue = server.app.state.queue

    fingerprint, _record = project_amendment_mod.propose_amendment(
        queue,
        project_id,
        kind="roadmap_reorder",
        payload={},
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )

    res = amendment_client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 409
    assert project_amendment_mod.get_pending_amendment(queue, fingerprint) is None


def test_approve_project_amendment_publishes_project_amendment_approved_event(
    amendment_client: TestClient,
) -> None:
    project_id = _create_project(amendment_client)
    items = _import_roadmap(amendment_client, project_id, "## Only\nbody\n")
    item_id = items[0]["id"]
    propose_res = amendment_client.post(
        f"/api/projects/{project_id}/roadmap/items/{item_id}",
        json={"title": "New Title"},
    )
    assert propose_res.status_code == 201
    fingerprint = propose_res.json()["fingerprint"]

    events: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(events.append)
    try:
        approve_res = amendment_client.post(
            f"/api/projects/{project_id}/amendments/{fingerprint}/approve"
        )
        assert approve_res.status_code == 200
    finally:
        unsubscribe()

    assert any(
        e.kind == "project_amendment_approved" and e.payload.get("fingerprint") == fingerprint
        for e in events
    )
    assert not any(e.kind == "inbox_disposition" for e in events)


def test_no_registered_route_starts_with_api_inbox() -> None:
    paths = [route.path for route in server.app.routes if hasattr(route, "path")]
    assert paths, "expected at least one registered route"
    assert not any(p.startswith("/api/inbox") for p in paths)


def test_approve_roadmap_edit_409_when_item_no_longer_exists(
    amendment_client: TestClient,
) -> None:
    project_id = _create_project(amendment_client)
    _import_roadmap(amendment_client, project_id, "## Only\nbody\n")
    queue = server.app.state.queue

    fingerprint, _record = project_amendment_mod.propose_amendment(
        queue,
        project_id,
        kind="roadmap_edit",
        payload={"item_id": "itm_ghost", "title": "X"},
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )

    res = amendment_client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 409
