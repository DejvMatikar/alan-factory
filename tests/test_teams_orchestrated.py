from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import ArtifactRef
from alan.teams import _base as base_mod
from alan.teams._base import TERSENESS_BLOCK, UNTRUSTED_CONTENT_NOTICE
from alan.teams._orchestrated import (
    CONFIDENCE_REPORTING_BLOCK,
    OrchestratedTeam,
    assemble_worker_prompt,
)
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run

_ORCH_PROMPT = "Test orchestrator prompt."
_SYNTH_PROMPT = "Test synthesizer prompt."
_WORKER_TEMPLATE = "Test worker '{role}'."


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Orchestrated Report Test", "desc", root=ws_root)


def _team(name: str = "test_team", *, writes_report: bool = True) -> OrchestratedTeam:
    return OrchestratedTeam(
        name=name,
        orchestrator_prompt=_ORCH_PROMPT,
        synthesizer_prompt=_SYNTH_PROMPT,
        worker_system_template=_WORKER_TEMPLATE,
        to_team_suggestions=(),
        writes_report=writes_report,
    )


def _turn_budgeted(prompt: str) -> str:
    return f"{prompt}\n\nTurn budget for this run: {base_mod.MAX_TURNS}"


def _sys(prompt: str) -> str:
    return _turn_budgeted(
        base_mod.compose_system_prompt(prompt, dynamic_block=base_mod.UNTRUSTED_CONTENT_NOTICE)
    )


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


def _patch_stream(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str]) -> None:
    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        text = outputs.get(system_prompt, outputs.get("default", ""))
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def test_assemble_worker_prompt_default_terse_true_includes_terseness() -> None:
    result = assemble_worker_prompt("You are {role}.", "analyst")
    assert "You are analyst." in result
    assert TERSENESS_BLOCK in result
    assert UNTRUSTED_CONTENT_NOTICE in result
    assert CONFIDENCE_REPORTING_BLOCK in result


def test_assemble_worker_prompt_terse_false_excludes_terseness() -> None:
    result = assemble_worker_prompt("You are {role}.", "analyst", terse=False)
    assert TERSENESS_BLOCK not in result
    assert UNTRUSTED_CONTENT_NOTICE in result
    assert CONFIDENCE_REPORTING_BLOCK in result


def test_assemble_worker_prompt_terse_true_ordering() -> None:
    result = assemble_worker_prompt("STATIC", "r", terse=True)
    si = result.index("STATIC")
    ti = result.index(TERSENESS_BLOCK)
    ui = result.index(UNTRUSTED_CONTENT_NOTICE)
    ci = result.index(CONFIDENCE_REPORTING_BLOCK)
    assert si < ti < ui < ci


def test_assemble_worker_prompt_terse_false_ordering() -> None:
    result = assemble_worker_prompt("STATIC", "r", terse=False)
    assert (
        result.index("STATIC")
        < result.index(UNTRUSTED_CONTENT_NOTICE)
        < result.index(CONFIDENCE_REPORTING_BLOCK)
    )
    assert TERSENESS_BLOCK not in result


def test_assemble_worker_prompt_substitutes_role() -> None:
    result = assemble_worker_prompt("Role: {role} here.", "deep_diver")
    assert "Role: deep_diver here." in result


def test_writes_report_defaults_true() -> None:
    assert _team().writes_report is True


def test_run_saves_report_and_adds_artifact_ref(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _team(name="test_team")
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_SYNTH_PROMPT): "final synthesis text",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    report_file = workflow_mod.report_path(ws, "inv_001")
    assert report_file.read_text(encoding="utf-8") == "final synthesis text"
    assert handover.artifacts_produced == [
        ArtifactRef(path="invocations/inv_001/report.md", description="Full test team report")
    ]


def test_run_survives_report_write_oserror(
    monkeypatch: pytest.MonkeyPatch,
    ws_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _make_ws(ws_root)
    team = _team(name="test_team")
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_SYNTH_PROMPT): "final synthesis text",
            "default": "worker output",
        },
    )

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(workflow_mod, "save_invocation_report", boom)
    with caplog.at_level("WARNING", logger="alan.teams._orchestrated"):
        handover = seed_and_run(team, ws, None, lambda _e: None)
    assert not workflow_mod.report_path(ws, "inv_001").exists()
    assert handover.artifacts_produced == []
    assert handover.full_synthesis == "final synthesis text"
    assert handover.from_team == "test_team"
    assert any(
        "failed to save invocation report" in r.message for r in caplog.records
    )


def test_run_skips_report_when_writes_report_false(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    team = _team(name="test_team", writes_report=False)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_SYNTH_PROMPT): "final synthesis text",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert not workflow_mod.report_path(ws, "inv_001").exists()
    assert handover.artifacts_produced == []


@pytest.mark.parametrize(
    ("name", "expected_description"),
    [
        ("research", "Full research report"),
        ("opportunity_scout", "Full opportunity scout report"),
        ("brainstorming", "Full brainstorming report"),
    ],
)
def test_run_report_artifact_description_humanizes_team_name(
    monkeypatch: pytest.MonkeyPatch,
    ws_root: Path,
    name: str,
    expected_description: str,
) -> None:
    ws = _make_ws(ws_root)
    team = _team(name=name)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(_ORCH_PROMPT): _dispatch_payload(["alpha"]),
            _sys(_SYNTH_PROMPT): "summary",
            "default": "worker output",
        },
    )
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.artifacts_produced[0].description == expected_description
