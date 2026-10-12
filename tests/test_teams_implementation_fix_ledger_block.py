from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.handover import ArtifactRef, Handover, save_handover
from alan.teams import implementation_fix as fix_mod
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot


def _ws(root: Path) -> Workspace:
    return workspace_mod.create("Fix WS", "d", root=root)


def _prior(ws: Workspace) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verification review",
        pr_number=None,
    )


def _save_fleet_commit(ws: Workspace) -> None:
    save_handover(
        ws,
        Handover(
            handover_id="fleet-1",
            handover_type="forward",
            task_id=ws.task_id,
            from_team="implementation",
            from_invocation_id="fleet-inv1",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="implementation run",
            artifacts_produced=[
                ArtifactRef(path="abc1234", description="abc1234 feat: fleet commit"),
            ],
        ),
    )


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
    )


def test_mid_blocks_appends_the_ledger_when_the_workspace_recorded_commits(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path / "ws")
    _save_fleet_commit(ws)
    text = fix_mod._build_user_text(ws, _prior(ws))
    assert "## Fleet commit ledger" in text
    assert "abc1234 feat: fleet commit" in text


def test_mid_blocks_omits_the_ledger_when_it_renders_empty(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    text = fix_mod._build_user_text(ws, _prior(ws))
    assert "authoritative about what the fleet recorded" not in text
    assert "### implementation" not in text


def test_mid_blocks_includes_the_ledger_for_a_non_default_repo(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    ws = stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )
    _save_fleet_commit(ws)
    text = fix_mod._build_user_text(ws, _prior(ws))
    assert "Current scenarios and their proving commands" not in text
    assert "## Fleet commit ledger" in text


def test_mid_blocks_includes_the_ledger_for_a_decomposed_subtask_id(
    tmp_path: Path,
) -> None:
    ws = _ws(tmp_path / "ws")
    _save_fleet_commit(ws)
    text = fix_mod._build_user_text(ws, _prior(ws), task_id="subtask-1")
    assert "## Fleet commit ledger" in text
