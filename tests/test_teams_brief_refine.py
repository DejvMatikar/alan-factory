from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan._dispatcher_brief_refine import apply as apply_brief_refine_decision
from alan.claude_cli import AgentStreamingResult, ServerErrorRetryable, UsageLimitError
from alan.house_rules import HOUSE_RULES
from alan.router import RouterDecision
from alan.teams import _base as base_mod
from alan.teams._brief_refine_block import BriefRefinePayload, RefinedRequirementItem
from alan.teams.brief_refine import (
    _SYSTEM_PROMPT,
    AUDIT_FILE,
    SOURCE_FILE_NAME,
    TEAM,
    BriefRefineTeam,
    _append_house_rules,
    _carry_forward,
    _combined_source_text,
    _correction_user_text,
    _origin_resolves,
    _read_source_file_text,
    merge,
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Requirement, create


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def test_team_name_is_brief_refine() -> None:
    assert TEAM.name == "brief_refine"


def test_team_has_no_to_team_suggestions() -> None:
    assert TEAM.to_team_suggestions == ()


def test_team_has_single_role() -> None:
    assert len(TEAM.roles) == 1
    assert TEAM.roles[0].name == "brief_refiner"


def test_system_prompt_instructs_floors_not_exact_counts() -> None:
    assert "never as an exact count" in _SYSTEM_PROMPT
    assert "floor or as intent" in _SYSTEM_PROMPT


def test_merge_with_no_gate_returns_none_requirements_and_folds_new_items_to_assumptions() -> None:
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=None, text="a", origin="user_text", locator="a"),
        ),
        assumptions=("b",),
    )
    outcome = merge(None, payload, source_text="a b")
    assert outcome.requirements is None
    assert set(outcome.assumptions) == {"a", "b"}


def test_merge_with_empty_prior_list_adds_house_rules() -> None:
    payload = BriefRefinePayload(requirements=(), assumptions=())
    outcome = merge([], payload, source_text="")
    assert outcome.requirements is not None
    assert len(outcome.requirements) == len(HOUSE_RULES)


def test_merge_kept_item_unchanged_text_preserves_id() -> None:
    prior = [Requirement(text="original text")]
    payload = BriefRefinePayload(
        requirements=(RefinedRequirementItem(id=prior[0].id, text="original text"),),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="")
    assert outcome.requirements is not None
    kept = next(r for r in outcome.requirements if r.id == prior[0].id)
    assert kept.text == "original text"


def test_merge_rewritten_item_with_preserved_direction_carries_status_forward() -> None:
    prior = [Requirement(text="the API must return 200", test_ref="t1", status="PASS")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must always return 200"),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="")
    assert outcome.requirements is not None
    rewritten = next(r for r in outcome.requirements if r.text == "the API must always return 200")
    assert rewritten.test_ref == "t1"
    assert rewritten.status == "PASS"
    assert rewritten.id != prior[0].id


def test_merge_rewritten_item_with_reversed_direction_keeps_original() -> None:
    prior = [Requirement(text="the API must return 200", test_ref="t1", status="PASS")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must not return 200"),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="")
    assert outcome.requirements is not None
    kept = next(r for r in outcome.requirements if r.id == prior[0].id)
    assert kept.text == "the API must return 200"
    assert any(row["action"] == "rewrite_direction_reversal_rejected" for row in outcome.audit_rows)


def test_merge_new_user_text_item_resolved_when_locator_found_in_source() -> None:
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(
                id=None, text="new req", origin="user_text", locator="please add X"
            ),
        ),
        assumptions=(),
    )
    outcome = merge([], payload, source_text="Title\n\nPlease add X to the form.")
    assert outcome.requirements is not None
    assert any(r.text == "new req" for r in outcome.requirements)


def test_merge_new_user_text_item_demoted_when_locator_not_found() -> None:
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(
                id=None, text="new req", origin="user_text", locator="nowhere to be found"
            ),
        ),
        assumptions=(),
    )
    outcome = merge([], payload, source_text="Title\n\nDescription with other words.")
    assert outcome.requirements is not None
    assert not any(r.text == "new req" for r in outcome.requirements)
    assert "new req" in outcome.assumptions
    assert outcome.audit_rows[0]["action"] == "requirement_demoted"


def test_merge_new_cited_source_item_resolved_with_issue_number() -> None:
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=None, text="new req", origin="cited_source", locator="#42"),
        ),
        assumptions=(),
    )
    outcome = merge([], payload, source_text="")
    assert outcome.requirements is not None
    assert any(r.text == "new req" for r in outcome.requirements)


def test_merge_new_cited_source_item_demoted_without_citation_shape() -> None:
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(
                id=None, text="new req", origin="cited_source", locator="just prose"
            ),
        ),
        assumptions=(),
    )
    outcome = merge([], payload, source_text="")
    assert outcome.requirements is not None
    assert "new req" in outcome.assumptions


def test_merge_over_supplied_list_carries_forward_clauses_and_adds_only_house_rules() -> None:
    prior = [Requirement(text="the API must return 200"), Requirement(text="the form must save")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must return 200"),
            RefinedRequirementItem(id=prior[1].id, text="the form must always save"),
            RefinedRequirementItem(
                id=None, text="a minted clause", origin="user_text", locator="please add X"
            ),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="Please add X to the form.")
    assert outcome.requirements is not None
    assert {r.text for r in outcome.requirements} == {
        "the API must return 200",
        "the form must always save",
        *(hr.text for hr in HOUSE_RULES),
    }


def test_merge_over_supplied_list_demotes_unmatched_user_text_item_whose_locator_resolves() -> None:
    prior = [Requirement(text="the API must return 200")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must return 200"),
            RefinedRequirementItem(
                id=None, text="minted from prose", origin="user_text", locator="please add X"
            ),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="Please add X to the form.")
    assert outcome.requirements is not None
    assert "minted from prose" in outcome.assumptions
    assert not any(r.text == "minted from prose" for r in outcome.requirements)


def test_merge_over_supplied_list_demotes_unmatched_cited_source_item_with_issue_locator() -> None:
    prior = [Requirement(text="the API must return 200")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must return 200"),
            RefinedRequirementItem(
                id=None, text="minted from citation", origin="cited_source", locator="#123"
            ),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="")
    assert outcome.requirements is not None
    assert "minted from citation" in outcome.assumptions
    assert not any(r.text == "minted from citation" for r in outcome.requirements)


def test_merge_over_supplied_list_demotes_item_carrying_id_that_matches_no_prior() -> None:
    prior = [Requirement(text="the API must return 200")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must return 200"),
            RefinedRequirementItem(
                id="req-doesnotexist",
                text="minted under a stray id",
                origin="user_text",
                locator="please add X",
            ),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="Please add X to the form.")
    assert outcome.requirements is not None
    assert "minted under a stray id" in outcome.assumptions
    assert not any(r.text == "minted under a stray id" for r in outcome.requirements)


def test_merge_over_supplied_list_audits_demotion_under_distinct_action() -> None:
    prior = [Requirement(text="the API must return 200")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the API must return 200"),
            RefinedRequirementItem(
                id=None, text="minted from prose", origin="user_text", locator="please add X"
            ),
            RefinedRequirementItem(
                id=None, text="minted from citation", origin="cited_source", locator="#123"
            ),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="Please add X to the form.")
    assert {
        "action": "requirement_demoted_supplied_list",
        "origin": "user_text",
        "text": "minted from prose",
    } in outcome.audit_rows
    assert {
        "action": "requirement_demoted_supplied_list",
        "origin": "cited_source",
        "text": "minted from citation",
    } in outcome.audit_rows
    assert not any(row["action"] == "requirement_accepted" for row in outcome.audit_rows)
    assert not any(row["action"] == "requirement_demoted" for row in outcome.audit_rows)


def test_origin_resolves_house_rule_origin_always_fails_even_with_valid_locator() -> None:
    item = RefinedRequirementItem(
        id=None, text="t", origin="house_rule", locator=HOUSE_RULES[0].id
    )
    assert not _origin_resolves(item, "")


def test_origin_resolves_house_rule_origin_cannot_smuggle_unrelated_text() -> None:
    item = RefinedRequirementItem(
        id=None, text="Deploy to Mars", origin="house_rule", locator=HOUSE_RULES[0].id
    )
    assert not _origin_resolves(item, "")


def test_origin_resolves_unknown_origin_fails() -> None:
    item = RefinedRequirementItem(id=None, text="t", origin=None, locator="x")
    assert not _origin_resolves(item, "")


def test_append_house_rules_is_idempotent_on_matching_text() -> None:
    requirements = [Requirement(text=HOUSE_RULES[0].text)]
    audit_rows: list[dict[str, object]] = []
    _append_house_rules(requirements, audit_rows)
    matching = [r for r in requirements if r.text == HOUSE_RULES[0].text]
    assert len(matching) == 1


def test_carry_forward_blank_rewrite_keeps_prior_text() -> None:
    prior = Requirement(text="keep me")
    item = RefinedRequirementItem(id=prior.id, text="keep me")
    kept, row = _carry_forward(prior, item)
    assert kept.text == "keep me"
    assert row is None


def test_carry_forward_rewrite_carries_withdrawn_marker_onto_new_identity() -> None:
    prior = Requirement(
        text="the endpoint must return 200", withdrawn_by_question_id="clause-stall-abc"
    )
    item = RefinedRequirementItem(id=prior.id, text="the endpoint must always return 200")
    rewritten, row = _carry_forward(prior, item)
    assert rewritten.id != prior.id
    assert rewritten.withdrawn_by_question_id == "clause-stall-abc"
    assert row is not None


def test_carry_forward_rewrite_keeps_record_kind() -> None:
    prior = Requirement(text="the doc must describe the API", kind="record")
    item = RefinedRequirementItem(id=prior.id, text="the doc must fully describe the API")
    rewritten, row = _carry_forward(prior, item)
    assert rewritten.kind == "record"
    assert rewritten.id != prior.id
    assert row is not None


def test_carry_forward_rewrite_keeps_behaviour_kind() -> None:
    prior = Requirement(text="the API must return 200", kind="behaviour")
    item = RefinedRequirementItem(id=prior.id, text="the API must always return 200")
    rewritten, _row = _carry_forward(prior, item)
    assert rewritten.kind == "behaviour"


def test_carry_forward_rewrite_of_kindless_clause_stays_kindless() -> None:
    prior = Requirement(text="the API must return 200")
    item = RefinedRequirementItem(id=prior.id, text="the API must always return 200")
    rewritten, _row = _carry_forward(prior, item)
    assert rewritten.kind is None


def test_carry_forward_without_rewrite_returns_prior_unchanged() -> None:
    prior = Requirement(text="keep me", kind="record")
    item = RefinedRequirementItem(id=prior.id, text="keep me")
    kept, row = _carry_forward(prior, item)
    assert kept is prior
    assert row is None


def test_merge_appends_house_rules_without_a_kind() -> None:
    outcome = merge([], BriefRefinePayload(requirements=(), assumptions=()), source_text="")
    assert outcome.requirements is not None
    assert all(r.kind is None for r in outcome.requirements)


def test_merge_keeps_record_kind_across_a_rewrite() -> None:
    prior = [Requirement(text="the doc must describe the API", kind="record")]
    payload = BriefRefinePayload(
        requirements=(
            RefinedRequirementItem(id=prior[0].id, text="the doc must fully describe the API"),
        ),
        assumptions=(),
    )
    outcome = merge(prior, payload, source_text="")
    assert outcome.requirements is not None
    rewritten = next(
        r for r in outcome.requirements if r.text == "the doc must fully describe the API"
    )
    assert rewritten.kind == "record"


def test_format_prior_requirements_annotates_withdrawn_clause() -> None:
    from alan.teams.brief_refine import _format_prior_requirements

    live = Requirement(text="live clause")
    withdrawn = Requirement(text="withdrawn clause", withdrawn_by_question_id="clause-stall-abc")
    text = _format_prior_requirements([live, withdrawn])
    assert f"- {live.id}: live clause\n" in text
    assert f"- {withdrawn.id}: withdrawn clause [WITHDRAWN]" in text


def test_run_with_malformed_output_leaves_workspace_unchanged(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Brief WS", "d", root=ws_root, requirements=[Requirement(text="orig")])

    def _fake(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        return "not a valid block at all"

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _fake)
    handover = TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")

    assert handover.from_team == "brief_refine"
    assert handover.handover_type == "terminal"
    assert handover.to_team_suggestions == []
    audit = (logs_root / AUDIT_FILE).read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["action"] == "brief_refine_rejected" for line in audit)

    from alan.workspace import read as read_ws

    reloaded = read_ws(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert [r.text for r in reloaded.meta.requirements] == ["orig"]


def test_run_with_pipeline_crash_still_hands_off_and_leaves_brief_unchanged(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Brief WS", "d", root=ws_root, requirements=[Requirement(text="orig")])

    def _boom(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        raise RuntimeError("subprocess crashed")

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _boom)
    handover = TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")

    assert handover.from_team == "brief_refine"
    assert handover.handover_type == "terminal"
    audit = (logs_root / AUDIT_FILE).read_text(encoding="utf-8").splitlines()
    actions = [json.loads(line)["action"] for line in audit]
    assert "brief_refine_pipeline_failed" in actions

    from alan.workspace import read as read_ws

    reloaded = read_ws(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert [r.text for r in reloaded.meta.requirements] == ["orig"]


@pytest.mark.parametrize("exc", [UsageLimitError("limit", 1.0), ServerErrorRetryable("boom")])
def test_run_reraises_retryable_errors_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, exc: Exception
) -> None:
    ws = create("Brief WS", "d", root=ws_root)

    def _raise(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        raise exc

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _raise)
    with pytest.raises(type(exc)):
        TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")


def test_run_with_malformed_output_composes_with_apply_to_enqueue_pending_first_hop(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create(
        "Brief WS", "d", root=ws_root, pending_first_hop="implementation_plan"
    )

    def _fake(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        return "not a valid block at all"

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _fake)
    handover = TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")

    decision = apply_brief_refine_decision(
        ws, handover, RouterDecision(kind="escalate", reason="unknown from_team: brief_refine")
    )
    assert decision.kind == "enqueue"
    assert decision.team == "implementation_plan"


def test_run_with_valid_output_persists_requirements_and_assumptions(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Brief WS", "Please add a login button.", root=ws_root, requirements=[])
    output = (
        "some prose\n\n```yaml brief_refine.yaml\n"
        "requirements:\n"
        '  - text: "add a login button"\n'
        "    origin: user_text\n"
        '    locator: "add a login button"\n'
        "assumptions:\n"
        '  - "assumed dark mode is not required"\n'
        "```\n"
    )

    def _fake(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        return output

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _fake)
    handover = TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")

    assert handover.assumptions == ["assumed dark mode is not required"]
    from alan.workspace import read as read_ws

    reloaded = read_ws(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    texts = {r.text for r in reloaded.meta.requirements}
    assert "add a login button" in texts
    for hr in HOUSE_RULES:
        assert hr.text in texts
    assert reloaded.meta.assumptions == ["assumed dark mode is not required"]


def test_role_overrides_grants_add_dirs_only_when_source_file_exists(ws_root: Path) -> None:
    ws = create("Brief WS", "d", root=ws_root)
    team = BriefRefineTeam()
    without_file = team._role_overrides("brief_refiner", ws)  # noqa: SLF001
    assert without_file is not None
    assert without_file.add_dirs is None

    (ws.path / SOURCE_FILE_NAME).write_text("transcript", encoding="utf-8")
    with_file = team._role_overrides("brief_refiner", ws)  # noqa: SLF001
    assert with_file is not None
    assert with_file.add_dirs == [str(ws.path)]


def test_build_base_user_text_references_source_file_when_present(ws_root: Path) -> None:
    ws = create("Brief WS", "d", root=ws_root)
    team = BriefRefineTeam()
    without_file = team._build_base_user_text(ws, None)  # noqa: SLF001
    assert SOURCE_FILE_NAME not in without_file

    (ws.path / SOURCE_FILE_NAME).write_text("transcript", encoding="utf-8")
    with_file = team._build_base_user_text(ws, None)  # noqa: SLF001
    assert SOURCE_FILE_NAME in with_file


def test_build_base_user_text_lists_prior_requirements_by_id(ws_root: Path) -> None:
    ws = create("Brief WS", "d", root=ws_root, requirements=[Requirement(text="orig")])
    team = BriefRefineTeam()
    text = team._build_base_user_text(ws, None)  # noqa: SLF001
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].id in text


def test_read_source_file_text_returns_none_when_absent(ws_root: Path) -> None:
    ws = create("Brief WS", "d", root=ws_root)
    assert _read_source_file_text(ws) is None


def test_read_source_file_text_returns_content_when_present(ws_root: Path) -> None:
    ws = create("Brief WS", "d", root=ws_root)
    (ws.path / SOURCE_FILE_NAME).write_text("hello", encoding="utf-8")
    assert _read_source_file_text(ws) == "hello"


def test_read_source_file_text_returns_none_on_read_error(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = create("Brief WS", "d", root=ws_root)
    (ws.path / SOURCE_FILE_NAME).write_text("hello", encoding="utf-8")

    def _boom(self: Path, encoding: str) -> str:
        raise OSError("boom")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert _read_source_file_text(ws) is None


def test_run_with_no_gate_workspace_leaves_requirements_none(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Brief WS", "d", root=ws_root, requirements=None)
    output = (
        "some prose\n\n```yaml brief_refine.yaml\n"
        "requirements:\n"
        '  - text: "a new clause"\n'
        "    origin: user_text\n"
        '    locator: "d"\n'
        "assumptions: []\n"
        "```\n"
    )

    def _fake(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        return output

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _fake)
    TEAM.run(ws, None, lambda _e: None, inv_id="inv_001")

    from alan.workspace import read as read_ws

    reloaded = read_ws(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is None
    assert reloaded.meta.assumptions == ["a new clause"]


def test_combined_source_text_joins_title_description_and_transcript(ws_root: Path) -> None:
    ws = create("Title Here", "Description here", root=ws_root)
    combined = _combined_source_text(ws, "transcript here")
    assert "Title Here" in combined
    assert "Description here" in combined
    assert "transcript here" in combined


def test_combined_source_text_omits_transcript_when_none(ws_root: Path) -> None:
    ws = create("Title Here", "Description here", root=ws_root)
    combined = _combined_source_text(ws, None)
    assert "Title Here" in combined
    assert "Description here" in combined


def test_correction_user_text_mentions_the_parser_error() -> None:
    text = _correction_user_text("missing fence")
    assert "missing fence" in text
    assert "brief_refine.yaml" in text


def test_run_recovers_via_correction_when_live_session_available(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, tmp_path: Path
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws = create("Brief WS", "Please add a login button.", root=ws_root, requirements=[])

    inv_id = workflow_mod.seed_pending_workflow(
        ws,
        team="brief_refine",
        tasks=[PendingTaskSpec(id="brief_refiner", role="brief_refiner", model="opus")],
    )
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node("brief_refiner", role="brief_refiner", model="opus", depends_on=[])
    graph.set_session_id("brief_refiner", "sess-1")
    graph.complete_node("brief_refiner")
    workflow_mod.save_workflow(ws, graph.doc)

    def _fake_pipeline(self: BriefRefineTeam, *args: object, **kwargs: object) -> str:
        return "not a valid block at all"

    monkeypatch.setattr(BriefRefineTeam, "run_pipeline", _fake_pipeline)

    good_output = (
        "```yaml brief_refine.yaml\n"
        "requirements:\n"
        '  - text: "add a login button"\n'
        "    origin: user_text\n"
        '    locator: "add a login button"\n'
        "assumptions: []\n"
        "```\n"
    )

    def _fake_streaming(
        system_prompt: str,
        user_text: str,
        *,
        on_event: object = None,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, on_event
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
        return AgentStreamingResult(result_text=good_output)

    monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming)

    handover = TEAM.run(ws, None, lambda _e: None, inv_id=inv_id)

    assert handover.assumptions == []
    from alan.workspace import read as read_ws

    reloaded = read_ws(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    texts = {r.text for r in reloaded.meta.requirements}
    assert "add a login button" in texts

    rows = [
        json.loads(line)
        for line in (logs_root / "block_correction_audit.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert rows[-1]["site"] == "brief_refine.yaml"
    assert rows[-1]["outcome"] == "corrected"
