from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alan.handover import Handover
from alan.queue import Job, JobStatus
from alan.terminal_outcome import (
    _REASON_MAX,
    TerminalOutcome,
    _escalated_reason,
    _failed_reason_from_jobs,
    _failed_reason_from_workflow,
    _first_line,
    _merged_sibling_prs,
    _with_merged_credit,
    has_merged_sibling,
    merged_sibling_pr_number,
    resolve_terminal_outcome,
)
from alan.workspace import (
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    Workspace,
    create,
)


def _ws(tmp_path: Path, status: Status = Status.ACTIVE) -> Workspace:
    ws = create("test", "desc", root=tmp_path)
    from alan.workspace import transition

    return transition(ws, status)


def _handover(handover_type: str = "forward", full_synthesis: str = "done") -> Handover:
    return Handover(
        handover_id="h1",
        handover_type=handover_type,  # type: ignore[arg-type]
        task_id="t1",
        from_team="implementation",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=full_synthesis,
    )


def _job(error: str | None = None) -> Job:
    return Job(
        id=1,
        task_id="t1",
        workspace_id="t1",
        team="implementation",
        status=JobStatus.FAILED,
        enqueued_at=datetime.now(UTC),
        error=error,
    )


def _task(task_id: str, status: TaskStatus, evidences: list[str | None]) -> Task:
    return Task(
        id=task_id,
        status=status,
        dod_clauses=[TaskDoDClause(text="c", evidence=e) for e in evidences],
    )


def _ws_with_tasks(
    tmp_path: Path, tasks: list[Task], status: Status = Status.FAILED
) -> Workspace:
    ws = _ws(tmp_path, status)
    meta = ws.meta.model_copy(update={"tasks": tasks})
    return ws.model_copy(update={"meta": meta})


class TestFirstLine:
    def test_single_line(self) -> None:
        assert _first_line("hello world") == "hello world"

    def test_multiline_takes_first(self) -> None:
        assert _first_line("first\nsecond\nthird") == "first"

    def test_truncates_at_max_chars(self) -> None:
        assert _first_line("x" * 300) == "x" * 200

    def test_strips_whitespace(self) -> None:
        assert _first_line("  hello  \n  rest") == "hello"


class TestEscalatedReason:
    def test_empty_handovers_returns_fallback(self) -> None:
        result = _escalated_reason([])
        assert "escalated" in result.lower()

    def test_no_escalation_type_returns_fallback(self) -> None:
        result = _escalated_reason([_handover("forward", "some synthesis")])
        assert "escalated" in result.lower()

    def test_escalation_with_synthesis_returns_first_line(self) -> None:
        h = _handover("escalation", "Cannot proceed\nbecause reasons")
        assert _escalated_reason([h]) == "Cannot proceed"

    def test_uses_last_escalation_handover(self) -> None:
        h1 = _handover("escalation", "first escalation")
        h2 = _handover("escalation", "second escalation")
        assert _escalated_reason([h1, h2]) == "second escalation"

    def test_escalation_with_empty_synthesis_skipped(self) -> None:
        h_empty = _handover("escalation", "")
        h_ok = _handover("escalation", "real reason")
        assert _escalated_reason([h_empty, h_ok]) == "real reason"


class TestFailedReasonFromWorkflow:
    def test_no_invocations_returns_none(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        assert _failed_reason_from_workflow(ws) is None

    def test_workflow_error_on_load_is_skipped(self, tmp_path: Path) -> None:
        from alan.workflow import WorkflowError

        ws = _ws(tmp_path, Status.FAILED)
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch(
                "alan.terminal_outcome.workflow_mod.load_workflow",
                side_effect=WorkflowError("corrupt"),
            ),
        ):
            assert _failed_reason_from_workflow(ws) is None

    def test_os_error_on_load_is_skipped(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch(
                "alan.terminal_outcome.workflow_mod.load_workflow",
                side_effect=OSError("no file"),
            ),
        ):
            assert _failed_reason_from_workflow(ws) is None

    def test_empty_task_list_returns_none(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        doc = MagicMock()
        doc.tasks = []
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            assert _failed_reason_from_workflow(ws) is None

    def test_task_with_other_status_skipped(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task = MagicMock()
        task.status = "running"
        task.error = "something"
        doc = MagicMock()
        doc.tasks = [task]
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            assert _failed_reason_from_workflow(ws) is None

    def test_failed_task_without_error_skipped(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task = MagicMock()
        task.status = "failed"
        task.error = None
        doc = MagicMock()
        doc.tasks = [task]
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            assert _failed_reason_from_workflow(ws) is None

    def test_failed_task_with_error_returned(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task = MagicMock()
        task.status = "failed"
        task.error = "build error\nmore details"
        doc = MagicMock()
        doc.tasks = [task]
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            assert _failed_reason_from_workflow(ws) == "build error"

    def test_interrupted_task_with_error_returned(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task = MagicMock()
        task.status = "interrupted"
        task.error = "interrupted by restart"
        doc = MagicMock()
        doc.tasks = [task]
        with (
            patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            assert _failed_reason_from_workflow(ws) == "interrupted by restart"

    def test_uses_latest_invocation_first(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task_old = MagicMock()
        task_old.status = "failed"
        task_old.error = "old error"
        doc_old = MagicMock()
        doc_old.tasks = [task_old]

        task_new = MagicMock()
        task_new.status = "failed"
        task_new.error = "new error"
        doc_new = MagicMock()
        doc_new.tasks = [task_new]

        def _load(path: Path) -> MagicMock:
            return doc_new if "inv_002" in str(path) else doc_old

        with (
            patch(
                "alan.terminal_outcome.workflow_mod.list_invocations",
                return_value=["inv_001", "inv_002"],
            ),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", side_effect=_load),
        ):
            assert _failed_reason_from_workflow(ws) == "new error"


class TestFailedReasonFromJobs:
    def test_empty_list_returns_none(self) -> None:
        assert _failed_reason_from_jobs([]) is None

    def test_job_without_error_returns_none(self) -> None:
        assert _failed_reason_from_jobs([_job(error=None)]) is None

    def test_job_with_error_returns_first_line(self) -> None:
        assert _failed_reason_from_jobs([_job(error="job failed\ndetails")]) == "job failed"

    def test_uses_latest_job_with_error(self) -> None:
        j1 = _job(error="first error")
        j2 = _job(error="second error")
        assert _failed_reason_from_jobs([j1, j2]) == "second error"


class TestResolveTerminalOutcome:
    def test_active_returns_none(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.ACTIVE)
        assert resolve_terminal_outcome(ws, [], []) is None

    def test_completed_returns_none(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.COMPLETED)
        assert resolve_terminal_outcome(ws, [], []) is None

    def test_escalated_with_synthesis(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.ESCALATED)
        h = _handover("escalation", "Could not resolve conflict")
        result = resolve_terminal_outcome(ws, [h], [])
        assert result == TerminalOutcome(outcome="escalated", reason="Could not resolve conflict")

    def test_escalated_fallback_when_no_matching_handover(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.ESCALATED)
        result = resolve_terminal_outcome(ws, [], [])
        assert result is not None
        assert result.outcome == "escalated"

    def test_escalated_carries_merge_block_class(self, tmp_path: Path) -> None:
        from alan.workspace import transition

        ws = transition(
            _ws(tmp_path, Status.ESCALATED),
            Status.ESCALATED,
            merge_block_class="stale_review",
        )
        h = _handover("escalation", "Stalled on stale review")
        result = resolve_terminal_outcome(ws, [h], [])
        assert result is not None
        assert result.block_class == "stale_review"

    def test_escalated_with_cleared_merge_block_class_yields_none(
        self, tmp_path: Path
    ) -> None:
        from alan.workspace import transition

        ws = transition(
            _ws(tmp_path, Status.ESCALATED),
            Status.ESCALATED,
            merge_block_class="stale_review",
        )
        ws = transition(ws, Status.ESCALATED, clear_merge_block_class=True)
        h = _handover("escalation", "Cannot proceed")
        result = resolve_terminal_outcome(ws, [h], [])
        assert result is not None
        assert result.block_class is None

    def test_failed_carries_merge_block_class(self, tmp_path: Path) -> None:
        from alan.workspace import transition

        ws = transition(
            _ws(tmp_path, Status.FAILED),
            Status.FAILED,
            merge_block_class="sonar_hotspot",
        )
        with patch(
            "alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]
        ):
            result = resolve_terminal_outcome(ws, [], [_job(error="boom")])
        assert result is not None
        assert result.block_class == "sonar_hotspot"

    def test_failed_retains_stale_merge_block_class_pending_1220(
        self, tmp_path: Path
    ) -> None:
        from alan.workspace import transition

        ws = transition(
            _ws(tmp_path, Status.FAILED), Status.FAILED, merge_block_class="dirty"
        )
        with patch(
            "alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]
        ):
            result = resolve_terminal_outcome(ws, [], [_job(error="boom")])
        assert result is not None
        assert result.block_class == "dirty"

    def test_failed_uses_workflow_reason(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        task = MagicMock()
        task.status = "failed"
        task.error = "workflow broke"
        doc = MagicMock()
        doc.tasks = [task]
        with (
            patch(
                "alan.terminal_outcome.workflow_mod.list_invocations", return_value=["inv_001"]
            ),
            patch("alan.terminal_outcome.workflow_mod.load_workflow", return_value=doc),
        ):
            result = resolve_terminal_outcome(ws, [], [])
        assert result == TerminalOutcome(outcome="failed", reason="workflow broke")

    def test_failed_falls_back_to_job_reason(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        with patch(
            "alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]
        ):
            result = resolve_terminal_outcome(ws, [], [_job(error="job bombed")])
        assert result == TerminalOutcome(outcome="failed", reason="job bombed")

    def test_failed_falls_back_to_static_message(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path, Status.FAILED)
        with patch(
            "alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]
        ):
            result = resolve_terminal_outcome(ws, [], [])
        assert result is not None
        assert result.outcome == "failed"
        assert "failed" in result.reason.lower()

    @pytest.mark.parametrize("status", [Status.AWAITING_INPUT, Status.ARCHIVED, Status.PAUSED])
    def test_other_statuses_return_none(self, tmp_path: Path, status: Status) -> None:
        ws = _ws(tmp_path, status)
        assert resolve_terminal_outcome(ws, [], []) is None


class TestMergedSiblingPrs:
    def test_collects_dedups_and_skips_non_completed_and_empty(
        self, tmp_path: Path
    ) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #334", "PR #334", None]),
                _task("t2", TaskStatus.FAILED, ["PR #999"]),
            ],
        )
        assert _merged_sibling_prs(ws) == ["PR #334"]

    def test_collects_multiple_distinct_prs(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #334"]),
                _task("t2", TaskStatus.COMPLETED, ["PR #335"]),
            ],
        )
        assert _merged_sibling_prs(ws) == ["PR #334", "PR #335"]

    def test_no_tasks_returns_empty(self, tmp_path: Path) -> None:
        assert _merged_sibling_prs(_ws(tmp_path, Status.FAILED)) == []


class TestWithMergedCredit:
    def test_no_merged_returns_base_unchanged(self) -> None:
        assert _with_merged_credit("base reason", []) == "base reason"

    def test_appends_credit(self) -> None:
        assert (
            _with_merged_credit("It failed.", ["PR #334"])
            == "It failed. Already merged: PR #334."
        )

    def test_truncates_to_reason_max_keeping_credit(self) -> None:
        base = "x" * _REASON_MAX
        result = _with_merged_credit(base, ["PR #334"])
        assert len(result) <= _REASON_MAX
        assert result.endswith("Already merged: PR #334.")
        assert result == result.strip()

    def test_credit_longer_than_max_drops_head_no_leading_space(self) -> None:
        result = _with_merged_credit("base", ["PR #" + "9" * _REASON_MAX])
        assert len(result) <= _REASON_MAX
        assert not result.startswith(" ")
        assert result.startswith("Already merged: PR #9")


class TestResolveCreditsMergedSiblings:
    def test_failed_credits_merged_sibling_pr(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #334"]),
                _task("t2", TaskStatus.FAILED, [None]),
            ],
        )
        with patch(
            "alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]
        ):
            result = resolve_terminal_outcome(ws, [], [])
        assert result is not None
        assert result.outcome == "failed"
        assert result.reason.endswith("Already merged: PR #334.")


class TestHasMergedSibling:
    def test_true_when_completed_with_pr_evidence(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #334"]),
                _task("t2", TaskStatus.FAILED, [None]),
            ],
        )
        assert has_merged_sibling(ws) is True

    def test_false_when_no_completed_tasks(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [_task("t1", TaskStatus.FAILED, [None])],
        )
        assert has_merged_sibling(ws) is False

    def test_false_when_completed_but_no_evidence(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, [None]),
                _task("t2", TaskStatus.FAILED, [None]),
            ],
        )
        assert has_merged_sibling(ws) is False


class TestMergedSiblingPrNumber:
    def test_parses_single(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path, [_task("t1", TaskStatus.COMPLETED, ["PR #498"])]
        )
        assert merged_sibling_pr_number(ws) == 498

    def test_returns_highest_of_several(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #498"]),
                _task("t2", TaskStatus.COMPLETED, ["PR #500", "PR #333"]),
            ],
        )
        assert merged_sibling_pr_number(ws) == 500

    def test_none_when_no_merged_sibling(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path, [_task("t1", TaskStatus.FAILED, [None])]
        )
        assert merged_sibling_pr_number(ws) is None

    def test_none_when_evidence_has_no_digits(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path, [_task("t1", TaskStatus.COMPLETED, ["PR #latest"])]
        )
        assert merged_sibling_pr_number(ws) is None


class TestResolveTerminalOutcomePartial:
    def test_partial_returns_partial_outcome(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [
                _task("t1", TaskStatus.COMPLETED, ["PR #334"]),
                _task("t2", TaskStatus.FAILED, [None]),
            ],
            status=Status.PARTIAL,
        )
        with patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]):
            result = resolve_terminal_outcome(ws, [], [])
        assert result is not None
        assert result.outcome == "partial"
        assert result.reason.endswith("Already merged: PR #334.")

    def test_partial_uses_job_reason(self, tmp_path: Path) -> None:
        ws = _ws_with_tasks(
            tmp_path,
            [_task("t1", TaskStatus.COMPLETED, ["PR #5"])],
            status=Status.PARTIAL,
        )
        with patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]):
            result = resolve_terminal_outcome(ws, [], [_job(error="partial fail")])
        assert result is not None
        assert result.outcome == "partial"
        assert "partial fail" in result.reason

    def test_partial_carries_merge_block_class(self, tmp_path: Path) -> None:
        from alan.workspace import transition

        ws = _ws_with_tasks(
            tmp_path,
            [_task("t1", TaskStatus.COMPLETED, ["PR #5"])],
            status=Status.PARTIAL,
        )
        ws = transition(ws, Status.PARTIAL, merge_block_class="stale_review")
        with patch("alan.terminal_outcome.workflow_mod.list_invocations", return_value=[]):
            result = resolve_terminal_outcome(ws, [], [])
        assert result is not None
        assert result.block_class == "stale_review"
