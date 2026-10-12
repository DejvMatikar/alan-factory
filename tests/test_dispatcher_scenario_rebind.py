from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from alan import _dispatcher_scenario_rebind as rebind_mod
from alan import process_runner, scenario_binding
from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    Posture,
    RepoIdentity,
)
from alan.engine_presets import ios_preset
from alan.handover import Handover, TestScenario
from alan.queue import Queue
from alan.router import DecisionKind, RouterDecision, select_next_team
from alan.teams import _fix_non_actionable
from alan.teams import _scenario_runner as scenario_runner_mod
from alan.teams._base import Team
from alan.teams._scenario_rebind_block import (
    RULE_PROSE_WHEN,
    RULE_REBIND_CAP,
    RULE_TEST_REF_CONFLICT,
    RULE_UNKNOWN_SCENARIO,
    RULE_WAIVED_CONFLICT,
)
from alan.workspace import (
    EngineSnapshot,
    ScenarioBinding,
    Task,
    TaskStatus,
    Workspace,
    stamp_engine_snapshot,
)


def _teams_with_verification() -> dict[str, Team]:
    return {"verification": cast(Team, object())}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _scenario(sid: str = "s1", when: str = "uv run pytest tests/test_x.py") -> TestScenario:
    return TestScenario(id=sid, description="d", given="g", when=when, then="t")


def _ws(root: Path) -> Workspace:
    return workspace_mod.create("Rebind WS", "d", root=root)


def _handover(
    ws: Workspace,
    *,
    from_team: str = "implementation_fix",
    synthesis: str,
    scenarios: list[TestScenario] | None = None,
) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team=from_team,
        from_invocation_id="inv1",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        test_scenarios=scenarios or [],
    )


def _fenced(body: str) -> str:
    return f"synthesis\n```yaml scenario_rebind.yaml\n{body}\n```\n"


def _audit_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / "scenario_override_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_non_implementation_fix_team_is_a_noop(root: Path) -> None:
    ws = _ws(root)
    handover = _handover(
        ws, from_team="verification",
        synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}


def test_non_actionable_handover_with_no_block_is_a_noop(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(ws, synthesis="implementation_fix found nothing actionable")
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert not (logs / "scenario_override_audit.jsonl").exists()


def _block_correction_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / "block_correction_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_rebind_fence_present_but_empty_records_unreachable_author(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(ws, synthesis=_fenced("s1:\n  nested: true"))
    rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    rows = _block_correction_rows(logs)
    assert rows[-1]["site"] == "scenario_rebind.yaml"
    assert rows[-1]["outcome"] == "unreachable_author"


def test_rebind_fence_present_and_valid_records_clean(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws,
        synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    rows = _block_correction_rows(logs)
    assert rows[-1]["site"] == "scenario_rebind.yaml"
    assert rows[-1]["outcome"] == "clean"


def test_unknown_scenario_id_rejected(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("ghost: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert "ghost" not in ws2.meta.scenario_bindings
    rows = _audit_rows(logs)
    assert rows[0]["action"] == "fix_rebind_rejected"
    assert rows[0]["scenario_id"] == "ghost"
    assert "scenario_id not in the workspace's scenario set" in str(rows[0]["reason"])


def test_prose_command_rejected(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: the user manually verifies in the browser"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert "s1" not in ws2.meta.scenario_bindings
    rows = _audit_rows(logs)
    assert rows[0]["action"] == "fix_rebind_rejected"
    assert "prose" in str(rows[0]["reason"])


def test_compound_command_rejected_naming_the_forbidden_operator_not_prose(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py && rm -rf /"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert "s1" not in ws2.meta.scenario_bindings
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert "forbidden shell operator" in str(rows[-1]["reason"])
    assert "prose" not in str(rows[-1]["reason"])


def test_unbalanced_quotes_rejected_naming_the_quoting_fault_not_prose(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced('s1: uv run pytest "tests/test_x.py'),
        scenarios=[_scenario("s1")],
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert "unbalanced quotes" in str(rows[-1]["reason"])
    assert "prose" not in str(rows[-1]["reason"])


def test_path_traversal_rejected_naming_the_offending_argument_not_prose(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest ../../etc/passwd"),
        scenarios=[_scenario("s1")],
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert "path traversal" in str(rows[-1]["reason"])
    assert "prose" not in str(rows[-1]["reason"])


def test_non_allowlisted_prefix_rejected_and_still_reported_as_the_prose_reason(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: curl http://example.com"),
        scenarios=[_scenario("s1")],
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert rows[-1]["reason"] == scenario_binding.PROSE_WHEN_DETAIL


def test_apply_rebinds_never_executes_a_subprocess(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    run_subprocess_mock = MagicMock(side_effect=AssertionError("must not run a subprocess"))
    run_scenarios_mock = MagicMock(side_effect=AssertionError("must not call run_scenarios"))
    monkeypatch.setattr(process_runner, "run_subprocess", run_subprocess_mock)
    monkeypatch.setattr(scenario_runner_mod, "run_scenarios", run_scenarios_mock)
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_x.py"}
    run_subprocess_mock.assert_not_called()
    run_scenarios_mock.assert_not_called()


def test_accepted_rebind_persists_to_disk(root: Path) -> None:
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.scenario_bindings["s1"].when == "uv run pytest tests/test_x.py"


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
    )


def _non_default_ws(root: Path) -> Workspace:
    ws = _ws(root)
    return stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )


def test_non_default_forge_accepted_when_command_admissible_under_its_own_engine(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _non_default_ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_x.py"}
    assert ws2.meta.scenario_bindings["s1"].when == "uv run pytest tests/test_x.py"
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind"


class TestNonDefaultForgeGuardsStillApply:
    def test_acceptance_carries_no_rule(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        handover = _handover(
            ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
            scenarios=[_scenario("s1")],
        )
        rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        rows = _audit_rows(logs)
        assert rows[-1]["action"] == "fix_rebind"
        assert rows[-1].get("rule") is None

    def test_unknown_scenario_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        handover = _handover(
            ws, synthesis=_fenced("ghost: uv run pytest tests/test_x.py"),
            scenarios=[_scenario("s1")],
        )
        _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert outcome.accepted == {}
        rows = _audit_rows(logs)
        assert rows[-1]["action"] == "fix_rebind_rejected"
        assert rows[-1]["rule"] == RULE_UNKNOWN_SCENARIO

    def test_prose_command_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        handover = _handover(
            ws, synthesis=_fenced("s1: the user manually verifies in the browser"),
            scenarios=[_scenario("s1")],
        )
        _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert outcome.accepted == {}
        rows = _audit_rows(logs)
        assert rows[-1]["rule"] == RULE_PROSE_WHEN

    def test_non_allowlisted_prefix_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        handover = _handover(
            ws, synthesis=_fenced("s1: curl http://example.com"),
            scenarios=[_scenario("s1")],
        )
        _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert outcome.accepted == {}
        rows = _audit_rows(logs)
        assert rows[-1]["rule"] == RULE_PROSE_WHEN
        assert rows[-1]["reason"] == scenario_binding.PROSE_WHEN_DETAIL

    def test_over_cap_entries_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        n = 11
        scenarios = [_scenario(f"s{i}") for i in range(1, n + 1)]
        ws = _non_default_ws(root)
        body = "\n".join(f"s{i}: uv run pytest tests/test_{i}.py" for i in range(1, n + 1))
        handover = _handover(ws, synthesis=_fenced(body), scenarios=scenarios)
        ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert len(outcome.accepted) == 10
        assert "s11" not in ws2.meta.scenario_bindings
        rows = _audit_rows(logs)
        rejected = [r for r in rows if r["action"] == "fix_rebind_rejected"]
        assert rejected[-1]["rule"] == RULE_REBIND_CAP

    def test_waived_conflict_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        ws = workspace_mod.update_scenario_binding(
            ws, "s1", ScenarioBinding(waived=True, reason="known broken")
        )
        handover = _handover(
            ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
            scenarios=[_scenario("s1")],
        )
        _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert outcome.accepted == {}
        rows = _audit_rows(logs)
        assert rows[-1]["rule"] == RULE_WAIVED_CONFLICT

    def test_test_ref_conflict_still_rejected(
        self, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = _non_default_ws(root)
        ws = workspace_mod.update_scenario_binding(
            ws, "s1",
            ScenarioBinding(test_ref="tests/test_old.py::test_x", reason="operator repoint"),
        )
        handover = _handover(
            ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
            scenarios=[_scenario("s1")],
        )
        _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
        assert outcome.accepted == {}
        rows = _audit_rows(logs)
        assert rows[-1]["rule"] == RULE_TEST_REF_CONFLICT


def test_ios_engine_rebind_to_python_interpreter_is_accepted(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    cfg = ios_preset()
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    handover = _handover(
        ws, synthesis=_fenced("s1: python3 scripts/check_stdlib.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "python3 scripts/check_stdlib.py"}
    assert ws2.meta.scenario_bindings["s1"].when == "python3 scripts/check_stdlib.py"


def test_over_cap_entries_rejected_without_persisting(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    n = 11
    scenarios = [_scenario(f"s{i}") for i in range(1, n + 1)]
    ws = _ws(root)
    body = "\n".join(f"s{i}: uv run pytest tests/test_{i}.py" for i in range(1, n + 1))
    handover = _handover(ws, synthesis=_fenced(body), scenarios=scenarios)
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert len(outcome.accepted) == 10
    assert "s11" not in outcome.accepted
    assert "s11" not in ws2.meta.scenario_bindings
    assert len(ws2.meta.scenario_bindings) == 10
    rows = _audit_rows(logs)
    rejected = [r for r in rows if r["action"] == "fix_rebind_rejected"]
    assert rejected
    assert rejected[-1]["scenario_id"] == "s11"
    assert "more than 10" in str(rejected[-1]["reason"])


def test_conflict_rejection_waived_existing_binding(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    ws = workspace_mod.update_scenario_binding(
        ws, "s1", ScenarioBinding(waived=True, reason="known broken")
    )
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert ws2.meta.scenario_bindings["s1"].waived is True
    assert ws2.meta.scenario_bindings["s1"].when is None
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert "waived" in str(rows[-1]["reason"])


def test_conflict_rejection_test_ref_existing_binding(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    ws = workspace_mod.update_scenario_binding(
        ws, "s1",
        ScenarioBinding(test_ref="tests/test_old.py::test_x", reason="operator repoint"),
    )
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    assert ws2.meta.scenario_bindings["s1"].test_ref == "tests/test_old.py::test_x"
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert "test_ref" in str(rows[-1]["reason"])


def test_no_conflict_when_existing_binding_has_neither_waived_nor_test_ref(
    root: Path,
) -> None:
    ws = _ws(root)
    ws = workspace_mod.update_scenario_binding(
        ws, "s1",
        ScenarioBinding(when="uv run pytest tests/test_stale.py", reason="operator repoint"),
    )
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_new.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_new.py"}
    binding = ws2.meta.scenario_bindings["s1"]
    assert binding.when == "uv run pytest tests/test_new.py"
    assert binding.written_by == "fix_rebind"


def test_accepted_rebind_provenance_is_fix_rebind_on_fresh_binding(root: Path) -> None:
    ws = _ws(root)
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_new.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_new.py"}
    binding = ws2.meta.scenario_bindings["s1"]
    assert binding.written_by == "fix_rebind"
    assert binding.reason == "fix_rebind"
    assert binding.waived is False
    assert binding.test_ref is None


def test_accepted_rebind_provenance_survives_layering_over_operator_binding(
    root: Path,
) -> None:
    ws = _ws(root)
    ws = workspace_mod.update_scenario_binding(
        ws, "s1",
        ScenarioBinding(when="uv run pytest tests/test_old.py", reason="operator repoint"),
    )
    assert ws.meta.scenario_bindings["s1"].written_by == "operator"
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_new.py"),
        scenarios=[_scenario("s1")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_new.py"}
    assert ws2.meta.scenario_bindings["s1"].written_by == "fix_rebind"


def test_apply_rebinds_partial_leaves_unnamed_sibling_byte_identical_including_waived(
    root: Path,
) -> None:
    ws = _ws(root)
    ws = workspace_mod.update_scenario_binding(
        ws, "s2", ScenarioBinding(waived=True, reason="known broken")
    )
    sibling_before = ws.meta.scenario_bindings["s2"]
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_new.py"),
        scenarios=[_scenario("s1"), _scenario("s2")],
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_new.py"}
    assert ws2.meta.scenario_bindings["s2"] == sibling_before


def test_decomposed_workspace_resolves_scenarios_via_dag_task_id(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    task = Task(id="sub-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"tasks": [task]})})
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"), scenarios=[]
    )
    ws2, outcome = rebind_mod.apply_rebinds(ws, handover, "sub-1")
    assert outcome.accepted == {"s1": "uv run pytest tests/test_x.py"}
    assert ws2.meta.scenario_bindings["s1"].when == "uv run pytest tests/test_x.py"


def test_workspace_task_id_does_not_see_a_sibling_subtasks_scenarios(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    task = Task(id="sub-1", status=TaskStatus.RUNNING, scenarios=[_scenario("s1")])
    ws = ws.model_copy(update={"meta": ws.meta.model_copy(update={"tasks": [task]})})
    handover = _handover(
        ws, synthesis=_fenced("s1: uv run pytest tests/test_x.py"), scenarios=[]
    )
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert rows[-1]["scenario_id"] == "s1"


def _non_actionable_handover_with_rebind(
    ws: Workspace, *, reason: str = "A blocking human question about scope remains open"
) -> Handover:
    prior = Handover(
        handover_id="h_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_v",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="## Verdict\nVERDICT: REQUEST_CHANGES\n",
        test_scenarios=[_scenario("s1")],
    )
    synthesis = (
        "## Summary\nNo code changes needed.\n\n"
        "```yaml non_actionable_feedback.yaml\n"
        f"reason: {reason}\n"
        "```\n\n"
        "```yaml scenario_rebind.yaml\n"
        "s1: uv run pytest tests/test_renamed.py\n"
        "```"
    )
    handover = _fix_non_actionable.check_non_actionable(
        ws, prior, "inv_fix", synthesis, [], reason,
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    return handover


def test_non_actionable_handover_rebind_is_validated_and_accepted(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _non_actionable_handover_with_rebind(ws)
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {"s1": "uv run pytest tests/test_renamed.py"}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind"


def test_non_actionable_handover_rebind_rejected_writes_audit_row_not_silently(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    prior = Handover(
        handover_id="h_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_v",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="## Verdict\nVERDICT: REQUEST_CHANGES\n",
        test_scenarios=[_scenario("s1")],
    )
    reason = "A blocking human question about scope remains open"
    synthesis = (
        "## Summary\nNo code changes needed.\n\n"
        "```yaml non_actionable_feedback.yaml\n"
        f"reason: {reason}\n"
        "```\n\n"
        "```yaml scenario_rebind.yaml\n"
        "s1: curl http://example.com\n"
        "```"
    )
    handover = _fix_non_actionable.check_non_actionable(
        ws, prior, "inv_fix", synthesis, [], reason,
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted == {}
    rows = _audit_rows(logs)
    assert rows[-1]["action"] == "fix_rebind_rejected"
    assert rows[-1]["scenario_id"] == "s1"


def test_non_actionable_handover_with_accepted_rebind_still_routes_await_human(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    handover = _non_actionable_handover_with_rebind(ws)
    _ws2, outcome = rebind_mod.apply_rebinds(ws, handover, ws.task_id)
    assert outcome.accepted
    decision = select_next_team(handover, _teams_with_verification())
    assert decision.kind == "await_human"
    routed = rebind_mod.route_after_rebind(
        decision, handover, outcome, _teams_with_verification()
    )
    assert routed is decision
    assert routed.kind == "await_human"


def _decision(kind: DecisionKind = "escalate", team: str | None = None) -> RouterDecision:
    return RouterDecision(kind=kind, team=team, reason="r")


def test_route_after_rebind_converts_escalation_to_verification_when_accepted(
    root: Path,
) -> None:
    handover = _handover(_ws(root), synthesis="ignored")
    outcome = rebind_mod.RebindOutcome(accepted={"s1": "uv run pytest tests/test_x.py"})
    decision = rebind_mod.route_after_rebind(
        _decision(), handover, outcome, _teams_with_verification()
    )
    assert decision.kind == "enqueue"
    assert decision.team == "verification"


def test_route_after_rebind_noop_when_no_rebinds_accepted(root: Path) -> None:
    handover = _handover(_ws(root), synthesis="ignored")
    original = _decision()
    decision = rebind_mod.route_after_rebind(
        original, handover, rebind_mod.RebindOutcome(), _teams_with_verification()
    )
    assert decision is original


def test_route_after_rebind_noop_when_decision_not_escalate(root: Path) -> None:
    handover = _handover(_ws(root), synthesis="ignored")
    outcome = rebind_mod.RebindOutcome(accepted={"s1": "uv run pytest tests/test_x.py"})
    original = _decision(kind="enqueue", team="verification")
    decision = rebind_mod.route_after_rebind(
        original, handover, outcome, _teams_with_verification()
    )
    assert decision is original


def test_route_after_rebind_noop_when_from_team_not_implementation_fix(root: Path) -> None:
    handover = _handover(_ws(root), synthesis="ignored", from_team="verification")
    outcome = rebind_mod.RebindOutcome(accepted={"s1": "uv run pytest tests/test_x.py"})
    original = _decision()
    decision = rebind_mod.route_after_rebind(
        original, handover, outcome, _teams_with_verification()
    )
    assert decision is original


def test_route_after_rebind_noop_when_verification_team_not_registered(root: Path) -> None:
    handover = _handover(_ws(root), synthesis="ignored")
    outcome = rebind_mod.RebindOutcome(accepted={"s1": "uv run pytest tests/test_x.py"})
    original = _decision()
    decision = rebind_mod.route_after_rebind(original, handover, outcome, {})
    assert decision is original


def test_apply_rebinds_and_route_after_rebind_run_inside_lock_before_forward_progress(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alan.dispatcher as dispatcher_mod

    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    ws = _ws(root)
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        teams: dict[str, Team] = {"verification": cast(Team, object())}
        d = Dispatcher(q, teams, workspaces_root=root)
        q.enqueue(ws.task_id, "implementation_fix")
        job = q.claim()
        assert job is not None
        handover = Handover(
            handover_id="h1",
            handover_type="forward",
            task_id=ws.task_id,
            from_team="implementation_fix",
            from_invocation_id="inv1",
            to_team_suggestions=["verification"],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis=_fenced("s1: uv run pytest tests/test_x.py"),
            test_scenarios=[_scenario("s1")],
        )
        calls: list[str] = []
        lock = d._get_workspace_lock(ws.workspace_id)  # noqa: SLF001
        real_apply_rebinds = rebind_mod.apply_rebinds

        def tracking_apply_rebinds(
            ws_arg: Workspace, handover_arg: Handover, task_id_arg: str
        ) -> tuple[Workspace, rebind_mod.RebindOutcome]:
            assert lock._local.held is True  # noqa: SLF001
            calls.append("apply_rebinds")
            return real_apply_rebinds(ws_arg, handover_arg, task_id_arg)

        def tracking_forward_progress(*_a: object, **_kw: object) -> bool:
            calls.append("has_forward_progress")
            return False

        monkeypatch.setattr(rebind_mod, "apply_rebinds", tracking_apply_rebinds)
        monkeypatch.setattr(dispatcher_mod, "has_forward_progress", tracking_forward_progress)
        monkeypatch.setattr(
            Dispatcher, "_apply_router_decision", lambda *_a, **_kw: None
        )

        d._route_after_handover(job, ws, handover)  # noqa: SLF001

        assert calls == ["apply_rebinds", "has_forward_progress"]
        reloaded = workspace_mod.read(ws.task_id, root=root)
        assert reloaded.meta.scenario_bindings["s1"].written_by == "fix_rebind"
    finally:
        q.close()
