from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import onboarding as onboarding_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig
from alan.engine_preflight import PreflightResult
from alan.queue import Queue
from alan.slot_pool import SlotPool


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def client(
    secrets_root: Path, engines_root: Path, projects_root: Path
) -> Iterator[TestClient]:
    _ = secrets_root, engines_root, projects_root
    with TestClient(server.app) as c:
        yield c


class _RecordingPoolFactory:
    def __init__(self, root: Path) -> None:
        self._root = root

    def __call__(self, cfg: EngineConfig) -> SlotPool:
        return SlotPool(
            root=self._root / cfg.repo.slug.replace("/", "_"),
            size=cfg.pool_size or 2,
            repo_root=str(self._root),
        )


@pytest.fixture
def dispatcher_queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "dispatcher-queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def dispatcher(
    client: TestClient, dispatcher_queue: Queue, tmp_path: Path
) -> Dispatcher:
    _ = client
    return Dispatcher(
        dispatcher_queue, {}, pool_factory=_RecordingPoolFactory(tmp_path / "pools")
    )


@pytest.fixture
def client_with_dispatcher(
    client: TestClient, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._dispatcher_dep] = lambda: dispatcher
    try:
        yield client
    finally:
        server.app.dependency_overrides.pop(server._dispatcher_dep, None)


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _pass_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug, checked_at="2026-07-22T00:00:00+00:00", overall="pass", findings=()
    )


def _fail_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug, checked_at="2026-07-22T00:00:00+00:00", overall="fail", findings=()
    )


def _create_body(**overrides: str) -> dict[str, str]:
    body = {
        "title": "My Project",
        "owner": "acme",
        "repo": "widget",
        "template": "python",
        "token": "ghp_abc",
    }
    body.update(overrides)
    return body


def _create(client, **overrides):
    return client.post(
        "/api/onboarding", json=_create_body(**overrides), headers=_control_headers()
    )


def test_create_onboarding_without_control_token_returns_401(
    client_with_dispatcher: TestClient,
) -> None:
    res = client_with_dispatcher.post("/api/onboarding", json=_create_body())
    assert res.status_code == 401


def test_create_onboarding_wrong_control_token_returns_403(
    client_with_dispatcher: TestClient,
) -> None:
    res = client_with_dispatcher.post(
        "/api/onboarding",
        json=_create_body(),
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_create_onboarding_without_dispatcher_returns_503(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 503


def test_create_onboarding_invalid_body_returns_422(client_with_dispatcher: TestClient) -> None:
    res = client_with_dispatcher.post(
        "/api/onboarding",
        json={"title": "", "owner": "acme", "repo": "widget", "template": "python", "token": "x"},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_onboarding_success_returns_201_provisioning_project(
    client_with_dispatcher: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)

    res = _create(client_with_dispatcher)

    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "provisioning"
    assert body["engine_key"] == "acme/widget"
    assert body["title"] == "My Project"


def test_create_onboarding_duplicate_repo_returns_409(
    client_with_dispatcher: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    _create(client_with_dispatcher)

    res = _create(client_with_dispatcher, title="Second Attempt")

    assert res.status_code == 409


def test_create_onboarding_unknown_template_returns_422(
    client_with_dispatcher: TestClient,
) -> None:
    res = _create(client_with_dispatcher, template="cobol")
    assert res.status_code == 422


def test_create_onboarding_preflight_failure_returns_422(
    client_with_dispatcher: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _fail_preflight)

    res = _create(client_with_dispatcher)

    assert res.status_code == 422


def test_delete_onboarding_without_control_token_returns_401(
    client_with_dispatcher: TestClient,
) -> None:
    res = client_with_dispatcher.delete("/api/onboarding/some-id")
    assert res.status_code == 401


def test_delete_onboarding_wrong_control_token_returns_403(
    client_with_dispatcher: TestClient,
) -> None:
    res = client_with_dispatcher.delete(
        "/api/onboarding/some-id",
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_delete_onboarding_without_dispatcher_returns_503(client: TestClient) -> None:
    res = client.delete("/api/onboarding/some-id", headers=_control_headers())
    assert res.status_code == 503


def test_delete_onboarding_unknown_id_returns_404(client_with_dispatcher: TestClient) -> None:
    res = client_with_dispatcher.delete(
        "/api/onboarding/no-such-project", headers=_control_headers()
    )
    assert res.status_code == 404


def test_delete_onboarding_tears_down_provisioning_attempt(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    create_res = _create(client_with_dispatcher)
    project_id = create_res.json()["id"]

    res = client_with_dispatcher.delete(
        f"/api/onboarding/{project_id}", headers=_control_headers()
    )

    assert res.status_code == 204
    assert client_with_dispatcher.get(f"/api/projects/{project_id}").status_code == 404
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_delete_onboarding_refuses_active_project(
    client_with_dispatcher: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_mod, "run_preflight", _pass_preflight)
    monkeypatch.setattr(onboarding_mod, "_spawn_warm_task", lambda *a: None)
    create_res = _create(client_with_dispatcher)
    project_id = create_res.json()["id"]
    client_with_dispatcher.patch(f"/api/projects/{project_id}", json={"status": "active"})

    res = client_with_dispatcher.delete(
        f"/api/onboarding/{project_id}", headers=_control_headers()
    )

    assert res.status_code == 409
