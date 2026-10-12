from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import TEAMS
from alan.teams import _base as base
from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION, PLANNING_UX_LENS
from alan.teams._static_pipeline import StaticPipelineParseError
from alan.teams.architecture import (
    _DEVIL_ADVOCATE_PROMPT,
    _JUDGE_PROMPT,
    ROLES,
    TEAM,
    ArchitectureTeam,
)
from alan.workspace import create
from tests._helpers import seed_and_run

_FIXTURE_ARCH_DECISIONS_BLOCK = """

```yaml decisions.yaml
decisions:
  - id: a1
    statement: "prefer sqlite for state storage"
    rationale: "single-file zero-server operational model"
    confidence: 0.9
    reversibility: medium
  - id: a2
    statement: "keep the pipeline single-process"
    rationale: "avoids distributed-systems complexity until proven necessary"
    confidence: 0.85
    reversibility: high
```
"""


def _patch_arch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output_factory: Callable[[int], str] | None = None,
) -> None:
    counter = {"n": 0}
    judge_call = len(ROLES)

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model
        counter["n"] += 1
        text = (
            output_factory(counter["n"]) if output_factory else f"out-{counter['n']}"
        )
        if counter["n"] == judge_call and output_factory is None:
            text = text + _FIXTURE_ARCH_DECISIONS_BLOCK
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def test_team_registered_under_architecture_name() -> None:
    assert TEAMS["architecture"] is TEAM
    assert TEAM.name == "architecture"


def test_role_order_includes_devil_advocate_before_judge() -> None:
    names = [r.name for r in ROLES]
    assert names == [
        "structure_mapper",
        "complexity_analyst",
        "debt_auditor",
        "change_planner",
        "devil_advocate",
        "judge",
    ]


def test_change_planner_prompt_has_analysis_only_short_circuit() -> None:
    change_planner = next(r for r in ROLES if r.name == "change_planner")
    assert "NO_CHANGE_LIST" in change_planner.system_prompt
    assert "analysis-only" in change_planner.system_prompt


def test_judge_prompt_mentions_sentinel() -> None:
    judge = next(r for r in ROLES if r.name == "judge")
    assert "NO_CHANGE_LIST" in judge.system_prompt


def test_to_team_suggestions_is_empty_for_exploratory_team() -> None:
    assert TEAM.to_team_suggestions == ()


def test_all_role_prompts_are_distinct() -> None:
    prompts = [r.system_prompt for r in ROLES]
    assert len(set(prompts)) == len(prompts)


def test_devil_advocate_prompt_has_adr_sections() -> None:
    for section in (
        "Alternative designs",
        "Assumptions",
        "Inversion test",
        "Prior-art collisions",
    ):
        assert section in _DEVIL_ADVOCATE_PROMPT


def test_judge_prompt_requires_decisions_yaml_block() -> None:
    assert "decisions.yaml" in _JUDGE_PROMPT
    assert "confidence" in _JUDGE_PROMPT
    assert "reversibility" in _JUDGE_PROMPT


def test_judge_prompt_contains_planning_ux_lens() -> None:
    assert PLANNING_UX_LENS in _JUDGE_PROMPT


def test_devil_advocate_prompt_has_no_planning_ux_lens() -> None:
    assert PLANNING_UX_LENS not in _DEVIL_ADVOCATE_PROMPT


def test_judge_prompt_decisions_yaml_is_last_fenced_block() -> None:
    assert _JUDGE_PROMPT.rfind("```yaml decisions.yaml") > _JUDGE_PROMPT.rfind(
        PLANNING_UX_LENS
    )


def test_judge_prompt_does_not_duplicate_lens() -> None:
    assert _JUDGE_PROMPT.count(PLANNING_UX_LENS) == 1


def test_judge_prompt_contains_planning_screen_capture_instruction() -> None:
    assert PLANNING_SCREEN_CAPTURE_INSTRUCTION in _JUDGE_PROMPT


def test_judge_prompt_carries_capture_degrade_path() -> None:
    assert "capture unavailable — reviewed from markup" in _JUDGE_PROMPT


def test_judge_prompt_capture_instruction_before_decisions_yaml_block() -> None:
    assert _JUDGE_PROMPT.rfind("```yaml decisions.yaml") > _JUDGE_PROMPT.rfind(
        PLANNING_SCREEN_CAPTURE_INSTRUCTION
    )


def test_devil_advocate_prompt_has_no_planning_screen_capture_instruction() -> None:
    assert PLANNING_SCREEN_CAPTURE_INSTRUCTION not in _DEVIL_ADVOCATE_PROMPT


def test_architecture_team_run_parses_decisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_arch_streaming(monkeypatch)
    ws = create("arch task", "desc", root=tmp_path / "workspaces")
    handover = seed_and_run(ArchitectureTeam(), ws, None, lambda _e: None)
    assert len(handover.decisions) == 2
    assert handover.decisions[0].id == "a1"
    assert handover.decisions[0].confidence == pytest.approx(0.9)
    assert handover.decisions[0].reversibility == "medium"
    assert handover.decisions[1].id == "a2"
    assert handover.decisions[1].reversibility == "high"
    assert handover.to_team_suggestions == []


def test_architecture_team_raises_when_decisions_block_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model
        text = "no block here"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("arch no block", "d", root=tmp_path / "workspaces")
    with pytest.raises(StaticPipelineParseError, match="decisions.yaml block"):
        seed_and_run(ArchitectureTeam(), ws, None, lambda _e: None)


def test_architecture_team_invalidates_judge_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan import workflow as workflow_mod
    from alan.workflow import load_workflow, workflow_path

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model
        text = "no block here"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("arch inv", "d", root=tmp_path / "workspaces")
    with pytest.raises(StaticPipelineParseError):
        seed_and_run(ArchitectureTeam(), ws, None, lambda _e: None)

    assert not workflow_mod.result_path(ws, "inv_001", "judge").exists()
    doc = load_workflow(workflow_path(ws, "inv_001"))
    judge = next(t for t in doc.tasks if t.id == "judge")
    assert judge.status == "failed"
