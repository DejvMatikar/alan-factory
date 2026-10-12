from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import config_files as config_files_mod
from alan import control_plane, engine_config, server
from alan.config_files import QuarantinedEngineFile
from alan.engine_config import Posture, RepoIdentity
from alan.routes.infra import _load_file_backed_engines


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def client(engines_root: Path) -> Iterator[TestClient]:
    _ = engines_root
    with TestClient(server.app) as c:
        yield c


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _create(client: TestClient) -> None:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}},
        headers=_control_headers(),
    )
    assert res.status_code == 201


def test_sources_empty_when_no_engines(client: TestClient) -> None:
    res = client.get("/api/engines/sources")
    assert res.status_code == 200
    assert res.json() == {"sources": []}


def test_sources_file_less_engine_reports_not_in_version_control(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines/sources")
    assert res.status_code == 200
    [source] = res.json()["sources"]
    assert source["slug"] == "acme/widget"
    assert source["in_version_control"] is False
    assert source["config_path"] is None
    assert source["posture_ceiling"] == "autonomous"
    assert source["export_command"] == (
        "uv run python scripts/export_engine_configs.py acme/widget"
    )


def test_sources_file_backed_engine_reports_path_and_ceiling(client: TestClient) -> None:
    _create(client)
    engine_config.set_file_ceilings({RepoIdentity(owner="acme", repo="widget"): Posture.propose})
    res = client.get("/api/engines/sources")
    [source] = res.json()["sources"]
    assert source["in_version_control"] is True
    assert source["config_path"] == "config/engines.d/widget.yaml"
    assert source["posture_ceiling"] == "propose"
    assert source["export_command"] is None


def test_sources_does_not_require_control_token(client: TestClient) -> None:
    res = client.get("/api/engines/sources")
    assert res.status_code == 200


def test_sources_quarantined_config_reports_version_control_and_ceiling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    record = QuarantinedEngineFile(path=Path("config/engines.d/widget.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    _load_file_backed_engines()
    res = client.get("/api/engines/sources")
    [source] = res.json()["sources"]
    assert source["slug"] == "acme/widget"
    assert source["in_version_control"] is True
    assert source["config_path"] == "config/engines.d/widget.yaml"
    assert source["posture_ceiling"] == "observe"
    assert source["export_command"] is None
