from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.handover import Handover
from alan.teams import _scenario_rebind_block
from alan.teams import implementation_fix as impl_fix_mod
from alan.workspace import EngineSnapshot, Workspace, create, stamp_engine_snapshot


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("rebind-visibility", "d", root=tmp_path / "ws")


def _prior(ws: Workspace) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
        pr_number=1,
    )


def _write_rejection_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, workspace_id: str, **extra: object
) -> Path:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    row: dict[str, object] = {
        "kind": "binding",
        "action": "fix_rebind_rejected",
        "workspace_id": workspace_id,
        "scenario_id": "s1",
        "when": "curl http://example.com",
        "reason": "when field is prose (command prefix not in the scenario runner allowlist)",
        "rule": _scenario_rebind_block.RULE_PROSE_WHEN,
    }
    row.update(extra)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return path


def test_rejection_rows_render_into_the_current_scenarios_block(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_rejection_row(tmp_path, monkeypatch, workspace_id=workspace.task_id)
    block = impl_fix_mod._current_scenarios_block(  # noqa: SLF001
        workspace, _prior(workspace), None
    )
    assert "Recent scenario rebind rejections" in block
    assert "s1" in block
    assert "curl http://example.com" in block


def test_no_block_when_no_rejections_exist(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    block = impl_fix_mod._current_scenarios_block(  # noqa: SLF001
        workspace, _prior(workspace), None
    )
    assert "Recent scenario rebind rejections" not in block


def test_rows_from_another_workspace_are_filtered_out(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_rejection_row(tmp_path, monkeypatch, workspace_id="some-other-workspace")
    block = impl_fix_mod._current_scenarios_block(  # noqa: SLF001
        workspace, _prior(workspace), None
    )
    assert "Recent scenario rebind rejections" not in block


def test_malformed_json_line_is_skipped_without_crashing(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "scenario_override_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    _write_rejection_row(tmp_path, monkeypatch, workspace_id=workspace.task_id)
    block = impl_fix_mod._current_scenarios_block(  # noqa: SLF001
        workspace, _prior(workspace), None
    )
    assert "Recent scenario rebind rejections" in block


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
    )


def test_rejection_rows_render_on_non_default_forge_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("rebind-visibility-non-default", "d", root=tmp_path / "ws")
    ws = stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )
    _write_rejection_row(tmp_path, monkeypatch, workspace_id=ws.task_id)
    block = impl_fix_mod._current_scenarios_block(  # noqa: SLF001
        ws, _prior(ws), None
    )
    assert "Recent scenario rebind rejections" in block
    assert "s1" in block
