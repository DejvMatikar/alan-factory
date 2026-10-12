from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import _base as base_mod
from alan.teams import brainstorming as brainstorming_mod
from alan.teams import opportunity_scout as scout_mod
from alan.teams import research as research_mod
from alan.teams._orchestrated import (
    ORCHESTRATOR_ROLE,
    SYNTHESIZER_ROLE,
    VERIFIER_ROLE,
    OrchestratedTeam,
)
from alan.workflow import load_node_config
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run

_SOURCE_FETCH_TOOL = "Bash(python3 -m alan.source_fetch:*)"
_ENTRY_POINT_MARKER = "python3 -m alan.source_fetch"
_INV_ID = "inv_001"
_WORKER_NODE_ID = "alpha"


def _stub_prompt(label: str) -> str:
    return (
        f"SCOPE: {label} narrow scope\n"
        f"COVERAGE: only {label}, sibling roles cover others\n"
        f"SUCCESS: when {label} delivers concrete numbers\n"
        f"CONTEXT: {label} context here"
    )


def _dispatch_payload(roles: list[str]) -> str:
    return json.dumps({"agents": [{"role": r, "prompt": _stub_prompt(r)} for r in roles]})


def _turn_budgeted(prompt: str) -> str:
    return f"{prompt}\n\nTurn budget for this run: {base_mod.MAX_TURNS}"


def _sys_with_notice(prompt: str) -> str:
    return _turn_budgeted(
        base_mod.compose_system_prompt(prompt, dynamic_block=base_mod.UNTRUSTED_CONTENT_NOTICE)
    )


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _make_ws(ws_root: Path) -> Workspace:
    return create("Task", "desc", root=ws_root)


def _patch_stream(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, str]
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
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return calls


def _run_team(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, team: OrchestratedTeam
) -> tuple[Workspace, list[tuple[str, str]]]:
    ws = _make_ws(ws_root)
    responses = {
        _turn_budgeted(team.orchestrator_prompt): _dispatch_payload([_WORKER_NODE_ID]),
        _sys_with_notice(team.synthesizer_prompt): "summary",
        "default": "worker out",
    }
    if team.verifier_prompt is not None:
        responses[_sys_with_notice(team.verifier_prompt)] = (
            "GAPS: none\nCONFIDENCE: 0.9\nCOMPLETENESS: 0.9"
        )
    calls = _patch_stream(monkeypatch, responses)
    seed_and_run(team, ws, None, lambda _e: None)
    return ws, calls


def _non_worker_node_ids(team: OrchestratedTeam) -> tuple[str, ...]:
    if team.verifier_prompt is None:
        return (ORCHESTRATOR_ROLE, SYNTHESIZER_ROLE)
    return (ORCHESTRATOR_ROLE, VERIFIER_ROLE, SYNTHESIZER_ROLE)


@pytest.mark.parametrize(
    "team",
    [research_mod.TEAM, brainstorming_mod.TEAM, scout_mod.TEAM],
    ids=lambda t: t.name,
)
def test_worker_node_grants_source_fetch(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, team: OrchestratedTeam
) -> None:
    ws, _calls = _run_team(monkeypatch, ws_root, team)
    cfg = load_node_config(ws, _INV_ID, _WORKER_NODE_ID)
    assert cfg is not None
    assert _SOURCE_FETCH_TOOL in cfg["allowed_tools"]


@pytest.mark.parametrize(
    "team",
    [research_mod.TEAM, brainstorming_mod.TEAM, scout_mod.TEAM],
    ids=lambda t: t.name,
)
def test_non_worker_nodes_omit_source_fetch(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, team: OrchestratedTeam
) -> None:
    ws, _calls = _run_team(monkeypatch, ws_root, team)
    for node_id in _non_worker_node_ids(team):
        cfg = load_node_config(ws, _INV_ID, node_id)
        assert cfg is not None
        assert _SOURCE_FETCH_TOOL not in cfg["allowed_tools"]


@pytest.mark.parametrize(
    "team",
    [research_mod.TEAM, brainstorming_mod.TEAM, scout_mod.TEAM],
    ids=lambda t: t.name,
)
def test_worker_prompt_names_entry_point_and_keeps_untrusted_content_notice(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, team: OrchestratedTeam
) -> None:
    _ws, calls = _run_team(monkeypatch, ws_root, team)
    matching = [prompt for prompt, _user_text in calls if _ENTRY_POINT_MARKER in prompt]
    assert matching
    for prompt in matching:
        assert base_mod.UNTRUSTED_CONTENT_NOTICE in prompt
