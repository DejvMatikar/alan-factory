from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.teams._clause_audit as ca_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import ClauseOutcome, Decision, Handover
from alan.house_rules import HOUSE_RULES
from alan.teams import _fix_forward, _verdict
from alan.teams._clause_line_parse import ClauseVerdict
from alan.workspace import Requirement, Task, TaskDoDClause, TaskStatus, Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, gh_unreachable

_JUDGE = "## Verdict\n\nVERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n\n## Rationale\nfine"
_BEHAVIOUR = Requirement(text="the endpoint rejects an unauthenticated call", kind="behaviour")
_RECORD = Requirement(text="the change description names the migration owner", kind="record")
_OTHER_RECORD = Requirement(text="the runbook records the rollback steps", kind="record")
_KINDLESS = Requirement(text="the endpoint returns the created id")
_HOUSE_RULE_REQS = [Requirement(text=hr.text) for hr in HOUSE_RULES]


@pytest.fixture(autouse=True)
def _gh_down(_active_fake_forge: FakeForge) -> None:
    gh_unreachable()
    always(_active_fake_forge, "changed_files", None)


@pytest.fixture(autouse=True)
def _gh_subprocess_fails(patch_subprocess: Callable[..., None]) -> None:
    patch_subprocess(returncode=1)


@pytest.fixture(autouse=True)
def _reset_clause_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CLAUSE_AUDIT", raising=False)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _ws(
    tmp_path: Path, requirements: list[Requirement] | None, tasks: list[Task] | None = None,
) -> Workspace:
    base = create("record-kind-ws", "d", root=tmp_path / "ws")
    meta = base.meta.model_copy(update={"requirements": requirements, "tasks": tasks or []})
    return base.model_copy(update={"meta": meta})


def _result(verdicts: dict[str, ClauseVerdict]) -> ca_mod.ClauseAuditResult:
    rows = tuple(
        ca_mod.ClauseAuditRow(req_id=rid, verdict=verdict, evidence="cited evidence")
        for rid, verdict in verdicts.items()
    )
    return ca_mod.ClauseAuditResult(
        rows=rows, reported_failures=None, count_mismatch=False, parse_failed=False,
    )


def _audit(
    ws: Workspace,
    verdicts: dict[str, ClauseVerdict],
    *,
    task_id: str | None = None,
    judge_output: str = _JUDGE,
) -> ca_mod.ClauseAuditOutcome:
    token = ca_mod.begin(ws, 7, task_id or ws.task_id, DEFAULT_FORGE_TARGET)
    try:
        context = ca_mod._clause_audit_var.get()  # noqa: SLF001
        assert context is not None
        context.result = _result(verdicts)
        return ca_mod.apply(judge_output)
    finally:
        ca_mod.end(token)


def _advisory_rows(audit_root: Path) -> list[dict[str, object]]:
    log = audit_root / "verification_audit.jsonl"
    if not log.exists():
        return []
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    return [e for e in entries if e["action"] == "clause_record_advisory"]


def _table_row(output: str, req_id: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(f"| {req_id} |"))


def _decomposed(ws_reqs: list[Requirement]) -> list[Task]:
    return [
        Task(
            id="t-behaviour", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="behaviour done", covers=[ws_reqs[0].id])],
        ),
        Task(
            id="t-record", status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="record done", covers=[ws_reqs[1].id])],
        ),
    ]


class TestRecordOnlyFailureAdvises:
    def test_verdict_stands_and_nothing_escalates(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL"})
        assert _verdict.parse_verdict(outcome.judge_output) == "APPROVE"
        assert outcome.escalate_unactionable is False
        assert outcome.unactionable_rows == ()

    def test_no_clause_outcome_is_produced(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL"})
        assert outcome.clause_outcome is None

    def test_table_marks_the_record_row_advisory(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL"})
        assert "advisory" in _table_row(outcome.judge_output, _RECORD.id)
        assert "advisory" not in _table_row(outcome.judge_output, _BEHAVIOUR.id)

    def test_kindless_sibling_counts_as_the_authored_behaviour_clause(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_KINDLESS, _RECORD])
        outcome = _audit(ws, {_KINDLESS.id: "PASS", _RECORD.id: "FAIL"})
        assert _verdict.parse_verdict(outcome.judge_output) == "APPROVE"
        assert outcome.clause_outcome is None

    def test_a_request_changes_verdict_is_left_as_the_judge_wrote_it(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        judge = "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(
            ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL"}, judge_output=judge,
        )
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is None


class TestMixedFailures:
    def test_only_the_behaviour_id_is_actionable(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "FAIL"})
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_BEHAVIOUR.id,)
        assert outcome.escalate_unactionable is False

    def test_table_marks_only_the_record_row(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "FAIL"})
        assert "advisory" in _table_row(outcome.judge_output, _RECORD.id)
        assert "advisory" not in _table_row(outcome.judge_output, _BEHAVIOUR.id)

    def test_record_id_appears_nowhere_in_the_clause_outcome(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "FAIL"})
        clause_outcome = outcome.clause_outcome
        assert clause_outcome is not None
        assert _RECORD.id not in clause_outcome.actionable_ids
        assert _RECORD.id not in clause_outcome.clause_texts
        assert _RECORD.id not in clause_outcome.evidence
        assert _RECORD.id not in clause_outcome.unactionable_ids
        assert _RECORD.id not in clause_outcome.operator_only_ids

    def test_fix_worker_block_omits_the_record_clause(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "FAIL"})
        prior = _prior(ws, outcome.clause_outcome)
        block = _fix_forward.render_failing_clause_block(ws, prior, None)
        assert _BEHAVIOUR.id in block
        assert _RECORD.id not in block
        assert _RECORD.text not in block

    def test_behaviour_only_failure_is_enforced_as_before(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "PASS"})
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_BEHAVIOUR.id,)
        assert _advisory_rows(audit_root) == []

    def test_kindless_failure_blocks_beside_a_record_failure(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_KINDLESS, _RECORD])
        outcome = _audit(ws, {_KINDLESS.id: "FAIL", _RECORD.id: "FAIL"})
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_KINDLESS.id,)

    @pytest.mark.parametrize(
        "blocking_twin",
        [
            Requirement(text=_RECORD.text, kind="behaviour"),
            Requirement(text=_RECORD.text),
        ],
    )
    def test_shared_id_with_a_blocking_copy_is_enforced(
        self, tmp_path: Path, audit_root: Path, blocking_twin: Requirement
    ) -> None:
        ws = _ws(tmp_path, [blocking_twin, _RECORD])
        outcome = _audit(ws, {_RECORD.id: "FAIL"})
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_RECORD.id,)
        assert "advisory" not in _table_row(outcome.judge_output, _RECORD.id)
        assert _advisory_rows(audit_root) == []


class TestDecomposedSubtasks:
    def test_record_only_subtask_takes_its_failure_as_advisory(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        reqs = [_BEHAVIOUR, _RECORD]
        ws = _ws(tmp_path, reqs, _decomposed(reqs))
        outcome = _audit(ws, {_RECORD.id: "FAIL"}, task_id="t-record")
        assert _verdict.parse_verdict(outcome.judge_output) == "APPROVE"
        assert outcome.escalate_unactionable is False
        assert outcome.clause_outcome is None
        assert "advisory" in _table_row(outcome.judge_output, _RECORD.id)

    def test_advisory_row_names_the_subtask(self, tmp_path: Path, audit_root: Path) -> None:
        reqs = [_BEHAVIOUR, _RECORD]
        ws = _ws(tmp_path, reqs, _decomposed(reqs))
        _audit(ws, {_RECORD.id: "FAIL"}, task_id="t-record")
        assert [(r["task_id"], r["req_id"]) for r in _advisory_rows(audit_root)] == [
            ("t-record", _RECORD.id)
        ]

    def test_behaviour_subtask_still_blocks(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        reqs = [_BEHAVIOUR, _RECORD]
        ws = _ws(tmp_path, reqs, _decomposed(reqs))
        outcome = _audit(ws, {_BEHAVIOUR.id: "FAIL"}, task_id="t-behaviour")
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_BEHAVIOUR.id,)

    def test_record_only_workspace_subtask_still_blocks(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        reqs = [_RECORD, _OTHER_RECORD]
        ws = _ws(tmp_path, reqs, _decomposed(reqs))
        outcome = _audit(ws, {_OTHER_RECORD.id: "FAIL"}, task_id="t-record")
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_OTHER_RECORD.id,)


class TestWithdrawalDoesNotFlipTheFloor:
    def test_withdrawing_every_behaviour_clause_keeps_record_failures_advisory(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        withdrawn = _BEHAVIOUR.model_copy(update={"withdrawn_by_question_id": "clause-stall-a"})
        ws = _ws(tmp_path, [withdrawn, _RECORD])
        outcome = _audit(ws, {_RECORD.id: "FAIL"})
        assert _verdict.parse_verdict(outcome.judge_output) == "APPROVE"
        assert outcome.escalate_unactionable is False
        assert outcome.clause_outcome is None

    def test_withdrawn_behaviour_clause_still_counts_as_authored(self, tmp_path: Path) -> None:
        withdrawn = _BEHAVIOUR.model_copy(update={"withdrawn_by_question_id": "clause-stall-a"})
        assert ca_mod._floor_active(_ws(tmp_path, [withdrawn, _RECORD])) is False  # noqa: SLF001


class TestAntiGamingFloor:
    def test_record_only_workspace_blocks_a_record_failure(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_RECORD, _OTHER_RECORD])
        outcome = _audit(ws, {_RECORD.id: "FAIL", _OTHER_RECORD.id: "PASS"})
        assert _verdict.parse_verdict(outcome.judge_output) == "REQUEST_CHANGES"
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_RECORD.id,)
        assert _advisory_rows(audit_root) == []

    def test_table_says_why_and_marks_no_row_advisory(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_RECORD, _OTHER_RECORD])
        outcome = _audit(ws, {_RECORD.id: "FAIL", _OTHER_RECORD.id: "PASS"})
        assert "No behaviour clause was authored; every clause is enforced." in (
            outcome.judge_output
        )
        assert "advisory" not in _table_row(outcome.judge_output, _RECORD.id)

    def test_house_rules_do_not_switch_the_floor_off(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_RECORD, *_HOUSE_RULE_REQS])
        outcome = _audit(ws, {_RECORD.id: "FAIL"})
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_RECORD.id,)
        assert _advisory_rows(audit_root) == []

    def test_house_rule_failure_blocks_in_a_record_only_workspace(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_RECORD, *_HOUSE_RULE_REQS])
        outcome = _audit(ws, {_HOUSE_RULE_REQS[0].id: "FAIL", _RECORD.id: "PASS"})
        assert outcome.clause_outcome is not None
        assert outcome.clause_outcome.actionable_ids == (_HOUSE_RULE_REQS[0].id,)

    def test_authored_behaviour_clause_switches_the_floor_off_beside_house_rules(
        self, tmp_path: Path, audit_root: Path  # noqa: ARG002
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD, *_HOUSE_RULE_REQS])
        outcome = _audit(ws, {_RECORD.id: "FAIL"})
        assert outcome.clause_outcome is None
        assert "No behaviour clause was authored" not in outcome.judge_output

    def test_floor_is_evaluated_over_the_workspace_not_the_selected_subtask(
        self, tmp_path: Path
    ) -> None:
        reqs = [_BEHAVIOUR, _RECORD]
        ws = _ws(tmp_path, reqs, _decomposed(reqs))
        token = ca_mod.begin(ws, 7, "t-record", DEFAULT_FORGE_TARGET)
        try:
            context = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert context is not None
            assert [r.id for r in context.requirements] == [_RECORD.id]
            assert context.floor_active is False
        finally:
            ca_mod.end(token)

    @pytest.mark.parametrize(
        ("requirements", "expected"),
        [
            ([_RECORD], True),
            ([_RECORD, _OTHER_RECORD], True),
            ([_RECORD, *_HOUSE_RULE_REQS], True),
            ([_BEHAVIOUR, _RECORD], False),
            ([_KINDLESS, _RECORD], False),
            ([_RECORD, *_HOUSE_RULE_REQS, _KINDLESS], False),
        ],
    )
    def test_floor_active_over_authored_clauses(
        self, tmp_path: Path, requirements: list[Requirement], expected: bool
    ) -> None:
        assert ca_mod._floor_active(_ws(tmp_path, requirements)) is expected  # noqa: SLF001

    def test_missing_requirements_do_not_raise(self, tmp_path: Path) -> None:
        assert ca_mod._floor_active(_ws(tmp_path, None)) is True  # noqa: SLF001

    def test_author_clause_whose_text_equals_a_house_rule_is_not_counted(
        self, tmp_path: Path
    ) -> None:
        echo = Requirement(text=HOUSE_RULES[0].text, kind="behaviour")
        assert ca_mod._floor_active(_ws(tmp_path, [echo, _RECORD])) is True  # noqa: SLF001


class TestAdvisoryAuditRows:
    def test_one_row_per_advisory_failure_naming_clause_and_verdict(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD, _OTHER_RECORD])
        _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL", _OTHER_RECORD.id: "FAIL"})
        rows = _advisory_rows(audit_root)
        assert [r["req_id"] for r in rows] == [_RECORD.id, _OTHER_RECORD.id]
        assert {r["judging_verdict"] for r in rows} == {"APPROVE"}
        assert {r["pr_number"] for r in rows} == {7}
        assert {r["task_id"] for r in rows} == {ws.task_id}

    def test_rows_carry_no_redundant_verdict_field(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        _audit(ws, {_RECORD.id: "FAIL"})
        assert "verdict" not in _advisory_rows(audit_root)[0]

    def test_no_row_for_a_behaviour_failure_in_a_mixed_pass(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        _audit(ws, {_BEHAVIOUR.id: "FAIL", _RECORD.id: "FAIL"})
        assert [r["req_id"] for r in _advisory_rows(audit_root)] == [_RECORD.id]

    def test_judging_verdict_is_null_when_the_judge_output_has_none(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        _audit(ws, {_RECORD.id: "FAIL"}, judge_output="no verdict line at all")
        assert _advisory_rows(audit_root)[0]["judging_verdict"] is None

    def test_judging_verdict_records_a_request_changes_judge(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        judge = "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
        _audit(ws, {_RECORD.id: "FAIL"}, judge_output=judge)
        assert _advisory_rows(audit_root)[0]["judging_verdict"] == "REQUEST_CHANGES"

    def test_no_row_when_the_record_clause_passes(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "PASS"})
        assert _advisory_rows(audit_root) == []

    def test_report_mode_writes_no_row_but_still_marks_the_table(
        self, tmp_path: Path, audit_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALAN_CLAUSE_AUDIT", "report")
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        outcome = _audit(ws, {_BEHAVIOUR.id: "PASS", _RECORD.id: "FAIL"})
        assert _advisory_rows(audit_root) == []
        assert "advisory" in _table_row(outcome.judge_output, _RECORD.id)
        assert _verdict.parse_verdict(outcome.judge_output) == "APPROVE"

    def test_unverifiable_record_row_is_neither_marked_nor_recorded(
        self, tmp_path: Path, audit_root: Path
    ) -> None:
        ws = _ws(tmp_path, [_BEHAVIOUR, _RECORD])
        token = ca_mod.begin(ws, 7, ws.task_id, DEFAULT_FORGE_TARGET)
        try:
            context = ca_mod._clause_audit_var.get()  # noqa: SLF001
            assert context is not None
            context.result = ca_mod._degraded_result(context.requirements)  # noqa: SLF001
            outcome = ca_mod.apply(_JUDGE)
        finally:
            ca_mod.end(token)
        assert "advisory" not in _table_row(outcome.judge_output, _RECORD.id)
        assert _advisory_rows(audit_root) == []


class TestRenderClauseTableMarks:
    def test_default_arguments_mark_nothing(self) -> None:
        table = ca_mod.render_clause_table(_result({"req-a": "FAIL"}))
        assert "advisory" not in table
        assert "No behaviour clause was authored" not in table

    def test_only_failed_advisory_rows_are_marked(self) -> None:
        table = ca_mod.render_clause_table(
            _result({"req-a": "FAIL", "req-b": "PASS", "req-c": "FAIL"}),
            frozenset({"req-a", "req-b"}),
        )
        assert "advisory" in _table_row(table, "req-a")
        assert "advisory" not in _table_row(table, "req-b")
        assert "advisory" not in _table_row(table, "req-c")

    def test_floor_note_is_rendered_when_the_floor_is_active(self) -> None:
        table = ca_mod.render_clause_table(_result({"req-a": "PASS"}), floor_active=True)
        assert "_No behaviour clause was authored; every clause is enforced._" in table


def _prior(ws: Workspace, outcome: ClauseOutcome | None) -> Handover:
    return Handover(
        handover_id="h1",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation_fix"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verification review",
        decisions=[
            Decision(id="d1", statement="s", rationale="r", confidence=0.5, reversibility="medium")
        ],
        clause_outcome=outcome,
    )
