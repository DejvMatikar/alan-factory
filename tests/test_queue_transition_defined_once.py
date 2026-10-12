from __future__ import annotations

import ast
from pathlib import Path

from alan import queue

_TRANSITION_SHAPE = "UPDATE jobs SET status"
_RETAINED = {"_CLAIM_SQL", "_FINISH_SQL"}
_DELETED = (
    "_INTERRUPT_SQL",
    "_INTERRUPT_WITH_RESUME_SQL",
    "_INTERRUPT_CLEAR_RESUME_SQL",
    "_REQUEUE_RESUME_SQL",
    "_REQUEUE_PENDING_SQL",
    "_REQUEUE_FAILED_FOR_RESUME_SQL",
    "_REQUEUE_FAILED_WITH_RESUME_SQL",
    "_REQUEUE_SERVER_ERROR_SQL",
)


def _status_update_definitions() -> set[str]:
    tree = ast.parse(Path(queue.__file__).read_text(encoding="utf-8"))
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value.startswith(_TRANSITION_SHAPE)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def test_exactly_one_transition_shaped_status_update_is_defined_beyond_the_retained_two() -> None:
    assert _status_update_definitions() - _RETAINED == {"_TRANSITION_SQL"}


def test_claim_and_finish_statements_are_deliberately_retained() -> None:
    assert _RETAINED <= _status_update_definitions()


def test_the_eight_collapsed_constants_are_gone() -> None:
    assert [name for name in _DELETED if hasattr(queue, name)] == []


def test_the_single_statement_binds_eighteen_parameters() -> None:
    assert queue._TRANSITION_SQL.count("?") == 18  # noqa: SLF001


def test_the_single_statement_is_guarded_by_id_and_source_status() -> None:
    assert queue._TRANSITION_SQL.endswith(  # noqa: SLF001
        "WHERE id = ? AND status = ? RETURNING *"
    )


def test_the_count_and_advance_statements_are_not_status_shaped_and_survive() -> None:
    assert queue._REQUEUE_COUNT_FOR_TASK_SQL.startswith("SELECT")  # noqa: SLF001
    assert queue._ADVANCE_ELIGIBLE_AT_SQL.startswith("UPDATE jobs SET eligible_at")  # noqa: SLF001
