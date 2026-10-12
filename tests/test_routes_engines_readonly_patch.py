from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

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


_IDENTITY = RepoIdentity(owner="acme", repo="widget")


def _create(client: TestClient) -> dict[str, object]:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}, "base_tools": ["Read"]},
        headers=_control_headers(),
    )
    assert res.status_code == 201
    return cast("dict[str, object]", res.json())


def _make_file_backed(ceiling: Posture = Posture.autonomous) -> None:
    engine_config.set_file_ceilings({_IDENTITY: ceiling})


def test_patch_structural_change_rejected_for_file_backed_engine(client: TestClient) -> None:
    body = _create(client)
    _make_file_backed()
    changed = {**body, "base_tools": ["Read", "Write"]}
    res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert res.status_code == 409
    live = client.get("/api/engines/acme/widget").json()
    assert live["base_tools"] == ["Read"]


def test_patch_identical_structural_body_succeeds_for_file_backed_engine(
    client: TestClient,
) -> None:
    body = _create(client)
    _make_file_backed()
    res = client.patch("/api/engines/acme/widget", json=body, headers=_control_headers())
    assert res.status_code == 200


def test_patch_hidden_only_change_persists_for_file_backed_engine(client: TestClient) -> None:
    body = _create(client)
    _make_file_backed()
    changed = {**body, "hidden": True}
    res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert res.status_code == 200
    assert res.json()["hidden"] is True
    live = client.get("/api/engines/acme/widget").json()
    assert live["hidden"] is True


def test_patch_dependency_sweep_enabled_only_change_persists_for_file_backed_engine(
    client: TestClient,
) -> None:
    body = _create(client)
    _make_file_backed()
    dep = cast("dict[str, object]", body["dependency_sweep"])
    changed = {**body, "dependency_sweep": {**dep, "enabled": True}}
    res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert res.status_code == 200
    assert res.json()["dependency_sweep"]["enabled"] is True
    live = client.get("/api/engines/acme/widget").json()
    assert live["dependency_sweep"]["enabled"] is True


def test_patch_structural_change_still_allowed_for_file_less_engine(client: TestClient) -> None:
    body = _create(client)
    changed = {**body, "base_tools": ["Read", "Write"]}
    res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert res.status_code == 200
    assert res.json()["base_tools"] == ["Read", "Write"]


def test_posture_above_ceiling_returns_422_and_leaves_config_unchanged(
    client: TestClient,
) -> None:
    _create(client)
    _make_file_backed(ceiling=Posture.propose)
    res = client.post(
        "/api/engines/acme/widget/posture",
        json={"posture": "autonomous", "confirm": True},
        headers=_control_headers(),
    )
    assert res.status_code == 422
    assert "config/engines.d/widget.yaml" in res.json()["detail"]
    live = client.get("/api/engines/acme/widget").json()
    assert live["posture"] == "observe"


def test_posture_at_ceiling_succeeds(client: TestClient) -> None:
    _create(client)
    _make_file_backed(ceiling=Posture.propose)
    res = client.post(
        "/api/engines/acme/widget/posture",
        json={"posture": "propose", "confirm": True},
        headers=_control_headers(),
    )
    assert res.status_code == 200
    assert res.json()["posture"] == "propose"


def test_quarantined_config_via_boot_pipeline_rejects_structural_patch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _create(client)
    record = QuarantinedEngineFile(path=Path("config/engines.d/widget.yaml"), error="boom")
    monkeypatch.setattr(config_files_mod, "load_tracked_configs", lambda: ({}, [record]))
    _load_file_backed_engines()
    changed = {**body, "base_tools": ["Read", "Write"]}
    res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert res.status_code == 409
    live = client.get("/api/engines/acme/widget").json()
    assert live["base_tools"] == ["Read"]


def test_patch_then_unhide_clamps_restore_posture_to_file_ceiling(client: TestClient) -> None:
    body = _create(client)
    _make_file_backed(ceiling=Posture.propose)
    changed = {**body, "hidden": True, "hidden_restore_posture": "autonomous"}
    patch_res = client.patch("/api/engines/acme/widget", json=changed, headers=_control_headers())
    assert patch_res.status_code == 200
    assert patch_res.json()["hidden"] is True
    assert patch_res.json()["hidden_restore_posture"] == "autonomous"

    unhide_res = client.post("/api/engines/acme/widget/unhide", headers=_control_headers())
    assert unhide_res.status_code == 200
    assert unhide_res.json()["posture"] == "propose"
    assert unhide_res.json()["hidden"] is False
