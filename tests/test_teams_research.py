from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Handover
from alan.teams import TEAMS
from alan.teams import _base as base_mod
from alan.teams import research as research_mod
from alan.teams._base import TeamEvent, build_forward_handover
from alan.teams.research import (
    _VERIFIER_PROMPT,
    DispatchedAgent,
    InvocationError,
    ResearchTeam,
    _parse_dispatch,
)
from alan.workflow import load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run


def _stub_prompt(label: str) -> str:
    return (
        f"SCOPE: {label} narrow scope\n"
        f"COVERAGE: only {label}, sibling roles cover others\n"
        f"SUCCESS: when {label} delivers concrete numbers\n"
        f"CONTEXT: {label} context here"
    )


def _dispatch_payload(roles: list[str]) -> str:
    return json.dumps(
        {"agents": [{"role": r, "prompt": _stub_prompt(r)} for r in roles]}
    )


def _turn_budgeted(prompt: str) -> str:
    return f"{prompt}\n\nTurn budget for this run: {base_mod.MAX_TURNS}"


def _sys(prompt: str) -> str:
    return _turn_budgeted(
        base_mod.compose_system_prompt(
            prompt, dynamic_block=base_mod.UNTRUSTED_CONTENT_NOTICE
        )
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Task", "desc", root=ws_root)


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    outputs: dict[str, str],
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

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
        calls.append((system_prompt, user_text))
        text = outputs.get(model + ":" + str(len(calls)), "")
        if not text:
            text = outputs.get(system_prompt, "")
        if not text:
            text = outputs.get("default", "")
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return calls


def test_orch_prompt_has_no_judge_instruction() -> None:
    assert "judge" not in research_mod._ORCH_PROMPT


def test_registry_research_is_research_team() -> None:
    assert isinstance(TEAMS["research"], ResearchTeam)
    assert TEAMS["research"].name == "research"


def test_default_to_team_suggestions() -> None:
    team = ResearchTeam()
    assert team.to_team_suggestions == ()


def test_run_orchestrator_then_workers_then_synth(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    orch_json = _dispatch_payload(["alpha", "beta"])
    outputs = {
        _turn_budgeted(research_mod._ORCH_PROMPT): orch_json,
        _sys(research_mod._SYNTH_PROMPT): "consolidated summary",
        "default": "worker output",
    }
    _patch_stream(monkeypatch, outputs)
    events: list[TeamEvent] = []
    team = ResearchTeam(name="research")
    handover = seed_and_run(team, ws, None, events.append)

    assert isinstance(handover, Handover)
    assert handover.from_team == "research"
    assert handover.from_invocation_id == "inv_001"
    assert handover.full_synthesis.startswith("consolidated summary")

    started = [e for e in events if e.kind == "node_started"]
    completed = [e for e in events if e.kind == "node_completed"]
    assert [e.payload["role"] for e in started] == [
        "orchestrator",
        "alpha",
        "beta",
        "verifier",
        "synthesizer",
    ]
    assert [e.payload["role"] for e in completed] == [
        "orchestrator",
        "alpha",
        "beta",
        "verifier",
        "synthesizer",
    ]

    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert doc.status == "success"
    assert {t.id for t in doc.tasks} == {
        "orchestrator", "alpha", "beta", "verifier", "synthesizer"
    }
    orch = next(t for t in doc.tasks if t.id == "orchestrator")
    assert orch.dispatched == ["alpha", "beta"]
    synth = next(t for t in doc.tasks if t.id == "synthesizer")
    assert synth.depends_on == ["alpha", "beta", "verifier"]


def test_stream_events_published(monkeypatch: pytest.MonkeyPatch, ws_root: Path) -> None:
    ws = _make_ws(ws_root)
    orch_json = _dispatch_payload(["alpha"])
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(research_mod._ORCH_PROMPT): orch_json,
            _sys(research_mod._SYNTH_PROMPT): "sum",
            "default": "worker",
        },
    )
    events: list[TeamEvent] = []
    seed_and_run(ResearchTeam(name="research"), ws, None, events.append)
    stream_evts = [e for e in events if e.kind == "stream"]
    assert {e.payload["role"] for e in stream_evts} == {
        "orchestrator",
        "alpha",
        "verifier",
        "synthesizer",
    }
    assert {e.payload["inv_id"] for e in stream_evts} == {"inv_001"}
    parsed = [json.loads(e.payload["event"]) for e in stream_evts]
    assert all(p["type"] == "result" for p in parsed)


def test_node_events_carry_inv_id(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    orch_json = _dispatch_payload(["alpha"])
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(research_mod._ORCH_PROMPT): orch_json,
            _sys(research_mod._SYNTH_PROMPT): "summary",
            "default": "worker",
        },
    )
    events: list[TeamEvent] = []
    seed_and_run(ResearchTeam(name="research"), ws, None, events.append)
    node_evts = [
        e for e in events if e.kind in {"node_started", "node_completed"}
    ]
    assert node_evts
    assert {e.payload["inv_id"] for e in node_evts} == {"inv_001"}


def test_orchestrator_prior_handover_inlined(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen_user_text: list[str] = []
    dispatch = _dispatch_payload(["alpha"])

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
        seen_user_text.append(user_text)
        if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            return AgentStreamingResult(result_text=dispatch)
        if system_prompt == _sys(research_mod._SYNTH_PROMPT):
            return AgentStreamingResult(result_text="summary")
        return AgentStreamingResult(result_text="worker out")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = build_forward_handover(
        ws,
        from_team="brainstorming",
        to_team_suggestions=["research"],
        summary="earlier ideas",
        prior=None,
    )
    seed_and_run(ResearchTeam(name="research"), ws, prior, lambda _e: None)
    assert any("earlier ideas" in t for t in seen_user_text)


def test_run_marks_only_running_node_failed_when_worker_blows_up(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    orch_json = _dispatch_payload(["alpha"])
    counter = {"n": 0}

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,  # noqa: ARG001
        stream_path: Path | None = None,  # noqa: ARG001
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            return AgentStreamingResult(result_text=orch_json)
        raise RuntimeError("alpha worker crashed")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    with pytest.raises(RuntimeError, match="alpha worker crashed"):
        seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert doc.status == "failed"
    by_id = {t.id: t for t in doc.tasks}
    assert by_id["orchestrator"].status == "success"
    assert by_id["orchestrator"].error is None
    assert by_id["alpha"].status == "failed"
    assert by_id["alpha"].error == "alpha worker crashed"


def test_run_marks_workflow_failed_on_error(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)

    def boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert doc.status == "failed"
    orch = next(t for t in doc.tasks if t.id == "orchestrator")
    assert orch.status == "failed"
    assert orch.error == "kaboom"


def test_orchestrator_parse_failure_invalidates_cache_and_marks_failed(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from pathlib import Path as _Path

    from alan import workflow as workflow_mod

    ws = _make_ws(ws_root)

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        stream_path: _Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        bad_text = '{"agents":[]}'
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": bad_text}) + "\n"
            )
        return AgentStreamingResult(result_text=bad_text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    with pytest.raises(InvocationError, match="missing non-empty"):
        seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    orch = next(t for t in doc.tasks if t.id == "orchestrator")
    assert orch.status == "failed"
    assert "parse_dispatch" in (orch.error or "")
    cache = workflow_mod.result_path(ws, "inv_001", "orchestrator")
    assert not cache.exists()


def test_research_team_rejects_team_mismatch(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan import workflow as workflow_mod
    from alan.workflow import PendingTaskSpec, WorkflowError

    ws = _make_ws(ws_root)
    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="architecture",
        tasks=[
            PendingTaskSpec(
                id="orchestrator", role="orchestrator", model="opus"
            )
        ],
    )
    _patch_stream(monkeypatch, {"default": "x"})
    with pytest.raises(WorkflowError, match="team mismatch"):
        ResearchTeam(name="research").run(
            ws, None, lambda _e: None, inv_id=inv_id
        )


def test_parse_dispatch_invalid_json() -> None:
    with pytest.raises(InvocationError, match="not valid JSON"):
        _parse_dispatch("nope")


def test_parse_dispatch_not_object() -> None:
    with pytest.raises(InvocationError, match="JSON object"):
        _parse_dispatch("[1, 2]")


def test_parse_dispatch_missing_agents() -> None:
    with pytest.raises(InvocationError, match="missing non-empty 'agents'"):
        _parse_dispatch('{"other": 1}')


def test_parse_dispatch_empty_agents() -> None:
    with pytest.raises(InvocationError, match="missing non-empty 'agents'"):
        _parse_dispatch('{"agents": []}')


def test_parse_dispatch_agent_not_object() -> None:
    with pytest.raises(InvocationError, match="must be object"):
        _parse_dispatch('{"agents": ["x"]}')


def test_parse_dispatch_agent_missing_role() -> None:
    with pytest.raises(InvocationError, match="missing string 'role'"):
        _parse_dispatch('{"agents": [{"prompt": "p"}]}')


def test_parse_dispatch_agent_blank_role() -> None:
    with pytest.raises(InvocationError, match="missing string 'role'"):
        _parse_dispatch('{"agents": [{"role": "  ", "prompt": "p"}]}')


def test_parse_dispatch_agent_missing_prompt() -> None:
    with pytest.raises(InvocationError, match="missing string 'prompt'"):
        _parse_dispatch('{"agents": [{"role": "alpha"}]}')


def test_parse_dispatch_agent_blank_prompt() -> None:
    with pytest.raises(InvocationError, match="missing string 'prompt'"):
        _parse_dispatch('{"agents": [{"role": "alpha", "prompt": "  "}]}')


def test_parse_dispatch_trims_whitespace() -> None:
    prompt = _stub_prompt("a")
    payload = json.dumps(
        {"agents": [{"role": "  a  ", "prompt": f"  {prompt}  "}]}
    )
    result = _parse_dispatch(payload)
    assert result == [DispatchedAgent(role="a", prompt=prompt)]


@pytest.mark.parametrize(
    "bad_role",
    ["../x", "a/b", "Worker", "1worker", "agent.stream", "a-b"],
)
def test_parse_dispatch_rejects_unsafe_role(bad_role: str) -> None:
    payload = json.dumps(
        {"agents": [{"role": bad_role, "prompt": _stub_prompt("x")}]}
    )
    with pytest.raises(InvocationError, match="must match"):
        _parse_dispatch(payload)


@pytest.mark.parametrize("reserved", ["orchestrator", "synthesizer", "verifier"])
def test_parse_dispatch_rejects_reserved_role(reserved: str) -> None:
    payload = json.dumps(
        {"agents": [{"role": reserved, "prompt": _stub_prompt("x")}]}
    )
    with pytest.raises(InvocationError, match="reserved"):
        _parse_dispatch(payload)


def test_parse_dispatch_rejects_duplicate_role() -> None:
    p = _stub_prompt("alpha")
    payload = json.dumps(
        {
            "agents": [
                {"role": "alpha", "prompt": p},
                {"role": "alpha", "prompt": p},
            ]
        }
    )
    with pytest.raises(InvocationError, match="duplicate agent role"):
        _parse_dispatch(payload)


@pytest.mark.parametrize(
    ("missing_field", "prompt"),
    [
        ("SCOPE:", "COVERAGE: x SUCCESS: y CONTEXT: z"),
        ("COVERAGE:", "SCOPE: x SUCCESS: y CONTEXT: z"),
        ("SUCCESS:", "SCOPE: x COVERAGE: y CONTEXT: z"),
        ("CONTEXT:", "SCOPE: x COVERAGE: y SUCCESS: z"),
    ],
)
def test_parse_dispatch_rejects_four_field_missing(
    missing_field: str, prompt: str
) -> None:
    payload = json.dumps({"agents": [{"role": "alpha", "prompt": prompt}]})
    with pytest.raises(
        InvocationError,
        match=f"missing required field.*{missing_field}",
    ):
        _parse_dispatch(payload)


def test_parse_dispatch_lists_multiple_missing_fields() -> None:
    payload = json.dumps(
        {"agents": [{"role": "alpha", "prompt": "SCOPE: only this"}]}
    )
    with pytest.raises(InvocationError, match="COVERAGE:.*SUCCESS:.*CONTEXT:"):
        _parse_dispatch(payload)


def test_run_with_no_prior_omits_prior_block(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen: list[str] = []
    dispatch = _dispatch_payload(["alpha"])

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
        if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            seen.append(user_text)
            return AgentStreamingResult(result_text=dispatch)
        if system_prompt == _sys(research_mod._SYNTH_PROMPT):
            return AgentStreamingResult(result_text="summary")
        return AgentStreamingResult(result_text="out")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    assert "Prior handover" not in seen[0]


def test_workflowgraph_used_through_team_save_after_each_phase(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    saves: list[str] = []
    real_save = workflow_mod.save_workflow

    def spy_save(workspace, doc):
        saves.append(doc.status)
        return real_save(workspace, doc)

    monkeypatch.setattr(workflow_mod, "save_workflow", spy_save)
    orch_json = _dispatch_payload(["a"])

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
        if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            return AgentStreamingResult(result_text=orch_json)
        if system_prompt == _sys(research_mod._SYNTH_PROMPT):
            return AgentStreamingResult(result_text="s")
        return AgentStreamingResult(result_text="w")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    assert "running" in saves
    assert saves[-1] == "success"


def test_worker_prompt_contains_confidence_block(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen_system_prompts: list[str] = []
    dispatch = _dispatch_payload(["alpha"])

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,  # noqa: ARG001
        stream_path: Path | None = None,  # noqa: ARG001
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        seen_system_prompts.append(system_prompt)
        if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            return AgentStreamingResult(result_text=dispatch)
        if system_prompt == _sys(research_mod._SYNTH_PROMPT):
            return AgentStreamingResult(result_text="summary")
        return AgentStreamingResult(result_text="worker out")

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    worker_prompts = [
        p
        for p in seen_system_prompts
        if p
        not in {
            _turn_budgeted(research_mod._ORCH_PROMPT),
            _sys(research_mod._SYNTH_PROMPT),
            _sys(_VERIFIER_PROMPT),
        }
    ]
    assert worker_prompts, "worker prompt was not seen"
    for prompt in worker_prompts:
        assert "CONFIDENCE REPORTING" in prompt
        assert "AGENT_STATUS" in prompt
        assert "alpha" in prompt


def test_worker_node_config_grants_web_tools(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(research_mod._ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(research_mod._SYNTH_PROMPT): "summary",
            "default": "worker out",
        },
    )
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    worker_cfg = workflow_mod.load_node_config(ws, "inv_001", "alpha")
    assert worker_cfg is not None
    assert "WebSearch" in worker_cfg["allowed_tools"]
    assert "WebFetch" in worker_cfg["allowed_tools"]
    for tool in base_mod.BASE_ALLOWED_TOOLS:
        assert tool in worker_cfg["allowed_tools"]


def test_non_worker_node_configs_omit_web_tools(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(research_mod._ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(research_mod._SYNTH_PROMPT): "summary",
            "default": "worker out",
        },
    )
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    for node_id in ("orchestrator", "verifier", "synthesizer"):
        node_cfg = workflow_mod.load_node_config(ws, "inv_001", node_id)
        assert node_cfg is not None
        assert "WebSearch" not in node_cfg["allowed_tools"]
        assert "WebFetch" not in node_cfg["allowed_tools"]


def test_engine_snapshot_governs_all_node_tools(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity
    from alan.workspace import EngineSnapshot, stamp_engine_snapshot

    ws = _make_ws(ws_root)
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read"],
        team_tools={"research": ["WebSearch"]},
    )
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.propose, transition_graph={}, config=cfg)
    )
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(research_mod._ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(research_mod._SYNTH_PROMPT): "summary",
            "default": "worker out",
        },
    )
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    worker_cfg = workflow_mod.load_node_config(ws, "inv_001", "alpha")
    assert worker_cfg is not None
    assert worker_cfg["allowed_tools"][:2] == ["Read", "WebSearch"]
    for node_id in ("orchestrator", "verifier", "synthesizer"):
        node_cfg = workflow_mod.load_node_config(ws, "inv_001", node_id)
        assert node_cfg is not None
        assert node_cfg["allowed_tools"][:1] == ["Read"]


def test_worker_verifier_synth_prompts_carry_untrusted_notice(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen_system_prompts: list[str] = []
    dispatch = _dispatch_payload(["alpha"])

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        seen_system_prompts.append(system_prompt)
        text = dispatch if system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT) else "out"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(ResearchTeam(name="research"), ws, None, lambda _e: None)
    orchestrator_prompt = seen_system_prompts[0]
    assert base_mod.UNTRUSTED_CONTENT_NOTICE not in orchestrator_prompt
    for prompt in seen_system_prompts[1:]:
        assert base_mod.UNTRUSTED_CONTENT_NOTICE in prompt


def test_verifier_prompt_ends_with_machine_parseable_tail() -> None:
    text = _VERIFIER_PROMPT
    assert len(re.findall(r"(?m)^GAPS:", text)) == 1
    assert len(re.findall(r"(?m)^CONFIDENCE:", text)) == 1
    assert len(re.findall(r"(?m)^COMPLETENESS:", text)) == 1
    gaps_match = re.search(r"(?m)^GAPS:", text)
    assert gaps_match is not None
    tail_lines = text[gaps_match.start() :].splitlines()
    assert len(tail_lines) == 3
    assert tail_lines[0].startswith("GAPS:")
    assert tail_lines[1].startswith("CONFIDENCE:")
    assert tail_lines[2].startswith("COMPLETENESS:")
    assert text.endswith(tail_lines[2])
    assert "GAPS: none" in text


def test_orch_prompt_has_coverage_union_rule() -> None:
    assert "Coverage-union rule" in research_mod._ORCH_PROMPT
    assert "SCOPE" in research_mod._COVERAGE_UNION_RULE
    assert research_mod._COVERAGE_UNION_RULE in research_mod._ORCH_PROMPT


def test_synth_prompt_full_length_exec_summary() -> None:
    assert "2500" not in research_mod._SYNTH_PROMPT
    assert "2,400" in research_mod._SYNTH_PROMPT
    assert "## Low-Confidence Findings" in research_mod._SYNTH_PROMPT


def test_opportunity_scout_synth_prompt_byte_unchanged() -> None:
    from alan.teams import opportunity_scout as opportunity_scout_mod

    expected = (
        f"{opportunity_scout_mod._FILTER_RULES}\n\n"
        "You are the opportunity_scout synthesizer. Produce a "
        "consolidated handover summary (<=2500 chars, plain markdown) with "
        "sections: ## Filter Verdicts (1 line per filter: GO/NO-GO + evidence), "
        "## Verdict (GO or NO-GO with rationale), ## Pitch Brief (only if GO — "
        "value prop, target customer, distribution channel, CAC estimate, "
        "moat), ## UI-debt candidate (only if a ui_debt_analyst agent ran and "
        "judged its candidate worth pursuing — at most one candidate, its "
        "severity and effort labels and evidence, and a recommendation to "
        "dispatch a dedicated ui_review run as the follow-up; omit this section "
        "entirely otherwise — never invent one), ## Risks to Watch, "
        "## Open questions. "
        "Incorporate verifier findings where they qualify or overturn filter "
        "verdicts. No preamble."
    )
    assert opportunity_scout_mod._SYNTH_PROMPT == expected


def test_brainstorming_synth_prompt_byte_unchanged() -> None:
    from alan.teams import brainstorming as brainstorming_mod

    expected = (
        "You are the brainstorming synthesizer. You receive the user's task and "
        "each perspective's output. Produce a consolidated handover summary "
        "(<=2500 chars, plain markdown), with sections: ## Options "
        "(3-5 ranked approaches with one-paragraph sketches), ## Recommended "
        "starting point (with rationale), ## Risks, ## Open questions. "
        "Resolve conflicts between perspectives explicitly. No preamble."
    )
    assert brainstorming_mod._SYNTH_PROMPT == expected
