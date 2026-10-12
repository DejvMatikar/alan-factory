from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import TEAMS
from alan.teams import _base as base_mod
from alan.teams import opportunity_scout as scout_mod
from alan.teams._orchestrated import SYNTHESIZER_ROLE, VERIFIER_ROLE, OrchestratedTeam
from alan.workflow import load_node_config, load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run

_PROMPTS = {
    "orchestrator": scout_mod._ORCH_PROMPT,
    "worker": scout_mod._WORKER_PROMPT_TEMPLATE,
    "synthesizer": scout_mod._SYNTH_PROMPT,
    "verifier": scout_mod._VERIFIER_PROMPT,
}


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
    return create("Opportunity", "Is SaaS X viable?", root=ws_root)


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


def test_registry_opportunity_scout_is_orchestrated_team() -> None:
    assert isinstance(TEAMS["opportunity_scout"], OrchestratedTeam)
    assert TEAMS["opportunity_scout"].name == "opportunity_scout"


def test_opportunity_scout_has_verifier_prompt() -> None:
    assert scout_mod.TEAM.verifier_prompt == scout_mod._VERIFIER_PROMPT
    assert scout_mod._VERIFIER_PROMPT is not None


def test_run_produces_handover(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    from alan.handover import Handover

    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(scout_mod._ORCH_PROMPT): _dispatch_payload(["risk_analyst"]),
            _sys(scout_mod._VERIFIER_PROMPT): "verifier output",
            _sys(scout_mod._SYNTH_PROMPT): "GO — all filters cleared",
            "default": "GO verdict",
        },
    )
    handover = seed_and_run(scout_mod.TEAM, ws, None, lambda _e: None)
    assert isinstance(handover, Handover)
    assert handover.from_team == "opportunity_scout"
    assert "GO" in handover.full_synthesis


def test_run_includes_verifier_node(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(scout_mod._ORCH_PROMPT): _dispatch_payload(
                ["risk_analyst", "distribution_analyst"]
            ),
            _sys(scout_mod._VERIFIER_PROMPT): "verifier output",
            _sys(scout_mod._SYNTH_PROMPT): "consolidated",
            "default": "verdict",
        },
    )
    seed_and_run(scout_mod.TEAM, ws, None, lambda _e: None)
    doc = load_workflow(workflow_path(ws, "inv_001"))
    node_ids = {t.id for t in doc.tasks}
    assert VERIFIER_ROLE in node_ids
    synth = next(t for t in doc.tasks if t.id == SYNTHESIZER_ROLE)
    assert VERIFIER_ROLE in synth.depends_on


def test_synth_prompt_references_verifier() -> None:
    assert "verifier" in scout_mod._SYNTH_PROMPT.lower()


def test_scout_worker_node_config_grants_web_tools(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = _make_ws(ws_root)
    _patch_stream(
        monkeypatch,
        {
            _turn_budgeted(scout_mod._ORCH_PROMPT): _dispatch_payload(["risk_analyst"]),
            _sys(scout_mod._VERIFIER_PROMPT): "verifier output",
            _sys(scout_mod._SYNTH_PROMPT): "consolidated",
            "default": "verdict",
        },
    )
    seed_and_run(scout_mod.TEAM, ws, None, lambda _e: None)
    worker_cfg = load_node_config(ws, "inv_001", "risk_analyst")
    assert worker_cfg is not None
    assert "WebSearch" in worker_cfg["allowed_tools"]
    assert "WebFetch" in worker_cfg["allowed_tools"]


@pytest.mark.parametrize("text", list(_PROMPTS.values()), ids=list(_PROMPTS))
def test_prompt_has_no_copycat_wording_and_names_replicability_analyst(text: str) -> None:
    assert "copycat" not in text.lower()
    assert "replicability_analyst" in text


def test_orchestrator_role_list_names_replicability_analyst_for_filter_three() -> None:
    assert "- replicability_analyst: owns Filter 3" in scout_mod._ORCH_PROMPT


def test_filter_three_is_labelled_replicability_and_owned_by_replicability_analyst() -> None:
    expected = "3. Replicability (owned by replicability_analyst): NO-GO if the "
    assert expected in scout_mod._FILTER_RULES


def test_filter_three_criterion_sentence_is_unchanged() -> None:
    criterion = (
        "NO-GO if the core value proposition requires proprietary data, patents, "
        "or network effects that a small team cannot replicate within 12 months."
    )
    assert criterion in scout_mod._FILTER_RULES


def test_filter_four_names_replicability_analyst_as_co_owner() -> None:
    expected = "4. Complexity Justification (risk_analyst + replicability_analyst): "
    assert expected in scout_mod._FILTER_RULES


def test_perspective_catalog_has_no_judge_entry() -> None:
    assert "judge" not in scout_mod._PERSPECTIVE_CATALOG
    assert "judge" not in scout_mod._ORCH_PROMPT


def test_orch_prompt_has_no_judge_instruction() -> None:
    assert "Always include" not in scout_mod._ORCH_PROMPT
    assert "'judge'" not in scout_mod._ORCH_PROMPT


def test_verifier_prompt_ends_with_machine_parseable_tail() -> None:
    text = scout_mod._VERIFIER_PROMPT
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
    assert "Coverage-union rule" in scout_mod._ORCH_PROMPT
    assert "SCOPE" in scout_mod._COVERAGE_UNION_RULE
    assert scout_mod._COVERAGE_UNION_RULE in scout_mod._ORCH_PROMPT


def test_ui_debt_lens_recognises_ui_debt_material() -> None:
    text = scout_mod._UI_DEBT_LENS
    assert "design-system drift" in text
    assert "UX friction" in text
    assert "ratchet" in text
    assert "ui_debt_analyst" in text


def test_ui_debt_lens_judges_by_ui_criteria_not_business_filters() -> None:
    text = scout_mod._UI_DEBT_LENS
    assert "severity" in text
    assert "effort" in text
    assert "reuse" in text
    assert "NEVER by the four business filters" in text
    assert "critical/high/medium/low" in text
    assert "trivial/small/medium/large" in text


def test_ui_debt_lens_recommends_ui_review_follow_up_only() -> None:
    text = scout_mod._UI_DEBT_LENS
    assert "recommends dispatching a dedicated ui_review run" in text
    assert "it recommends only, it never triggers that run" in text


def test_ui_debt_lens_bounds_to_one_candidate() -> None:
    assert "at most one such agent" in scout_mod._UI_DEBT_LENS


def test_ui_debt_lens_respects_declined_convention() -> None:
    text = scout_mod._UI_DEBT_LENS
    assert "Declined UI candidates" in text
    assert "human-curated" in text
    assert "never re-surface a candidate listed there" in text
    assert "dead-ideas" not in text


def test_ui_debt_lens_exempt_from_coverage_union() -> None:
    assert "exempt from the coverage-union rule" in scout_mod._UI_DEBT_LENS


def test_orch_prompt_includes_ui_debt_lens() -> None:
    assert scout_mod._UI_DEBT_LENS in scout_mod._ORCH_PROMPT


def test_worker_template_includes_ui_debt_lens() -> None:
    assert scout_mod._UI_DEBT_LENS in scout_mod._WORKER_PROMPT_TEMPLATE
    assert "ui_debt_analyst" in scout_mod._WORKER_PROMPT_TEMPLATE


def test_synth_prompt_has_ui_debt_candidate_section() -> None:
    text = scout_mod._SYNTH_PROMPT
    assert "## UI-debt candidate" in text
    assert "ui_debt_analyst" in text
    assert "ui_review" in text
    assert "never invent one" in text


def test_verifier_prompt_excludes_ui_debt_from_scoring() -> None:
    text = scout_mod._VERIFIER_PROMPT
    assert scout_mod._UI_DEBT_VERIFIER_EXCLUSION in text
    assert "exclude its output entirely from the CONFIDENCE and COMPLETENESS" in text
    assert "COMPLETENESS as 1.0" in text


def test_verifier_prompt_ui_debt_exclusion_precedes_tail_and_tail_unchanged() -> None:
    text = scout_mod._VERIFIER_PROMPT
    exclusion_idx = text.index(scout_mod._UI_DEBT_VERIFIER_EXCLUSION)
    gaps_match = re.search(r"(?m)^GAPS:", text)
    assert gaps_match is not None
    assert exclusion_idx < gaps_match.start()
    tail_lines = text[gaps_match.start() :].splitlines()
    assert len(tail_lines) == 3
    assert tail_lines[0].startswith("GAPS:")
    assert tail_lines[1].startswith("CONFIDENCE:")
    assert tail_lines[2].startswith("COMPLETENESS:")
    assert text.endswith(tail_lines[2])
