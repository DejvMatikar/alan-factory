from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from alan import workspace as workspace_mod
from alan.handover import ClauseOutcome, Decision, Handover, save_handover
from alan.teams import _fix_forward
from alan.workspace import Requirement, Workspace

HEADING = "## Failing acceptance clauses"
_DECISION = Decision(
    id="d1", statement="s", rationale="r", confidence=0.5, reversibility="medium"
)


def _ws(root: Path, requirements: list[Requirement] | None = None) -> Workspace:
    ws = workspace_mod.create("Fix WS", "d", root=root)
    if requirements is None:
        return ws
    return workspace_mod.update_requirements(ws, requirements)


def _outcome(
    ws: Workspace,
    *,
    actionable_ids: tuple[str, ...],
    clause_texts: dict[str, str],
    evidence: dict[str, str] | None = None,
    task_id: str | None = None,
) -> ClauseOutcome:
    return ClauseOutcome(
        task_id=task_id if task_id is not None else ws.task_id,
        actionable_ids=actionable_ids,
        clause_texts=clause_texts,
        evidence=evidence or {},
    )


def _prior(
    ws: Workspace, outcome: ClauseOutcome | None, *, handover_id: str = "h1", iteration: int = 1
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv1",
        to_team_suggestions=["implementation_fix"],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="verification review",
        decisions=[_DECISION],
        clause_outcome=outcome,
    )


def _render(ws: Workspace, outcome: ClauseOutcome | None, task_id: str | None = None) -> str:
    return _fix_forward.render_failing_clause_block(ws, _prior(ws, outcome), task_id)


def _single(ws: Workspace, text: str) -> ClauseOutcome:
    return _outcome(ws, actionable_ids=("req-a",), clause_texts={"req-a": text})


def test_renders_live_actionable_clauses_sorted_by_id(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(
        ws,
        _outcome(
            ws,
            actionable_ids=("req-b", "req-a"),
            clause_texts={"req-b": "second clause", "req-a": "first clause"},
        ),
    )
    assert HEADING in block
    assert "- req-a: first clause" in block
    assert "- req-b: second clause" in block
    assert block.index("req-a") < block.index("req-b")


def test_block_omits_auditor_evidence_and_points_at_clause_table(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(
        ws,
        _outcome(
            ws,
            actionable_ids=("req-a",),
            clause_texts={"req-a": "the clause"},
            evidence={"req-a": "AUDITOR-EVIDENCE-SENTINEL"},
        ),
    )
    assert "AUDITOR-EVIDENCE-SENTINEL" not in block
    assert "clause table" in block


def test_block_carries_exactly_three_instructions(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _single(ws, "the clause"))
    rules = re.findall(r"^\d+\. .*$", block, flags=re.MULTILINE)
    assert len(rules) == 3
    assert "Satisfy each clause below as written." in rules[0]
    assert "does not satisfy the clause text is not a remedy" in rules[1]
    assert "`## Decisions`" in rules[2]
    assert "commit that satisfies it" in rules[2]


def test_undecomposed_workspace_with_no_task_id_renders_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _single(ws, "the clause"), task_id=None)
    assert "- req-a: the clause" in block


def test_explicit_matching_task_id_renders_block(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    outcome = _outcome(
        ws, actionable_ids=("req-a",), clause_texts={"req-a": "the clause"}, task_id="subtask-1"
    )
    assert "- req-a: the clause" in _render(ws, outcome, task_id="subtask-1")


def test_mismatched_outcome_task_id_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    outcome = _outcome(
        ws, actionable_ids=("req-a",), clause_texts={"req-a": "the clause"}, task_id="subtask-1"
    )
    assert _render(ws, outcome, task_id="subtask-2") == ""
    assert _render(ws, outcome, task_id=None) == ""


def test_absent_clause_outcome_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    assert _render(ws, None) == ""


def test_outcome_with_no_actionable_ids_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    outcome = _outcome(ws, actionable_ids=(), clause_texts={"req-a": "the clause"})
    assert _render(ws, outcome) == ""


def test_all_actionable_clauses_withdrawn_renders_nothing(tmp_path: Path) -> None:
    ws = _ws(
        tmp_path / "ws",
        [Requirement(id="req-a", text="the clause", withdrawn_by_question_id="q-1")],
    )
    assert _render(ws, _single(ws, "the clause")) == ""


def test_withdrawn_clause_omitted_and_live_clause_kept(tmp_path: Path) -> None:
    ws = _ws(
        tmp_path / "ws",
        [
            Requirement(id="req-a", text="gone", withdrawn_by_question_id="q-1"),
            Requirement(id="req-b", text="kept"),
        ],
    )
    block = _render(
        ws,
        _outcome(
            ws,
            actionable_ids=("req-a", "req-b"),
            clause_texts={"req-a": "gone", "req-b": "kept"},
        ),
    )
    assert "req-a" not in block
    assert "- req-b: kept" in block


def _two_clauses(ws: Workspace, text_a: str | None) -> ClauseOutcome:
    texts = {"req-b": "kept"}
    if text_a is not None:
        texts["req-a"] = text_a
    return _outcome(ws, actionable_ids=("req-a", "req-b"), clause_texts=texts)


def test_empty_clause_text_omitted(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _two_clauses(ws, ""))
    assert "req-a" not in block
    assert "- req-b: kept" in block
    assert _render(ws, _single(ws, "")) == ""


def test_whitespace_only_clause_text_omitted(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _two_clauses(ws, " \n\t "))
    assert "req-a" not in block
    assert "- req-b: kept" in block
    assert _render(ws, _single(ws, " \n\t ")) == ""


def test_missing_clause_text_key_omitted(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _two_clauses(ws, None))
    assert "req-a" not in block
    assert "- req-b: kept" in block


def test_clause_id_absent_from_requirements_still_renders(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws", [Requirement(id="req-other", text="unrelated")])
    assert "- req-a: the clause" in _render(ws, _single(ws, "the clause"))


def test_requirements_none_renders_all_clauses(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    assert ws.meta.requirements is None
    block = _render(
        ws,
        _outcome(
            ws,
            actionable_ids=("req-a", "req-b"),
            clause_texts={"req-a": "one", "req-b": "two"},
        ),
    )
    assert "- req-a: one" in block
    assert "- req-b: two" in block


def test_multiline_clause_text_collapses_to_one_line(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _single(ws, "line one\n\n   line   two\r\nline three"))
    assert "- req-a: line one line two line three" in block


def test_clause_text_with_markdown_heading_collapses_to_one_bullet(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    block = _render(ws, _single(ws, "first\n## Injected heading\n- injected bullet"))
    assert "- req-a: first ## Injected heading - injected bullet" in block
    assert "\n## Injected heading" not in block
    assert "\n- injected bullet" not in block
    assert block.count("\n- ") == 1


def test_older_handover_clause_outcome_is_not_walked(tmp_path: Path) -> None:
    ws = _ws(tmp_path / "ws")
    save_handover(ws, _prior(ws, _single(ws, "the clause"), handover_id="older"))
    newer = _prior(ws, None, handover_id="newer", iteration=2)
    assert _fix_forward.render_failing_clause_block(ws, newer, None) == ""


def test_renderer_is_exported_in_all() -> None:
    assert "render_failing_clause_block" in _fix_forward.__all__
