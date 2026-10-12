from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from alan.handover import Handover, save_handover
from alan.loop_guard import REVISIT_CAP, check_loop_guard, detect_spiral
from alan.workspace import Workspace, create, increment_revisit_count


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _corpus_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "transitions"


def _materialize_corpus(ws: Workspace, scenario_dir: Path) -> Handover:
    final: Handover | None = None
    for path in sorted(scenario_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        handover = Handover.model_validate(raw)
        handover = handover.model_copy(
            update={"task_id": ws.task_id, "workspace_id": ws.task_id}
        )
        save_handover(ws, handover)
        final = handover
    assert final is not None, f"empty scenario corpus: {scenario_dir}"
    return final


@pytest.mark.parametrize(
    ("scenario", "next_team", "revisit_runs", "target", "expected"),
    [
        pytest.param(
            "converging_fix_verify_fix",
            "implementation_fix",
            1,
            "check_loop_guard",
            None,
            id="converging-cycle-does-not-escalate",
        ),
        pytest.param(
            "clean_external_gate_repeat",
            "implementation_fix",
            1,
            "detect_spiral",
            "external_gate_blocked",
            id="clean-gate-block-classified",
        ),
        pytest.param(
            "non_actionable_dead_end",
            "verification",
            1,
            "detect_spiral",
            "non_actionable_finding",
            id="non-actionable-dead-end-classified",
        ),
        pytest.param(
            "non_actionable_gate_reroute",
            "pr_merge",
            1,
            "check_loop_guard",
            None,
            id="non-actionable-gate-reroutes-to-pr-merge-without-tripping-the-guard",
        ),
        pytest.param(
            "cap_breach_4th_round",
            "implementation_fix",
            REVISIT_CAP + 1,
            "check_loop_guard",
            "escalate",
            id="cap-breach-escalates",
        ),
    ],
)
def test_replay_scenario_classification(
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    next_team: str,
    revisit_runs: int,
    target: str,
    expected: str | None,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create(scenario, "replay corpus", root=ws_root)
    final = _materialize_corpus(ws, _corpus_dir() / scenario)
    for _ in range(revisit_runs):
        ws = increment_revisit_count(ws, next_team, final.handover_chain_id)

    if target == "check_loop_guard":
        decision = check_loop_guard(ws, final, next_team)
        if expected is None:
            assert decision is None
            return
        assert decision is not None
        assert decision.kind == "escalate"
        assert decision.reason is not None
        assert "revisit cap exceeded" in decision.reason
        return

    finding = detect_spiral(ws, final, next_team)
    assert finding is not None
    assert finding.kind == expected
