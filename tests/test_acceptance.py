from __future__ import annotations

from datetime import UTC, datetime

from alan.acceptance import (
    TASK_COUNT_BUDGET,
    all_clauses_satisfied,
    all_requirements_satisfied,
    clause_owner_map,
    evaluate_requirements,
    owned_clause_texts,
    reconcile_clauses,
    requirements_unbound,
    resolve_owners,
    scoped_clause_requirements,
    scoped_dod,
    unproven_clause_ids,
)
from alan.workspace import (
    Meta,
    Requirement,
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    _clause_id,
)


def _make_meta(
    requirements: list[Requirement] | None = None,
    tasks: list[Task] | None = None,
) -> Meta:
    return Meta(
        task_id="t1",
        title="T1",
        description="d",
        status=Status.ACTIVE,
        requirements=requirements,
        tasks=tasks or [],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _covering_task(
    clause_id: str, *, evidence: str | None = None, task_id: str = "sub"
) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="deliver it", covers=[clause_id], evidence=evidence)],
    )


class TestAllClausesSatisfied:
    def test_none_returns_true(self) -> None:
        assert all_clauses_satisfied(_make_meta(None)) is True

    def test_empty_returns_false(self) -> None:
        assert all_clauses_satisfied(_make_meta([])) is False

    def test_no_coverage_degrades_to_legacy_unbound_passes(self) -> None:
        assert all_clauses_satisfied(_make_meta([Requirement(text="c")])) is True

    def test_no_coverage_degrades_to_legacy_bound_fail_blocks(self) -> None:
        meta = _make_meta([Requirement(text="c", test_ref="t1", status="FAIL")])
        assert all_clauses_satisfied(meta) is False

    def test_covered_without_evidence_blocks(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta([clause], [_covering_task(clause.id)])
        assert all_clauses_satisfied(meta) is False

    def test_covered_with_evidence_passes(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta([clause], [_covering_task(clause.id, evidence="PR #5")])
        assert all_clauses_satisfied(meta) is True

    def test_test_bound_pass_overrides_coverage(self) -> None:
        clause = Requirement(text="c", test_ref="t1", status="PASS")
        meta = _make_meta([clause], [_covering_task(clause.id)])
        assert all_clauses_satisfied(meta) is True

    def test_uncovered_clause_does_not_block(self) -> None:
        covered = Requirement(text="covered")
        other = Requirement(text="uncovered")
        meta = _make_meta(
            [covered, other], [_covering_task(covered.id, evidence="PR #1")]
        )
        assert all_clauses_satisfied(meta) is True

    def test_mixed_evidenced_and_unevidenced_blocks(self) -> None:
        a = Requirement(text="a")
        b = Requirement(text="b")
        meta = _make_meta(
            [a, b],
            [
                _covering_task(a.id, evidence="PR #1", task_id="sub-a"),
                _covering_task(b.id, task_id="sub-b"),
            ],
        )
        assert all_clauses_satisfied(meta) is False

    def test_test_bound_fail_blocks_under_coverage(self) -> None:
        clause = Requirement(text="c", test_ref="t1", status="FAIL")
        meta = _make_meta([clause], [_covering_task(clause.id, evidence="PR #1")])
        assert all_clauses_satisfied(meta) is False

    def test_bogus_covers_does_not_enable_coverage_mode(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta([clause], [_covering_task("req-bogus")])
        assert all_clauses_satisfied(meta) is True

    def test_non_owner_evidence_does_not_satisfy_double_claimed_req(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta(
            [clause],
            [
                Task(
                    id="t-a", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[
                        TaskDoDClause(text="a", covers=[clause.id], evidence="PR #1")
                    ],
                ),
                Task(
                    id="t-b", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[TaskDoDClause(text="b", covers=[clause.id])],
                ),
            ],
        )
        assert all_clauses_satisfied(meta) is False

    def test_owner_evidence_satisfies_double_claimed_req(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta(
            [clause],
            [
                Task(
                    id="t-a", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[TaskDoDClause(text="a", covers=[clause.id])],
                ),
                Task(
                    id="t-b", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[
                        TaskDoDClause(text="b", covers=[clause.id], evidence="PR #2")
                    ],
                ),
            ],
        )
        assert all_clauses_satisfied(meta) is True

    def test_covered_union_includes_non_owner_task_own_unique_coverage(self) -> None:
        shared = Requirement(text="shared")
        a_only = Requirement(text="a only")
        meta = _make_meta(
            [shared, a_only],
            [
                Task(
                    id="t-a", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[
                        TaskDoDClause(text="a", covers=[shared.id, a_only.id])
                    ],
                ),
                Task(
                    id="t-b", status=TaskStatus.COMPLETED, parent_task_id="parent",
                    dod_clauses=[
                        TaskDoDClause(text="b", covers=[shared.id], evidence="PR #1")
                    ],
                ),
            ],
        )
        assert all_clauses_satisfied(meta) is False


class TestRequirementsUnbound:
    def test_none_returns_false(self) -> None:
        assert requirements_unbound(_make_meta(None)) is False

    def test_empty_returns_false(self) -> None:
        assert requirements_unbound(_make_meta([])) is False

    def test_genuinely_vacuous_returns_true(self) -> None:
        meta = _make_meta([Requirement(text="c")])
        assert requirements_unbound(meta) is True

    def test_test_ref_bound_clause_is_not_vacuous_even_when_uncovered(self) -> None:
        meta = _make_meta([Requirement(text="c", test_ref="t1")])
        assert requirements_unbound(meta) is False

    def test_covered_clause_is_not_vacuous(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta([clause], [_covering_task(clause.id, evidence="PR #1")])
        assert requirements_unbound(meta) is False

    def test_covered_without_evidence_is_not_vacuous(self) -> None:
        clause = Requirement(text="c")
        meta = _make_meta([clause], [_covering_task(clause.id)])
        assert requirements_unbound(meta) is False

    def test_does_not_change_all_clauses_satisfied_on_vacuous_fixture(self) -> None:
        meta = _make_meta([Requirement(text="c")])
        assert requirements_unbound(meta) is True
        assert all_clauses_satisfied(meta) is True


class TestUnprovenClauseIds:
    def test_none_requirements_returns_empty_set(self) -> None:
        assert unproven_clause_ids(None, []) == set()

    def test_empty_requirements_returns_empty_set(self) -> None:
        assert unproven_clause_ids([], []) == set()

    def test_unbound_uncovered_clause_is_unproven(self) -> None:
        clause = Requirement(text="c")
        assert unproven_clause_ids([clause], []) == {clause.id}

    def test_test_ref_bound_clause_is_proven_regardless_of_status(self) -> None:
        clause = Requirement(text="c", test_ref="t1", status="PENDING")
        assert unproven_clause_ids([clause], []) == set()

    def test_covered_clause_is_proven_even_without_evidence(self) -> None:
        clause = Requirement(text="c")
        task = _covering_task(clause.id)
        assert unproven_clause_ids([clause], [task]) == set()

    def test_bogus_covers_does_not_count_as_proof(self) -> None:
        clause = Requirement(text="c")
        task = _covering_task("req-bogus")
        assert unproven_clause_ids([clause], [task]) == {clause.id}

    def test_mixed_proven_and_unproven_clauses(self) -> None:
        proven = Requirement(text="proven", test_ref="t1")
        unproven = Requirement(text="unproven")
        result = unproven_clause_ids([proven, unproven], [])
        assert result == {unproven.id}

    def test_subsumes_requirements_unbound_on_partial_coverage(self) -> None:
        covered = Requirement(text="covered")
        uncovered = Requirement(text="uncovered")
        meta = _make_meta(
            [covered, uncovered], [_covering_task(covered.id, evidence="PR #1")]
        )
        result = unproven_clause_ids(meta.requirements, meta.tasks)
        assert result == {uncovered.id}
        assert requirements_unbound(meta) is False

    def test_returns_only_the_single_uncovered_id(self) -> None:
        covered = Requirement(text="covered")
        uncovered = Requirement(text="uncovered")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=[covered.id])],
        )
        result = unproven_clause_ids([covered, uncovered], [task])
        assert result == {uncovered.id}


def test_task_count_budget_is_ten() -> None:
    assert TASK_COUNT_BUDGET == 10


class TestAllRequirementsSatisfied:
    def test_legacy_none_returns_true(self) -> None:
        meta = _make_meta(requirements=None)
        assert all_requirements_satisfied(meta) is True

    def test_empty_list_returns_false(self) -> None:
        meta = _make_meta(requirements=[])
        assert all_requirements_satisfied(meta) is False

    def test_all_pass_returns_true(self) -> None:
        reqs = [
            Requirement(text="req1", test_ref="t1", status="PASS"),
            Requirement(text="req2", test_ref="t2", status="PASS"),
        ]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is True

    def test_one_fail_returns_false(self) -> None:
        reqs = [
            Requirement(text="req1", test_ref="t1", status="PASS"),
            Requirement(text="req2", test_ref="t2", status="FAIL"),
        ]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is False

    def test_one_pending_returns_false(self) -> None:
        reqs = [
            Requirement(text="req1", test_ref="t1", status="PASS"),
            Requirement(text="req2", test_ref="t2", status="PENDING"),
        ]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is False

    def test_single_pending_returns_false(self) -> None:
        reqs = [Requirement(text="req1", test_ref="t1", status="PENDING")]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is False

    def test_only_unbound_clauses_returns_true(self) -> None:
        reqs = [Requirement(text="clause a"), Requirement(text="clause b")]
        meta = _make_meta(requirements=reqs)
        assert all(r.test_ref is None for r in reqs)
        assert all_requirements_satisfied(meta) is True

    def test_single_unbound_clause_returns_true(self) -> None:
        meta = _make_meta(requirements=[Requirement(text="clause a")])
        assert all_requirements_satisfied(meta) is True

    def test_mixed_unbound_and_bound_pass_returns_true(self) -> None:
        reqs = [
            Requirement(text="unbound clause"),
            Requirement(text="bound clause", test_ref="t1", status="PASS"),
        ]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is True

    def test_mixed_unbound_and_bound_fail_returns_false(self) -> None:
        reqs = [
            Requirement(text="unbound clause"),
            Requirement(text="bound clause", test_ref="t1", status="FAIL"),
        ]
        meta = _make_meta(requirements=reqs)
        assert all_requirements_satisfied(meta) is False


class TestEvaluateRequirements:
    def test_matched_pass_scenario(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_foo")]
        results = [("test_foo", "PASS")]
        updated = evaluate_requirements(results, reqs)
        assert len(updated) == 1
        assert updated[0].status == "PASS"

    def test_matched_fail_scenario(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_foo")]
        results = [("test_foo", "FAIL")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "FAIL"

    def test_matched_advisory_scenario_resolves_pass(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_foo")]
        results = [("test_foo", "ADVISORY")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "PASS"

    def test_matched_skipped_maps_to_fail(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_foo")]
        results = [("test_foo", "SKIPPED")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "FAIL"

    def test_unmatched_stays_pending(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_missing")]
        results = [("test_other", "PASS")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "PENDING"

    def test_multiple_requirements_updated_independently(self) -> None:
        reqs = [
            Requirement(text="r1", test_ref="t_a"),
            Requirement(text="r2", test_ref="t_b"),
            Requirement(text="r3", test_ref="t_c"),
        ]
        results = [("t_a", "PASS"), ("t_b", "FAIL")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "PASS"
        assert updated[1].status == "FAIL"
        assert updated[2].status == "PENDING"

    def test_empty_requirements_returns_empty(self) -> None:
        updated = evaluate_requirements([("t_a", "PASS")], [])
        assert updated == []

    def test_empty_results_all_stay_pending(self) -> None:
        reqs = [Requirement(text="r1", test_ref="t_a")]
        updated = evaluate_requirements([], reqs)
        assert updated[0].status == "PENDING"

    def test_text_and_test_ref_preserved(self) -> None:
        reqs = [Requirement(text="the text", test_ref="my_test")]
        results = [("my_test", "PASS")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].text == "the text"
        assert updated[0].test_ref == "my_test"

    def test_matched_pending_stays_pending(self) -> None:
        reqs = [Requirement(text="r1", test_ref="test_foo")]
        results = [("test_foo", "PENDING")]
        updated = evaluate_requirements(results, reqs)
        assert updated[0].status == "PENDING"

    def test_unbound_clause_stays_pending(self) -> None:
        reqs = [Requirement(text="r1")]
        updated = evaluate_requirements([("", "PASS")], reqs)
        assert updated[0].test_ref is None
        assert updated[0].status == "PENDING"


class TestReconcileClauses:
    def test_empty_existing_mints_unbound_clauses(self) -> None:
        result = reconcile_clauses([], ["clause a", "clause b"])
        assert len(result) == 2
        assert all(c.test_ref is None and c.status == "PENDING" for c in result)
        assert result[0].id == _clause_id("req", "clause a")

    def test_idempotent_on_identical_text(self) -> None:
        existing = [Requirement(text="clause a", status="PASS")]
        result = reconcile_clauses(existing, ["clause a"])
        assert len(result) == 1
        assert result[0].status == "PASS"

    def test_preserves_existing_and_appends_new(self) -> None:
        existing = [Requirement(text="clause a", test_ref="t1", status="PASS")]
        result = reconcile_clauses(existing, ["clause b"])
        assert len(result) == 2
        assert result[0].status == "PASS"
        assert result[1].text == "clause b"

    def test_dedups_within_new_texts(self) -> None:
        result = reconcile_clauses([], ["dup", "dup"])
        assert len(result) == 1

    def test_empty_new_texts_returns_existing_unchanged(self) -> None:
        existing = [Requirement(text="clause a")]
        assert reconcile_clauses(existing, []) == existing

    def test_blank_text_skipped(self) -> None:
        assert reconcile_clauses([], ["   ", "", "real"]) == [
            Requirement(text="real")
        ]


class TestScopedDod:
    def test_task_not_found_returns_none(self) -> None:
        meta = _make_meta(requirements=[Requirement(text="r1")])
        assert scoped_dod(meta, "nonexistent") is None

    def test_empty_dod_clauses_returns_none(self) -> None:
        task = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[])
        meta = _make_meta(requirements=[Requirement(text="r1")], tasks=[task])
        assert scoped_dod(meta, "t-a") is None

    def test_no_parent_no_out_of_scope(self) -> None:
        req = Requirement(text="deliver feature")
        task = Task(
            id="t-a",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task])
        result = scoped_dod(meta, "t-a")
        assert result is not None
        in_scope, out_of_scope = result
        assert in_scope == ["deliver feature"]
        assert out_of_scope == []

    def test_requirements_none_falls_back_to_own_clause_texts(self) -> None:
        task = Task(
            id="t-a",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=["req-bogus"])],
        )
        meta = _make_meta(requirements=None, tasks=[task])
        assert scoped_dod(meta, "t-a") == (["done"], [])

    def test_bogus_covers_fall_back_to_clause_texts_instead_of_an_empty_fence(self) -> None:
        req = Requirement(text="real req")
        task = Task(
            id="t-a",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=["req-bogus"])],
        )
        meta = _make_meta(requirements=[req], tasks=[task])
        assert scoped_dod(meta, "t-a") == (["done"], [])

    def test_fallback_lists_sibling_clause_texts_as_out_of_scope(self) -> None:
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="hook keeps last-good data across a refetch")],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[
                TaskDoDClause(text="above-list region reserves its space"),
                TaskDoDClause(text="hook keeps last-good data across a refetch"),
            ],
        )
        unrelated = Task(
            id="t-c",
            status=TaskStatus.READY,
            parent_task_id="other-parent",
            dod_clauses=[TaskDoDClause(text="unrelated clause")],
        )
        meta = _make_meta(requirements=[], tasks=[task_a, task_b, unrelated])
        assert scoped_dod(meta, "t-a") == (
            ["hook keeps last-good data across a refetch"],
            ["above-list region reserves its space"],
        )

    def test_fallback_without_parent_has_no_out_of_scope(self) -> None:
        task = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[TaskDoDClause(text="done")])
        other = Task(id="t-b", status=TaskStatus.READY, dod_clauses=[TaskDoDClause(text="other")])
        meta = _make_meta(requirements=[], tasks=[task, other])
        assert scoped_dod(meta, "t-a") == (["done"], [])

    def test_sibling_owned_appears_in_out_of_scope(self) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req_b.id])],
        )
        meta = _make_meta(requirements=[req_a, req_b], tasks=[task_a, task_b])
        result = scoped_dod(meta, "t-a")
        assert result is not None
        in_scope, out_of_scope = result
        assert in_scope == ["feature A"]
        assert out_of_scope == ["feature B"]

    def test_shared_req_stays_in_scope_not_out(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        result_owner = scoped_dod(meta, "t-b")
        assert result_owner == (["shared feature"], [])
        result_non_owner = scoped_dod(meta, "t-a")
        assert result_non_owner == (["done a"], ["done b"])

    def test_parent_set_no_sibling_out_of_scope_empty(self) -> None:
        req = Requirement(text="solo feature")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a])
        result = scoped_dod(meta, "t-a")
        assert result is not None
        in_scope, out_of_scope = result
        assert in_scope == ["solo feature"]
        assert out_of_scope == []

    def test_partial_sibling_overlap(self) -> None:
        req_shared = Requirement(text="shared")
        req_own = Requirement(text="own only")
        req_sibling = Requirement(text="sibling only")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[
                TaskDoDClause(text="done a", covers=[req_shared.id, req_own.id])
            ],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[
                TaskDoDClause(text="done b", covers=[req_shared.id, req_sibling.id])
            ],
        )
        meta = _make_meta(
            requirements=[req_shared, req_own, req_sibling], tasks=[task_a, task_b]
        )
        result = scoped_dod(meta, "t-a")
        assert result is not None
        in_scope, out_of_scope = result
        assert in_scope == ["own only"]
        assert out_of_scope == ["shared", "sibling only"]

    def test_double_claimed_req_is_out_of_scope_for_non_owner(self) -> None:
        req_shared = Requirement(text="shared")
        req_a_only = Requirement(text="a only")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[
                TaskDoDClause(text="done a", covers=[req_shared.id, req_a_only.id])
            ],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req_shared.id])],
        )
        meta = _make_meta(requirements=[req_shared, req_a_only], tasks=[task_a, task_b])
        result = scoped_dod(meta, "t-a")
        assert result is not None
        in_scope, out_of_scope = result
        assert req_shared.text not in in_scope
        assert req_shared.text in out_of_scope

    def test_subtask_losing_every_claim_falls_back_to_clause_text_scope(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="clause text a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="clause text b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        assert scoped_dod(meta, "t-a") == (["clause text a"], ["clause text b"])

    def test_three_way_claim_last_sibling_owns(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="b", covers=[req.id])],
        )
        task_c = Task(
            id="t-c", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="c", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b, task_c])
        assert scoped_dod(meta, "t-c") == (["shared feature"], [])
        assert scoped_dod(meta, "t-a") == (["a"], ["b", "c"])
        assert scoped_dod(meta, "t-b") == (["b"], ["a", "c"])

    def test_parentless_tasks_unaffected_by_owner_resolution(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        assert scoped_dod(meta, "t-a") == (["shared feature"], [])
        assert scoped_dod(meta, "t-b") == (["shared feature"], [])

    def test_clean_partition_regression_unaffected_by_owner_resolution(self) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(
            id="t-a",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
        )
        task_b = Task(
            id="t-b",
            status=TaskStatus.READY,
            parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req_b.id])],
        )
        meta = _make_meta(requirements=[req_a, req_b], tasks=[task_a, task_b])
        assert scoped_dod(meta, "t-a") == (["feature A"], ["feature B"])
        assert scoped_dod(meta, "t-b") == (["feature B"], ["feature A"])


class TestScopedClauseRequirements:
    def test_task_not_found_returns_none(self) -> None:
        meta = _make_meta(requirements=[Requirement(text="r1")])
        assert scoped_clause_requirements(meta, "nonexistent") is None

    def test_empty_dod_clauses_returns_none(self) -> None:
        task = Task(id="t-a", status=TaskStatus.READY, dod_clauses=[])
        meta = _make_meta(requirements=[Requirement(text="r1")], tasks=[task])
        assert scoped_clause_requirements(meta, "t-a") is None

    def test_returns_owned_requirement_objects(self) -> None:
        req = Requirement(text="deliver feature")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task])
        result = scoped_clause_requirements(meta, "t-a")
        assert result == [req]

    def test_requirements_none_returns_empty_list(self) -> None:
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=["req-bogus"])],
        )
        meta = _make_meta(requirements=None, tasks=[task])
        assert scoped_clause_requirements(meta, "t-a") == []

    def test_bogus_covers_excluded(self) -> None:
        req = Requirement(text="real req")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=["req-bogus"])],
        )
        meta = _make_meta(requirements=[req], tasks=[task])
        assert scoped_clause_requirements(meta, "t-a") == []

    def test_sibling_owned_clause_never_returned(self) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req_b.id])],
        )
        meta = _make_meta(requirements=[req_a, req_b], tasks=[task_a, task_b])
        result = scoped_clause_requirements(meta, "t-a")
        assert result == [req_a]
        assert req_b not in result
        dod_result = scoped_dod(meta, "t-a")
        assert dod_result is not None
        assert dod_result[1] == ["feature B"]

    def test_shared_req_included_once(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        assert scoped_clause_requirements(meta, "t-a") == []
        assert scoped_clause_requirements(meta, "t-b") == [req]

    def test_double_claimed_req_excluded_for_non_owner(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        assert req not in (scoped_clause_requirements(meta, "t-a") or [])
        assert req in (scoped_clause_requirements(meta, "t-b") or [])

    def test_losing_every_claim_returns_empty_list_not_none(self) -> None:
        req = Requirement(text="shared feature")
        task_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[task_a, task_b])
        result = scoped_clause_requirements(meta, "t-a")
        assert result == []
        assert result is not None


class TestResolveOwners:
    def test_empty_entries_returns_empty_dict(self) -> None:
        assert resolve_owners([]) == {}

    def test_single_entry_maps_each_id_to_its_task(self) -> None:
        assert resolve_owners([("t-a", {"r1", "r2"})]) == {"r1": "t-a", "r2": "t-a"}

    def test_multi_way_overlap_last_entry_wins(self) -> None:
        result = resolve_owners([
            ("t-a", {"r1"}),
            ("t-b", {"r1", "r2"}),
            ("t-c", {"r1"}),
        ])
        assert result == {"r1": "t-c", "r2": "t-b"}


class TestClauseOwnerMap:
    def test_resolves_owners_for_flat_dispatch_tasks(self) -> None:
        req_a = Requirement(text="feature A")
        req_b = Requirement(text="feature B")
        task_a = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done a", covers=[req_a.id])],
        )
        task_b = Task(
            id="t-b", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done b", covers=[req_b.id])],
        )
        meta = _make_meta(requirements=[req_a, req_b], tasks=[task_a, task_b])
        assert clause_owner_map(meta) == {req_a.id: "t-a", req_b.id: "t-b"}

    def test_prefers_sibling_group_owner_over_root_task(self) -> None:
        req = Requirement(text="shared feature")
        parent = Task(
            id="parent", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="parent done", covers=[req.id])],
        )
        child_a = Task(
            id="t-a", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done a", covers=[req.id])],
        )
        child_b = Task(
            id="t-b", status=TaskStatus.READY, parent_task_id="parent",
            dod_clauses=[TaskDoDClause(text="done b", covers=[req.id])],
        )
        meta = _make_meta(requirements=[req], tasks=[parent, child_a, child_b])
        assert clause_owner_map(meta) == {req.id: "t-b"}

    def test_omits_unclaimed_clause_ids(self) -> None:
        claimed = Requirement(text="claimed")
        unclaimed = Requirement(text="unclaimed")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="done", covers=[claimed.id])],
        )
        meta = _make_meta(requirements=[claimed, unclaimed], tasks=[task])
        owners = clause_owner_map(meta)
        assert owners == {claimed.id: "t-a"}
        assert unclaimed.id not in owners


class TestOwnedClauseTexts:
    def test_task_not_found_returns_empty_dict(self) -> None:
        meta = _make_meta(requirements=[Requirement(text="r1")])
        assert owned_clause_texts(meta, "nonexistent") == {}

    def test_multiple_clauses_on_one_req_id_preserved_in_order(self) -> None:
        req = Requirement(text="r1")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[
                TaskDoDClause(text="first clause", covers=[req.id]),
                TaskDoDClause(text="second clause", covers=[req.id]),
            ],
        )
        meta = _make_meta(requirements=[req], tasks=[task])
        assert owned_clause_texts(meta, "t-a") == {
            req.id: ["first clause", "second clause"]
        }

    def test_clauses_grouped_by_distinct_covered_ids(self) -> None:
        req_a = Requirement(text="a")
        req_b = Requirement(text="b")
        task = Task(
            id="t-a", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="clause one", covers=[req_a.id, req_b.id])],
        )
        meta = _make_meta(requirements=[req_a, req_b], tasks=[task])
        assert owned_clause_texts(meta, "t-a") == {
            req_a.id: ["clause one"],
            req_b.id: ["clause one"],
        }
