from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.handover import Handover, TestScenario
from alan.teams import implementation_fix as fix_mod
from alan.workspace import (
    EngineSnapshot,
    ScenarioBinding,
    Workspace,
    stamp_engine_snapshot,
    update_scenario_binding,
)


def _ws(root: Path) -> Workspace:
    return workspace_mod.create("Fix WS", "d", root=root)


def _prior(ws: Workspace, *, scenarios: list[TestScenario] | None = None) -> Handover:
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
        test_scenarios=scenarios or [],
    )


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
    )


def test_scenario_blind_is_false() -> None:
    assert fix_mod._TEAM_CONFIG.scenario_blind is False


def test_both_prompt_bodies_carry_rebind_guidance() -> None:
    for body in (fix_mod._SYSTEM_PROMPT, fix_mod._GENERIC_FIX_STATIC_TAIL):
        assert "```yaml scenario_rebind.yaml" in body
        assert "never waive a scenario" in body or "can never waive" in body


def test_mid_blocks_renders_current_scenarios_on_default_forge(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="uv run pytest tests/test_x.py", then="t",
    )
    prior = _prior(ws, scenarios=[scenario])
    text = fix_mod._build_user_text(ws, prior)
    assert "Current scenarios and their proving commands" in text
    assert "s1" in text
    assert "uv run pytest tests/test_x.py" in text


def test_mid_blocks_includes_current_scenarios_on_non_default_forge(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    ws = stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="uv run pytest tests/test_x.py", then="t",
    )
    prior = _prior(ws, scenarios=[scenario])
    text = fix_mod._build_user_text(ws, prior)
    assert "Current scenarios and their proving commands" in text
    assert "s1" in text
    assert "uv run pytest tests/test_x.py" in text


def test_mid_blocks_renders_post_overlay_command(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    ws = update_scenario_binding(
        ws, "s1", ScenarioBinding(when="uv run pytest tests/test_rebound.py", reason="moved")
    )
    scenario = TestScenario(
        id="s1", description="d", given="g",
        when="uv run pytest tests/test_stale.py", then="t",
    )
    prior = _prior(ws, scenarios=[scenario])
    text = fix_mod._build_user_text(ws, prior)
    assert "uv run pytest tests/test_rebound.py" in text
    assert "uv run pytest tests/test_stale.py" not in text


def test_mid_blocks_omits_scenarios_block_when_no_scenarios(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    prior = _prior(ws, scenarios=[])
    text = fix_mod._build_user_text(ws, prior)
    assert "Current scenarios and their proving commands" not in text


def test_finalize_fix_writes_unparsed_rebind_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(tmp_path / "ws")
    synthesis = "fixed things\n```yaml scenario_rebind.yaml\n- s1\n- s2\n```\n"
    fix_mod._finalize_fix(
        ws, None, "inv1", synthesis, ["abc123 fix things"], None, pushed=True,
    )
    audit = (logs / "impl_audit.jsonl").read_text(encoding="utf-8")
    assert "scenario_rebind_unparsed" in audit
