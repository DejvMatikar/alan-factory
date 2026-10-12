from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import TEAMS
from alan.teams import _base as base_mod
from alan.teams import brainstorming as brainstorming_mod
from alan.teams._base import PLANNING_SCREEN_CAPTURE_INSTRUCTION, PLANNING_UX_LENS
from alan.teams._orchestrated import OrchestratedTeam, assemble_worker_prompt
from alan.workflow import load_node_config
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


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Brainstorm UI", "Should we add a new modal?", root=ws_root)


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


def test_registry_brainstorming_is_orchestrated_team() -> None:
    assert isinstance(TEAMS["brainstorming"], OrchestratedTeam)
    assert TEAMS["brainstorming"].name == "brainstorming"


def test_worker_prompt_template_contains_planning_ux_lens() -> None:
    assert PLANNING_UX_LENS in brainstorming_mod._WORKER_PROMPT_TEMPLATE


def test_worker_prompt_template_is_format_safe_with_planning_ux_lens() -> None:
    rendered = brainstorming_mod._WORKER_PROMPT_TEMPLATE.format(role="user_advocate")
    assert "user_advocate" in rendered
    assert PLANNING_UX_LENS in rendered


def test_assemble_worker_prompt_carries_planning_ux_lens_to_worker() -> None:
    assembled = assemble_worker_prompt(
        brainstorming_mod._WORKER_PROMPT_TEMPLATE, "user_advocate"
    )
    assert PLANNING_UX_LENS in assembled


def test_orch_prompt_instructs_design_doc_pointer_for_ui_workers() -> None:
    assert "docs/DESIGN_SYSTEM.md" in brainstorming_mod._ORCH_PROMPT
    assert "alan/web/" in brainstorming_mod._ORCH_PROMPT


def test_orch_prompt_does_not_duplicate_the_full_lens_body() -> None:
    assert PLANNING_UX_LENS not in brainstorming_mod._ORCH_PROMPT


def test_worker_node_system_prompt_persists_planning_ux_lens(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(brainstorming_mod._ORCH_PROMPT): _dispatch_payload(
                ["user_advocate"]
            ),
            "default": "worker output",
        },
    )
    seed_and_run(TEAMS["brainstorming"], ws, None, lambda _e: None)
    worker_cfg = load_node_config(ws, "inv_001", "user_advocate")
    assert worker_cfg is not None
    assert PLANNING_UX_LENS in worker_cfg["system_prompt"]


def test_worker_prompt_template_contains_planning_screen_capture_instruction() -> None:
    assert (
        PLANNING_SCREEN_CAPTURE_INSTRUCTION in brainstorming_mod._WORKER_PROMPT_TEMPLATE
    )


def test_worker_prompt_template_is_format_safe_with_capture_instruction() -> None:
    rendered = brainstorming_mod._WORKER_PROMPT_TEMPLATE.format(role="user_advocate")
    assert "user_advocate" in rendered
    assert PLANNING_SCREEN_CAPTURE_INSTRUCTION in rendered


def test_worker_node_system_prompt_persists_planning_screen_capture_instruction(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(brainstorming_mod._ORCH_PROMPT): _dispatch_payload(
                ["user_advocate"]
            ),
            "default": "worker output",
        },
    )
    seed_and_run(TEAMS["brainstorming"], ws, None, lambda _e: None)
    worker_cfg = load_node_config(ws, "inv_001", "user_advocate")
    assert worker_cfg is not None
    assert PLANNING_SCREEN_CAPTURE_INSTRUCTION in worker_cfg["system_prompt"]
