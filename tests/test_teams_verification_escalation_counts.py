from __future__ import annotations

from collections.abc import Callable, Iterator
from contextvars import Token
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.routes import _answer_waiver
from alan.teams import _verification_review_post as review_post_mod
from alan.teams._base import TeamEvent
from alan.teams._clause_stall import ClauseStall, StalledClause
from alan.teams._verdict import CriticalCountReport
from alan.teams._verification_artifacts import (
    count_record_path,
    read_count_record,
    reviewer_count_var,
)
from alan.workspace import Workspace, create

_INV_ID = "inv_escalation"
_CLEARING_OUTPUT = "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
_NONZERO_OUTPUT = "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"

Emitter = Callable[[Workspace, str, str], Handover]


def _discard(event: TeamEvent) -> None:
    return None


def _stall() -> ClauseStall:
    return ClauseStall(
        clause_ids=("req-a",),
        repeated_ids=("req-a",),
        clauses=(StalledClause(req_id="req-a", text="clause", evidence="evidence"),),
        judge_rationale="no fleet role can fix this",
        prior_handover_id="handover_prior",
    )


def _review_post_failed(ws: Workspace, inv_id: str, output: str) -> Handover:
    return review_post_mod._emit_review_post_failed(  # noqa: SLF001
        ws, None, _discard, inv_id, output,
        forge_target=DEFAULT_FORGE_TARGET,
    )


def _judge_parse_failed(ws: Workspace, inv_id: str, output: str) -> Handover:
    return review_post_mod._emit_judge_parse_failed(  # noqa: SLF001
        ws, None, _discard, inv_id, output,
    )


def _capability_boundary(ws: Workspace, inv_id: str, output: str) -> Handover:
    return review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        ws, None, _discard, inv_id, output, reason="needs a human with vault access",
    )


def _clause_stall(ws: Workspace, inv_id: str, output: str) -> Handover:
    return review_post_mod._emit_clause_stall_escalation(  # noqa: SLF001
        ws, None, _discard, inv_id, output, stall=_stall(),
    )


def _pr_state(ws: Workspace, inv_id: str, output: str) -> Handover:
    return review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        ws, None, _discard, inv_id, output,
    )


_EMITTERS = [
    pytest.param(_review_post_failed, id="review-post-failed"),
    pytest.param(_judge_parse_failed, id="judge-parse"),
    pytest.param(_capability_boundary, id="capability-boundary"),
    pytest.param(_clause_stall, id="clause-stall"),
    pytest.param(_pr_state, id="pr-state"),
]


@pytest.fixture(autouse=True)
def _audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture
def workspace(ws_root: Path) -> Workspace:
    return create("verify-task", "do verification", root=ws_root)


@pytest.fixture
def set_ambient_counts() -> Iterator[Callable[[CriticalCountReport], None]]:
    tokens: list[Token[CriticalCountReport | None]] = []

    def _set(counts: CriticalCountReport) -> None:
        tokens.append(reviewer_count_var.set(counts))

    try:
        yield _set
    finally:
        for token in reversed(tokens):
            reviewer_count_var.reset(token)


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_escalation_with_ambient_counts_leaves_record_beside_artifact(
    workspace: Workspace,
    emitter: Emitter,
    set_ambient_counts: Callable[[CriticalCountReport], None],
) -> None:
    counts = CriticalCountReport(reviewer=0, judge_reported=True)
    set_ambient_counts(counts)
    emitter(workspace, _INV_ID, _CLEARING_OUTPUT)
    assert read_count_record(workspace, _INV_ID) == counts


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_escalation_without_ambient_counts_leaves_no_record(
    workspace: Workspace, emitter: Emitter,
) -> None:
    emitter(workspace, _INV_ID, _CLEARING_OUTPUT)
    assert not count_record_path(workspace, _INV_ID).exists()


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_clearing_count_on_escalation_lets_floor_pass(
    workspace: Workspace,
    emitter: Emitter,
    set_ambient_counts: Callable[[CriticalCountReport], None],
) -> None:
    set_ambient_counts(CriticalCountReport(reviewer=0, judge_reported=True))
    blocking = emitter(workspace, _INV_ID, _CLEARING_OUTPUT)
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(workspace, blocking)
    assert reviewer_count == 0
    assert floor_reason is None


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_reviewer_nonzero_count_on_escalation_reports_its_own_reason(
    workspace: Workspace,
    emitter: Emitter,
    set_ambient_counts: Callable[[CriticalCountReport], None],
) -> None:
    set_ambient_counts(CriticalCountReport(reviewer=2, judge_reported=True))
    blocking = emitter(workspace, _INV_ID, _NONZERO_OUTPUT)
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(workspace, blocking)
    assert reviewer_count == 2
    assert floor_reason == "reviewer_nonzero"


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_posted_nonzero_count_on_escalation_reports_its_own_reason(
    workspace: Workspace,
    emitter: Emitter,
    set_ambient_counts: Callable[[CriticalCountReport], None],
) -> None:
    set_ambient_counts(CriticalCountReport(reviewer=0, judge_reported=True))
    blocking = emitter(workspace, _INV_ID, _NONZERO_OUTPUT)
    reviewer_count, floor_reason = _answer_waiver.waiver_floor_inputs(workspace, blocking)
    assert reviewer_count == 0
    assert floor_reason == "posted_count_nonzero"


@pytest.mark.parametrize("emitter", _EMITTERS)
def test_escalation_without_any_count_still_fails_closed(
    workspace: Workspace, emitter: Emitter,
) -> None:
    blocking = emitter(workspace, _INV_ID, _CLEARING_OUTPUT)
    assert _answer_waiver.waiver_floor_inputs(workspace, blocking) == (
        None, "count_not_reported",
    )
