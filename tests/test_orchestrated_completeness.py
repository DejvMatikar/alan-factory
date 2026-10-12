from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import OpenQuestion
from alan.teams import _base as base_mod
from alan.teams._orchestrated import (
    _COMPLETENESS_RE,
    _CONFIDENCE_RE,
    OrchestratedTeam,
    VerifierMetrics,
    _completeness_options,
    _extract_gaps,
    _last_clamped_float,
    _open_questions_for_metrics,
    parse_verifier_metrics,
)
from alan.workflow import load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run


def test_last_clamped_float_no_match_returns_none() -> None:
    assert _last_clamped_float(_COMPLETENESS_RE, "no signal here") is None


def test_last_clamped_float_parses_plain_value() -> None:
    assert _last_clamped_float(_COMPLETENESS_RE, "COMPLETENESS: 0.45") == 0.45


def test_last_clamped_float_clamps_above_one() -> None:
    assert _last_clamped_float(_COMPLETENESS_RE, "COMPLETENESS: 1.5") == 1.0


def test_last_clamped_float_last_occurrence_wins() -> None:
    text = "COMPLETENESS: 0.2\nsome prose\nCOMPLETENESS: 0.9"
    assert _last_clamped_float(_COMPLETENESS_RE, text) == 0.9


def test_last_clamped_float_case_insensitive_and_indented() -> None:
    assert _last_clamped_float(_CONFIDENCE_RE, "  confidence: 0.33") == 0.33


def test_extract_gaps_no_header_returns_empty() -> None:
    assert _extract_gaps("no gaps header at all") == []


def test_extract_gaps_none_inline_returns_empty() -> None:
    assert _extract_gaps("GAPS: none\nCONFIDENCE: 0.9") == []


def test_extract_gaps_empty_inline_no_trailing_content_returns_empty() -> None:
    assert _extract_gaps("some prose\nGAPS:") == []


def test_extract_gaps_dash_items() -> None:
    text = "GAPS:\n- first gap\n- second gap\nCONFIDENCE: 0.5"
    assert _extract_gaps(text) == ["first gap", "second gap"]


def test_extract_gaps_dash_items_at_end_of_text() -> None:
    text = "GAPS:\n- only gap"
    assert _extract_gaps(text) == ["only gap"]


def test_extract_gaps_single_inline_fallback() -> None:
    text = "GAPS: one thing was missed\nCONFIDENCE: 0.5"
    assert _extract_gaps(text) == ["one thing was missed"]


def test_extract_gaps_last_header_wins() -> None:
    text = "GAPS: stale gap\nmore text\nGAPS:\n- fresh gap"
    assert _extract_gaps(text) == ["fresh gap"]


def test_extract_gaps_caps_at_twenty_items() -> None:
    lines = "\n".join(f"- gap {i}" for i in range(25))
    text = f"GAPS:\n{lines}"
    result = _extract_gaps(text)
    assert len(result) == 20
    assert result[0] == "gap 0"
    assert result[-1] == "gap 19"


def test_extract_gaps_excerpts_long_item() -> None:
    long_gap = "x" * 300
    text = f"GAPS:\n- {long_gap}"
    result = _extract_gaps(text)
    assert len(result) == 1
    assert len(result[0]) == 200


def test_extract_gaps_stops_at_non_dash_line() -> None:
    text = "GAPS:\n- first gap\nCONFIDENCE: 0.5\n- not consumed"
    assert _extract_gaps(text) == ["first gap"]


def test_parse_verifier_metrics_none_text_returns_none() -> None:
    assert parse_verifier_metrics(None) is None


def test_parse_verifier_metrics_empty_text_returns_none() -> None:
    assert parse_verifier_metrics("") is None


def test_parse_verifier_metrics_unparseable_text_returns_none() -> None:
    assert parse_verifier_metrics("just prose, no signal") is None


def test_parse_verifier_metrics_full_block() -> None:
    text = "GAPS:\n- missed x\nCONFIDENCE: 0.8\nCOMPLETENESS: 0.6"
    metrics = parse_verifier_metrics(text)
    assert metrics == VerifierMetrics(confidence=0.8, completeness=0.6, gaps=["missed x"])


def test_parse_verifier_metrics_completeness_only() -> None:
    metrics = parse_verifier_metrics("COMPLETENESS: 0.95")
    assert metrics is not None
    assert metrics.completeness == 0.95
    assert metrics.confidence is None
    assert metrics.gaps == []


def test_parse_verifier_metrics_gaps_only_is_not_inert() -> None:
    metrics = parse_verifier_metrics("GAPS:\n- something")
    assert metrics is not None
    assert metrics.completeness is None
    assert metrics.confidence is None
    assert metrics.gaps == ["something"]


def test_open_questions_for_metrics_none_metrics() -> None:
    assert _open_questions_for_metrics(None) == []


def test_open_questions_for_metrics_no_completeness() -> None:
    metrics = VerifierMetrics(confidence=0.9, completeness=None, gaps=[])
    assert _open_questions_for_metrics(metrics) == []


def test_open_questions_for_metrics_above_threshold() -> None:
    metrics = VerifierMetrics(confidence=0.9, completeness=0.95, gaps=[])
    assert _open_questions_for_metrics(metrics) == []


def test_open_questions_for_metrics_at_threshold_not_blocking() -> None:
    metrics = VerifierMetrics(confidence=0.7, completeness=0.7, gaps=[])
    assert _open_questions_for_metrics(metrics) == []


def test_open_questions_for_metrics_below_threshold_with_gaps() -> None:
    metrics = VerifierMetrics(confidence=0.5, completeness=0.45, gaps=["gap a", "gap b"])
    [question] = _open_questions_for_metrics(metrics)
    assert isinstance(question, OpenQuestion)
    assert question.id == "low_completeness"
    assert question.resolver_role == "human"
    assert question.blocking is True
    assert "0.45" in question.question
    assert "0.70" in question.question
    assert "gap a" in question.question
    assert "gap b" in question.question
    assert question.question_class == "orchestrated_completeness"
    assert 2 <= len(question.proposed_answer_space) <= 4
    assert question.recommended == question.proposed_answer_space[0]
    assert any("gap a" in o for o in question.proposed_answer_space)


def test_open_questions_for_metrics_below_threshold_no_gaps() -> None:
    metrics = VerifierMetrics(confidence=0.5, completeness=0.1, gaps=[])
    [question] = _open_questions_for_metrics(metrics)
    assert "none reported" in question.question
    assert 2 <= len(question.proposed_answer_space) <= 4
    assert question.recommended == question.proposed_answer_space[0]


def test_completeness_options_no_gaps_has_two_fallback_options() -> None:
    options = _completeness_options([])
    assert len(options) == 2


def test_completeness_options_caps_gap_options_at_two() -> None:
    options = _completeness_options(["gap a", "gap b", "gap c", "gap d"])
    assert len(options) == 4
    assert options[0] == "Address gap: gap a"
    assert options[1] == "Address gap: gap b"
    assert not any("gap c" in o or "gap d" in o for o in options)


def test_open_questions_for_metrics_threshold_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_COMPLETENESS_THRESHOLD", "0.3")
    metrics = VerifierMetrics(confidence=0.5, completeness=0.45, gaps=[])
    assert _open_questions_for_metrics(metrics) == []


def test_open_questions_for_metrics_threshold_env_garbage_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_COMPLETENESS_THRESHOLD", "not-a-number")
    metrics = VerifierMetrics(confidence=0.5, completeness=0.45, gaps=[])
    [question] = _open_questions_for_metrics(metrics)
    assert "0.70" in question.question


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


_ORCH_PROMPT = "You are the completeness-gate test orchestrator."
_SYNTH_PROMPT = "You are the completeness-gate test synthesizer."
_VERIFIER_PROMPT = "You are the completeness-gate test verifier."


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Task", "desc", root=ws_root)


def _make_team_with_verifier() -> OrchestratedTeam:
    return OrchestratedTeam(
        name="gate_test_team",
        orchestrator_prompt=_ORCH_PROMPT,
        synthesizer_prompt=_SYNTH_PROMPT,
        worker_system_template="You are a worker with role '{role}'.",
        to_team_suggestions=(),
        verifier_prompt=_VERIFIER_PROMPT,
    )


def _make_team_without_verifier() -> OrchestratedTeam:
    return OrchestratedTeam(
        name="gate_test_team_no_verifier",
        orchestrator_prompt=_ORCH_PROMPT,
        synthesizer_prompt=_SYNTH_PROMPT,
        worker_system_template="You are a worker with role '{role}'.",
        to_team_suggestions=(),
    )


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


def test_run_gate_omitted_when_completeness_above_threshold(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): "GAPS: none\nCONFIDENCE: 0.9\nCOMPLETENESS: 0.95",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.open_questions == []
    assert handover.verifier_completeness == 0.95
    assert handover.verifier_confidence == 0.9
    assert handover.verifier_gaps == []


def test_run_gate_attached_when_completeness_below_threshold(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): (
                "GAPS:\n- missed the api surface\n- missed perf numbers\n"
                "CONFIDENCE: 0.5\nCOMPLETENESS: 0.45"
            ),
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    [question] = handover.open_questions
    assert question.id == "low_completeness"
    assert question.blocking is True
    assert question.resolver_role == "human"
    assert "0.45" in question.question
    assert handover.verifier_completeness == 0.45
    assert handover.verifier_confidence == 0.5
    assert handover.verifier_gaps == ["missed the api surface", "missed perf numbers"]


def test_run_gate_inert_on_unparseable_verifier_output(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): "the verifier said some unrelated prose",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.open_questions == []
    assert handover.verifier_completeness is None
    assert handover.verifier_confidence is None
    assert handover.verifier_gaps == []


def test_run_gate_inert_for_team_without_verifier(
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
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.open_questions == []
    assert handover.verifier_completeness is None
    assert handover.verifier_confidence is None
    assert handover.verifier_gaps == []
    doc = load_workflow(workflow_path(ws, "inv_001"))
    assert "verifier" not in {t.id for t in doc.tasks}


def test_run_gate_respects_env_threshold_override(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    monkeypatch.setenv("ALAN_COMPLETENESS_THRESHOLD", "0.3")
    ws = _make_ws(ws_root)
    team = _make_team_with_verifier()
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_VERIFIER_PROMPT): "GAPS: none\nCONFIDENCE: 0.5\nCOMPLETENESS: 0.45",
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.open_questions == []
    assert handover.verifier_completeness == 0.45
