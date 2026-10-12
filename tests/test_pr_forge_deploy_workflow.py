from __future__ import annotations

from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.engine_config import (
    CiConfig,
    EngineConfig,
    ForgeTarget,
    Posture,
    RepoIdentity,
)
from alan.forge import JsonObject
from alan.teams import _pr_forge
from alan.teams import deploy_verify as deploy_verify_mod
from alan.workspace import EngineSnapshot, Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import rows_read

_OWNER_DEPLOY_WORKFLOW = "ci-deploy.yml"


def _engine(
    *, ci: CiConfig | None, owner: str = "acme", repo: str = "widget"
) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner=owner, repo=repo), ci=ci)


def _ci(deploy_workflow: str | None) -> CiConfig:
    return CiConfig(main_ci_workflow="build.yml", deploy_workflow=deploy_workflow)


def _workspace(
    tmp_path: Path,
    *,
    engine_key: str | None = None,
    snapshot_config: EngineConfig | None = None,
    snapshot: bool = False,
) -> Workspace:
    ws = create("t", "d", root=tmp_path / "ws", engine_key=engine_key)
    if snapshot or snapshot_config is not None:
        pinned = EngineSnapshot(
            posture=Posture.propose, transition_graph={}, config=snapshot_config
        )
        ws = workspace_mod.stamp_engine_snapshot(ws, pinned)
    return ws


def _registry(monkeypatch: pytest.MonkeyPatch, *configs: EngineConfig) -> None:
    by_slug = {cfg.repo.slug: cfg for cfg in configs}
    monkeypatch.setattr("alan.engine_config.lookup_by_slug", by_slug.get)


def _run_row() -> JsonObject:
    return {
        "databaseId": 7,
        "createdAt": "2024-01-15T10:05:00Z",
        "status": "completed",
        "conclusion": "failure",
    }


def test_snapshot_ci_deploy_workflow_wins_over_registry_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci("registry.yml")))
    ws = _workspace(
        tmp_path,
        engine_key="acme/widget",
        snapshot_config=_engine(ci=_ci("snapshot.yml")),
    )
    assert _pr_forge.deploy_workflow_for_ws(ws) == "snapshot.yml"


def test_snapshot_ci_without_deploy_workflow_resolves_to_none_without_falling_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci("registry.yml")))
    ws = _workspace(
        tmp_path, engine_key="acme/widget", snapshot_config=_engine(ci=_ci(None))
    )
    assert _pr_forge.deploy_workflow_for_ws(ws) is None


def test_snapshot_with_null_ci_falls_through_to_the_registry_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci("registry.yml")))
    ws = _workspace(
        tmp_path, engine_key="acme/widget", snapshot_config=_engine(ci=None)
    )
    assert _pr_forge.deploy_workflow_for_ws(ws) == "registry.yml"


def test_legacy_snapshot_without_config_falls_through_to_the_registry_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci("registry.yml")))
    ws = _workspace(tmp_path, engine_key="acme/widget", snapshot=True)
    assert _pr_forge.deploy_workflow_for_ws(ws) == "registry.yml"


def test_no_snapshot_uses_the_registry_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci("registry.yml")))
    ws = _workspace(tmp_path, engine_key="acme/widget")
    assert _pr_forge.deploy_workflow_for_ws(ws) == "registry.yml"


def test_registry_record_without_deploy_workflow_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=_ci(None)))
    ws = _workspace(tmp_path, engine_key="acme/widget")
    assert _pr_forge.deploy_workflow_for_ws(ws) is None


def test_registry_record_with_null_ci_stays_on_the_owner_deploy_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, _engine(ci=None, owner="acme", repo="example-repo"))
    ws = _workspace(tmp_path, engine_key="acme/example-repo")
    assert _pr_forge.deploy_workflow_for_ws(ws) == _OWNER_DEPLOY_WORKFLOW


def test_unknown_engine_key_falls_back_to_the_default_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch)
    ws = _workspace(tmp_path, engine_key="ghost/repo")
    assert _pr_forge.deploy_workflow_for_ws(ws) == _OWNER_DEPLOY_WORKFLOW


def test_workspace_without_snapshot_or_engine_key_uses_the_default_engine(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    assert _pr_forge.deploy_workflow_for_ws(ws) == _OWNER_DEPLOY_WORKFLOW


def test_legacy_snapshot_on_the_default_engine_stays_on_the_owner_deploy_workflow(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path, snapshot=True)
    assert _pr_forge.deploy_workflow_for_ws(ws) == _OWNER_DEPLOY_WORKFLOW


def test_default_engine_without_ci_resolves_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "alan.engine_config.default_engine_config", lambda: _engine(ci=None)
    )
    ws = _workspace(tmp_path)
    assert _pr_forge.deploy_workflow_for_ws(ws) is None


def test_diagnostic_detail_on_the_owner_workspace_keeps_the_ci_deploy_wording(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    ws = _workspace(tmp_path)
    assert deploy_verify_mod._diagnostic_detail(ws) == "no ci-deploy runs observed"  # noqa: SLF001
    (call,) = _active_fake_forge.calls
    assert call[1] == (_OWNER_DEPLOY_WORKFLOW,)


def test_diagnostic_detail_on_the_owner_workspace_names_the_latest_ci_deploy_run(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read((_run_row(),)))
    ws = _workspace(tmp_path)
    assert deploy_verify_mod._diagnostic_detail(ws) == (  # noqa: SLF001
        "latest ci-deploy run status=completed conclusion=failure"
    )


def test_diagnostic_detail_lists_the_engines_own_deploy_workflow(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read((_run_row(),)))
    ws = _workspace(tmp_path, snapshot_config=_engine(ci=_ci("ship-it.yml")))
    detail = deploy_verify_mod._diagnostic_detail(ws)  # noqa: SLF001
    assert detail == "latest ship-it run status=completed conclusion=failure"
    (call,) = _active_fake_forge.calls
    assert call[1] == ("ship-it.yml",)


def test_diagnostic_detail_scopes_the_lookup_to_the_workspaces_forge_target(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    ws = _workspace(tmp_path, snapshot_config=_engine(ci=_ci("ship-it.yml")))
    deploy_verify_mod._diagnostic_detail(ws)  # noqa: SLF001
    (call,) = _active_fake_forge.calls
    forge_target = call[2]["forge_target"]
    assert isinstance(forge_target, ForgeTarget)
    assert forge_target == _pr_forge.forge_target_for_ws(ws)


def test_diagnostic_detail_without_a_deploy_workflow_never_contacts_the_forge(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    ws = _workspace(tmp_path, snapshot_config=_engine(ci=_ci(None)))
    detail = deploy_verify_mod._diagnostic_detail(ws)  # noqa: SLF001
    assert detail == "no deploy workflow configured"
    assert _active_fake_forge.calls == []


def test_diagnostic_detail_fails_soft_when_the_forge_raises(
    tmp_path: Path, _active_fake_forge: FakeForge
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("gh unreachable")

    _active_fake_forge.stub("workflow_runs", _boom)
    ws = _workspace(tmp_path, snapshot_config=_engine(ci=_ci("ship-it.yml")))
    assert (
        deploy_verify_mod._diagnostic_detail(ws)  # noqa: SLF001
        == "deploy-run diagnostics unavailable"
    )

