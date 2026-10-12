from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.compression import DEFAULT_MAX_CHARS
from alan.engine_config import default_engine_config
from alan.teams import TEAMS
from alan.teams import _base as base_mod
from alan.teams import ui_review as ui_review_mod
from alan.teams._base import render_allowed_tools
from alan.teams._orchestrated import OrchestratedTeam
from alan.workflow import load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run

_VERDICT_VOCAB = frozenset({"APPROVE", "CHANGES_REQUESTED", "GO", "NO-GO", "pass", "fail"})


def _stub_prompt(label: str) -> str:
    return (
        f"SCOPE: {label} narrow scope\n"
        f"COVERAGE: only {label}, sibling roles cover others\n"
        f"SUCCESS: when {label} delivers concrete findings with evidence\n"
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
    return create("UI Review", "Review the existing web UI", root=ws_root)


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


def test_registry_ui_review_is_orchestrated_team() -> None:
    assert isinstance(TEAMS["ui_review"], OrchestratedTeam)
    assert TEAMS["ui_review"].name == "ui_review"


def test_ui_review_has_verifier_prompt() -> None:
    assert ui_review_mod.TEAM.verifier_prompt == ui_review_mod._VERIFIER_PROMPT
    assert ui_review_mod._VERIFIER_PROMPT is not None


def test_ui_review_never_writes_a_report() -> None:
    assert ui_review_mod.TEAM.writes_report is False


def test_run_preserves_long_synthesis_with_findings_table_and_briefs(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    rows = "\n".join(
        f"| Finding {i} | ux_reviewer | medium | small | Component.tsx:{i} |"
        for i in range(1, 400)
    )
    long_synth = (
        "## Findings Inventory\n\n"
        "| Finding | Perspective | Severity | Effort | Evidence |\n"
        "|---|---|---|---|---|\n"
        f"{rows}\n\n"
        "## Proposed Briefs\n\n"
        "### Brief 1\n\nGoal: tidy up spacing drift.\n"
    )
    assert len(long_synth) > DEFAULT_MAX_CHARS
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(ui_review_mod._ORCH_PROMPT): _dispatch_payload(
                ["design_system_reviewer", "ux_reviewer"]
            ),
            _sys(ui_review_mod._VERIFIER_PROMPT): "verifier output",
            _sys(ui_review_mod._SYNTH_PROMPT): long_synth,
            "default": "worker findings",
        },
    )
    handover = seed_and_run(ui_review_mod.TEAM, ws, None, lambda _e: None)
    assert "## Findings Inventory" in handover.full_synthesis
    assert "## Proposed Briefs" in handover.full_synthesis
    assert len(handover.full_synthesis) >= 5000
    assert handover.full_synthesis == long_synth


def test_run_selects_between_two_and_four_perspectives(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    roles = ["ux_reviewer", "accessibility_reviewer", "consistency_reviewer"]
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(ui_review_mod._ORCH_PROMPT): _dispatch_payload(roles),
            _sys(ui_review_mod._VERIFIER_PROMPT): "verifier output",
            _sys(ui_review_mod._SYNTH_PROMPT): (
                "## Findings Inventory\n\n## Proposed Briefs\n"
            ),
            "default": "worker findings",
        },
    )
    seed_and_run(ui_review_mod.TEAM, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    dispatched = {t.id for t in doc.tasks if t.id in ui_review_mod.PERSPECTIVE_ROLES}
    assert 2 <= len(dispatched) <= 4
    assert dispatched <= set(ui_review_mod.PERSPECTIVE_ROLES)


def test_run_uses_base_tools_only() -> None:
    cfg = default_engine_config()
    assert "ui_review" not in cfg.team_tools
    rendered = render_allowed_tools(cfg, "ui_review")
    for forbidden in ("Edit", "Write", "NotebookEdit"):
        assert forbidden not in rendered


def test_run_defaults_grounding_to_accessibility() -> None:
    grounding_idx = ui_review_mod._ORCH_PROMPT.index("GROUNDING")
    block = ui_review_mod._ORCH_PROMPT[grounding_idx:]
    assert "accessibility_reviewer if it is among the selected roles" in block


def test_grounding_falls_to_first_selected_when_accessibility_absent() -> None:
    grounding_idx = ui_review_mod._ORCH_PROMPT.index("GROUNDING")
    block = ui_review_mod._ORCH_PROMPT[grounding_idx:].lower()
    assert "first selected" in block
    assert "catalogue order" in block


def test_severity_effort_vocab_disjoint_from_verdict_vocab() -> None:
    labels = set(ui_review_mod.SEVERITY_LABELS) | set(ui_review_mod.EFFORT_LABELS)
    assert labels.isdisjoint(_VERDICT_VOCAB)


def test_run_forwards_nothing_and_carries_no_decisions(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(ui_review_mod._ORCH_PROMPT): _dispatch_payload(
                ["design_system_reviewer", "ux_reviewer"]
            ),
            _sys(ui_review_mod._VERIFIER_PROMPT): "verifier output",
            _sys(ui_review_mod._SYNTH_PROMPT): (
                "## Findings Inventory\n\n## Proposed Briefs\n"
            ),
            "default": "worker findings",
        },
    )
    handover = seed_and_run(ui_review_mod.TEAM, ws, None, lambda _e: None)
    assert ui_review_mod.TEAM.to_team_suggestions == ()
    assert handover.decisions == []


def test_synthesizer_prompt_forbids_length_limit() -> None:
    assert "NO LENGTH LIMIT" in ui_review_mod._SYNTH_PROMPT
    assert re.search(r"<=\s*\d+\s*chars", ui_review_mod._SYNTH_PROMPT) is None
