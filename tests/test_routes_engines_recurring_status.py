from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import engine_config as engine_config_mod
from alan import server
from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, ForgeConfig, RepoIdentity

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105


def _engine(owner: str = "acme", repo: str = "widget", *, with_forge: bool = True) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    forge = (
        ForgeConfig(
            repo=identity, credential_env_name=_FORGE_CREDENTIAL, authoritative_checks=frozenset()
        )
        if with_forge
        else None
    )
    return EngineConfig(repo=identity, forge=forge)


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
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    engines_root: Path, secrets_root: Path, projects_root: Path, workspaces_root: Path
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root, projects_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)


def test_get_recurring_status_returns_404_for_unknown_repo(client: TestClient) -> None:
    res = client.get("/api/engines/no/such/recurring-status")
    assert res.status_code == 404


def test_get_recurring_status_returns_200_with_schema_for_known_repo(client: TestClient) -> None:
    engine_config_mod.register(_engine(with_forge=False), default=True, validate_env=False)
    res = client.get("/api/engines/acme/widget/recurring-status")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["is_default"] is True
    assert isinstance(body["processes"], list)
    assert body["service_scope_note"] is None
    assert "backup_coverage" in body
    assert "excluded_caveat" in body["backup_coverage"]


def test_get_recurring_status_non_default_engine_gets_service_scope_note(
    client: TestClient,
) -> None:
    engine_config_mod.register(
        _engine("default-owner", "default-repo"), default=True, validate_env=False
    )
    engine_config_mod.register(_engine(with_forge=False), default=False, validate_env=False)
    res = client.get("/api/engines/acme/widget/recurring-status")
    assert res.status_code == 200
    body = res.json()
    assert body["is_default"] is False
    assert body["service_scope_note"] is not None
    process_ids = {p["process_id"] for p in body["processes"]}
    assert "triage_sweep" not in process_ids
    assert "backup_job" not in process_ids


def test_get_recurring_status_does_not_require_control_token(client: TestClient) -> None:
    engine_config_mod.register(_engine(with_forge=False), default=True, validate_env=False)
    res = client.get("/api/engines/acme/widget/recurring-status")
    assert res.status_code == 200


def test_get_recurring_status_response_includes_run_state_and_url_fields(
    client: TestClient,
) -> None:
    engine_config_mod.register(_engine(with_forge=True), default=True, validate_env=False)
    res = client.get("/api/engines/acme/widget/recurring-status")
    assert res.status_code == 200
    by_id = {p["process_id"]: p for p in res.json()["processes"]}
    assert "state" in by_id["scheduled_ci"]["last_run"]
    assert "url" in by_id["scheduled_ci"]["last_run"]
    assert "state" in by_id["triage_sweep"]["last_run"]
    assert "url" in by_id["triage_sweep"]["last_run"]


def _write_backup_receipt(root: Path, *, finished_at: str) -> None:
    path = root / "backup_receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "outcome": "success",
                "started_at": finished_at,
                "finished_at": finished_at,
                "duration_seconds": 1.0,
            }
        ),
        encoding="utf-8",
    )


def _strip_tzinfo(iso_value: str) -> str:
    return datetime.fromisoformat(iso_value).replace(tzinfo=None).isoformat()


def _seed_naive_updated_at_workspace(workspaces_root: Path) -> None:
    ws = workspace_mod.create(
        "naive-updated-at", "d", root=workspaces_root, engine_key="acme/widget"
    )
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["updated_at"] = _strip_tzinfo(raw["updated_at"])
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")


def test_get_recurring_status_returns_200_with_naive_meta_timestamp(
    client: TestClient, _state_root: Path, workspaces_root: Path
) -> None:
    engine_config_mod.register(_engine(with_forge=False), default=True, validate_env=False)
    _write_backup_receipt(_state_root, finished_at="2026-01-01T00:00:00+00:00")
    _seed_naive_updated_at_workspace(workspaces_root)

    res = client.get("/api/engines/acme/widget/recurring-status")

    assert res.status_code == 200
    assert res.json()["backup_coverage"]["changed_since_count"] is not None


def test_get_recurring_status_non_default_repo_200_with_naive_meta_timestamp(
    client: TestClient, _state_root: Path, workspaces_root: Path
) -> None:
    engine_config_mod.register(
        _engine("acme", "default-repo"), default=True, validate_env=False
    )
    engine_config_mod.register(
        _engine("acme", "widget", with_forge=False), default=False, validate_env=False
    )
    _write_backup_receipt(_state_root, finished_at="2026-01-01T00:00:00+00:00")
    _seed_naive_updated_at_workspace(workspaces_root)

    res = client.get("/api/engines/acme/widget/recurring-status")

    assert res.status_code == 200
    body = res.json()
    assert body["is_default"] is False
    assert body["backup_coverage"]["changed_since_count"] is not None
