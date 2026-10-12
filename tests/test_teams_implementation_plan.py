from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, ClaudeCliError, StreamEvent
from alan.handover import TestScenario
from alan.teams import _base as base
from alan.teams import implementation_plan as ipt
from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION, PLANNING_UX_LENS, TeamEvent
from alan.teams.implementation_plan import (
    ROLES,
    ImplementationPlanTeam,
    _build_user_text,
    _slugify,
    _write_plan,
    run_team,
)
from alan.workflow import load_workflow, workflow_path
from alan.workspace import create
from tests._helpers import seed_and_run

_FIXTURE_DECISIONS_BLOCK = """

```yaml decisions.yaml
decisions:
  - id: d1
    statement: "use option A"
    rationale: "cheapest path forward"
    confidence: 0.85
    reversibility: medium
test_scenarios:
  - id: s1
    description: a thing happens
    given: precondition
    when: action
    then: outcome
```
"""


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: list[tuple[str, str, str]] | None = None,
    output_factory: Callable[[int], str] | None = None,
) -> None:
    counter = {"n": 0}
    synth_call = len(ipt.ROLES)

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if captured is not None:
            captured.append((system_prompt, user_text, model))
        text = (
            output_factory(counter["n"]) if output_factory else f"out-{counter['n']}"
        )
        if counter["n"] == synth_call and output_factory is None:
            text = text + _FIXTURE_DECISIONS_BLOCK
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


@pytest.fixture
def patch_plans_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ALAN_PLANS_ROOT", str(tmp_path))
    return tmp_path


def test_six_roles_in_expected_order():
    assert [r.name for r in ROLES] == [
        "architect",
        "backend_analyst",
        "frontend_analyst",
        "qa_planner",
        "devil_advocate",
        "synthesizer",
    ]


def test_stages_dag_frontend_and_qa_concurrent():
    assert ImplementationPlanTeam().stages == (
        ("architect",),
        ("backend_analyst",),
        ("frontend_analyst", "qa_planner"),
        ("devil_advocate",),
        ("synthesizer",),
    )


def test_role_system_prompts_are_distinct_and_nonempty():
    prompts = [r.system_prompt for r in ROLES]
    assert all(p.strip() for p in prompts)
    assert len(set(prompts)) == len(prompts)


def test_frontend_analyst_prompt_contains_planning_ux_lens():
    from alan.teams.implementation_plan import _FRONTEND_ANALYST_PROMPT

    assert PLANNING_UX_LENS in _FRONTEND_ANALYST_PROMPT


def test_frontend_analyst_prompt_has_no_frontend_surface_exit():
    from alan.teams.implementation_plan import _FRONTEND_ANALYST_PROMPT

    assert (
        "If the task has no frontend surface, say so in one line and stop"
        in _FRONTEND_ANALYST_PROMPT
    )
    assert "do not invent frontend work" in _FRONTEND_ANALYST_PROMPT


def test_frontend_analyst_prompt_output_sections_unchanged():
    from alan.teams.implementation_plan import _FRONTEND_ANALYST_PROMPT

    assert "## Frontend changes" in _FRONTEND_ANALYST_PROMPT
    assert "## Components" in _FRONTEND_ANALYST_PROMPT
    assert "## State and effects" in _FRONTEND_ANALYST_PROMPT


def test_frontend_analyst_prompt_contains_planning_screen_capture_instruction():
    from alan.teams.implementation_plan import _FRONTEND_ANALYST_PROMPT

    assert PLANNING_SCREEN_CAPTURE_INSTRUCTION in _FRONTEND_ANALYST_PROMPT


def test_frontend_analyst_prompt_carries_capture_degrade_path():
    from alan.teams.implementation_plan import _FRONTEND_ANALYST_PROMPT

    assert (
        "capture unavailable — reviewed from markup" in _FRONTEND_ANALYST_PROMPT
    )


def test_devil_advocate_prompt_cites_design_system_and_ui_spec_for_parity():
    from alan.teams.implementation_plan import _DEVIL_ADVOCATE_PROMPT

    assert "docs/DESIGN_SYSTEM.md" in _DEVIL_ADVOCATE_PROMPT
    assert "alan/web/src/ui-spec.ts" in _DEVIL_ADVOCATE_PROMPT


def test_devil_advocate_prompt_does_not_duplicate_the_full_lens_body():
    from alan.teams.implementation_plan import _DEVIL_ADVOCATE_PROMPT

    assert PLANNING_UX_LENS not in _DEVIL_ADVOCATE_PROMPT


def test_devil_advocate_prompt_output_sections_unchanged():
    from alan.teams.implementation_plan import _DEVIL_ADVOCATE_PROMPT

    for header in (
        "## Adjacent code surface",
        "## Invariant risk",
        "## Ripple effects",
        "## UX consistency",
        "## Risks",
        "## Gaps",
        "## Suggested clarifications",
    ):
        assert header in _DEVIL_ADVOCATE_PROMPT


def test_happy_path_runs_all_roles_and_writes_plan(monkeypatch, patch_plans_dir):
    calls: list[tuple[str, str]] = []

    def fake_run_agent(system_prompt: str, user_text: str, **_kwargs: object) -> str:
        idx = len(calls)
        calls.append((system_prompt, user_text))
        return f"output-{idx}"

    monkeypatch.setattr(ipt, "run_agent", fake_run_agent)
    events: list[TeamEvent] = []
    run_team("My Task", "do stuff", events.append)

    assert len(calls) == len(ROLES)
    role_pairs = [(events[i].kind, events[i].payload["role"]) for i in range(12)]
    assert role_pairs == [
        ("role_started", "architect"),
        ("role_completed", "architect"),
        ("role_started", "backend_analyst"),
        ("role_completed", "backend_analyst"),
        ("role_started", "frontend_analyst"),
        ("role_completed", "frontend_analyst"),
        ("role_started", "qa_planner"),
        ("role_completed", "qa_planner"),
        ("role_started", "devil_advocate"),
        ("role_completed", "devil_advocate"),
        ("role_started", "synthesizer"),
        ("role_completed", "synthesizer"),
    ]
    final = events[-1]
    assert final.kind == "complete"
    assert final.payload["plan"] == "output-5"
    assert final.payload["path"].endswith("my-task.md")
    assert Path(final.payload["path"]).read_text() == "output-5"


def test_prior_outputs_are_passed_forward(monkeypatch, patch_plans_dir):
    calls: list[str] = []

    def fake_run_agent(system_prompt: str, user_text: str, **_kwargs: object) -> str:
        calls.append(user_text)
        return f"out-{len(calls)}"

    monkeypatch.setattr(ipt, "run_agent", fake_run_agent)
    run_team("Title", "Desc", lambda _e: None)

    assert "Prior agent outputs:" not in calls[0]
    assert "## architect" in calls[1]
    assert "out-1" in calls[1]
    assert "## architect" in calls[-1]
    assert "## devil_advocate" in calls[-1]


def test_role_failure_emits_error_and_stops(monkeypatch, patch_plans_dir):
    counter = {"n": 0}

    def fake_run_agent(system_prompt: str, user_text: str, **_kwargs: object) -> str:
        counter["n"] += 1
        if counter["n"] == 3:
            raise ClaudeCliError("backend agent failed")
        return "ok"

    monkeypatch.setattr(ipt, "run_agent", fake_run_agent)
    events: list[TeamEvent] = []
    run_team("title", "desc", events.append)

    assert counter["n"] == 3
    assert events[-1].kind == "error"
    assert events[-1].payload == {
        "role": "frontend_analyst",
        "detail": "backend agent failed",
    }
    assert list(patch_plans_dir.iterdir()) == []


def test_build_user_text_without_prior_returns_base():
    base = "Title: T\n\nDescription:\nD"
    assert _build_user_text(base, []) == base


def test_build_user_text_with_prior_appends_sections():
    base = "Title: T\n\nDescription:\nD"
    result = _build_user_text(
        base, [("architect", "A out"), ("backend_analyst", "B out")]
    )
    assert result.startswith(base)
    assert "Prior agent outputs:" in result
    assert "## architect\n\nA out" in result
    assert "## backend_analyst\n\nB out" in result


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello World!", "hello-world"),
        ("  Mix3d Stuff 99 ", "mix3d-stuff-99"),
        ("", "plan"),
        ("!!! ???", "plan"),
        ("a" * 200, "a" * 60),
    ],
)
def test_slugify(raw, expected):
    assert _slugify(raw) == expected


def test_write_plan_uses_base_slug_when_free(patch_plans_dir):
    path = _write_plan("My Task", "body")
    assert path == patch_plans_dir / "my-task.md"
    assert path.read_text() == "body"


def test_write_plan_targets_the_isolated_root_not_the_home_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ALAN_PLANS_ROOT", str(tmp_path / "isolated"))
    path = _write_plan("My Task", "body")
    assert path == tmp_path / "isolated" / "my-task.md"
    assert Path.home() / ".alan" / "plans" not in path.parents


def test_write_plan_resolves_the_root_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("ALAN_PLANS_ROOT", str(tmp_path / "first"))
    first = _write_plan("shared title", "one")
    monkeypatch.setenv("ALAN_PLANS_ROOT", str(tmp_path / "second"))
    second = _write_plan("shared title", "two")
    assert first == tmp_path / "first" / "shared-title.md"
    assert second == tmp_path / "second" / "shared-title.md"


def test_write_plan_walks_collisions(patch_plans_dir):
    (patch_plans_dir / "plan.md").write_text("a")
    (patch_plans_dir / "plan-2.md").write_text("b")
    path = _write_plan("plan", "fresh")
    assert path == patch_plans_dir / "plan-3.md"
    assert path.read_text() == "fresh"


def test_write_plan_raises_when_max_one_and_collision(patch_plans_dir, monkeypatch):
    monkeypatch.setattr(ipt, "_MAX_SLUG_SUFFIX", 1)
    (patch_plans_dir / "plan.md").write_text("existing")
    with pytest.raises(FileExistsError, match="no unique path"):
        _write_plan("plan", "fresh")


def test_write_plan_raises_when_exhausted(patch_plans_dir, monkeypatch):
    monkeypatch.setattr(ipt, "_MAX_SLUG_SUFFIX", 3)
    for name in ("plan.md", "plan-2.md", "plan-3.md"):
        (patch_plans_dir / name).write_text("x")
    with pytest.raises(FileExistsError, match="no unique path"):
        _write_plan("plan", "fresh")


def test_finalize_write_failure_emits_error(monkeypatch, patch_plans_dir):
    monkeypatch.setattr(ipt, "_MAX_SLUG_SUFFIX", 1)
    (patch_plans_dir / "title.md").write_text("existing")

    def fake_run_agent(system_prompt: str, user_text: str, **_kwargs: object) -> str:
        return "synth"

    monkeypatch.setattr(ipt, "run_agent", fake_run_agent)
    events: list[TeamEvent] = []
    run_team("title", "desc", events.append)

    assert events[-1].kind == "error"
    assert events[-1].payload["role"] == "synthesizer"
    assert "write failed" in events[-1].payload["detail"]


def test_collision_writes_to_suffixed_path(monkeypatch, patch_plans_dir):
    (patch_plans_dir / "title.md").write_text("existing")

    def fake_run_agent(system_prompt: str, user_text: str, **_kwargs: object) -> str:
        return "fresh synth"

    monkeypatch.setattr(ipt, "run_agent", fake_run_agent)
    events: list[TeamEvent] = []
    run_team("title", "desc", events.append)

    assert events[-1].kind == "complete"
    assert events[-1].payload["path"].endswith("title-2.md")
    assert (patch_plans_dir / "title-2.md").read_text() == "fresh synth"
    assert (patch_plans_dir / "title.md").read_text() == "existing"


def test_implementation_plan_team_runs_six_roles_and_returns_handover(
    monkeypatch, tmp_path
):
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws_root = tmp_path / "workspaces"
    ws = create("plan task", "desc", root=ws_root)
    events: list[TeamEvent] = []
    team = ImplementationPlanTeam()
    handover = seed_and_run(team, ws, None, events.append)
    assert len(captured) == len(ROLES)
    node_started = [e.payload["role"] for e in events if e.kind == "node_started"]
    node_completed = [e.payload["role"] for e in events if e.kind == "node_completed"]
    assert sorted(node_started) == sorted(r.name for r in ROLES)
    assert sorted(node_completed) == sorted(r.name for r in ROLES)
    assert node_started[0] == "architect"
    assert node_started[1] == "backend_analyst"
    assert set(node_started[2:4]) == {"frontend_analyst", "qa_planner"}
    assert node_started[4] == "devil_advocate"
    assert node_started[5] == "synthesizer"
    assert node_completed[0] == "architect"
    assert node_completed[1] == "backend_analyst"
    assert set(node_completed[2:4]) == {"frontend_analyst", "qa_planner"}
    assert node_completed[4] == "devil_advocate"
    assert node_completed[5] == "synthesizer"
    assert handover.from_team == "implementation_plan"
    assert handover.full_synthesis.startswith("out-6")
    assert "decisions.yaml" in handover.full_synthesis
    assert handover.decisions
    assert handover.decisions[0].id == "d1"
    assert handover.to_team_suggestions == ["decomposer"]
    assert handover.from_invocation_id == "inv_001"
    payload_inv_ids = {
        e.payload["inv_id"]
        for e in events
        if e.kind in {"node_started", "node_completed", "stream"}
    }
    assert payload_inv_ids == {"inv_001"}


def test_handover_always_suggests_decomposer(monkeypatch, tmp_path):
    _patch_streaming(monkeypatch)
    ws_root = tmp_path / "workspaces"
    ws = create("always suggest", "desc", root=ws_root)
    handover = seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    assert handover.to_team_suggestions == ["decomposer"]


def test_implementation_plan_team_writes_six_node_workflow(monkeypatch, tmp_path):
    _patch_streaming(monkeypatch)
    ws_root = tmp_path / "workspaces"
    ws = create("six nodes", "desc", root=ws_root)
    seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert doc.status == "success"
    assert {t.id for t in doc.tasks} == {r.name for r in ROLES}
    by_id = {t.id: t for t in doc.tasks}
    assert by_id["architect"].depends_on == []
    assert by_id["backend_analyst"].depends_on == ["architect"]
    assert by_id["frontend_analyst"].depends_on == ["backend_analyst"]
    assert by_id["qa_planner"].depends_on == ["backend_analyst"]
    assert by_id["devil_advocate"].depends_on == ["frontend_analyst", "qa_planner"]
    assert by_id["synthesizer"].depends_on == ["devil_advocate"]
    for task in doc.tasks:
        assert task.status == "success"
    assert by_id["architect"].model == "opus"
    assert by_id["synthesizer"].model == "opus"
    assert by_id["backend_analyst"].model == "sonnet"


def test_implementation_plan_team_failure_finalizes_workflow_failed(
    monkeypatch, tmp_path
):
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del user_text, model, on_event, max_turns, cwd
        if system_prompt.startswith("You are the frontend analyst"):
            raise ClaudeCliError("backend agent failed")
        text = "ok"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws_root = tmp_path / "workspaces"
    ws = create("fail plan", "desc", root=ws_root)
    with pytest.raises(ClaudeCliError, match="backend agent failed"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert doc.status == "failed"
    by_id = {t.id: t for t in doc.tasks}
    assert {t.id for t in doc.tasks} == {r.name for r in ROLES}
    assert by_id["architect"].status == "success"
    assert by_id["backend_analyst"].status == "success"
    assert by_id["frontend_analyst"].status == "failed"
    assert by_id["frontend_analyst"].error == "backend agent failed"
    assert by_id["qa_planner"].status == "success"
    assert by_id["devil_advocate"].status == "pending"
    assert by_id["synthesizer"].status == "pending"


def test_implementation_plan_team_passes_prior_outputs_through_roles(
    monkeypatch, tmp_path
):
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws_root = tmp_path / "workspaces"
    ws = create("plan b", "desc", root=ws_root)
    seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    assert "Prior agent outputs:" not in captured[0][1]
    assert "## architect" in captured[1][1]
    assert "out-1" in captured[1][1]


def test_implementation_plan_team_prepends_cross_team_on_revisit(
    monkeypatch, tmp_path
):
    from datetime import UTC, datetime

    from alan.handover import Handover, save_handover

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws_root = tmp_path / "workspaces"
    ws = create("rev plan", "desc", root=ws_root)

    seed = Handover(
        handover_id=f"handover_{ws.task_id}_seed",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior brainstorm output",
    )
    save_handover(ws, seed)

    prior = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_plan"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="updated brainstorm",
    )
    seed_and_run(ImplementationPlanTeam(), ws, prior, lambda _e: None)
    assert "Prior context for implementation_plan" in captured[0][1]
    assert "prior brainstorm output" in captured[0][1]


def test_implementation_plan_team_revisit_no_cross_team_uses_base(
    monkeypatch, tmp_path
):
    from datetime import UTC, datetime

    from alan.handover import Handover

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws_root = tmp_path / "workspaces"
    ws = create("rev plan b", "desc", root=ws_root)
    prior = Handover(
        handover_id=f"handover_{ws.task_id}_prior",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="brainstorming",
        from_invocation_id="inv_002",
        to_team_suggestions=["implementation_plan"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="updated brainstorm",
    )
    seed_and_run(ImplementationPlanTeam(), ws, prior, lambda _e: None)
    assert "Prior context for" not in captured[0][1]


def test_implementation_plan_team_raises_when_decisions_block_missing(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "no block here"}) + "\n"
            )
        return AgentStreamingResult(result_text="no block here")

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan no block", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="decisions.yaml block"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_raises_on_empty_decisions_list(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = "plan\n\n```yaml decisions.yaml\ndecisions: []\n```"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan empty", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="at least one decision"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_raises_on_invalid_yaml_block(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = "plan\n\n```yaml decisions.yaml\n: : bad\n```"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan bad yaml", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="not valid YAML"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_raises_on_non_mapping_block(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = "plan\n\n```yaml decisions.yaml\n- not\n- a\n- mapping\n```"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan not map", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="mapping with a"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_raises_on_non_mapping_decision_entry(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = "plan\n\n```yaml decisions.yaml\ndecisions:\n  - just-a-string\n```"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan str entry", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="must be a mapping"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_extracts_files_to_edit_from_block(
    monkeypatch, tmp_path
):
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = (
            "plan\n\n```yaml decisions.yaml\n"
            "decisions:\n"
            "  - id: d1\n"
            "    statement: x\n"
            "    rationale: y\n"
            "    confidence: 0.9\n"
            "    reversibility: high\n"
            "files_to_edit:\n"
            "  - alan/router.py\n"
            "  - tests/test_router.py\n"
            "  - alan/web/src/api.ts\n"
            "test_scenarios:\n"
            "  - id: s1\n"
            "    description: routes correctly\n"
            "    given: a request\n"
            "    when: the router runs\n"
            "    then: the right team is chosen\n"
            "```"
        )
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan files", "d", root=tmp_path / "workspaces")
    handover = seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    assert handover.files_to_edit == [
        "alan/router.py",
        "tests/test_router.py",
        "alan/web/src/api.ts",
    ]


def test_implementation_plan_team_files_to_edit_defaults_to_empty(
    monkeypatch, tmp_path
):
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws = create("no files", "desc", root=tmp_path / "workspaces")
    handover = seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    assert handover.files_to_edit == []


def test_implementation_plan_team_rejects_files_to_edit_non_list(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = (
            "plan\n\n```yaml decisions.yaml\n"
            "decisions:\n"
            "  - id: d1\n"
            "    statement: x\n"
            "    rationale: y\n"
            "    confidence: 0.9\n"
            "    reversibility: high\n"
            "files_to_edit: not-a-list\n"
            "```"
        )
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan bad files", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="files_to_edit must be a list"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_rejects_blank_files_to_edit_entry(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = (
            "plan\n\n```yaml decisions.yaml\n"
            "decisions:\n"
            "  - id: d1\n"
            "    statement: x\n"
            "    rationale: y\n"
            "    confidence: 0.9\n"
            "    reversibility: high\n"
            "files_to_edit:\n"
            "  - alan/ok.py\n"
            "  - '   '\n"
            "```"
        )
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan blank entry", "d", root=tmp_path / "workspaces")
    with pytest.raises(
        ImplementationPlanError, match=r"files_to_edit\[1\] must be a non-empty"
    ):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_implementation_plan_team_raises_on_decision_validation_failure(
    monkeypatch, tmp_path
):
    from alan.teams.implementation_plan import ImplementationPlanError

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        body = (
            "plan\n\n```yaml decisions.yaml\ndecisions:\n  - id: d1\n"
            "    statement: x\n    rationale: y\n    confidence: 1.5\n"
            "    reversibility: medium\n```"
        )
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": body}) + "\n"
            )
        return AgentStreamingResult(result_text=body)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("plan bad dec", "d", root=tmp_path / "workspaces")
    with pytest.raises(ImplementationPlanError, match="failed validation"):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)


def test_synthesizer_prompt_requires_test_scenarios_block():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert "test_scenarios:" in _SYNTHESIZER_PROMPT
    assert "Given/When/Then" in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_when_is_the_command_rule():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert "uv run --no-active pytest" in _SYNTHESIZER_PROMPT
    assert "npm --prefix alan/web run" in _SYNTHESIZER_PROMPT
    assert "works from the repository root" in _SYNTHESIZER_PROMPT
    assert "must carry a" not in _SYNTHESIZER_PROMPT
    assert "PASS/FAIL gates the verdict" not in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_then_is_advisory_only():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert "advisory only" in _SYNTHESIZER_PROMPT
    assert "only the `when` command's exit status gates" in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_states_timeout_and_budget_defaults():
    from alan.teams._scenario_runner import (
        AGGREGATE_BUDGET_DEFAULT,
        SCENARIO_TIMEOUT_DEFAULT,
    )
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert f"{SCENARIO_TIMEOUT_DEFAULT:.0f}s" in _SYNTHESIZER_PROMPT
    assert f"{AGGREGATE_BUDGET_DEFAULT:.0f}s" in _SYNTHESIZER_PROMPT
    assert "blocking TIMEOUT" in _SYNTHESIZER_PROMPT
    assert "SHRINKS" in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_worked_example_uses_mandated_form():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert 'when: "uv run --no-active pytest <target>"' in _SYNTHESIZER_PROMPT
    assert '"uv run pytest <target>"' not in _SYNTHESIZER_PROMPT


def _scenarios_yaml_block(test_scenarios_body: str) -> str:
    return (
        "plan\n\n```yaml decisions.yaml\n"
        "decisions:\n  - id: d1\n    statement: x\n    rationale: y\n"
        "    confidence: 0.9\n    reversibility: high\n"
        f"{test_scenarios_body}"
        "```"
    )


def test_parse_test_scenarios_returns_validated_entries():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n    description: d\n    given: g\n    when: w\n    then: t\n"
        "  - id: s2\n    description: d2\n    given: g2\n    when: w2\n    then: t2\n"
    )
    out = ipt._parse_test_scenarios(body)
    assert [s.id for s in out] == ["s1", "s2"]
    assert out[0].then == "t"
    assert isinstance(out[0], TestScenario)


def test_parse_test_scenarios_missing_key_raises():
    body = _scenarios_yaml_block("")
    with pytest.raises(ipt.ImplementationPlanError, match="missing required"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_empty_list_raises():
    body = _scenarios_yaml_block("test_scenarios: []\n")
    with pytest.raises(ipt.ImplementationPlanError, match="at least one entry"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_non_list_raises():
    body = _scenarios_yaml_block("test_scenarios: just-a-string\n")
    with pytest.raises(ipt.ImplementationPlanError, match="must be a list"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_entry_must_be_mapping():
    body = _scenarios_yaml_block(
        "test_scenarios:\n  - just-a-string\n"
    )
    with pytest.raises(ipt.ImplementationPlanError, match="must be a mapping"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_entry_requires_all_fields():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n    description: d\n    given: g\n    when: w\n"
    )
    with pytest.raises(ipt.ImplementationPlanError, match=r"\.then"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_rejects_empty_string_field():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n    description: '   '\n    given: g\n    when: w\n    then: t\n"
    )
    with pytest.raises(ipt.ImplementationPlanError, match="description"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_accepts_quoted_prose_with_colons():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        '    description: "Row shows summary \\"Bash: ls\\""\n'
        '    given: "Bash tool_use with input.command \\"ls\\""\n'
        '    when: "The user expands the row"\n'
        '    then: "Stats bar shows \\"Read x3 . Bash x2\\"; raw JSON hidden"\n'
    )
    out = ipt._parse_test_scenarios(body)
    assert out[0].then.startswith('Stats bar shows "Read x3 . Bash x2"')
    assert "Bash: ls" in out[0].description


def test_parse_test_scenarios_rejects_unscoped_whole_suite_when():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: d\n"
        "    given: g\n"
        '    when: "uv run --no-active pytest"\n'
        "    then: t\n"
    )
    with pytest.raises(ipt.ImplementationPlanError, match="unscoped whole-suite command"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_rejects_unscoped_npm_whole_suite_when():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: d\n"
        "    given: g\n"
        '    when: "npm --prefix alan/web run e2e"\n'
        "    then: t\n"
    )
    with pytest.raises(ipt.ImplementationPlanError, match="unscoped whole-suite command"):
        ipt._parse_test_scenarios(body)


def test_parse_test_scenarios_accepts_scoped_when():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: d\n"
        "    given: g\n"
        '    when: "uv run --no-active pytest tests/test_x.py::test_y"\n'
        "    then: t\n"
    )
    out = ipt._parse_test_scenarios(body)
    assert out[0].when == "uv run --no-active pytest tests/test_x.py::test_y"


def test_parse_test_scenarios_accepts_prose_when_unaffected_by_unscoped_check():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: d\n"
        "    given: g\n"
        '    when: "The user clicks the submit button"\n'
        "    then: t\n"
    )
    out = ipt._parse_test_scenarios(body)
    assert out[0].when == "The user clicks the submit button"


def test_parse_test_scenarios_recovers_unquoted_prose_with_colons():
    body = _scenarios_yaml_block(
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: d\n"
        "    given: g\n"
        "    when: w\n"
        '    then: Stats bar shows "Bash: ls" inline\n'
    )
    out = ipt._parse_test_scenarios(body)
    assert out[0].then == 'Stats bar shows "Bash: ls" inline'


def test_synthesizer_prompt_documents_yaml_quoting_rule():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert "YAML safety rule" in _SYNTHESIZER_PROMPT
    assert "When in doubt, quote." in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_states_three_mandatory_keys():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    marker = "REQUIRED: end the response with a fenced YAML block"
    idx = _SYNTHESIZER_PROMPT.index(marker)
    skeleton_region = _SYNTHESIZER_PROMPT[idx : idx + 400]
    assert "decisions" in skeleton_region
    assert "files_to_edit" in skeleton_region
    assert "test_scenarios" in skeleton_region
    assert "MANDATORY" in skeleton_region
    assert "omitting any one" in skeleton_region


def test_synthesizer_prompt_contains_drift_gate_rule():
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    assert "DRIFT GATE RULE" in _SYNTHESIZER_PROMPT


def test_synthesizer_prompt_drift_gate_sourced_from_constants():
    from alan.drift_gate import LOAD_BEARING_FILES
    from alan.teams.implementation_plan import _SYNTHESIZER_PROMPT

    for path in LOAD_BEARING_FILES:
        assert path in _SYNTHESIZER_PROMPT, (
            f"Load-bearing file {path!r} not found in synthesizer prompt — "
            "prompt may have drifted from drift_gate.LOAD_BEARING_FILES"
        )


def test_implementation_plan_team_attaches_test_scenarios_to_handover(
    monkeypatch, tmp_path
):
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    ws = create("plan scenarios", "d", root=tmp_path / "workspaces")
    handover = seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)
    assert [s.id for s in handover.test_scenarios] == ["s1"]
    assert handover.test_scenarios[0].then == "outcome"


def test_implementation_plan_invalidates_synthesizer_on_parse_failure(
    monkeypatch, tmp_path
):
    from alan import workflow as workflow_mod
    from alan.workflow import load_workflow, workflow_path

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        text = "no decisions block here"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("inv plan", "d", root=tmp_path / "workspaces")
    with pytest.raises(ipt.ImplementationPlanError):
        seed_and_run(ImplementationPlanTeam(), ws, None, lambda _e: None)

    assert not workflow_mod.result_path(ws, "inv_001", "synthesizer").exists()
    doc = load_workflow(workflow_path(ws, "inv_001"))
    synth = next(t for t in doc.tasks if t.id == "synthesizer")
    assert synth.status == "failed"
