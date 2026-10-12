from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import workspace as workspace_mod
from alan.app.workspace_create_models import (
    _SUPPLIED_CLAUSE_ID_DETAIL,
    UNCOVERED_CLAUSES_DETAIL,
    CreateWorkspaceBody,
    TaskCreateEntry,
    _reaches_decomposer,
    reject_uncovered_clauses,
    reject_unseedable_teams,
)
from alan.errors import AppError
from alan.workspace import Requirement, Task, TaskDoDClause, TaskStatus


class TestTaskCreateEntryToTask:
    def test_threads_dod_clauses(self) -> None:
        clause = TaskDoDClause(text="done", covers=["req-1"])
        entry = TaskCreateEntry(id="a", dod_clauses=[clause])
        assert entry.to_task().dod_clauses == [clause]

    def test_omitted_dod_clauses_defaults_to_empty(self) -> None:
        entry = TaskCreateEntry(id="a")
        assert entry.to_task().dod_clauses == []

    def test_sets_pending_status_and_clears_depends_on(self) -> None:
        entry = TaskCreateEntry(id="a")
        task = entry.to_task()
        assert task.status == TaskStatus.PENDING
        assert task.depends_on == []

    def test_clears_caller_supplied_evidence(self) -> None:
        clause = TaskDoDClause(text="done", covers=["req-1"], evidence="pre-seeded")
        entry = TaskCreateEntry(id="a", dod_clauses=[clause])
        task = entry.to_task()
        assert task.dod_clauses[0].evidence is None
        assert task.dod_clauses[0].text == "done"
        assert task.dod_clauses[0].covers == ["req-1"]


class TestCreateWorkspaceBodyRoundTrip:
    def test_tasks_carrying_clauses_round_trip(self) -> None:
        clause = TaskDoDClause(text="done", covers=["req-1"])
        body = CreateWorkspaceBody(
            title="t", tasks=[TaskCreateEntry(id="a", dod_clauses=[clause])]
        )
        assert body.tasks[0].dod_clauses == [clause]


class TestRejectUncoveredClauses:
    def test_returns_silently_for_fewer_than_two_tasks(self) -> None:
        body = CreateWorkspaceBody(title="t", requirements=[Requirement(text="x")])
        reject_uncovered_clauses(body, [Task(id="a", status=TaskStatus.PENDING)])

    def test_returns_silently_for_none_requirements(self) -> None:
        body = CreateWorkspaceBody(title="t")
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        reject_uncovered_clauses(body, tasks)

    def test_returns_silently_for_empty_requirements_list(self) -> None:
        body = CreateWorkspaceBody(title="t", requirements=[])
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        reject_uncovered_clauses(body, tasks)

    def test_raises_with_sorted_uncovered_ids_when_no_ownership_at_all(self) -> None:
        req_a = Requirement(text="clause a")
        req_b = Requirement(text="clause b")
        body = CreateWorkspaceBody(title="t", requirements=[req_a, req_b])
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        with pytest.raises(AppError) as exc_info:
            reject_uncovered_clauses(body, tasks)
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error"] == UNCOVERED_CLAUSES_DETAIL
        assert detail["uncovered_clause_ids"] == sorted([req_a.id, req_b.id])

    def test_raises_naming_only_the_partially_uncovered_id(self) -> None:
        req_a = Requirement(text="clause a")
        req_b = Requirement(text="clause b")
        body = CreateWorkspaceBody(title="t", requirements=[req_a, req_b])
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
            ),
            Task(id="b", status=TaskStatus.PENDING),
        ]
        with pytest.raises(AppError) as exc_info:
            reject_uncovered_clauses(body, tasks)
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["uncovered_clause_ids"] == [req_b.id]

    def test_does_not_raise_when_coverage_is_full(self) -> None:
        req_a = Requirement(text="clause a")
        req_b = Requirement(text="clause b")
        body = CreateWorkspaceBody(title="t", requirements=[req_a, req_b])
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
            ),
            Task(
                id="b", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done b", covers=[req_b.id])],
            ),
        ]
        reject_uncovered_clauses(body, tasks)

    def test_does_not_raise_when_ownership_overlaps(self) -> None:
        req = Requirement(text="shared clause")
        body = CreateWorkspaceBody(title="t", requirements=[req])
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
            ),
            Task(
                id="b", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
            ),
        ]
        reject_uncovered_clauses(body, tasks)

    def test_does_not_raise_for_test_ref_bound_clause(self) -> None:
        req = Requirement(text="test bound clause", test_ref="scn-1")
        body = CreateWorkspaceBody(title="t", requirements=[req])
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        reject_uncovered_clauses(body, tasks)

    def test_raises_when_caller_chosen_id_does_not_match_persisted_id(self) -> None:
        canonical = Requirement(text="clause a")
        spoofed = Requirement(id="req-caller-chosen", text="clause a")
        body = CreateWorkspaceBody(title="t", requirements=[spoofed])
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done a", covers=["req-caller-chosen"])],
            ),
            Task(id="b", status=TaskStatus.PENDING),
        ]
        with pytest.raises(AppError) as exc_info:
            reject_uncovered_clauses(body, tasks)
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["uncovered_clause_ids"] == [canonical.id]

    def test_raises_when_caller_marks_clause_withdrawn_on_the_request(self) -> None:
        req = Requirement(text="clause a", withdrawn_by_question_id="q-1")
        body = CreateWorkspaceBody(title="t", requirements=[req])
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        with pytest.raises(AppError) as exc_info:
            reject_uncovered_clauses(body, tasks)
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["uncovered_clause_ids"] == [req.id]


class TestRecordKindClauses:
    def test_create_body_round_trips_kind_from_a_raw_request(self) -> None:
        body = CreateWorkspaceBody.model_validate({
            "title": "t",
            "requirements": [{"text": "clause a", "kind": "record"}, {"text": "clause b"}],
        })
        assert body.requirements is not None
        assert [r.kind for r in body.requirements] == ["record", None]

    def test_create_body_rejects_a_record_clause_naming_a_test_ref(self) -> None:
        with pytest.raises(ValidationError):
            CreateWorkspaceBody.model_validate({
                "title": "t",
                "requirements": [{"text": "clause a", "kind": "record", "test_ref": "scn-1"}],
            })

    def test_decomposed_create_persists_kind_on_every_clause(self, tmp_path: Path) -> None:
        record = Requirement(text="the doc describes the API", kind="record")
        behaviour = Requirement(text="the API returns 200", kind="behaviour")
        tasks = [
            Task(
                id="a", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done a", covers=[record.id])],
            ),
            Task(
                id="b", status=TaskStatus.PENDING,
                dod_clauses=[TaskDoDClause(text="done b", covers=[behaviour.id])],
            ),
        ]
        ws = workspace_mod.create(
            "kind-ws", "d", root=tmp_path / "ws", tasks=tasks, requirements=[record, behaviour],
        )
        persisted = workspace_mod.read(ws.task_id, root=tmp_path / "ws")
        assert persisted.meta.requirements is not None
        assert [(r.text, r.kind) for r in persisted.meta.requirements] == [
            ("the doc describes the API", "record"),
            ("the API returns 200", "behaviour"),
        ]

    def test_uncovered_record_clause_is_still_refused_on_a_decomposed_create(self) -> None:
        record = Requirement(text="the doc describes the API", kind="record")
        body = CreateWorkspaceBody(title="t", requirements=[record])
        tasks = [Task(id="a", status=TaskStatus.PENDING), Task(id="b", status=TaskStatus.PENDING)]
        with pytest.raises(AppError) as exc_info:
            reject_uncovered_clauses(body, tasks)
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["uncovered_clause_ids"] == [record.id]


class TestSuppliedClauseIdValidation:
    def test_supplied_acceptance_clause_id_is_rejected_with_derived_id_message(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CreateWorkspaceBody.model_validate(
                {"title": "t", "requirements": [{"id": "req-caller-chosen", "text": "clause a"}]}
            )
        assert _SUPPLIED_CLAUSE_ID_DETAIL in str(exc_info.value)

    def test_empty_string_acceptance_clause_id_is_treated_as_omitted(self) -> None:
        body = CreateWorkspaceBody.model_validate(
            {"title": "t", "requirements": [{"id": "", "text": "clause a"}]}
        )
        assert body.requirements is not None
        assert body.requirements[0].id == Requirement(text="clause a").id

    def test_mixed_supplied_and_omitted_clause_ids_rejects_whole_body(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CreateWorkspaceBody.model_validate({
                "title": "t",
                "requirements": [
                    {"text": "clause a"},
                    {"id": "req-caller-chosen", "text": "clause b"},
                ],
            })
        assert _SUPPLIED_CLAUSE_ID_DETAIL in str(exc_info.value)

    def test_omitted_acceptance_clause_id_still_heals_to_canonical_id(self) -> None:
        body = CreateWorkspaceBody.model_validate(
            {"title": "t", "requirements": [{"text": "clause a"}]}
        )
        assert body.requirements is not None
        assert body.requirements[0].id == Requirement(text="clause a").id

    def test_constructed_requirement_object_bypasses_raw_clause_id_check(self) -> None:
        req = Requirement(id="req-caller-chosen", text="clause a")
        body = CreateWorkspaceBody(title="t", requirements=[req])
        assert body.requirements == [req]

    def test_non_dict_validation_input_is_returned_unchanged_and_still_fails(self) -> None:
        with pytest.raises(ValidationError):
            CreateWorkspaceBody.model_validate("not-a-mapping")


class TestReachesDecomposer:
    def test_reaches_decomposer_terminates_on_cyclic_graph(self) -> None:
        graph = {"a": ["b"], "b": ["a"]}
        assert _reaches_decomposer(graph, "a") is False

    def test_reaches_decomposer_returns_false_for_node_absent_from_graph(self) -> None:
        assert _reaches_decomposer({}, "ghost-team") is False

    def test_reaches_decomposer_returns_true_for_decomposer_itself(self) -> None:
        assert _reaches_decomposer({}, "decomposer") is True


class TestRejectUnseedableTeams:
    def test_reject_unseedable_teams_noop_when_no_tasks(self) -> None:
        body = CreateWorkspaceBody(title="t")
        reject_unseedable_teams(body, None)

    def test_reject_unseedable_teams_skips_entries_without_a_team(self) -> None:
        body = CreateWorkspaceBody(
            title="t", tasks=[TaskCreateEntry(id="a"), TaskCreateEntry(id="b")]
        )
        reject_unseedable_teams(body, None)

    @pytest.mark.parametrize(
        "team",
        ["research", "brainstorming", "architecture", "implementation_plan", "opportunity_scout"],
    )
    def test_reject_unseedable_teams_rejects_decomposer_reaching_teams(self, team: str) -> None:
        body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team=team)])
        with pytest.raises(AppError) as exc_info:
            reject_unseedable_teams(body, None)
        assert exc_info.value.status_code == 400
        detail = str(exc_info.value.detail)
        assert "'a'" in detail
        assert team in detail

    @pytest.mark.parametrize(
        "team", ["implementation", "implementation_fix", "verification", "pr_merge"]
    )
    def test_reject_unseedable_teams_accepts_implementation_shaped_teams(self, team: str) -> None:
        body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team=team)])
        reject_unseedable_teams(body, None)

    @pytest.mark.parametrize("team", ["decomposer", "brief_refine", "dependency_sweep"])
    def test_reject_unseedable_teams_rejects_internal_only_team_as_not_dispatchable(
        self, team: str
    ) -> None:
        body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team=team)])
        with pytest.raises(AppError) as exc_info:
            reject_unseedable_teams(body, None)
        assert exc_info.value.status_code == 400
        assert "not manually dispatchable" in str(exc_info.value.detail)

    def test_reject_unseedable_teams_rejects_unknown_team(self) -> None:
        body = CreateWorkspaceBody(
            title="t", tasks=[TaskCreateEntry(id="a", team="not-a-real-team")]
        )
        with pytest.raises(AppError) as exc_info:
            reject_unseedable_teams(body, None)
        assert exc_info.value.status_code == 400
        assert "unknown team" in str(exc_info.value.detail)

    def test_reject_unseedable_teams_names_the_offending_unit_among_several(self) -> None:
        body = CreateWorkspaceBody(
            title="t",
            tasks=[
                TaskCreateEntry(id="a", team="implementation"),
                TaskCreateEntry(id="b", team="research"),
                TaskCreateEntry(id="c", team="verification"),
            ],
        )
        with pytest.raises(AppError) as exc_info:
            reject_unseedable_teams(body, None)
        assert "'b'" in str(exc_info.value.detail)

    def test_reject_unseedable_teams_resolves_graph_from_requested_engine_key(self) -> None:
        from alan.engine_config import EngineConfig, RepoIdentity, register

        register(
            EngineConfig(
                repo=RepoIdentity(owner="acme", repo="no-decomposer-engine"),
                transition_graph={"research": ["architecture"], "architecture": []},
            )
        )
        body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team="research")])
        reject_unseedable_teams(body, "acme/no-decomposer-engine")

    def test_reject_unseedable_teams_falls_back_to_default_graph_for_unregistered_engine_key(
        self,
    ) -> None:
        body = CreateWorkspaceBody(title="t", tasks=[TaskCreateEntry(id="a", team="research")])
        with pytest.raises(AppError):
            reject_unseedable_teams(body, "no/such-engine")
