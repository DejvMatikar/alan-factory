from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import EngineConfig, RepoIdentity, register
from alan.health_repo_metrics import (
    AUDIT_TAIL_BYTES,
    BLOCK_CORRECTION_AUDIT_FILE,
    BlockCorrectionRow,
    classify_build_workspaces,
    compute_format_correction_rates,
    compute_format_error_rate,
    compute_recovery_rate,
    engine_keys_by_task,
    format_correction_rates_from_audit_dir,
    in_flight_by_repo,
    is_build_success,
    read_audit_tail,
    read_block_correction_rows,
    task_completed_without_gap,
    unproven_clause_count,
)
from alan.workspace import (
    Meta,
    Requirement,
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    Workspace,
    transition,
)
from alan.workspace import (
    create as create_workspace,
)
from alan.workspace import (
    read as read_workspace,
)

_DEFAULT_SLUG = "DejvMatikar/alan"
_SPROCKET_SLUG = "sprocket-co/sprocket"


def _register_sprocket_engine() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))


def test_read_audit_tail_returns_full_content_within_limit(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text('{"a": 1}\n')
    assert read_audit_tail(f) == '{"a": 1}\n'


def test_read_audit_tail_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert read_audit_tail(tmp_path / "missing.jsonl") == ""


def test_read_audit_tail_truncates_to_custom_tail_bytes(tmp_path: Path) -> None:
    f = tmp_path / "big.jsonl"
    f.write_text("0123456789")
    assert read_audit_tail(f, tail_bytes=4) == "6789"


def test_audit_tail_bytes_constant() -> None:
    assert AUDIT_TAIL_BYTES == 256 * 1024


def test_task_completed_without_gap_false_when_not_completed() -> None:
    task = Task(id="t1", status=TaskStatus.PENDING)
    assert task_completed_without_gap(task) is False


def test_task_completed_without_gap_true_when_no_dod_clauses() -> None:
    task = Task(id="t1", status=TaskStatus.COMPLETED)
    assert task_completed_without_gap(task) is True


def test_task_completed_without_gap_true_when_all_evidenced() -> None:
    task = Task(
        id="t1", status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="x", evidence="PR #1")],
    )
    assert task_completed_without_gap(task) is True


def test_task_completed_without_gap_false_when_evidence_missing() -> None:
    task = Task(
        id="t1", status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="x", evidence=None)],
    )
    assert task_completed_without_gap(task) is False


def _meta(*, tasks: list[Task] | None = None, requirements: Any = None) -> Meta:
    now = datetime.now(UTC)
    return Meta(
        task_id="t1", title="t", description="d", status=Status.COMPLETED,
        tasks=tasks or [], requirements=requirements,
        created_at=now, updated_at=now,
    )


def test_is_build_success_false_when_no_tasks() -> None:
    assert is_build_success(_meta(tasks=[])) is False


def test_is_build_success_false_when_task_incomplete() -> None:
    task = Task(id="t1", status=TaskStatus.PENDING)
    assert is_build_success(_meta(tasks=[task])) is False


def test_is_build_success_false_when_acceptance_unsatisfied() -> None:
    task = Task(id="t1", status=TaskStatus.COMPLETED)
    assert is_build_success(_meta(tasks=[task], requirements=[])) is False


def test_is_build_success_true_when_all_conditions_met() -> None:
    task = Task(id="t1", status=TaskStatus.COMPLETED)
    assert is_build_success(_meta(tasks=[task])) is True


def _task_with_evidence(*, team: str = "implementation", evidenced: bool = True) -> Task:
    clause = TaskDoDClause(text="feature", evidence="PR #1" if evidenced else None)
    return Task(id="t1", team=team, status=TaskStatus.COMPLETED, dod_clauses=[clause])


def _make_ws(
    ws_root: Path,
    slug: str,
    status: Status,
    *,
    team: str = "implementation",
    evidenced: bool = True,
    engine_key: str | None = None,
) -> Workspace:
    ws = create_workspace(
        slug, "d", root=ws_root,
        tasks=[_task_with_evidence(team=team, evidenced=evidenced)],
        engine_key=engine_key,
    )
    transition(ws, status)
    return read_workspace(ws.task_id, root=ws_root)


def test_classify_build_workspaces_excludes_non_build(ws_root: Path) -> None:
    build_ws = _make_ws(ws_root, "build-a", Status.COMPLETED)
    research_ws = _make_ws(ws_root, "research-a", Status.COMPLETED, team="research")
    terminal, in_flight = classify_build_workspaces([build_ws, research_ws])
    assert len(terminal) == 1
    assert in_flight == 0


def test_classify_build_workspaces_counts_in_flight(ws_root: Path) -> None:
    active_ws = _make_ws(ws_root, "build-active", Status.ACTIVE)
    paused_ws = _make_ws(ws_root, "build-paused", Status.PAUSED)
    awaiting_ws = _make_ws(ws_root, "build-awaiting", Status.AWAITING_INPUT)
    terminal, in_flight = classify_build_workspaces([active_ws, paused_ws, awaiting_ws])
    assert terminal == []
    assert in_flight == 3


def test_classify_build_workspaces_partial_is_terminal_not_in_flight(
    ws_root: Path,
) -> None:
    partial_ws = _make_ws(ws_root, "build-partial", Status.PARTIAL, evidenced=False)
    terminal, in_flight = classify_build_workspaces([partial_ws])
    assert len(terminal) == 1
    assert in_flight == 0


def test_unproven_clause_count_zero_for_no_terminal_workspaces() -> None:
    assert unproven_clause_count([]) == 0


def test_unproven_clause_count_zero_when_no_requirements(ws_root: Path) -> None:
    ws = _make_ws(ws_root, "build-no-reqs", Status.COMPLETED)
    assert unproven_clause_count([ws]) == 0


def test_unproven_clause_count_counts_unbound_uncovered_clauses(ws_root: Path) -> None:
    ws = create_workspace(
        "build-unproven",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="unbound clause")],
    )
    transition(ws, Status.COMPLETED)
    ws = read_workspace(ws.task_id, root=ws_root)
    assert unproven_clause_count([ws]) == 1


def test_unproven_clause_count_excludes_withdrawn_clauses(ws_root: Path) -> None:
    from alan.workspace import update_requirements

    ws = create_workspace(
        "build-withdrawn",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="withdrawn clause")],
    )
    assert ws.meta.requirements is not None
    withdrawn = ws.meta.requirements[0].model_copy(
        update={"withdrawn_by_question_id": "clause-stall-x"}
    )
    ws = update_requirements(ws, [withdrawn])
    transition(ws, Status.COMPLETED)
    ws = read_workspace(ws.task_id, root=ws_root)
    assert unproven_clause_count([ws]) == 0


def test_unproven_clause_count_excludes_test_ref_bound_clauses(ws_root: Path) -> None:
    ws = create_workspace(
        "build-bound",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="bound clause", test_ref="t1")],
    )
    transition(ws, Status.COMPLETED)
    ws = read_workspace(ws.task_id, root=ws_root)
    assert unproven_clause_count([ws]) == 0


def test_unproven_clause_count_excludes_in_flight_workspaces(ws_root: Path) -> None:
    ws = create_workspace(
        "build-in-flight",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="unbound clause")],
    )
    ws = read_workspace(ws.task_id, root=ws_root)
    assert unproven_clause_count([ws]) == 0


def test_unproven_clause_count_sums_across_multiple_workspaces(ws_root: Path) -> None:
    ws1 = create_workspace(
        "build-multi-a",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="clause one")],
    )
    transition(ws1, Status.COMPLETED)
    ws1 = read_workspace(ws1.task_id, root=ws_root)
    ws2 = create_workspace(
        "build-multi-b",
        "d",
        root=ws_root,
        tasks=[_task_with_evidence()],
        requirements=[Requirement(text="clause two"), Requirement(text="clause three")],
    )
    transition(ws2, Status.COMPLETED)
    ws2 = read_workspace(ws2.task_id, root=ws_root)
    assert unproven_clause_count([ws1, ws2]) == 3


def test_engine_keys_by_task_maps_task_id_to_engine_key(ws_root: Path) -> None:
    _register_sprocket_engine()
    ws = _make_ws(ws_root, "build-x", Status.COMPLETED, engine_key=_SPROCKET_SLUG)
    result = engine_keys_by_task([ws])
    assert result[ws.task_id] == _SPROCKET_SLUG


def test_in_flight_by_repo_counts_in_flight_build_workspaces_per_engine(ws_root: Path) -> None:
    _register_sprocket_engine()
    default_ws = _make_ws(ws_root, "build-default", Status.ACTIVE)
    paused_ws = _make_ws(ws_root, "build-paused", Status.PAUSED)
    sprocket_ws = _make_ws(
        ws_root, "build-sprocket", Status.AWAITING_INPUT, engine_key=_SPROCKET_SLUG
    )
    result = in_flight_by_repo([default_ws, paused_ws, sprocket_ws])
    assert result == {_DEFAULT_SLUG: 2, _SPROCKET_SLUG: 1}


def test_in_flight_by_repo_reports_zero_for_a_repo_with_only_finished_work(
    ws_root: Path,
) -> None:
    finished_ws = _make_ws(ws_root, "build-done", Status.COMPLETED)
    research_ws = _make_ws(ws_root, "research-live", Status.ACTIVE, team="research")
    assert in_flight_by_repo([finished_ws, research_ws]) == {_DEFAULT_SLUG: 0}


def test_in_flight_by_repo_folds_a_missing_engine_key_into_the_default_repo(
    ws_root: Path,
) -> None:
    ws = _make_ws(ws_root, "build-noeng", Status.ACTIVE, engine_key=None)
    assert in_flight_by_repo([ws]) == {_DEFAULT_SLUG: 1}


def test_in_flight_by_repo_is_empty_without_workspaces() -> None:
    assert in_flight_by_repo([]) == {}


def _write_bc_rows(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir(exist_ok=True)
    text = "\n".join(json.dumps(r) for r in rows) + "\n" if rows else ""
    (audit_dir / BLOCK_CORRECTION_AUDIT_FILE).write_text(text)
    return audit_dir


def _bc_row(
    *, site: str = "decisions.yaml", outcome: str = "clean", attempts: int = 1,
    role: str = "judge", failure_kind: str | None = None, task_id: str | None = "t1",
) -> dict[str, Any]:
    return {
        "site": site, "block": site, "role": role, "outcome": outcome,
        "attempts": attempts, "failure_kind": failure_kind, "task_id": task_id,
    }


def test_read_block_correction_rows_parses_valid_rows(tmp_path: Path) -> None:
    audit_dir = _write_bc_rows(tmp_path, [_bc_row(outcome="clean")])
    rows = read_block_correction_rows(audit_dir)
    assert rows == [
        BlockCorrectionRow(
            site="decisions.yaml", block="decisions.yaml", role="judge",
            outcome="clean", attempts=1, failure_kind=None, task_id="t1",
        )
    ]


def test_read_block_correction_rows_parses_the_row_timestamp_as_utc(tmp_path: Path) -> None:
    audit_dir = _write_bc_rows(
        tmp_path, [{**_bc_row(), "timestamp_utc": "2026-09-20T08:30:00+02:00"}]
    )
    (row,) = read_block_correction_rows(audit_dir)
    assert row.ts == datetime(2026, 9, 20, 6, 30, tzinfo=UTC)


def test_read_block_correction_rows_treats_a_naive_timestamp_as_utc(tmp_path: Path) -> None:
    audit_dir = _write_bc_rows(tmp_path, [{**_bc_row(), "timestamp_utc": "2026-09-20T08:30:00"}])
    (row,) = read_block_correction_rows(audit_dir)
    assert row.ts == datetime(2026, 9, 20, 8, 30, tzinfo=UTC)


@pytest.mark.parametrize("timestamp", [None, 12, "yesterday"])
def test_read_block_correction_rows_keeps_a_row_with_no_usable_timestamp_undated(
    tmp_path: Path, timestamp: Any
) -> None:
    audit_dir = _write_bc_rows(tmp_path, [{**_bc_row(), "timestamp_utc": timestamp}])
    (row,) = read_block_correction_rows(audit_dir)
    assert row.ts is None


def test_read_block_correction_rows_skips_blank_lines(tmp_path: Path) -> None:
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    (audit_dir / BLOCK_CORRECTION_AUDIT_FILE).write_text(
        "\n" + json.dumps(_bc_row()) + "\n\n"
    )
    rows = read_block_correction_rows(audit_dir)
    assert len(rows) == 1


def test_read_block_correction_rows_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_block_correction_rows(tmp_path / "logs") == []


def test_read_block_correction_rows_skips_malformed_json_lines(tmp_path: Path) -> None:
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    text = "not json\n" + json.dumps(_bc_row()) + "\n"
    (audit_dir / BLOCK_CORRECTION_AUDIT_FILE).write_text(text)
    rows = read_block_correction_rows(audit_dir)
    assert len(rows) == 1


def test_read_block_correction_rows_skips_non_dict_json(tmp_path: Path) -> None:
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    (audit_dir / BLOCK_CORRECTION_AUDIT_FILE).write_text("[1, 2]\n")
    assert read_block_correction_rows(audit_dir) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"site": None},
        {"site": ""},
        {"attempts": "one"},
        {"attempts": True},
        {"attempts": 0},
    ],
)
def test_read_block_correction_rows_skips_invalid_rows(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    row = {**_bc_row(), **overrides}
    audit_dir = _write_bc_rows(tmp_path, [row])
    assert read_block_correction_rows(audit_dir) == []


def test_read_block_correction_rows_tolerates_missing_optional_fields(tmp_path: Path) -> None:
    row = _bc_row()
    del row["failure_kind"]
    del row["task_id"]
    audit_dir = _write_bc_rows(tmp_path, [row])
    rows = read_block_correction_rows(audit_dir)
    assert rows[0].failure_kind is None
    assert rows[0].task_id is None


def test_compute_format_error_rate_zero_sample_is_null_and_low_data() -> None:
    rate, sample, low_data = compute_format_error_rate([])
    assert rate is None
    assert sample == 0
    assert low_data is True


def test_compute_format_error_rate_counts_non_clean_attempt1_rows() -> None:
    rows = [
        BlockCorrectionRow("s", "s", "r", "clean", 1, None, None),
        BlockCorrectionRow("s", "s", "r", "exhausted", 1, "parse_error", None),
        BlockCorrectionRow("s", "s", "r", "corrected", 2, None, None),
    ]
    rate, sample, low_data = compute_format_error_rate(rows)
    assert sample == 2
    assert rate == pytest.approx(0.5)
    assert low_data is False


def test_compute_format_error_rate_repaired_counts_as_error() -> None:
    rows = [
        BlockCorrectionRow("s", "s", "r", "repaired", 1, None, None),
        BlockCorrectionRow("s", "s", "r", "clean", 1, None, None),
    ]
    rate, sample, _low_data = compute_format_error_rate(rows)
    assert sample == 2
    assert rate == pytest.approx(0.5)


def test_compute_recovery_rate_zero_sample_is_null_and_low_data() -> None:
    rate, sample, low_data = compute_recovery_rate([])
    assert rate is None
    assert sample == 0
    assert low_data is True


def test_compute_recovery_rate_counts_corrected_attempt2_rows() -> None:
    rows = [
        BlockCorrectionRow("s", "s", "r", "corrected", 2, None, None),
        BlockCorrectionRow("s", "s", "r", "exhausted", 2, "validation_error", None),
        BlockCorrectionRow("s", "s", "r", "clean", 1, None, None),
    ]
    rate, sample, low_data = compute_recovery_rate(rows)
    assert sample == 2
    assert rate == pytest.approx(0.5)
    assert low_data is False


def test_compute_format_correction_rates_bundles_both_metrics() -> None:
    rows = [
        BlockCorrectionRow("s", "s", "r", "clean", 1, None, None),
        BlockCorrectionRow("s", "s", "r", "corrected", 2, None, None),
    ]
    result = compute_format_correction_rates(rows)
    assert result.format_error_rate == pytest.approx(0.0)
    assert result.format_error_sample == 1
    assert result.format_error_low_data is False
    assert result.recovery_rate == pytest.approx(1.0)
    assert result.recovery_sample == 1
    assert result.recovery_low_data is False


def test_format_correction_rates_from_audit_dir_reads_and_computes(tmp_path: Path) -> None:
    audit_dir = _write_bc_rows(
        tmp_path,
        [_bc_row(outcome="clean", attempts=1), _bc_row(outcome="corrected", attempts=2)],
    )
    result = format_correction_rates_from_audit_dir(audit_dir)
    assert result.format_error_rate == pytest.approx(0.0)
    assert result.recovery_rate == pytest.approx(1.0)
