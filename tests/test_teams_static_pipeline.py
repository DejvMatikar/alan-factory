from __future__ import annotations

import json
import re
import threading
import time as _time_mod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Handover
from alan.teams import _base as base
from alan.teams import _static_pipeline as sp
from alan.teams._base import TeamEvent
from alan.teams._orchestrated import CONFIDENCE_REPORTING_BLOCK
from alan.teams._static_pipeline import (
    _ALREADY_SAFE_PREFIXES,
    _FREETEXT_KEYS,
    Role,
    StaticPipelineParseError,
    StaticPipelineTeam,
    _needs_quoting,
    _repair_freetext_values,
    assemble_role_system_prompt,
    build_role_user_text,
    load_decisions_yaml_block,
    parse_decisions_block,
)
from alan.teams.implementation_plan import _parse_test_scenarios
from alan.teams.role_output_fence import role_fence
from alan.workflow import PendingTaskSpec, load_workflow, workflow_path
from alan.workspace import Workspace, create
from tests._helpers import make_handover, seed_and_run


def _setup_success_node(ws: Workspace, team_name: str, role_name: str) -> str:
    specs = [PendingTaskSpec(id=role_name, role=role_name, model="sonnet")]
    inv_id = workflow_mod.seed_pending_workflow(ws, team=team_name, tasks=specs)
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(role_name, role=role_name, model="sonnet", depends_on=[])
    graph.complete_node(role_name)
    workflow_mod.save_workflow(ws, graph.doc)
    workflow_mod.save_node_result(
        ws, inv_id, role_name, role=role_name, model="sonnet", result_text="cached"
    )
    return inv_id


def _patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    captured: list[tuple[str, str, str]] | None = None,
) -> None:
    counter = {"n": 0}

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
        text = f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            with stream_path.open("a", encoding="utf-8") as h:
                h.write(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def test_assemble_role_system_prompt_excludes_confidence_block() -> None:
    out = assemble_role_system_prompt("ROLE PROMPT")
    assert out.startswith("ROLE PROMPT")
    assert CONFIDENCE_REPORTING_BLOCK not in out


def test_assemble_role_system_prompt_includes_untrusted_content_notice() -> None:
    from alan.teams._base import UNTRUSTED_CONTENT_NOTICE

    out = assemble_role_system_prompt("ROLE PROMPT")
    assert UNTRUSTED_CONTENT_NOTICE in out
    assert out.index("ROLE PROMPT") < out.index(UNTRUSTED_CONTENT_NOTICE)


def test_assemble_worker_prompt_for_non_scout_team_excludes_scout_filter_rules() -> None:
    from alan.teams._orchestrated import assemble_worker_prompt
    from alan.teams.opportunity_scout import _FILTER_RULES
    from alan.teams.research import _WORKER_PROMPT_TEMPLATE as _RESEARCH_WORKER_TEMPLATE

    out = assemble_worker_prompt(_RESEARCH_WORKER_TEMPLATE, "deep_diver")
    assert _FILTER_RULES not in out
    assert "Four-Filter Decision Rule" not in out


@pytest.mark.parametrize("role_name", ["diff_reviewer", "scenario_runner", "judge"])
def test_verification_role_prompts_carry_untrusted_content_notice(role_name: str) -> None:
    from alan.teams._base import UNTRUSTED_CONTENT_NOTICE
    from alan.teams.verification import ROLES as VERIFICATION_ROLES

    role = next(r for r in VERIFICATION_ROLES if r.name == role_name)
    assembled = assemble_role_system_prompt(role.system_prompt)
    assert UNTRUSTED_CONTENT_NOTICE in assembled


def test_build_role_user_text_includes_coverage() -> None:
    out = build_role_user_text("BASE", [], "COVERAGE TEXT")
    assert out.startswith("BASE")
    assert "## COVERAGE" in out
    assert "COVERAGE TEXT" in out
    assert "## CONTEXT" not in out


def test_build_role_user_text_includes_prior_context() -> None:
    out = build_role_user_text("BASE", [("a", "out-a"), ("b", "out-b")], "COVERAGE")
    nonce = re.search(r"nonce=([0-9a-f]{16})", out)
    assert nonce is not None
    assert "## CONTEXT" in out
    fence_a = "### a\n\n" + role_fence("a", "out-a", nonce.group(1))
    fence_b = "### b\n\n" + role_fence("b", "out-b", nonce.group(1))
    assert out.index("## CONTEXT") < out.index(fence_a) < out.index(fence_b)


def test_static_pipeline_single_role_pipeline_coverage_is_singleton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)

    team = StaticPipelineTeam(
        name="solo",
        roles=(Role("only", "ONLY PROMPT", "sonnet"),),
        to_team_suggestions=("implementation_plan",),
    )
    ws_root = tmp_path / "workspaces"
    ws = create("solo task", "desc", root=ws_root)
    handover = seed_and_run(team, ws, None, lambda _e: None)

    assert isinstance(handover, Handover)
    assert len(captured) == 1
    assert "You are the only role" in captured[0][1]


def test_static_pipeline_finalize_callback_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_streaming(monkeypatch)
    finalized: list[tuple[str, str]] = []

    def finalize_fn(ws: Workspace, output: str) -> None:
        finalized.append((ws.task_id, output))

    team = StaticPipelineTeam(
        name="solo",
        roles=(Role("only", "X", "sonnet"),),
        to_team_suggestions=(),
        finalize=finalize_fn,
    )
    ws_root = tmp_path / "workspaces"
    ws = create("with finalize", "d", root=ws_root)
    seed_and_run(team, ws, None, lambda _e: None)
    assert len(finalized) == 1
    assert finalized[0][0] == ws.task_id
    assert finalized[0][1] == "out-1"


def test_static_pipeline_multi_role_coverage_lists_siblings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = StaticPipelineTeam(
        name="trio",
        roles=(
            Role("a", "A", "sonnet"),
            Role("b", "B", "sonnet"),
            Role("c", "C", "opus"),
        ),
        to_team_suggestions=(),
    )
    ws_root = tmp_path / "workspaces"
    ws = create("trio task", "d", root=ws_root)
    seed_and_run(team, ws, None, lambda _e: None)
    a_user = captured[0][1]
    b_user = captured[1][1]
    c_user = captured[2][1]
    assert "Other roles in this pipeline: b, c" in a_user
    assert "Other roles in this pipeline: a, c" in b_user
    assert "Other roles in this pipeline: a, b" in c_user
    assert "## CONTEXT" not in a_user
    assert "## CONTEXT" in b_user
    assert "## CONTEXT" in c_user


def test_static_pipeline_rejects_empty_roles_tuple() -> None:
    with pytest.raises(ValueError, match="requires at least one role"):
        StaticPipelineTeam(
            name="empty", roles=(), to_team_suggestions=()
        )


def test_static_pipeline_inlines_prior_handover_first_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams._base import build_forward_handover

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = StaticPipelineTeam(
        name="solo",
        roles=(Role("only", "ROLE PROMPT", "sonnet"),),
        to_team_suggestions=(),
    )
    ws_root = tmp_path / "workspaces"
    ws = create("inline prior", "d", root=ws_root)
    prior = build_forward_handover(
        ws,
        from_team="brainstorming",
        to_team_suggestions=["solo"],
        summary="brainstorm-output-text",
        prior=None,
    )
    seed_and_run(team, ws, prior, lambda _e: None)
    assert "Prior handover from brainstorming" in captured[0][1]
    assert "brainstorm-output-text" in captured[0][1]


def test_static_pipeline_emits_node_events_for_each_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_streaming(monkeypatch)
    team = StaticPipelineTeam(
        name="pair",
        roles=(Role("first", "F", "sonnet"), Role("second", "S", "opus")),
        to_team_suggestions=(),
    )
    ws_root = tmp_path / "workspaces"
    ws = create("pair task", "d", root=ws_root)
    events: list[TeamEvent] = []
    seed_and_run(team, ws, None, events.append)
    started = [e.payload["role"] for e in events if e.kind == "node_started"]
    completed = [
        e.payload["role"] for e in events if e.kind == "node_completed"
    ]
    assert started == ["first", "second"]
    assert completed == ["first", "second"]


def test_role_user_prefix_default_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = StaticPipelineTeam(
        name="solo",
        roles=(Role("only", "X", "sonnet"),),
        to_team_suggestions=(),
    )
    ws = create("no prefix", "d", root=tmp_path / "ws")
    seed_and_run(team, ws, None, lambda _e: None)
    assert "## Scenario execution results" not in captured[0][1]


def test_role_user_prefix_hook_prepends_to_user_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)

    @dataclass(frozen=True)
    class PrefixingTeam(StaticPipelineTeam):
        name: str = "prefixer"
        roles: tuple[Role, ...] = (Role("first", "F", "sonnet"),)
        to_team_suggestions: tuple[str, ...] = ()

        def _role_user_prefix(self, role_name: str) -> str:
            if role_name == "first":
                return "## PREFIX INJECTED\n\n"
            return ""

    team = PrefixingTeam()
    ws = create("prefix test", "d", root=tmp_path / "ws2")
    seed_and_run(team, ws, None, lambda _e: None)
    assert captured[0][1].startswith("## PREFIX INJECTED\n\n")


def test_role_user_prefix_selective_per_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)

    @dataclass(frozen=True)
    class SelectiveTeam(StaticPipelineTeam):
        name: str = "selective"
        roles: tuple[Role, ...] = (
            Role("first", "F", "sonnet"),
            Role("second", "S", "sonnet"),
        )
        to_team_suggestions: tuple[str, ...] = ()

        def _role_user_prefix(self, role_name: str) -> str:
            if role_name == "second":
                return "## SECOND ONLY PREFIX\n\n"
            return ""

    team = SelectiveTeam()
    ws = create("selective test", "d", root=tmp_path / "ws3")
    seed_and_run(team, ws, None, lambda _e: None)
    assert "## SECOND ONLY PREFIX" not in captured[0][1]
    assert captured[1][1].startswith("## SECOND ONLY PREFIX\n\n")


def test_static_pipeline_terminal_flag_emits_terminal_handover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_streaming(monkeypatch)
    team = StaticPipelineTeam(
        name="maintenance",
        roles=(Role("solo", "S", "sonnet"),),
        to_team_suggestions=(),
        terminal=True,
    )
    ws = create("terminal test", "d", root=tmp_path / "ws4")
    handover = seed_and_run(team, ws, None, lambda _e: None)
    assert handover.handover_type == "terminal"
    assert handover.to_team_suggestions == []


def test_static_pipeline_default_role_overrides_resolves_default_engine_tools(
    tmp_path: Path,
) -> None:
    from alan.teams._base import BASE_ALLOWED_TOOLS, WorkerOverrides  # noqa: PLC0415

    team = StaticPipelineTeam(
        name="architecture", roles=(Role("only", "X", "sonnet"),), to_team_suggestions=(),
    )
    ws = create("default-overrides", "d", root=tmp_path / "ws")
    overrides = team._role_overrides("only", ws)  # noqa: SLF001
    assert isinstance(overrides, WorkerOverrides)
    assert overrides.allowed_tools is not None
    assert overrides.allowed_tools[: len(BASE_ALLOWED_TOOLS)] == list(BASE_ALLOWED_TOOLS)


def test_static_pipeline_default_role_overrides_resolves_nonpython_engine_tools(
    tmp_path: Path,
) -> None:
    from alan import workspace as workspace_mod  # noqa: PLC0415
    from alan.engine_config import EngineConfig, Posture, RepoIdentity  # noqa: PLC0415
    from alan.teams._base import WorkerOverrides  # noqa: PLC0415
    from alan.workspace import EngineSnapshot  # noqa: PLC0415

    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", "Bash(dotnet:*)"],
        posture=Posture.autonomous,
    )
    team = StaticPipelineTeam(
        name="architecture", roles=(Role("only", "X", "sonnet"),), to_team_suggestions=(),
    )
    ws = create("pinned-overrides", "d", root=tmp_path / "ws")
    ws = workspace_mod.stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    overrides = team._role_overrides("only", ws)  # noqa: SLF001
    assert isinstance(overrides, WorkerOverrides)
    assert overrides.allowed_tools is not None
    assert overrides.allowed_tools[:2] == ["Read", "Bash(dotnet:*)"]


def test_assemble_role_system_prompt_default_terse_true_includes_terseness() -> None:
    from alan.teams._base import TERSENESS_BLOCK

    out = assemble_role_system_prompt("ROLE PROMPT")
    assert TERSENESS_BLOCK in out
    assert CONFIDENCE_REPORTING_BLOCK not in out
    assert out.index("ROLE PROMPT") < out.index(TERSENESS_BLOCK)


def test_assemble_role_system_prompt_terse_false_excludes_terseness() -> None:
    from alan.teams._base import TERSENESS_BLOCK, UNTRUSTED_CONTENT_NOTICE

    out = assemble_role_system_prompt("ROLE PROMPT", terse=False)
    assert TERSENESS_BLOCK not in out
    assert CONFIDENCE_REPORTING_BLOCK not in out
    assert out.index("ROLE PROMPT") < out.index(UNTRUSTED_CONTENT_NOTICE)


def test_static_pipeline_role_terse_default_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams._base import TERSENESS_BLOCK

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)
    team = StaticPipelineTeam(
        name="solo",
        roles=(Role("only", "ONLY PROMPT", "sonnet"),),
        to_team_suggestions=(),
    )
    ws = create("terse default", "d", root=tmp_path / "ws_t")
    seed_and_run(team, ws, None, lambda _e: None)
    assert TERSENESS_BLOCK in captured[0][0]


def test_static_pipeline_role_terse_override_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import dataclass

    from alan.teams._base import TERSENESS_BLOCK
    from alan.teams._static_pipeline import Role, StaticPipelineTeam

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)

    @dataclass(frozen=True)
    class TerseOffTeam(StaticPipelineTeam):
        name: str = "terseless"
        roles: tuple[Role, ...] = (Role("only", "ONLY PROMPT", "sonnet"),)
        to_team_suggestions: tuple[str, ...] = ()

        def _role_terse(self, role_name: str) -> bool:
            del role_name
            return False

    team = TerseOffTeam()
    ws = create("terse off", "d", root=tmp_path / "ws_to")
    seed_and_run(team, ws, None, lambda _e: None)
    assert TERSENESS_BLOCK not in captured[0][0]


def test_static_pipeline_revisit_uses_decisions_only_cross_team(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.handover import save_handover
    from alan.workspace import create, increment_revisit_count

    captured: list[tuple[str, str, str]] = []
    _patch_streaming(monkeypatch, captured=captured)

    ws_root = tmp_path / "workspaces"
    ws = create("pipeline-revisit", "d", root=ws_root)
    chain_id = f"handover_{ws.task_id}_chain"

    seed = make_handover(
        ws.task_id,
        "seed",
        to_team="plan-team",
        full_synthesis="synthesis absent on revisit",
        decision_statement="use pipeline approach",
    )
    save_handover(ws, seed)

    ws = increment_revisit_count(ws, "plan-team", chain_id)

    prior = make_handover(
        ws.task_id,
        "prior",
        to_team="plan-team",
        from_invocation_id="inv_002",
        iteration=2,
        full_synthesis="current thinking",
    )

    team = StaticPipelineTeam(
        name="plan-team",
        roles=(Role("analyst", "ANALYSIS", "sonnet"),),
        to_team_suggestions=(),
    )
    seed_and_run(team, ws, prior, lambda _e: None)

    user_text = captured[0][1]
    assert "Prior context for plan-team" in user_text
    assert "use pipeline approach" in user_text
    assert "synthesis absent on revisit" not in user_text


def test_parse_or_invalidate_returns_value_on_success(tmp_path: Path) -> None:
    ws = create("ok", "d", root=tmp_path / "ws")
    team = StaticPipelineTeam(
        name="t", roles=(Role("r", "P", "sonnet"),), to_team_suggestions=()
    )
    result = team._parse_or_invalidate(ws, "inv_001", lambda t: t.upper(), "hello")
    assert result == "HELLO"


def test_parse_or_invalidate_on_error_deletes_cache_and_fails_node(
    tmp_path: Path,
) -> None:
    ws = create("err", "d", root=tmp_path / "ws")
    team = StaticPipelineTeam(
        name="t", roles=(Role("r", "P", "sonnet"),), to_team_suggestions=()
    )
    inv_id = _setup_success_node(ws, "t", "r")

    def fail(text: str) -> str:
        raise StaticPipelineParseError("bad output")

    with pytest.raises(StaticPipelineParseError, match="bad output"):
        team._parse_or_invalidate(ws, inv_id, fail, "text")

    assert not workflow_mod.result_path(ws, inv_id, "r").exists()
    doc = load_workflow(workflow_path(ws, inv_id))
    assert doc.status == "failed"
    node = next(t for t in doc.tasks if t.id == "r")
    assert node.status == "failed"
    assert node.error == "bad output"


def test_parse_or_invalidate_on_error_invalidates_even_if_workflow_missing(
    tmp_path: Path,
) -> None:
    ws = create("nometa", "d", root=tmp_path / "ws")
    team = StaticPipelineTeam(
        name="t", roles=(Role("r", "P", "sonnet"),), to_team_suggestions=()
    )
    workflow_mod.save_node_result(
        ws, "inv_001", "r", role="r", model="sonnet", result_text="cached"
    )

    def fail(text: str) -> str:
        raise StaticPipelineParseError("bad")

    with pytest.raises(StaticPipelineParseError, match="bad"):
        team._parse_or_invalidate(ws, "inv_001", fail, "text")

    assert not workflow_mod.result_path(ws, "inv_001", "r").exists()


def test_freetext_keys_resolves_to_expected_set() -> None:
    assert _FREETEXT_KEYS == frozenset(
        {"id", "statement", "rationale", "description", "given", "when", "then"}
    )


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("confidence", "0.9: suspicious", False),
        ("test_ref", "tests/x.py: y", False),
        ("statement", "", False),
        ("statement", "   ", False),
        ("statement", '"already: quoted"', False),
        ("statement", "'already: quoted'", False),
        ("statement", "| block literal header", False),
        ("statement", "> folded scalar header", False),
        ("statement", "[a, b]", False),
        ("statement", "{a: b}", False),
        ("statement", "plain safe value", False),
        ("statement", "use sqlite: fast enough", True),
        ("rationale", "already covered:", True),
        ("then", "shows {status: ok}", True),
    ],
)
def test_needs_quoting_branches(key: str, value: str, expected: bool) -> None:
    assert _needs_quoting(key, value) is expected


def test_needs_quoting_safe_prefixes_cover_all_declared_markers() -> None:
    for marker in _ALREADY_SAFE_PREFIXES:
        assert _needs_quoting("statement", f"{marker}rest: of value") is False


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "statement: use sqlite: fast enough",
            'statement: "use sqlite: fast enough"',
        ),
        (
            "rationale: already covered:",
            'rationale: "already covered:"',
        ),
        (
            "then: shows {status: ok}",
            'then: "shows {status: ok}"',
        ),
    ],
)
def test_repair_freetext_values_fixes_shape_a_lines(body: str, expected: str) -> None:
    out = _repair_freetext_values(body)
    assert out == expected
    assert yaml.safe_load(out) is not None


def test_repair_freetext_values_preserves_list_item_prefix() -> None:
    body = "  - statement: use sqlite: for storage"
    out = _repair_freetext_values(body)
    assert out == '  - statement: "use sqlite: for storage"'
    assert yaml.safe_load(out) == [{"statement": "use sqlite: for storage"}]


@pytest.mark.parametrize("marker", ["|", ">"])
def test_repair_freetext_values_skips_block_scalar_header(marker: str) -> None:
    body = f"rationale: {marker}\n  freeform paragraph without a colon-key shape"
    assert _repair_freetext_values(body) == body


def test_repair_freetext_values_protects_block_scalar_continuation_lines() -> None:
    body = (
        "rationale: |\n"
        "  given: something happened: then this\n"
        "confidence: 0.9"
    )
    assert _repair_freetext_values(body) == body


def test_repair_freetext_values_protects_block_scalar_continuation_in_list_item() -> None:
    body = (
        "  - rationale: |\n"
        "      given: nested prose: with colon\n"
        "    confidence: 0.9"
    )
    assert _repair_freetext_values(body) == body


def test_repair_freetext_values_block_scalar_continuation_tolerates_blank_lines() -> None:
    body = "rationale: |\n\n  more prose: with a colon"
    assert _repair_freetext_values(body) == body


def test_repair_freetext_values_resumes_repair_after_block_scalar_ends() -> None:
    body = (
        "rationale: |\n"
        "  given: something happened: then this\n"
        "statement: use sqlite: fast enough"
    )
    expected = (
        "rationale: |\n"
        "  given: something happened: then this\n"
        'statement: "use sqlite: fast enough"'
    )
    assert _repair_freetext_values(body) == expected


def test_repair_freetext_values_escapes_embedded_quotes_and_backslashes() -> None:
    raw_value = 'shows "Bash: ls" and a \\ backslash'
    body = f"then: {raw_value}"
    out = _repair_freetext_values(body)
    escaped = raw_value.replace("\\", "\\\\").replace('"', '\\"')
    assert out == f'then: "{escaped}"'
    assert yaml.safe_load(out) == {"then": raw_value}


def test_repair_freetext_values_never_alters_non_freetext_or_path_lines() -> None:
    body = "confidence: 0.9: suspicious\nreversibility: medium: also weird\n  - alan/queue.py"
    assert _repair_freetext_values(body) == body


def test_repair_freetext_values_is_idempotent_on_already_safe_body() -> None:
    good = (
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "use sqlite for the queue"\n'
        '    rationale: "single-file, atomic writes already covered"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "files_to_edit:\n"
        "  - alan/queue.py"
    )
    assert _repair_freetext_values(good) == good


def test_load_decisions_yaml_block_first_parse_success_never_calls_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def spy(body: str) -> str:
        calls.append(body)
        return body

    monkeypatch.setattr(sp, "_repair_freetext_values", spy)
    synthesis = (
        "plan\n\n```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "ok"\n'
        "```"
    )
    result = load_decisions_yaml_block(synthesis)
    assert result["decisions"][0]["id"] == "d1"
    assert calls == []


def test_load_decisions_yaml_block_retry_still_fails_reraises_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair_calls: list[str] = []
    load_calls = {"n": 0}

    def spy_repair(body: str) -> str:
        repair_calls.append(body)
        return body

    def fake_safe_load(_text: str) -> object:
        load_calls["n"] += 1
        if load_calls["n"] == 1:
            raise yaml.YAMLError("ORIGINAL_ERROR_MARKER")
        raise yaml.YAMLError("REPAIRED_ERROR_MARKER")

    monkeypatch.setattr(sp, "_repair_freetext_values", spy_repair)
    monkeypatch.setattr(yaml, "safe_load", fake_safe_load)

    synthesis = "plan\n\n```yaml decisions.yaml\nstatement: irrelevant\n```"
    with pytest.raises(StaticPipelineParseError) as excinfo:
        load_decisions_yaml_block(synthesis)

    assert "ORIGINAL_ERROR_MARKER" in str(excinfo.value)
    assert "REPAIRED_ERROR_MARKER" not in str(excinfo.value)
    assert len(repair_calls) == 1


def test_load_decisions_yaml_block_repaired_non_mapping_falls_through_to_dict_check() -> (
    None
):
    synthesis = (
        "plan\n\n```yaml decisions.yaml\n"
        "- statement: use sqlite: for storage\n"
        "```"
    )
    with pytest.raises(StaticPipelineParseError, match="mapping with a"):
        load_decisions_yaml_block(synthesis)


def test_parse_decisions_block_recovers_shape_a_colon_space() -> None:
    synthesis = (
        "plan\n\n```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        "    statement: use sqlite: fast enough\n"
        '    rationale: "cheap"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "```"
    )
    decisions, _files = parse_decisions_block(synthesis)
    assert decisions[0].id == "d1"
    assert decisions[0].statement == "use sqlite: fast enough"


def test_parse_decisions_block_shape_a_recovery_covers_architecture_team_too() -> None:
    synthesis = (
        "plan\n\n```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: a1\n"
        "    statement: prefer sqlite: single-file storage\n"
        '    rationale: "simple"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "```"
    )
    decisions, _files = parse_decisions_block(synthesis)
    assert decisions[0].statement == "prefer sqlite: single-file storage"


def test_parse_test_scenarios_recovers_shape_a_input() -> None:
    synthesis = (
        "plan\n\n```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "ok"\n'
        '    rationale: "ok"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "test_scenarios:\n"
        "  - id: s1\n"
        '    description: "d"\n'
        '    given: "g"\n'
        '    when: "w"\n'
        "    then: shows summary: done\n"
        "```"
    )
    scenarios = _parse_test_scenarios(synthesis)
    assert scenarios[0].then == "shows summary: done"


_REPRESENTATIVE_PASSING_BODIES = [
    (
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "use option A"\n'
        '    rationale: "cheapest path forward"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "test_scenarios:\n"
        "  - id: s1\n"
        "    description: a thing happens\n"
        "    given: precondition\n"
        "    when: action\n"
        "    then: outcome"
    ),
    (
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "use sqlite for the queue"\n'
        '    rationale: "single-file, atomic writes already covered"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "files_to_edit:\n"
        "  - alan/queue.py\n"
        "  - tests/test_queue.py\n"
        "test_scenarios:\n"
        "  - id: enqueue_persists_across_restart\n"
        '    description: "Jobs enqueued before restart are claimable '
        'after restart."\n'
        '    given: "A job is enqueued and the alan service is restarted."\n'
        '    when: "uv run --no-active pytest '
        'tests/test_queue.py::test_enqueue_persists_across_restart"\n'
        '    then: "1 passed"'
    ),
    (
        "decisions:\n"
        "  - id: a1\n"
        '    statement: "prefer sqlite for state storage"\n'
        '    rationale: "single-file zero-server operational model"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "  - id: a2\n"
        '    statement: "keep the pipeline single-process"\n'
        '    rationale: "avoids distributed-systems complexity until '
        'proven necessary"\n'
        "    confidence: 0.85\n"
        "    reversibility: high"
    ),
]


@pytest.mark.parametrize("body", _REPRESENTATIVE_PASSING_BODIES)
def test_repair_regression_representative_passing_bodies_unchanged(
    body: str,
) -> None:
    assert _repair_freetext_values(body) == body
    assert yaml.safe_load(body) == yaml.safe_load(_repair_freetext_values(body))


def test_capture_observation_prose_never_leaks_into_decisions_or_files() -> None:
    synthesis = (
        "## Rendered-screen observation\n\n"
        "capture unavailable — reviewed from markup\n\n"
        "```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "use option A"\n'
        '    rationale: "cheapest path forward"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "files_to_edit:\n"
        "  - alan/queue.py\n"
        "```"
    )
    decisions, files = parse_decisions_block(synthesis)
    assert not any("capture" in d.statement.lower() for d in decisions)
    assert not any("capture" in d.rationale.lower() for d in decisions)
    assert not any("capture" in f.lower() for f in files)
    assert len(decisions) == 1
    assert decisions[0].id == "d1"
    assert decisions[0].statement == "use option A"
    assert decisions[0].rationale == "cheapest path forward"
    assert decisions[0].confidence == 0.85
    assert decisions[0].reversibility == "medium"
    assert files == ["alan/queue.py"]


def test_capture_observation_prose_never_leaks_into_test_scenarios() -> None:
    synthesis = (
        "capture unavailable — reviewed from markup\n\n"
        "```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "ok"\n'
        '    rationale: "ok"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "test_scenarios:\n"
        "  - id: s1\n"
        '    description: "d"\n'
        '    given: "g"\n'
        '    when: "w"\n'
        '    then: "t"\n'
        "```"
    )
    scenarios = _parse_test_scenarios(synthesis)
    assert not any("capture" in s.then.lower() for s in scenarios)
    assert not any("capture" in s.description.lower() for s in scenarios)
    assert len(scenarios) == 1
    assert scenarios[0].id == "s1"
    assert scenarios[0].description == "d"
    assert scenarios[0].given == "g"
    assert scenarios[0].when == "w"
    assert scenarios[0].then == "t"


def test_capture_text_inside_fenced_block_would_surface_positive_control() -> None:
    synthesis = (
        "```yaml decisions.yaml\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "capture unavailable — reviewed from markup"\n'
        '    rationale: "cheapest path forward"\n'
        "    confidence: 0.85\n"
        "    reversibility: medium\n"
        "```"
    )
    decisions, _files = parse_decisions_block(synthesis)
    assert decisions[0].statement == "capture unavailable — reviewed from markup"


def test_default_stages_is_singleton_per_role() -> None:
    roles = (Role("a", "A", "sonnet"), Role("b", "B", "sonnet"))
    assert sp._default_stages(roles) == (("a",), ("b",))  # noqa: SLF001


def test_validate_stages_rejects_mismatched_roles() -> None:
    with pytest.raises(ValueError, match="stages must list every role exactly once"):
        StaticPipelineTeam(
            name="bad-stages",
            roles=(Role("a", "A", "sonnet"), Role("b", "B", "sonnet")),
            to_team_suggestions=(),
            stages=(("a",),),
        )


def test_validate_stages_accepts_matching_roles() -> None:
    team = StaticPipelineTeam(
        name="good-stages",
        roles=(Role("a", "A", "sonnet"), Role("b", "B", "sonnet")),
        to_team_suggestions=(),
        stages=(("a", "b"),),
    )
    assert team.stages == (("a", "b"),)


def test_effective_stages_defaults_to_singleton_per_role() -> None:
    team = StaticPipelineTeam(
        name="default-stages",
        roles=(Role("a", "A", "sonnet"), Role("b", "B", "sonnet")),
        to_team_suggestions=(),
    )
    assert team._effective_stages() == (("a",), ("b",))  # noqa: SLF001


def test_effective_stages_uses_declared_stages_when_set() -> None:
    team = StaticPipelineTeam(
        name="declared-stages",
        roles=(Role("a", "A", "sonnet"), Role("b", "B", "sonnet")),
        to_team_suggestions=(),
        stages=(("a", "b"),),
    )
    assert team._effective_stages() == (("a", "b"),)  # noqa: SLF001


def test_depends_on_map_singleton_stages_chains_sequentially() -> None:
    deps = sp._depends_on_map((("a",), ("b",), ("c",)))  # noqa: SLF001
    assert deps == {"a": [], "b": ["a"], "c": ["b"]}


def test_depends_on_map_multi_role_stage_shares_same_deps() -> None:
    deps = sp._depends_on_map((("a",), ("b", "c"), ("d",)))  # noqa: SLF001
    assert deps == {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}


def test_run_stage_roles_singleton_calls_directly_without_threadpool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def run_role(role: Role) -> str:
        calls.append(role.name)
        return f"{role.name}-out"

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("singleton stage must not use a ThreadPoolExecutor")

    monkeypatch.setattr(sp, "ThreadPoolExecutor", boom)
    result = sp._run_stage_roles((Role("solo", "P", "sonnet"),), run_role)  # noqa: SLF001
    assert result == [("solo", "solo-out")]
    assert calls == ["solo"]


def test_run_stage_roles_multi_role_runs_concurrently() -> None:
    barrier = threading.Barrier(2, timeout=5.0)
    started: list[str] = []
    lock = threading.Lock()

    def run_role(role: Role) -> str:
        with lock:
            started.append(role.name)
        barrier.wait(timeout=5.0)
        return f"{role.name}-out"

    roles = (Role("first", "F", "sonnet"), Role("second", "S", "sonnet"))
    result = sp._run_stage_roles(roles, run_role)  # noqa: SLF001
    assert result == [("first", "first-out"), ("second", "second-out")]
    assert set(started) == {"first", "second"}


def test_run_stage_roles_returns_declared_order_even_when_second_finishes_first() -> None:
    def run_role(role: Role) -> str:
        if role.name == "slow":
            _time_mod.sleep(0.05)
        return f"{role.name}-out"

    roles = (Role("slow", "F", "sonnet"), Role("fast", "S", "sonnet"))
    result = sp._run_stage_roles(roles, run_role)  # noqa: SLF001
    assert result == [("slow", "slow-out"), ("fast", "fast-out")]


def test_run_stage_roles_sibling_exception_does_not_orphan_other_thread() -> None:
    completed: list[str] = []

    def run_role(role: Role) -> str:
        if role.name == "boom":
            raise RuntimeError("sibling failure")
        _time_mod.sleep(0.05)
        completed.append(role.name)
        return "ok"

    roles = (Role("boom", "F", "sonnet"), Role("slow", "S", "sonnet"))
    with pytest.raises(RuntimeError, match="sibling failure"):
        sp._run_stage_roles(roles, run_role)  # noqa: SLF001
    assert completed == ["slow"]


def test_run_stage_roles_propagates_contextvars_to_worker_threads() -> None:
    import contextvars

    var: contextvars.ContextVar[str] = contextvars.ContextVar("test_var", default="default")
    token = var.set("propagated")
    seen: dict[str, str] = {}
    try:

        def run_role(role: Role) -> str:
            seen[role.name] = var.get()
            return "ok"

        roles = (Role("a", "A", "sonnet"), Role("b", "B", "sonnet"))
        sp._run_stage_roles(roles, run_role)  # noqa: SLF001
    finally:
        var.reset(token)
    assert seen == {"a": "propagated", "b": "propagated"}


def test_run_stage_roles_resume_reruns_only_incomplete_concurrent_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("resume concurrent stage", "d", root=tmp_path / "ws")
    specs = [
        PendingTaskSpec(id="done", role="done", model="sonnet"),
        PendingTaskSpec(id="crashed", role="crashed", model="sonnet"),
    ]
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=specs)
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("done", role="done", model="sonnet")
    graph.complete_node("done")
    workflow_mod.save_node_result(
        ws, inv_id, "done", role="done", model="sonnet", result_text="done-output"
    )
    graph.start_node("crashed", role="crashed", model="sonnet")
    graph.set_session_id("crashed", "sid-crashed")
    graph.mark_interrupted("crashed", error="interrupted_by_restart")
    workflow_mod.save_workflow(ws, graph.doc)

    spawned: list[str | None] = []

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        resume_session_id: str | None = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text
        spawned.append(resume_session_id)
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": "resumed-output"}) + "\n"
            )
        return AgentStreamingResult(result_text="resumed-output")

    monkeypatch.setattr(base, "run_agent_streaming", fake)

    roles = (Role("done", "P", "sonnet"), Role("crashed", "P", "sonnet"))

    def run_role(role: Role) -> str:
        return base.run_or_resume_node(
            ws, graph, inv_id, lambda _e: None,
            node_id=role.name, role=role.name, model=role.model,
            depends_on=[], system_prompt="sys", user_text="txt",
        )

    result = sp._run_stage_roles(roles, run_role)  # noqa: SLF001

    assert result == [("done", "done-output"), ("crashed", "resumed-output")]
    assert spawned == ["sid-crashed"]

    doc = load_workflow(workflow_path(ws, inv_id))
    by_id = {t.id: t for t in doc.tasks}
    assert set(by_id) == {"done", "crashed"}
    assert by_id["done"].status == "success"
    assert by_id["crashed"].status == "success"


def test_plan_workflow_uses_stages_depends_on(tmp_path: Path) -> None:
    team = StaticPipelineTeam(
        name="staged-plan",
        roles=(Role("a", "A", "sonnet"), Role("b", "B", "sonnet"), Role("c", "C", "sonnet")),
        to_team_suggestions=(),
        stages=(("a", "b"), ("c",)),
    )
    ws = create("staged plan", "d", root=tmp_path / "ws")
    specs = team.plan_workflow(ws, None)
    by_id = {s.id: s.depends_on for s in specs}
    assert by_id == {"a": [], "b": [], "c": ["a", "b"]}


def test_static_pipeline_stages_field_runs_concurrent_stage_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_streaming(monkeypatch)
    team = StaticPipelineTeam(
        name="staged",
        roles=(
            Role("first", "F", "sonnet"),
            Role("second_a", "SA", "sonnet"),
            Role("second_b", "SB", "sonnet"),
            Role("third", "T", "sonnet"),
        ),
        to_team_suggestions=(),
        stages=(("first",), ("second_a", "second_b"), ("third",)),
    )
    ws = create("staged task", "d", root=tmp_path / "ws")
    seed_and_run(team, ws, None, lambda _e: None)
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, "inv_001"))
    assert {t.id for t in doc.tasks} == {"first", "second_a", "second_b", "third"}
    by_id = {t.id: t for t in doc.tasks}
    assert by_id["first"].depends_on == []
    assert by_id["second_a"].depends_on == ["first"]
    assert by_id["second_b"].depends_on == ["first"]
    assert by_id["third"].depends_on == ["second_a", "second_b"]
    for task in doc.tasks:
        assert task.status == "success"
