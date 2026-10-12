from __future__ import annotations

from datetime import UTC, datetime

from alan import acceptance
from alan.workspace import Meta, Requirement, Status, Task, TaskDoDClause, TaskStatus


def _meta(requirements: list[Requirement] | None, tasks: list[Task]) -> Meta:
    now = datetime.now(UTC)
    return Meta(
        task_id="ws-1", title="T", description="d", status=Status.ACTIVE,
        requirements=requirements, tasks=tasks, created_at=now, updated_at=now,
    )


class TestIsWithdrawnAndLiveClauses:
    def test_is_withdrawn_true_when_field_set(self) -> None:
        req = Requirement(text="x", withdrawn_by_question_id="clause-stall-x")
        assert acceptance.is_withdrawn(req) is True

    def test_is_withdrawn_false_by_default(self) -> None:
        assert acceptance.is_withdrawn(Requirement(text="x")) is False

    def test_live_clauses_filters_withdrawn(self) -> None:
        live = Requirement(text="live")
        dead = Requirement(text="dead", withdrawn_by_question_id="clause-stall-x")
        assert acceptance.live_clauses([live, dead]) == [live]

    def test_operator_only_withdrawn_clause_hidden_from_a_later_pass(self) -> None:
        live = Requirement(text="live")
        operator_only = Requirement(
            text="needs an operator-only post-merge measurement",
            withdrawn_by_question_id="clause-audit-operator-only",
        )
        assert acceptance.live_clauses([live, operator_only]) == [live]


class TestAllRequirementsSatisfiedWithdrawal:
    def test_withdrawn_failing_test_bound_clause_does_not_block(self) -> None:
        req = Requirement(
            text="x", test_ref="t1", status="FAIL", withdrawn_by_question_id="clause-stall-x"
        )
        meta = _meta([req], [])
        assert acceptance.all_requirements_satisfied(meta) is True

    def test_live_failing_clause_still_blocks(self) -> None:
        req = Requirement(text="x", test_ref="t1", status="FAIL")
        meta = _meta([req], [])
        assert acceptance.all_requirements_satisfied(meta) is False


class TestAllClausesSatisfiedWithdrawal:
    def test_all_withdrawn_is_satisfied(self) -> None:
        req = Requirement(
            text="x", test_ref="t1", status="FAIL", withdrawn_by_question_id="clause-stall-x"
        )
        meta = _meta([req], [])
        assert acceptance.all_clauses_satisfied(meta) is True

    def test_withdrawn_clause_ignored_alongside_satisfied_live_clause(self) -> None:
        live = Requirement(text="live", test_ref="t1", status="PASS")
        dead = Requirement(
            text="dead", test_ref="t2", status="FAIL", withdrawn_by_question_id="clause-stall-x"
        )
        meta = _meta([live, dead], [])
        assert acceptance.all_clauses_satisfied(meta) is True

    def test_withdrawn_covered_unevidenced_clause_does_not_block(self) -> None:
        dead = Requirement(text="dead", withdrawn_by_question_id="clause-stall-x")
        task = Task(
            id="t1", status=TaskStatus.COMPLETED,
            dod_clauses=[TaskDoDClause(text="deliver", covers=[dead.id], evidence=None)],
        )
        meta = _meta([dead], [task])
        assert acceptance.all_clauses_satisfied(meta) is True


class TestUnprovenClauseIdsWithdrawal:
    def test_withdrawn_uncovered_unbound_clause_is_not_unproven(self) -> None:
        dead = Requirement(text="dead", withdrawn_by_question_id="clause-stall-x")
        assert acceptance.unproven_clause_ids([dead], []) == set()

    def test_live_uncovered_unbound_clause_is_still_unproven(self) -> None:
        live = Requirement(text="live")
        assert acceptance.unproven_clause_ids([live], []) == {live.id}


class TestScopedDodWithdrawal:
    def test_clause_covering_only_withdrawn_id_dropped_from_live_view(self) -> None:
        dead = Requirement(text="dead clause text", withdrawn_by_question_id="clause-stall-x")
        current = Task(
            id="current", parent_task_id="parent", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="dead clause text", covers=[dead.id])],
        )
        sibling = Task(
            id="sibling", parent_task_id="parent", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="sibling clause text", covers=[])],
        )
        meta = _meta([dead], [current, sibling])
        result = acceptance.scoped_dod(meta, "current")
        assert result == ([], ["sibling clause text"])

    def test_withdrawal_emptying_view_never_leaks_raw_dod_text_via_fallback(self) -> None:
        dead = Requirement(text="the withdrawn obligation", withdrawn_by_question_id="q1")
        current = Task(
            id="current", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="the withdrawn obligation", covers=[dead.id])],
        )
        meta = _meta([dead], [current])
        in_scope, _out_of_scope = acceptance.scoped_dod(meta, "current")  # type: ignore[misc]
        assert in_scope == []
        assert "the withdrawn obligation" not in in_scope

    def test_mixed_clause_covering_withdrawn_and_live_ids_keeps_full_text(self) -> None:
        live = Requirement(text="live clause text")
        dead = Requirement(text="dead clause text", withdrawn_by_question_id="clause-stall-x")
        current = Task(
            id="current", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="mixed", covers=[live.id, dead.id])],
        )
        meta = _meta([live, dead], [current])
        in_scope, _out_of_scope = acceptance.scoped_dod(meta, "current")  # type: ignore[misc]
        assert set(in_scope) == {"live clause text", "dead clause text"}

    def test_clause_covering_nothing_is_untouched(self) -> None:
        dead = Requirement(text="dead", withdrawn_by_question_id="clause-stall-x")
        current = Task(
            id="current", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="freeform clause", covers=[])],
        )
        meta = _meta([dead], [current])
        result = acceptance.scoped_dod(meta, "current")
        assert result == (["freeform clause"], [])

    def test_no_withdrawn_clauses_matches_pre_existing_behavior(self) -> None:
        live = Requirement(text="live clause text")
        current = Task(
            id="current", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="x", covers=[live.id])],
        )
        meta = _meta([live], [current])
        assert acceptance.scoped_dod(meta, "current") == (["live clause text"], [])
