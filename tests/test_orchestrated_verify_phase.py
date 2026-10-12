from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import _base as base_mod
from alan.teams import opportunity_scout as scout_mod
from alan.teams import research as research_mod
from alan.teams._orchestrated import (
    ORCHESTRATOR_ROLE,
    SYNTHESIZER_ROLE,
    VERIFIER_ROLE,
    InvocationError,
    OrchestratedTeam,
    parse_dispatch,
)
from alan.workflow import invalidate_node_result, load_workflow, save_workflow, workflow_path
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


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Task", "desc", root=ws_root)


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, str],
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        calls.append((system_prompt, user_text))
        text = responses.get(system_prompt, responses.get("default", ""))
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return calls


_VERIFIER_PROMPT = "You are the test verifier."
_SYNTH_PROMPT = "You are the test synthesizer."
_ORCH_PROMPT = "You are the test orchestrator."


def _turn_budgeted(prompt: str) -> str:
    return f"{prompt}\n\nTurn budget for this run: {base_mod.MAX_TURNS}"


def _sys(prompt: str) -> str:
    return _turn_budgeted(
        base_mod.compose_system_prompt(
            prompt, dynamic_block=base_mod.UNTRUSTED_CONTENT_NOTICE
        )
    )


def _make_team_with_verifier() -> OrchestratedTeam:
    return OrchestratedTeam(
        name="test_team",
        orchestrator_prompt=_ORCH_PROMPT,
        synthesizer_prompt=_SYNTH_PROMPT,
        worker_system_template="You are a worker with role '{role}'.",
        to_team_suggestions=(),
        verifier_prompt=_VERIFIER_PROMPT,
    )


def _make_team_without_verifier() -> OrchestratedTeam:
    return OrchestratedTeam(
        name="test_team_no_verifier",
        orchestrator_prompt=_ORCH_PROMPT,
        synthesizer_prompt=_SYNTH_PROMPT,
        worker_system_template="You are a worker with role '{role}'.",
        to_team_suggestions=(),
    )


def test_verifier_role_is_reserved() -> None:
    payload = json.dumps(
        {"agents": [{"role": "verifier", "prompt": _stub_prompt("x")}]}
    )
    with pytest.raises(InvocationError, match="reserved"):
        parse_dispatch(payload)


def test_team_without_verifier_has_no_verifier_node(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_without_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    node_ids = {t.id for t in doc.tasks}
    assert VERIFIER_ROLE not in node_ids
    assert node_ids == {ORCHESTRATOR_ROLE, "alpha", SYNTHESIZER_ROLE}


def test_team_without_verifier_synthesizer_depends_on_workers_only(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_without_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha", "beta"]),
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    synth = next(t for t in doc.tasks if t.id == SYNTHESIZER_ROLE)
    assert synth.depends_on == ["alpha", "beta"]


def test_team_with_verifier_has_verifier_node(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha", "beta"]),
            _sys(_VERIFIER_PROMPT): "verifier output",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    node_ids = {t.id for t in doc.tasks}
    assert VERIFIER_ROLE in node_ids
    assert node_ids == {ORCHESTRATOR_ROLE, "alpha", "beta", VERIFIER_ROLE, SYNTHESIZER_ROLE}


def test_verifier_depends_on_all_workers(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha", "beta"]),
            _sys(_VERIFIER_PROMPT): "verifier output",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    verifier = next(t for t in doc.tasks if t.id == VERIFIER_ROLE)
    assert set(verifier.depends_on) == {"alpha", "beta"}


def test_synthesizer_depends_on_verifier(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): "verifier output",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    synth = next(t for t in doc.tasks if t.id == SYNTHESIZER_ROLE)
    assert synth.depends_on == ["alpha", VERIFIER_ROLE]


def test_verifier_input_contains_all_worker_outputs(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    seen_verifier_user_text: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt == _sys(_VERIFIER_PROMPT):
            seen_verifier_user_text.append(user_text)
            text = "verifier findings"
        elif system_prompt == _turn_budgeted(_ORCH_PROMPT):
            text = _dispatch_payload(["alpha", "beta"])
        elif system_prompt == _sys(_SYNTH_PROMPT):
            text = "summary"
        else:
            role = "alpha" if "alpha" in system_prompt else "beta"
            text = f"output from {role} worker"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(team, ws, None, lambda _e: None)

    assert len(seen_verifier_user_text) == 1
    verifier_input = seen_verifier_user_text[0]
    assert "output from alpha worker" in verifier_input
    assert "output from beta worker" in verifier_input
    assert "## alpha" in verifier_input
    assert "## beta" in verifier_input


def test_synthesizer_input_contains_verifier_output(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    seen_synth_user_text: list[str] = []
    verifier_text = "verifier found critical gap in beta coverage"

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt == _sys(_SYNTH_PROMPT):
            seen_synth_user_text.append(user_text)
            text = "summary"
        elif system_prompt == _turn_budgeted(_ORCH_PROMPT):
            text = _dispatch_payload(["alpha"])
        elif system_prompt == _sys(_VERIFIER_PROMPT):
            text = verifier_text
        else:
            text = "worker output"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(team, ws, None, lambda _e: None)

    assert len(seen_synth_user_text) == 1
    synth_input = seen_synth_user_text[0]
    assert verifier_text in synth_input
    assert f"## {VERIFIER_ROLE}" in synth_input


def test_research_team_verifier_receives_worker_outputs(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen_verifier_user_text: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt == _sys(research_mod._VERIFIER_PROMPT):
            seen_verifier_user_text.append(user_text)
            text = "research verifier findings"
        elif system_prompt == _turn_budgeted(research_mod._ORCH_PROMPT):
            text = _dispatch_payload(["deep_diver", "comparator"])
        elif system_prompt == _sys(research_mod._SYNTH_PROMPT):
            text = "consolidated research"
        else:
            role = "deep_diver" if "deep_diver" in system_prompt else "comparator"
            text = f"detailed findings from {role}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    team = research_mod.TEAM
    seed_and_run(team, ws, None, lambda _e: None)

    assert len(seen_verifier_user_text) == 1
    verifier_input = seen_verifier_user_text[0]
    assert "detailed findings from deep_diver" in verifier_input
    assert "detailed findings from comparator" in verifier_input


def test_opportunity_scout_team_verifier_receives_worker_outputs(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    seen_verifier_user_text: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt == _sys(scout_mod._VERIFIER_PROMPT):
            seen_verifier_user_text.append(user_text)
            text = "scout verifier findings"
        elif system_prompt == _turn_budgeted(scout_mod._ORCH_PROMPT):
            text = _dispatch_payload(["risk_analyst", "distribution_analyst"])
        elif system_prompt == _sys(scout_mod._SYNTH_PROMPT):
            text = "consolidated scout result"
        else:
            role = (
                "risk_analyst"
                if "role 'risk_analyst'" in system_prompt
                else "distribution_analyst"
            )
            text = f"filter verdict from {role}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    team = scout_mod.TEAM
    seed_and_run(team, ws, None, lambda _e: None)

    assert len(seen_verifier_user_text) == 1
    verifier_input = seen_verifier_user_text[0]
    assert "filter verdict from risk_analyst" in verifier_input
    assert "filter verdict from distribution_analyst" in verifier_input


def test_resume_skips_cached_verifier(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    cached_verifier_text = "cached verifier findings from resume run"

    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): cached_verifier_text,
            _sys(_SYNTH_PROMPT): "first summary",
            "default": "alpha output",
        },
    )
    seed_and_run(team, ws, None, lambda _e: None)

    doc = load_workflow(workflow_path(ws, "inv_001"))
    synth_task = next(t for t in doc.tasks if t.id == SYNTHESIZER_ROLE)
    synth_task.status = "pending"
    save_workflow(ws, doc)
    invalidate_node_result(ws, "inv_001", SYNTHESIZER_ROLE)

    seen_synth_user_text: list[str] = []
    streaming_calls: list[str] = []

    def recorder(
        system_prompt: str,
        user_text: str,
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        streaming_calls.append(system_prompt)
        if system_prompt == _sys(_SYNTH_PROMPT):
            seen_synth_user_text.append(user_text)
            text = "second summary"
        else:
            raise AssertionError(f"unexpected streaming call: {system_prompt[:60]!r}")
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", recorder)
    team.run(ws, None, lambda _e: None, inv_id="inv_001")

    assert _sys(_VERIFIER_PROMPT) not in streaming_calls
    assert len(seen_synth_user_text) == 1
    assert cached_verifier_text in seen_synth_user_text[0]
    assert f"## {VERIFIER_ROLE}" in seen_synth_user_text[0]


def test_node_order_verifier_before_synthesizer(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    call_order: list[str] = []

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        if system_prompt == _turn_budgeted(_ORCH_PROMPT):
            call_order.append(ORCHESTRATOR_ROLE)
            text = _dispatch_payload(["alpha"])
        elif system_prompt == _sys(_VERIFIER_PROMPT):
            call_order.append(VERIFIER_ROLE)
            text = "verifier out"
        elif system_prompt == _sys(_SYNTH_PROMPT):
            call_order.append(SYNTHESIZER_ROLE)
            text = "summary"
        else:
            call_order.append("worker")
            text = "worker out"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    seed_and_run(team, ws, None, lambda _e: None)

    verifier_idx = call_order.index(VERIFIER_ROLE)
    synth_idx = call_order.index(SYNTHESIZER_ROLE)
    worker_idx = call_order.index("worker")
    assert worker_idx < verifier_idx < synth_idx
