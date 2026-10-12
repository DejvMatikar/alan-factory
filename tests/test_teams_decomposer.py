from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import Decision, Handover, HandoverError, TestScenario, save_handover
from alan.teams import decomposer as dc
from alan.teams.decomposer import (
    TEAM,
    DecomposerParseError,
    DecomposerTeam,
    _build_keep_handover,
    _build_split_handover,
    _derive_dependencies,
    _format_clause_context,
    _format_scenarios_context,
    parse_decomposition,
)
from alan.workspace import Meta, Requirement, Status, Workspace, create

_PARENT_REQ = Requirement(text="parent requirement one")


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _block(body: str) -> str:
    return f"prose\n\n```yaml decomposition.yaml\n{body}\n```\ntrailing"


def _split_body(subtasks: str) -> str:
    return _block(f'split_count: 2\nsplit_rationale: "why split"\nsubtasks:\n{subtasks}')


def _bad_split(first: str) -> str:
    return _split_body(f"  - {first}\n  - {{id: b, team: implementation}}\n")


_KEEP = _block('split_count: 1\nsplit_rationale: "single subsystem, <= 5 files"')

_SPLIT = _split_body(
    "  - id: backend\n"
    "    team: implementation\n"
    '    instructions: "build the endpoint"\n'
    "    files: [alan/x.py, alan/shared.py]\n"
    "    dod:\n"
    '      - text: "endpoint returns 200"\n'
    f"        covers: [{_PARENT_REQ.id}]\n"
    "  - id: frontend\n"
    "    team: implementation\n"
    "    instructions: null\n"
    "    files: [alan/web/y.tsx, alan/shared.py]\n"
    "    dod:\n"
    '      - text: "renders the result"\n'
    "        covers: []\n"
)

_SPLIT_DECLARED = _split_body(
    "  - id: backend\n"
    "    team: implementation\n"
    '    instructions: "build the endpoint + SSE event"\n'
    "    depends_on: []\n"
    "    files: [alan/api.py]\n"
    "    dod:\n"
    '      - text: "endpoint returns 200"\n'
    "        covers: []\n"
    "  - id: frontend\n"
    "    team: implementation\n"
    '    instructions: "consume the SSE event"\n'
    "    depends_on: [backend]\n"
    "    files: [alan/web/y.tsx]\n"
    "    dod:\n"
    '      - text: "renders the result"\n'
    "        covers: []\n"
)


def _dummy_meta() -> Meta:
    return Meta(
        task_id="t",
        title="T",
        description="d",
        status=Status.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _prior(task_id: str, *, decisions: bool = True) -> Handover:
    decided = [
        Decision(
            id="d1", statement="s", rationale="r", confidence=0.9, reversibility="medium"
        )
    ]
    return Handover(
        handover_id=f"handover_{task_id}_001",
        handover_type="forward",
        task_id=task_id,
        from_team="implementation_plan",
        from_invocation_id="inv_001",
        to_team_suggestions=["decomposer"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="the plan",
        decisions=decided if decisions else [],
        files_to_edit=["alan/x.py"],
        test_scenarios=[
            TestScenario(id="s1", description="d", given="g", when="w", then="t")
        ],
    )


def test_team_identity() -> None:
    assert TEAM.name == "decomposer"
    assert TEAM.supports_resume is True
    assert TEAM.terminal is False
    ws = Workspace(task_id="t", path=Path("/x"), meta=_dummy_meta())
    assert [s.role for s in TEAM.plan_workflow(ws, None)] == ["decomposer"]


def test_module_team_is_decomposer_instance() -> None:
    assert isinstance(dc.TEAM, DecomposerTeam)


def test_parse_keep() -> None:
    plan = parse_decomposition(_KEEP)
    assert plan.split is False
    assert plan.rationale == "single subsystem, <= 5 files"
    assert plan.subtasks == []


def test_parse_split_derives_file_overlap_dependencies() -> None:
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    assert plan.split is True
    assert plan.rationale == "why split"
    assert [t.id for t in plan.subtasks] == ["backend", "frontend"]
    assert plan.subtasks[0].depends_on == []
    assert plan.subtasks[1].depends_on == ["backend"]
    assert plan.subtasks[1].instructions is None
    assert plan.subtasks[0].dod_clauses[0].covers == [_PARENT_REQ.id]
    assert plan.subtasks[1].dod_clauses[0].covers == []
    assert plan.repairs == ()


def test_split_path_threads_files_to_injected_task() -> None:
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    assert plan.subtasks[0].files == ["alan/x.py", "alan/shared.py"]
    assert plan.subtasks[1].files == ["alan/web/y.tsx", "alan/shared.py"]


def test_parse_split_no_overlap_is_parallel() -> None:
    plan = parse_decomposition(
        _split_body(
            "  - {id: a, team: implementation, files: [alan/a.py]}\n"
            "  - {id: b, team: implementation, files: [alan/b.py]}\n"
        )
    )
    assert plan.subtasks[0].depends_on == []
    assert plan.subtasks[1].depends_on == []


def test_parse_split_subtask_without_files_has_no_edges() -> None:
    plan = parse_decomposition(
        _split_body(
            "  - {id: a, team: implementation}\n"
            "  - {id: b, team: implementation}\n"
        )
    )
    assert all(t.depends_on == [] for t in plan.subtasks)
    assert all(t.dod_clauses == [] for t in plan.subtasks)


def test_declared_edge_with_disjoint_files() -> None:
    plan = parse_decomposition(
        _split_body(
            "  - id: backend\n"
            "    team: implementation\n"
            "    files: [alan/api.py]\n"
            "    depends_on: []\n"
            "  - id: frontend\n"
            "    team: implementation\n"
            "    files: [alan/web/x.tsx]\n"
            "    depends_on: [backend]\n"
        )
    )
    assert plan.subtasks[0].depends_on == []
    assert plan.subtasks[1].depends_on == ["backend"]


def test_declared_and_overlap_dedupe() -> None:
    plan = parse_decomposition(
        _split_body(
            "  - id: backend\n"
            "    team: implementation\n"
            "    files: [alan/shared.py]\n"
            "    depends_on: []\n"
            "  - id: frontend\n"
            "    team: implementation\n"
            "    files: [alan/shared.py]\n"
            "    depends_on: [backend]\n"
        )
    )
    assert plan.subtasks[1].depends_on == ["backend"]


def test_file_overlap_only_regression() -> None:
    plan = parse_decomposition(
        _split_body(
            "  - {id: backend, team: implementation, files: [alan/x.py]}\n"
            "  - {id: frontend, team: implementation, files: [alan/x.py]}\n"
        )
    )
    assert plan.subtasks[1].depends_on == ["backend"]


def test_dropped_forward_self_unknown_refs() -> None:
    plan = parse_decomposition(
        _block(
            'split_count: 3\nsplit_rationale: "r"\nsubtasks:\n'
            "  - {id: a, team: implementation, files: [alan/a.py], depends_on: [b, a, ghost]}\n"
            "  - {id: b, team: implementation, files: [alan/b.py], depends_on: []}\n"
            "  - {id: c, team: implementation, files: [alan/c.py], depends_on: [a]}\n"
        )
    )
    assert plan.subtasks[0].depends_on == []
    assert plan.subtasks[1].depends_on == []
    assert plan.subtasks[2].depends_on == ["a"]


def _covers_split(a_covers: str, b_covers: str | None) -> str:
    return _split_body(
        "  - id: a\n"
        "    team: implementation\n"
        "    files: [alan/a.py]\n"
        "    dod:\n"
        '      - text: "x"\n'
        f"        covers: [{a_covers}]\n"
        "  - id: b\n"
        "    team: implementation\n"
        "    files: [alan/b.py]\n"
        + (
            "    dod:\n"
            '      - text: "y"\n'
            f"        covers: [{b_covers}]\n"
            if b_covers is not None
            else ""
        )
    )


def test_parse_decomposition_unknown_clause_covers_raises() -> None:
    text = _covers_split("req-totally-unknown", None)
    with pytest.raises(DecomposerParseError, match="unknown requirement id"):
        parse_decomposition(text, parent_requirements=[_PARENT_REQ])


def test_parse_decomposition_covers_with_no_parent_requirements_raises() -> None:
    text = _covers_split(_PARENT_REQ.id, None)
    with pytest.raises(DecomposerParseError, match="unknown requirement id"):
        parse_decomposition(text, parent_requirements=None)


def test_parse_decomposition_two_siblings_claim_one_id_last_wins() -> None:
    text = _covers_split(_PARENT_REQ.id, _PARENT_REQ.id)
    plan = parse_decomposition(text, parent_requirements=[_PARENT_REQ])
    assert plan.subtasks[0].dod_clauses[0].covers == []
    assert plan.subtasks[1].dod_clauses[0].covers == [_PARENT_REQ.id]
    assert len(plan.repairs) == 1
    repair = plan.repairs[0]
    assert repair.req_id == _PARENT_REQ.id
    assert repair.losing_subtask_id == "a"
    assert repair.owning_subtask_id == "b"


def test_parse_decomposition_three_way_claim_produces_two_repairs() -> None:
    text = _block(
        'split_count: 3\nsplit_rationale: "r"\nsubtasks:\n'
        "  - id: a\n"
        "    team: implementation\n"
        "    files: [alan/a.py]\n"
        "    dod:\n"
        '      - text: "x"\n'
        f"        covers: [{_PARENT_REQ.id}]\n"
        "  - id: b\n"
        "    team: implementation\n"
        "    files: [alan/b.py]\n"
        "    dod:\n"
        '      - text: "y"\n'
        f"        covers: [{_PARENT_REQ.id}]\n"
        "  - id: c\n"
        "    team: implementation\n"
        "    files: [alan/c.py]\n"
        "    dod:\n"
        '      - text: "z"\n'
        f"        covers: [{_PARENT_REQ.id}]\n"
    )
    plan = parse_decomposition(text, parent_requirements=[_PARENT_REQ])
    assert len(plan.repairs) == 2
    assert plan.subtasks[0].dod_clauses[0].covers == []
    assert plan.subtasks[1].dod_clauses[0].covers == []
    assert plan.subtasks[2].dod_clauses[0].covers == [_PARENT_REQ.id]


def test_parse_decomposition_no_duplicate_claims_has_empty_repairs() -> None:
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    assert plan.repairs == ()


def test_parse_decomposition_keep_has_empty_repairs() -> None:
    plan = parse_decomposition(_KEEP)
    assert plan.repairs == ()


def test_run_split_with_repair_writes_clause_owner_audit_row(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    text = _covers_split(_PARENT_REQ.id, _PARENT_REQ.id)
    ws = create("run-split-repair", "d", root=ws_root, requirements=[_PARENT_REQ])
    _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), text)
    audit_path = tmp_path / "logs" / "clause_owner_audit.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["action"] == "clause_owner_repaired"
    assert entries[0]["req_id"] == _PARENT_REQ.id
    assert entries[0]["losing_subtask_id"] == "a"
    assert entries[0]["owning_subtask_id"] == "b"


def test_run_split_without_repair_writes_no_audit_file(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    ws = create("run-split-norepair", "d", root=ws_root, requirements=[_PARENT_REQ])
    _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), _SPLIT)
    assert not (tmp_path / "logs" / "clause_owner_audit.jsonl").exists()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("no fenced block at all", id="missing_block"),
        pytest.param(_block("foo: [unclosed"), id="invalid_yaml"),
        pytest.param(_block("- a\n- b"), id="non_dict_block"),
        pytest.param(_block("split_count: 1"), id="missing_rationale"),
        pytest.param(
            _block('split_count: 1\nsplit_rationale: "   "'), id="blank_rationale"
        ),
        pytest.param(
            _block('split_rationale: "r"\nsplit_count: two'), id="split_count_not_int"
        ),
        pytest.param(
            _block('split_rationale: "r"\nsplit_count: true'), id="split_count_bool"
        ),
        pytest.param(
            _block('split_rationale: "r"\nsplit_count: 0'), id="split_count_zero"
        ),
        pytest.param(_bad_split("{id: a, team: bogus}"), id="subtask_unknown_team"),
        pytest.param(
            _block(
                'split_count: 3\nsplit_rationale: "r"\nsubtasks:\n'
                "  - {id: a, team: implementation}\n"
                "  - {id: b, team: implementation}"
            ),
            id="count_mismatch",
        ),
        pytest.param(
            _block(
                'split_count: 2\nsplit_rationale: "r"\nsubtasks:\n'
                "  - {id: a, team: implementation}\n"
                "  - {id: a, team: implementation}"
            ),
            id="duplicate_subtask_ids",
        ),
        pytest.param(
            _block('split_count: 2\nsplit_rationale: "r"\nsubtasks: nope'),
            id="subtasks_not_list",
        ),
        pytest.param(
            _split_body("  - {id: a, team: implementation}\n"), id="subtasks_too_few"
        ),
        pytest.param(_bad_split('"juststring"'), id="subtask_not_mapping"),
        pytest.param(_bad_split("{team: implementation}"), id="subtask_missing_id"),
        pytest.param(_bad_split("{id: a}"), id="subtask_missing_team"),
        pytest.param(
            _bad_split('{id: a, team: implementation, files: "x.py"}'),
            id="files_not_list",
        ),
        pytest.param(
            _bad_split("{id: a, team: implementation, files: [123]}"),
            id="files_entry_not_str",
        ),
        pytest.param(
            _bad_split('{id: a, team: implementation, dod: "x"}'), id="dod_not_list"
        ),
        pytest.param(
            _bad_split('{id: a, team: implementation, dod: ["s"]}'),
            id="dod_entry_not_mapping",
        ),
        pytest.param(
            _bad_split("{id: a, team: implementation, dod: [{covers: []}]}"),
            id="dod_missing_text",
        ),
        pytest.param(
            _bad_split('{id: a, team: implementation, dod: [{text: "x", covers: "r"}]}'),
            id="covers_not_list",
        ),
        pytest.param(
            _bad_split('{id: a, team: implementation, depends_on: "backend"}'),
            id="depends_on_not_list",
        ),
        pytest.param(
            _bad_split("{id: a, team: implementation, depends_on: [123]}"),
            id="depends_on_entry_not_str",
        ),
    ],
)
def test_parse_decomposition_rejects(text: str) -> None:
    with pytest.raises(DecomposerParseError):
        parse_decomposition(text)


def test_derive_dependencies_chain() -> None:
    assert _derive_dependencies([["a"], ["a"], ["b"]]) == [[], [0], []]


def test_format_clause_context_empty() -> None:
    assert _format_clause_context(None) == ""
    assert _format_clause_context([]) == ""


def test_format_clause_context_lists_ids() -> None:
    reqs = [Requirement(text="clause one"), Requirement(text="clause two")]
    out = _format_clause_context(reqs)
    assert reqs[0].id in out
    assert "clause two" in out


def test_format_clause_context_excludes_withdrawn_clause() -> None:
    withdrawn = Requirement(text="clause one", withdrawn_by_question_id="clause-stall-x")
    live = Requirement(text="clause two")
    out = _format_clause_context([withdrawn, live])
    assert withdrawn.id not in out
    assert live.id in out


def test_format_clause_context_all_withdrawn_returns_empty() -> None:
    withdrawn = Requirement(text="clause one", withdrawn_by_question_id="clause-stall-x")
    assert _format_clause_context([withdrawn]) == ""


def test_parse_decomposition_covers_referencing_withdrawn_clause_raises() -> None:
    withdrawn = Requirement(text="req text", withdrawn_by_question_id="clause-stall-x")
    text = _covers_split(withdrawn.id, None)
    with pytest.raises(DecomposerParseError, match="unknown requirement id"):
        parse_decomposition(text, parent_requirements=[withdrawn])


def test_build_keep_handover_carries_prior_decisions(ws_root: Path) -> None:
    ws = create("keep-h", "d", root=ws_root)
    h = _build_keep_handover(ws, _prior(ws.task_id), "inv_001", "s", parse_decomposition(_KEEP))
    assert h.handover_type == "forward"
    assert h.to_team_suggestions == ["implementation"]
    assert h.next_hop == "implementation"
    assert h.split_rationale == "single subsystem, <= 5 files"
    assert [d.id for d in h.decisions] == ["d1"]
    assert h.files_to_edit == ["alan/x.py"]
    assert h.plan_synthesis == "the plan"
    save_handover(ws, h)


def test_build_keep_handover_without_prior(ws_root: Path) -> None:
    ws = create("keep-noprior", "d", root=ws_root)
    h = _build_keep_handover(ws, None, "inv_001", "s", parse_decomposition(_KEEP))
    assert h.decisions == []
    assert h.files_to_edit == []
    assert h.plan_synthesis is None


def test_build_split_handover_is_terminal_with_injected_tasks(ws_root: Path) -> None:
    ws = create("split-h", "d", root=ws_root)
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    h = _build_split_handover(ws, _prior(ws.task_id), "inv_001", "s", plan)
    assert h.handover_type == "terminal"
    assert h.to_team_suggestions == []
    assert [t.id for t in h.injected_tasks] == ["backend", "frontend"]
    assert h.split_rationale == "why split"
    assert [d.id for d in h.decisions] == ["d1"]
    assert h.files_to_edit == ["alan/x.py"]
    assert [s.id for s in h.test_scenarios] == ["s1"]
    save_handover(ws, h)


def test_build_split_handover_without_prior(ws_root: Path) -> None:
    ws = create("split-noprior", "d", root=ws_root)
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    h = _build_split_handover(ws, None, "inv_001", "s", plan)
    assert h.decisions == []
    assert h.test_scenarios == []
    assert [t.id for t in h.injected_tasks] == ["backend", "frontend"]


def test_keep_handover_without_decisions_fails_layer3(ws_root: Path) -> None:
    ws = create("keep-nodec", "d", root=ws_root)
    prior = _prior(ws.task_id, decisions=False)
    h = _build_keep_handover(ws, prior, "inv_001", "s", parse_decomposition(_KEEP))
    with pytest.raises(HandoverError):
        save_handover(ws, h)


def _run_with_pipeline(
    monkeypatch: pytest.MonkeyPatch, ws: Workspace, prior: Handover | None, output: str
) -> Handover:
    def _fake(self: DecomposerTeam, *args: object, **kwargs: object) -> str:
        return output

    monkeypatch.setattr(DecomposerTeam, "run_pipeline", _fake)
    return DecomposerTeam().run(ws, prior, lambda _e: None, inv_id="inv_001")


def test_run_keep_routes_to_implementation(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = create("run-keep", "d", root=ws_root)
    h = _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), _KEEP)
    assert h.handover_type == "forward"
    assert h.to_team_suggestions == ["implementation"]
    assert h.injected_tasks == []


def test_run_split_injects_sub_dag(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = create("run-split", "d", root=ws_root, requirements=[_PARENT_REQ])
    h = _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), _SPLIT)
    assert h.handover_type == "terminal"
    assert len(h.injected_tasks) == 2
    assert h.split_rationale == "why split"


def test_run_split_carries_declared_edge_end_to_end(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = create("run-split-declared", "d", root=ws_root)
    h = _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), _SPLIT_DECLARED)
    assert h.handover_type == "terminal"
    assert [t.id for t in h.injected_tasks] == ["backend", "frontend"]
    assert h.injected_tasks[1].depends_on == ["backend"]


def test_run_split_carries_files_end_to_end(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = create("run-split-files", "d", root=ws_root, requirements=[_PARENT_REQ])
    h = _run_with_pipeline(monkeypatch, ws, _prior(ws.task_id), _SPLIT)
    assert h.injected_tasks[0].files == ["alan/x.py", "alan/shared.py"]
    assert h.injected_tasks[1].files == ["alan/web/y.tsx", "alan/shared.py"]


def test_build_base_user_text_appends_clause_context(ws_root: Path) -> None:
    ws = create("ctx", "d", root=ws_root, requirements=[Requirement(text="must do X")])
    text = DecomposerTeam()._build_base_user_text(ws, None)
    assert "Acceptance clauses" in text
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].id in text


def test_build_base_user_text_no_requirements(ws_root: Path) -> None:
    ws = create("noctx", "d", root=ws_root)
    assert "Acceptance clauses" not in DecomposerTeam()._build_base_user_text(ws, None)


def test_build_base_user_text_includes_files_to_edit_section(ws_root: Path) -> None:
    ws = create("files-ctx", "d", root=ws_root)
    text = DecomposerTeam()._build_base_user_text(ws, _prior(ws.task_id))
    assert "## Files to edit (from plan)" in text
    assert "- alan/x.py" in text


def test_build_base_user_text_omits_files_to_edit_section_when_empty(
    ws_root: Path,
) -> None:
    ws = create("files-ctx-empty", "d", root=ws_root)
    prior = _prior(ws.task_id).model_copy(update={"files_to_edit": []})
    text = DecomposerTeam()._build_base_user_text(ws, prior)
    assert "## Files to edit (from plan)" not in text


def test_build_base_user_text_includes_plan_decisions_section(ws_root: Path) -> None:
    ws = create("decisions-ctx", "d", root=ws_root)
    text = DecomposerTeam()._build_base_user_text(ws, _prior(ws.task_id))
    assert "## Plan decisions" in text
    assert "- s (because: r)" in text


def test_build_base_user_text_omits_plan_decisions_section_when_empty(
    ws_root: Path,
) -> None:
    ws = create("decisions-ctx-empty", "d", root=ws_root)
    prior = _prior(ws.task_id, decisions=False)
    text = DecomposerTeam()._build_base_user_text(ws, prior)
    assert "## Plan decisions" not in text


def test_build_base_user_text_omits_plan_sections_without_prior(ws_root: Path) -> None:
    ws = create("no-prior-ctx", "d", root=ws_root)
    text = DecomposerTeam()._build_base_user_text(ws, None)
    assert "## Files to edit (from plan)" not in text
    assert "## Plan decisions" not in text


def test_decomposer_prompt_worked_example_has_one_title_per_subtask() -> None:
    block = dc._DECOMPOSER_PROMPT.split("```yaml decomposition.yaml", 1)[1]
    subtask_chunks = block.split("  - id: ")[1:]
    assert len(subtask_chunks) == 2
    for chunk in subtask_chunks:
        assert chunk.count("title:") == 1


def test_decomposer_prompt_worked_example_demonstrates_scenarios_field() -> None:
    prompt = dc._DECOMPOSER_PROMPT
    assert "scenarios: [s1]" in prompt
    assert "scenarios: [s2]" in prompt


def test_decomposer_prompt_states_scenario_assignment_rule() -> None:
    prompt = dc._DECOMPOSER_PROMPT
    assert "assign EVERY parent scenario id to exactly one" in prompt
    assert "copy ids verbatim" in prompt
    assert "unassigned runs against every subtask" in prompt


def test_decomposer_prompt_caps_subtask_count() -> None:
    assert "Prefer 2-3 subtasks; never more than 4" in dc._DECOMPOSER_PROMPT


def test_decomposer_prompt_states_sequential_chain_not_reason_to_split() -> None:
    assert (
        "purely sequential chain is NOT itself a reason to split"
        in dc._DECOMPOSER_PROMPT
    )


def test_decomposer_prompt_requires_scenarios_satisfiable_within_subtask_files() -> None:
    prompt = dc._DECOMPOSER_PROMPT
    assert "ONLY if it can be made to pass by editing that subtask's own files" in prompt
    assert "belongs to the LAST subtask in the dependency chain" in prompt
    assert "earlier subtasks assert only what their own files control" in prompt


def test_parse_split_threads_title_to_injected_task() -> None:
    text = _split_body(
        "  - id: backend\n"
        "    team: implementation\n"
        '    title: "Add X endpoint"\n'
        "    files: [alan/x.py]\n"
        "  - id: frontend\n"
        "    team: implementation\n"
        '    title: "Wire X into the UI"\n'
        "    files: [alan/web/x.tsx]\n"
    )
    plan = parse_decomposition(text)
    assert plan.subtasks[0].title == "Add X endpoint"
    assert plan.subtasks[1].title == "Wire X into the UI"


def test_parse_split_title_defaults_none_when_absent() -> None:
    plan = parse_decomposition(_SPLIT, parent_requirements=[_PARENT_REQ])
    assert all(t.title is None for t in plan.subtasks)


def _make_scenario(sid: str, desc: str = "d") -> TestScenario:
    return TestScenario(id=sid, description=desc, given="g", when="uv run pytest", then="ok")


def test_format_scenarios_context_empty_returns_empty() -> None:
    assert _format_scenarios_context([]) == ""


def test_format_scenarios_context_includes_header_and_ids() -> None:
    ctx = _format_scenarios_context([_make_scenario("s1", "first"), _make_scenario("s2", "second")])
    assert "scenarios" in ctx.lower()
    assert "s1: first" in ctx
    assert "s2: second" in ctx


def test_parse_decomposition_threads_scenarios_to_injected_task() -> None:
    parent_scenarios = [_make_scenario("s1"), _make_scenario("s2")]
    text = _split_body(
        "  - id: backend\n"
        "    team: implementation\n"
        "    files: [alan/x.py]\n"
        "    scenarios: [s1]\n"
        "  - id: frontend\n"
        "    team: implementation\n"
        "    files: [alan/web/y.tsx]\n"
        "    scenarios: [s2]\n"
    )
    plan = parse_decomposition(text, parent_scenarios=parent_scenarios)
    assert plan.subtasks[0].scenarios == ["s1"]
    assert plan.subtasks[1].scenarios == ["s2"]


def test_parse_decomposition_unknown_scenario_raises() -> None:
    parent_scenarios = [_make_scenario("s1")]
    text = _split_body(
        "  - id: backend\n"
        "    team: implementation\n"
        "    files: [alan/x.py]\n"
        "    scenarios: [unknown_id]\n"
        "  - id: frontend\n"
        "    team: implementation\n"
        "    files: [alan/web/y.tsx]\n"
    )
    with pytest.raises(DecomposerParseError, match="unknown scenario id"):
        parse_decomposition(text, parent_scenarios=parent_scenarios)


def test_parse_decomposition_scenarios_waived_threaded() -> None:
    text = _split_body(
        "  - id: backend\n"
        "    team: implementation\n"
        "    files: [alan/x.py]\n"
        "    scenarios_waived: true\n"
        "  - id: frontend\n"
        "    team: implementation\n"
        "    files: [alan/web/y.tsx]\n"
    )
    plan = parse_decomposition(text)
    assert plan.subtasks[0].scenarios_waived is True
    assert plan.subtasks[1].scenarios_waived is False


def test_parse_decomposition_scenarios_waived_non_bool_raises() -> None:
    text = _split_body(
        "  - id: backend\n"
        "    team: implementation\n"
        "    files: [alan/x.py]\n"
        '    scenarios_waived: "true"\n'
        "  - id: frontend\n"
        "    team: implementation\n"
        "    files: [alan/web/y.tsx]\n"
    )
    with pytest.raises(
        DecomposerParseError, match="scenarios_waived must be a boolean"
    ):
        parse_decomposition(text)


def test_build_base_user_text_includes_scenarios_context(ws_root: Path) -> None:
    ws = create("sc-ctx", "d", root=ws_root)
    prior = Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_plan",
        from_invocation_id="inv_001",
        to_team_suggestions=["decomposer"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[],
        full_synthesis="done",
        test_scenarios=[_make_scenario("s1", "the scenario")],
    )
    text = DecomposerTeam()._build_base_user_text(ws, prior)
    assert "s1: the scenario" in text


def test_decomposer_invalidates_cache_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    import json

    from alan import workflow as workflow_mod
    from alan.claude_cli import AgentStreamingResult
    from alan.teams import _base as base
    from alan.workflow import load_workflow, workflow_path
    from tests._helpers import seed_and_run

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
        text = "no decomposition block here"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)
    ws = create("dc-inv", "d", root=ws_root)
    with pytest.raises(DecomposerParseError):
        seed_and_run(DecomposerTeam(), ws, _prior(ws.task_id), lambda _e: None)

    assert not workflow_mod.result_path(ws, "inv_001", "decomposer").exists()
    doc = load_workflow(workflow_path(ws, "inv_001"))
    decomp_node = next(t for t in doc.tasks if t.id == "decomposer")
    assert decomp_node.status == "failed"
