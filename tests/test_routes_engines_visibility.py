from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import engine_config as engine_config_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig
from alan.slot_pool import SlotPool


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


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
def build_chat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "build-chat"
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", str(root))
    return root


@pytest.fixture
def explore_chat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "explore-chat"
    monkeypatch.setenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", str(root))
    return root


@pytest.fixture
def client(
    engines_root: Path,
    secrets_root: Path,
    workspaces_root: Path,
    build_chat_root: Path,
    explore_chat_root: Path,
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root, build_chat_root, explore_chat_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)


class _RecordingPoolFactory:
    def __init__(self, pools_root: Path) -> None:
        self._pools_root = pools_root
        self.calls: list[EngineConfig] = []

    def __call__(self, cfg: EngineConfig) -> SlotPool:
        self.calls.append(cfg)
        size = cfg.pool_size or 2
        return SlotPool(
            root=self._pools_root / cfg.repo.slug.replace("/", "_"),
            size=size,
            repo_root=str(self._pools_root),
        )


@pytest.fixture
def pool_factory(tmp_path: Path) -> _RecordingPoolFactory:
    return _RecordingPoolFactory(tmp_path / "pools")


@pytest.fixture
def dispatcher(
    client: TestClient,
    workspaces_root: Path,
    pool_factory: _RecordingPoolFactory,
) -> Dispatcher:
    return Dispatcher(
        server.app.state.queue,
        {},
        pubsub=server.app.state.pubsub,
        workspaces_root=workspaces_root,
        pool_factory=pool_factory,
    )


@pytest.fixture
def client_with_dispatcher(
    client: TestClient, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: dispatcher
    try:
        yield client
    finally:
        server.app.dependency_overrides.pop(server._optional_dispatcher_dep, None)


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _minimal_body(owner: str = "acme", repo: str = "widget") -> dict[str, object]:
    return {"repo": {"owner": owner, "repo": repo}}


def _create(client, body=None, **kwargs):
    return client.post(
        "/api/engines", json=body or _minimal_body(**kwargs), headers=_control_headers()
    )


def test_hide_engine_requires_control_token(client: TestClient) -> None:
    _create(client)
    res = client.post("/api/engines/acme/widget/hide")
    assert res.status_code == 401


def test_hide_engine_wrong_token_403(client: TestClient) -> None:
    _create(client)
    res = client.post(
        "/api/engines/acme/widget/hide",
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_hide_engine_unknown_repo_404(client: TestClient) -> None:
    res = client.post("/api/engines/no/such/hide", headers=_control_headers())
    assert res.status_code == 404


def test_hide_engine_default_repo_409_and_mutates_no_state(client: TestClient) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)

    res = client.post(
        f"/api/engines/{default_cfg.repo.owner}/{default_cfg.repo.repo}/hide",
        headers=_control_headers(),
    )

    assert res.status_code == 409
    live = engine_config_mod.lookup_by_repo(default_cfg.repo)
    assert live is not None
    assert live.hidden is False
    assert live.posture == default_cfg.posture


def test_hide_engine_returns_200_with_quiesce_report(client: TestClient) -> None:
    _create(client)

    res = client.post("/api/engines/acme/widget/hide", headers=_control_headers())

    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["residue_workspace_ids"] == []
    assert body["pool_disposed"] is False
    assert body["pool_residue"] is None


def test_hide_engine_persists_hidden_and_floors_posture(client: TestClient) -> None:
    _create(client, body={**_minimal_body(), "posture": "self_merge"})

    client.post("/api/engines/acme/widget/hide", headers=_control_headers())

    res = client.get("/api/engines/acme/widget")
    body = res.json()
    assert body["hidden"] is True
    assert body["posture"] == "observe"
    assert body["hidden_restore_posture"] == "self_merge"


def test_hide_engine_disposes_registered_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001

    res = client_with_dispatcher.post(
        "/api/engines/acme/widget/hide", headers=_control_headers()
    )

    assert res.status_code == 200
    assert res.json()["pool_disposed"] is True
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_hide_engine_reports_residue_and_still_returns_200(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher, workspaces_root: Path
) -> None:
    from alan import workspace as workspace_mod

    _create(client_with_dispatcher)
    ws = workspace_mod.create(
        "t1", "d", root=workspaces_root, engine_key="acme/widget"
    )
    dispatcher._queue.enqueue(ws.task_id, "research")  # noqa: SLF001
    dispatcher._queue.claim()  # noqa: SLF001

    res = client_with_dispatcher.post(
        "/api/engines/acme/widget/hide", headers=_control_headers()
    )

    assert res.status_code == 200
    assert res.json()["residue_workspace_ids"] == [ws.task_id]


def test_unhide_engine_requires_control_token(client: TestClient) -> None:
    _create(client)
    res = client.post("/api/engines/acme/widget/unhide")
    assert res.status_code == 401


def test_unhide_engine_unknown_repo_404(client: TestClient) -> None:
    res = client.post("/api/engines/no/such/unhide", headers=_control_headers())
    assert res.status_code == 404


def test_unhide_engine_restores_exact_posture(client: TestClient) -> None:
    _create(client, body={**_minimal_body(), "posture": "self_merge"})
    client.post("/api/engines/acme/widget/hide", headers=_control_headers())

    res = client.post("/api/engines/acme/widget/unhide", headers=_control_headers())

    assert res.status_code == 200
    body = res.json()
    assert body["hidden"] is False
    assert body["posture"] == "self_merge"
    assert body["hidden_restore_posture"] is None


def test_unhide_engine_is_noop_when_not_hidden(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    pool_before = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001

    res = client_with_dispatcher.post(
        "/api/engines/acme/widget/unhide", headers=_control_headers()
    )

    assert res.status_code == 200
    assert res.json()["hidden"] is False
    assert dispatcher._slot_pools_by_engine["acme/widget"] is pool_before  # noqa: SLF001


def test_unhide_engine_rebuilds_pool_when_dispatcher_present(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    client_with_dispatcher.post("/api/engines/acme/widget/hide", headers=_control_headers())
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001

    res = client_with_dispatcher.post(
        "/api/engines/acme/widget/unhide", headers=_control_headers()
    )

    assert res.status_code == 200
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_unhide_engine_without_dispatcher_does_not_error(client: TestClient) -> None:
    _create(client)
    client.post("/api/engines/acme/widget/hide", headers=_control_headers())

    res = client.post("/api/engines/acme/widget/unhide", headers=_control_headers())

    assert res.status_code == 200
    assert res.json()["hidden"] is False
