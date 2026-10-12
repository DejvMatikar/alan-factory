from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest

from alan.finisher_rate import (
    _BOOKKEEPING_AUDIT_KINDS,
    _BUILD_TERMINAL_STATUSES,
    FINISHER_EPOCH,
    FINISHER_FLOOR,
    FinisherRate,
    FunnelCounts,
    LeakReasons,
    _as_utc,
    _build_failure_index,
    _build_merge_set,
    _build_spiral_index,
    _classify_stage,
    _clause_missing_evidence,
    _counts_from_dict,
    _counts_to_dict,
    _has_build_revisit,
    _has_dod_evidence,
    _in_window,
    _int_field,
    _is_autonomous,
    _is_build_pipeline,
    _is_intervened,
    _leak_from_dict,
    _parse_created_at,
    _parse_jsonl,
    _tally_stage,
    _task_has_gap,
    _track_convergence,
    _track_crash,
    aggregate,
    compute_finisher_rate,
    filter_metas,
    fr_from_dict,
    fr_to_dict,
    parse_revisit_key,
)
from alan.loop_guard import LoopGuardAuditKind, SpiralKind


def test_parse_revisit_key_three_pipe_format() -> None:
    result = parse_revisit_key("ws-abc|task-def|implementation|chain-ghi")
    assert result == ("ws-abc", "task-def", "implementation", "chain-ghi")


def test_parse_revisit_key_one_pipe_legacy_format() -> None:
    result = parse_revisit_key("implementation|chain-ghi")
    assert result == ("", "", "implementation", "chain-ghi")


def test_parse_revisit_key_unrecognized_format() -> None:
    result = parse_revisit_key("somethingweird")
    assert result == ("", "", "somethingweird", "")


def test_parse_revisit_key_two_pipe_format() -> None:
    result = parse_revisit_key("ws|team|chain")
    assert result == ("", "", "ws|team|chain", "")


def test_parse_revisit_key_empty_string() -> None:
    result = parse_revisit_key("")
    assert result == ("", "", "", "")


def test_parse_jsonl_valid_lines() -> None:
    text = '{"a": 1}\n{"b": 2}\n'
    rows = _parse_jsonl(text)
    assert rows == [{"a": 1}, {"b": 2}]


def test_parse_jsonl_skips_blank_lines() -> None:
    text = '{"a": 1}\n\n{"b": 2}'
    assert len(_parse_jsonl(text)) == 2


def test_parse_jsonl_skips_invalid_json() -> None:
    text = '{"a": 1}\nnot-json\n{"b": 2}'
    assert len(_parse_jsonl(text)) == 2


def test_parse_jsonl_skips_non_dict() -> None:
    text = '[1,2,3]\n{"a": 1}'
    rows = _parse_jsonl(text)
    assert rows == [{"a": 1}]


def test_parse_jsonl_empty_text() -> None:
    assert _parse_jsonl("") == []


def test_build_failure_index_basic() -> None:
    rows = [{"workspace_id": "ws1", "error": "OOM"}, {"task_id": "ws2", "error": "timeout"}]
    idx = _build_failure_index(rows)
    assert idx == {"ws1": ["OOM"], "ws2": ["timeout"]}


def test_build_failure_index_workspace_id_wins_over_task_id() -> None:
    rows = [{"workspace_id": "ws1", "task_id": "t1", "error": "crash"}]
    idx = _build_failure_index(rows)
    assert "ws1" in idx
    assert "t1" not in idx


def test_build_failure_index_skips_missing_id() -> None:
    rows = [{"error": "crash"}]
    idx = _build_failure_index(rows)
    assert idx == {}


def test_build_failure_index_defaults_error_to_unknown() -> None:
    rows = [{"workspace_id": "ws1"}]
    idx = _build_failure_index(rows)
    assert idx["ws1"] == ["unknown"]


def test_build_failure_index_multiple_errors_same_ws() -> None:
    rows = [{"workspace_id": "ws1", "error": "e1"}, {"workspace_id": "ws1", "error": "e2"}]
    idx = _build_failure_index(rows)
    assert idx["ws1"] == ["e1", "e2"]


def test_build_merge_set_collects_merge_ok() -> None:
    rows = [{"action": "merge_ok", "task_id": "t1"}, {"action": "merge_ok", "task_id": "t2"}]
    result = _build_merge_set(rows)
    assert result == {"t1", "t2"}


def test_build_merge_set_excludes_non_merge_ok() -> None:
    rows = [{"action": "push", "task_id": "t1"}, {"action": "merge_ok", "task_id": "t2"}]
    result = _build_merge_set(rows)
    assert result == {"t2"}


def test_build_merge_set_skips_missing_task_id() -> None:
    rows = [{"action": "merge_ok"}]
    result = _build_merge_set(rows)
    assert result == set()


def test_build_merge_set_empty() -> None:
    assert _build_merge_set([]) == set()


def test_build_spiral_index_basic() -> None:
    rows = [{"task_id": "t1", "kind": "cap_exhausted_no_spiral"}]
    idx = _build_spiral_index(rows)
    assert idx == {"t1": ["cap_exhausted_no_spiral"]}


def test_build_spiral_index_defaults_kind_to_unclassified() -> None:
    rows = [{"task_id": "t1"}]
    idx = _build_spiral_index(rows)
    assert idx["t1"] == ["unclassified"]


def test_build_spiral_index_skips_missing_task_id() -> None:
    rows = [{"kind": "cap_exhausted_no_spiral"}]
    idx = _build_spiral_index(rows)
    assert idx == {}


def test_build_spiral_index_multiple_kinds_same_task() -> None:
    rows = [{"task_id": "t1", "kind": "k1"}, {"task_id": "t1", "kind": "k2"}]
    idx = _build_spiral_index(rows)
    assert idx["t1"] == ["k1", "k2"]


def test_build_spiral_index_excludes_chain_decay() -> None:
    rows = [{"task_id": "t1", "kind": "chain_decay"}]
    idx = _build_spiral_index(rows)
    assert idx == {}


def test_build_spiral_index_excludes_chain_reset() -> None:
    rows = [{"task_id": "t1", "kind": "chain_reset"}]
    idx = _build_spiral_index(rows)
    assert idx == {}


def test_build_spiral_index_keeps_real_kind_alongside_bookkeeping() -> None:
    rows = [
        {"task_id": "t1", "kind": "chain_decay"},
        {"task_id": "t1", "kind": "external_gate_blocked"},
    ]
    idx = _build_spiral_index(rows)
    assert idx["t1"] == ["external_gate_blocked"]


def test_bookkeeping_kinds_partition_loop_guard_audit_kinds() -> None:
    assert set(get_args(LoopGuardAuditKind)) == (
        set(get_args(SpiralKind)) | {"cap_exhausted_no_spiral"} | _BOOKKEEPING_AUDIT_KINDS
    )
    assert _BOOKKEEPING_AUDIT_KINDS.isdisjoint(set(get_args(SpiralKind)))


def test_has_build_revisit_true_for_three_pipe_build_team() -> None:
    meta = {"revisit_counts": {"ws|task|implementation|chain": 1}}
    assert _has_build_revisit(meta) is True


def test_has_build_revisit_true_for_one_pipe_build_team() -> None:
    meta = {"revisit_counts": {"implementation_plan|chain": 1}}
    assert _has_build_revisit(meta) is True


def test_has_build_revisit_true_for_zero_pipe_unknown_format() -> None:
    meta = {"revisit_counts": {"implementation": 1}}
    assert _has_build_revisit(meta) is True


def test_has_build_revisit_false_for_non_build_team() -> None:
    meta = {"revisit_counts": {"ws|task|research|chain": 1}}
    assert _has_build_revisit(meta) is False


def test_has_build_revisit_false_for_non_dict() -> None:
    meta: dict[str, Any] = {"revisit_counts": "invalid"}
    assert _has_build_revisit(meta) is False


def test_has_build_revisit_false_for_empty() -> None:
    assert _has_build_revisit({}) is False


def test_is_build_pipeline_true_via_tasks_field() -> None:
    meta: dict[str, Any] = {"tasks": [{"team": "implementation"}]}
    assert _is_build_pipeline(meta) is True


def test_is_build_pipeline_true_via_revisit_fallback() -> None:
    meta = {"revisit_counts": {"implementation|chain": 2}}
    assert _is_build_pipeline(meta) is True


def test_is_build_pipeline_false_for_non_build() -> None:
    meta = {"tasks": [{"team": "chat"}]}
    assert _is_build_pipeline(meta) is False


def test_is_build_pipeline_false_for_empty_meta() -> None:
    assert _is_build_pipeline({}) is False


def test_clause_missing_evidence_true_when_none() -> None:
    assert _clause_missing_evidence({"evidence": None}) is True


def test_clause_missing_evidence_false_when_present() -> None:
    assert _clause_missing_evidence({"evidence": "PR #42"}) is False


def test_clause_missing_evidence_false_for_non_dict() -> None:
    assert _clause_missing_evidence("string") is False


def test_task_has_gap_true_when_clause_missing() -> None:
    task = {"dod_clauses": [{"evidence": None}]}
    assert _task_has_gap(task) is True


def test_task_has_gap_false_when_all_evidenced() -> None:
    task = {"dod_clauses": [{"evidence": "PR #1"}]}
    assert _task_has_gap(task) is False


def test_task_has_gap_false_for_non_dict() -> None:
    assert _task_has_gap("not-a-task") is False


def test_task_has_gap_false_for_no_clauses() -> None:
    assert _task_has_gap({}) is False


def test_has_dod_evidence_true_for_no_tasks() -> None:
    assert _has_dod_evidence({}) is True


def test_has_dod_evidence_true_for_all_evidenced() -> None:
    meta: dict[str, Any] = {"tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    assert _has_dod_evidence(meta) is True


def test_has_dod_evidence_false_when_gap() -> None:
    meta: dict[str, Any] = {"tasks": [{"dod_clauses": [{"evidence": None}]}]}
    assert _has_dod_evidence(meta) is False


def test_parse_created_at_valid_utc() -> None:
    meta = {"created_at": "2026-05-14T20:56:34.072597Z"}
    result = _parse_created_at(meta)
    assert result is not None
    assert result.tzinfo is not None


def test_parse_created_at_valid_offset() -> None:
    meta = {"created_at": "2026-05-14T20:56:34+00:00"}
    result = _parse_created_at(meta)
    assert result is not None


def test_parse_created_at_naive_coerced_to_utc() -> None:
    meta = {"created_at": "2026-05-14T20:56:34"}
    result = _parse_created_at(meta)
    assert result is not None
    assert result.tzinfo is not None


def test_parse_created_at_missing_key() -> None:
    assert _parse_created_at({}) is None


def test_parse_created_at_invalid_string() -> None:
    assert _parse_created_at({"created_at": "not-a-date"}) is None


def test_parse_created_at_non_string() -> None:
    meta: dict[str, Any] = {"created_at": 12345}
    assert _parse_created_at(meta) is None


def test_in_window_true_when_recent() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    meta = {"created_at": datetime.now(UTC).isoformat()}
    assert _in_window(meta, cutoff) is True


def test_in_window_false_when_old() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    meta = {"created_at": (datetime.now(UTC) - timedelta(days=60)).isoformat()}
    assert _in_window(meta, cutoff) is False


def test_in_window_false_when_no_created_at() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    assert _in_window({}, cutoff) is False


def _make_meta(status: str = "completed") -> dict[str, Any]:
    return {
        "task_id": "ws1",
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "revisit_counts": {"implementation|chain": 1},
    }


def test_classify_stage_crashed() -> None:
    stage = _classify_stage("ws1", _make_meta(), {"ws1": ["OOM"]}, {}, set())
    assert stage == "crashed"


def test_classify_stage_not_converged() -> None:
    stage = _classify_stage("ws1", _make_meta(), {}, {"ws1": ["k1"]}, set())
    assert stage == "not_converged"


def test_classify_stage_needed_human_awaiting_input() -> None:
    stage = _classify_stage("ws1", _make_meta("awaiting_input"), {}, {}, set())
    assert stage == "needed_human"


def test_classify_stage_needed_human_escalated() -> None:
    stage = _classify_stage("ws1", _make_meta("escalated"), {}, {}, set())
    assert stage == "needed_human"


def test_classify_stage_not_merged() -> None:
    stage = _classify_stage("ws1", _make_meta("completed"), {}, {}, set())
    assert stage == "not_merged"


def test_classify_stage_not_evidenced() -> None:
    meta = {**_make_meta(), "tasks": [{"dod_clauses": [{"evidence": None}]}]}
    stage = _classify_stage("ws1", meta, {}, {}, {"ws1"})
    assert stage == "not_evidenced"


def test_classify_stage_success() -> None:
    meta = {**_make_meta(), "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    stage = _classify_stage("ws1", meta, {}, {}, {"ws1"})
    assert stage == "success"


def test_classify_stage_spiral_priority_over_escalated_status() -> None:
    meta = _make_meta("escalated")
    stage = _classify_stage("ws1", meta, {}, {"ws1": ["cap_exhausted_no_spiral"]}, set())
    assert stage == "not_converged"


def test_classify_stage_merged_wins_over_crash() -> None:
    meta = {**_make_meta(), "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    stage = _classify_stage("ws1", meta, {"ws1": ["OOM"]}, {}, {"ws1"})
    assert stage == "success"


def test_classify_stage_merged_wins_over_spiral() -> None:
    meta = {**_make_meta(), "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    stage = _classify_stage("ws1", meta, {}, {"ws1": ["external_gate_blocked"]}, {"ws1"})
    assert stage == "success"


def test_classify_stage_merged_without_evidence_still_not_evidenced_over_spiral() -> None:
    meta = {**_make_meta(), "tasks": [{"dod_clauses": [{"evidence": None}]}]}
    stage = _classify_stage("ws1", meta, {}, {"ws1": ["k1"]}, {"ws1"})
    assert stage == "not_evidenced"


def test_is_intervened_false_when_key_missing() -> None:
    assert _is_intervened({}) is False


def test_is_intervened_false_when_not_a_list() -> None:
    assert _is_intervened({"interventions": "answer"}) is False


def test_is_intervened_false_when_empty_list() -> None:
    assert _is_intervened({"interventions": []}) is False


def test_is_intervened_true_when_non_empty() -> None:
    meta = {"interventions": [{"action": "answer", "timestamp_utc": "t"}]}
    assert _is_intervened(meta) is True


def test_is_autonomous_true_for_untouched_success() -> None:
    assert _is_autonomous("success", _make_meta()) is True


def test_is_autonomous_false_when_intervened() -> None:
    meta = {**_make_meta(), "interventions": [{"action": "resume", "timestamp_utc": "t"}]}
    assert _is_autonomous("success", meta) is False


def test_is_autonomous_false_when_stage_not_success() -> None:
    assert _is_autonomous("not_merged", _make_meta()) is False


def test_is_autonomous_false_for_merged_success_with_awaiting_input_status() -> None:
    assert _is_autonomous("success", _make_meta("awaiting_input")) is False


def test_is_autonomous_false_for_merged_success_with_escalated_status() -> None:
    assert _is_autonomous("success", _make_meta("escalated")) is False


def test_track_crash_records_first_error() -> None:
    reasons: dict[str, int] = {}
    _track_crash("ws1", {"ws1": ["OOM", "timeout"]}, reasons)
    assert reasons == {"OOM": 1}


def test_track_crash_no_entry_in_index() -> None:
    reasons: dict[str, int] = {}
    _track_crash("ws1", {}, reasons)
    assert reasons == {}


def test_track_convergence_records_first_kind() -> None:
    kinds: dict[str, int] = {}
    _track_convergence("ws1", {"ws1": ["cap_exhausted_no_spiral", "other"]}, kinds)
    assert kinds == {"cap_exhausted_no_spiral": 1}


def test_track_convergence_no_entry() -> None:
    kinds: dict[str, int] = {}
    _track_convergence("ws1", {}, kinds)
    assert kinds == {}


def test_tally_stage_crashed() -> None:
    crash_reasons: dict[str, int] = {}
    conv_kinds: dict[str, int] = {}
    result = _tally_stage("crashed", "ws1", {"ws1": ["OOM"]}, {}, crash_reasons, conv_kinds)
    assert result == (0, 0, 0, 0, 0)
    assert crash_reasons == {"OOM": 1}


def test_tally_stage_not_converged() -> None:
    conv_kinds: dict[str, int] = {}
    result = _tally_stage("not_converged", "ws1", {}, {"ws1": ["k1"]}, {}, conv_kinds)
    assert result == (1, 1, 0, 0, 0)
    assert conv_kinds == {"k1": 1}


def test_tally_stage_needed_human() -> None:
    result = _tally_stage("needed_human", "ws1", {}, {}, {}, {})
    assert result == (1, 0, 0, 0, 0)


def test_tally_stage_not_merged() -> None:
    result = _tally_stage("not_merged", "ws1", {}, {}, {}, {})
    assert result == (1, 1, 1, 0, 0)


def test_tally_stage_not_evidenced() -> None:
    result = _tally_stage("not_evidenced", "ws1", {}, {}, {}, {})
    assert result == (1, 1, 1, 1, 0)


def test_tally_stage_success() -> None:
    result = _tally_stage("success", "ws1", {}, {}, {}, {})
    assert result == (1, 1, 1, 1, 1)


def _fresh_build_meta(task_id: str = "ws1", status: str = "completed") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "revisit_counts": {"implementation|chain": 1},
    }


def test_filter_metas_includes_build_terminal_in_window() -> None:
    metas = [_fresh_build_meta()]
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = filter_metas(metas, cutoff)
    assert len(result) == 1


def test_filter_metas_excludes_non_build() -> None:
    meta = {
        "task_id": "ws1", "status": "completed",
        "created_at": datetime.now(UTC).isoformat(),
    }
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = filter_metas([meta], cutoff)
    assert result == []


def test_filter_metas_excludes_non_terminal() -> None:
    meta = {**_fresh_build_meta(), "status": "active"}
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = filter_metas([meta], cutoff)
    assert result == []


def test_filter_metas_excludes_out_of_window() -> None:
    meta = {
        **_fresh_build_meta(),
        "created_at": (datetime.now(UTC) - timedelta(days=60)).isoformat(),
    }
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = filter_metas([meta], cutoff)
    assert result == []


def test_filter_metas_includes_archived_status() -> None:
    meta = {**_fresh_build_meta(), "status": "archived"}
    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = filter_metas([meta], cutoff)
    assert len(result) == 1


def test_build_terminal_statuses_includes_completed_and_failed() -> None:
    assert "completed" in _BUILD_TERMINAL_STATUSES
    assert "failed" in _BUILD_TERMINAL_STATUSES


def test_build_terminal_statuses_includes_partial_and_escalated() -> None:
    assert "partial" in _BUILD_TERMINAL_STATUSES
    assert "escalated" in _BUILD_TERMINAL_STATUSES


def test_build_terminal_statuses_includes_archived() -> None:
    assert "archived" in _BUILD_TERMINAL_STATUSES


def test_build_terminal_statuses_excludes_awaiting_input() -> None:
    assert "awaiting_input" not in _BUILD_TERMINAL_STATUSES


def test_build_terminal_statuses_excludes_live_statuses() -> None:
    assert "active" not in _BUILD_TERMINAL_STATUSES
    assert "paused" not in _BUILD_TERMINAL_STATUSES


def test_aggregate_empty_metas() -> None:
    counts, reasons = aggregate([], {}, {}, set())
    assert counts.dispatched == 0
    assert counts.dod_evidenced == 0
    assert reasons.crash_reasons == {}


def test_aggregate_all_success() -> None:
    meta = {**_fresh_build_meta(), "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    counts, _ = aggregate([meta], {}, {}, {"ws1"})
    assert counts.dispatched == 1
    assert counts.dod_evidenced == 1


def test_aggregate_crashed() -> None:
    meta = _fresh_build_meta()
    counts, reasons = aggregate([meta], {"ws1": ["OOM"]}, {}, set())
    assert counts.dispatched == 1
    assert counts.not_crashed == 0
    assert reasons.crash_reasons == {"OOM": 1}


def test_aggregate_not_converged() -> None:
    meta = _fresh_build_meta()
    counts, reasons = aggregate([meta], {}, {"ws1": ["cap_exhausted_no_spiral"]}, set())
    assert counts.not_needed_human == 1
    assert counts.converged == 0
    assert reasons.convergence_kinds == {"cap_exhausted_no_spiral": 1}


def test_aggregate_autonomous_counts_untouched_success() -> None:
    meta = {**_fresh_build_meta(), "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}]}
    counts, _ = aggregate([meta], {}, {}, {"ws1"})
    assert counts.autonomous == 1


def test_aggregate_autonomous_excludes_intervened_success() -> None:
    meta = {
        **_fresh_build_meta(),
        "tasks": [{"dod_clauses": [{"evidence": "PR #1"}]}],
        "interventions": [{"action": "answer", "timestamp_utc": "t"}],
    }
    counts, _ = aggregate([meta], {}, {}, {"ws1"})
    assert counts.dod_evidenced == 1
    assert counts.autonomous == 0


def _make_jsonl(*rows: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def _make_ws_meta(
    task_id: str = "ws1",
    status: str = "completed",
    days_ago: int = 5,
    with_evidence: bool = True,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "created_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        "revisit_counts": {"implementation|chain": 1},
    }
    if with_evidence:
        meta["tasks"] = [{"dod_clauses": [{"evidence": "PR #1"}]}]
    return meta


def test_compute_finisher_rate_low_data_when_below_min_sample() -> None:
    fr = compute_finisher_rate("", "", "", [_make_ws_meta()], min_sample=10)
    assert fr.low_data is True
    assert fr.rate is None


def test_compute_finisher_rate_returns_rate_above_min_sample() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    fr = compute_finisher_rate("", _make_jsonl(*merge_rows), "", metas, min_sample=10)
    assert fr.rate is not None
    assert fr.rate == pytest.approx(1.0)


def test_compute_finisher_rate_floor_alert_below_threshold() -> None:
    metas = [_make_ws_meta(f"ws{i}", status="failed", with_evidence=False) for i in range(15)]
    fr = compute_finisher_rate("", "", "", metas, min_sample=10)
    assert fr.floor_alert is True


def test_compute_finisher_rate_no_floor_alert_above_threshold() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    fr = compute_finisher_rate("", _make_jsonl(*merge_rows), "", metas, min_sample=10)
    assert fr.floor_alert is False


def test_compute_finisher_rate_crash_tracked_in_leak_reasons() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    failure_rows = [{"workspace_id": "ws0", "error": "SIGKILL"}]
    fr = compute_finisher_rate(_make_jsonl(*failure_rows), "", "", metas, min_sample=10)
    assert "SIGKILL" in fr.leak_reasons.crash_reasons


def test_compute_finisher_rate_spiral_tracked_in_leak_reasons() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    spiral_rows = [{"task_id": "ws0", "kind": "cap_exhausted_no_spiral"}]
    fr = compute_finisher_rate("", "", _make_jsonl(*spiral_rows), metas, min_sample=10)
    assert "cap_exhausted_no_spiral" in fr.leak_reasons.convergence_kinds


def test_compute_finisher_rate_chain_decay_only_ws_counts_as_success() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    spiral_rows = [{"task_id": "ws0", "kind": "chain_decay"}]
    fr = compute_finisher_rate(
        "", _make_jsonl(*merge_rows), _make_jsonl(*spiral_rows), metas, min_sample=10
    )
    assert fr.rate == pytest.approx(1.0)
    assert fr.leak_reasons.convergence_kinds == {}


def test_compute_finisher_rate_chain_reset_unmerged_ws_not_counted_as_spiral() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    spiral_rows = [{"task_id": "ws0", "kind": "chain_reset"}]
    fr = compute_finisher_rate("", "", _make_jsonl(*spiral_rows), metas, min_sample=10)
    assert fr.counts.converged == 15
    assert fr.leak_reasons.convergence_kinds == {}


def test_compute_finisher_rate_merged_ws_with_real_spiral_counts_as_success() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    spiral_rows = [{"task_id": "ws0", "kind": "external_gate_blocked"}]
    fr = compute_finisher_rate(
        "", _make_jsonl(*merge_rows), _make_jsonl(*spiral_rows), metas, min_sample=10
    )
    assert fr.rate == pytest.approx(1.0)
    assert fr.leak_reasons.convergence_kinds == {}


def test_compute_finisher_rate_merged_ws_with_crash_counts_as_success() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    failure_rows = [{"workspace_id": "ws0", "error": "SIGKILL"}]
    fr = compute_finisher_rate(
        _make_jsonl(*failure_rows), _make_jsonl(*merge_rows), "", metas, min_sample=10
    )
    assert fr.rate == pytest.approx(1.0)
    assert fr.leak_reasons.crash_reasons == {}


def test_compute_finisher_rate_excludes_out_of_window() -> None:
    metas = [_make_ws_meta(f"ws{i}", days_ago=60) for i in range(15)]
    fr = compute_finisher_rate("", "", "", metas, min_sample=1)
    assert fr.sample_size == 0


def test_compute_finisher_rate_window_days_param() -> None:
    metas = [_make_ws_meta(f"ws{i}", days_ago=45) for i in range(15)]
    fr = compute_finisher_rate("", "", "", metas, window_days=60, min_sample=10)
    assert fr.sample_size == 15


def test_compute_finisher_rate_autonomous_rate_excludes_intervened_success() -> None:
    metas = [_make_ws_meta(f"ws{i}") for i in range(15)]
    metas[0]["interventions"] = [{"action": "redispatch", "timestamp_utc": "t"}]
    merge_rows = [{"action": "merge_ok", "task_id": f"ws{i}"} for i in range(15)]
    fr = compute_finisher_rate("", _make_jsonl(*merge_rows), "", metas, min_sample=10)
    assert fr.rate == pytest.approx(1.0)
    assert fr.autonomous_rate == pytest.approx(14 / 15)


def test_compute_finisher_rate_autonomous_rate_none_when_low_data() -> None:
    fr = compute_finisher_rate("", "", "", [_make_ws_meta()], min_sample=10)
    assert fr.autonomous_rate is None


def test_compute_finisher_rate_zero_sample_with_min_sample_zero_rates_none() -> None:
    fr = compute_finisher_rate("", "", "", [], min_sample=0)
    assert fr.low_data is False
    assert fr.rate is None
    assert fr.autonomous_rate is None


def test_compute_finisher_rate_floor_alert_constant() -> None:
    assert FINISHER_FLOOR == 0.5


def test_finisher_epoch_constant() -> None:
    assert FINISHER_EPOCH == datetime(2026, 6, 17, tzinfo=UTC)


def test_compute_finisher_rate_epoch_floors_out_pre_epoch_tasks() -> None:
    epoch = datetime.now(UTC) - timedelta(days=10)
    metas = [_make_ws_meta("old", days_ago=20), _make_ws_meta("new", days_ago=2)]
    fr = compute_finisher_rate(
        "", "", "", metas, window_days=3650, min_sample=1, epoch=epoch
    )
    assert fr.sample_size == 1


def test_compute_finisher_rate_epoch_yields_to_tighter_rolling_window() -> None:
    epoch = datetime.now(UTC) - timedelta(days=3650)
    metas = [_make_ws_meta("old", days_ago=60), _make_ws_meta("new", days_ago=5)]
    fr = compute_finisher_rate(
        "", "", "", metas, window_days=30, min_sample=1, epoch=epoch
    )
    assert fr.sample_size == 1


def test_compute_finisher_rate_naive_epoch_coerced_to_utc() -> None:
    naive_epoch = (datetime.now(UTC) - timedelta(days=10)).replace(tzinfo=None)
    metas = [_make_ws_meta("old", days_ago=20), _make_ws_meta("new", days_ago=2)]
    fr = compute_finisher_rate(
        "", "", "", metas, window_days=3650, min_sample=1, epoch=naive_epoch
    )
    assert fr.sample_size == 1


def test_as_utc_preserves_aware_and_coerces_naive() -> None:
    aware = datetime(2026, 6, 17, tzinfo=UTC)
    assert _as_utc(aware) is aware
    assert _as_utc(datetime(2026, 6, 17)).tzinfo == UTC


def test_int_field_integer() -> None:
    assert _int_field({"x": 5}, "x") == 5


def test_int_field_float() -> None:
    assert _int_field({"x": 3.7}, "x") == 3


def test_int_field_bool_returns_zero() -> None:
    assert _int_field({"x": True}, "x") == 0


def test_int_field_missing_returns_zero() -> None:
    assert _int_field({}, "x") == 0


def test_int_field_string_returns_zero() -> None:
    assert _int_field({"x": "5"}, "x") == 0


def _make_fr() -> FinisherRate:
    return FinisherRate(
        rate=0.75,
        counts=FunnelCounts(
            dispatched=20,
            not_crashed=18,
            not_needed_human=16,
            converged=14,
            merged_pr=16,
            dod_evidenced=15,
            autonomous=12,
        ),
        leak_reasons=LeakReasons(
            crash_reasons={"OOM": 2},
            convergence_kinds={"cap_exhausted_no_spiral": 1},
        ),
        low_data=False,
        window_days=30,
        sample_size=20,
        floor_alert=False,
        autonomous_rate=0.6,
    )


def test_fr_to_dict_roundtrip() -> None:
    fr = _make_fr()
    d = fr_to_dict(fr)
    restored = fr_from_dict(d)
    assert restored.rate == pytest.approx(fr.rate)
    assert restored.counts == fr.counts
    assert restored.leak_reasons == fr.leak_reasons
    assert restored.low_data == fr.low_data
    assert restored.window_days == fr.window_days
    assert restored.sample_size == fr.sample_size
    assert restored.floor_alert == fr.floor_alert
    assert restored.autonomous_rate == pytest.approx(fr.autonomous_rate)


def test_fr_to_dict_structure() -> None:
    d = fr_to_dict(_make_fr())
    assert "rate" in d
    assert "counts" in d
    assert "leak_reasons" in d
    assert "dispatched" in d["counts"]
    assert "autonomous" in d["counts"]
    assert "autonomous_rate" in d
    assert "crash_reasons" in d["leak_reasons"]


def test_fr_from_dict_none_rate_when_missing() -> None:
    fr = fr_from_dict({"rate": None})
    assert fr.rate is None


def test_fr_from_dict_defaults_window_days() -> None:
    fr = fr_from_dict({})
    assert fr.window_days == 30


def test_fr_from_dict_old_blob_without_autonomous_keys() -> None:
    d = fr_to_dict(_make_fr())
    del d["autonomous_rate"]
    del d["counts"]["autonomous"]
    fr = fr_from_dict(d)
    assert fr.autonomous_rate is None
    assert fr.counts.autonomous == 0


def test_fr_from_dict_non_numeric_autonomous_rate_is_none() -> None:
    fr = fr_from_dict({"autonomous_rate": "0.9"})
    assert fr.autonomous_rate is None


def test_counts_to_dict_keys() -> None:
    counts = FunnelCounts(
        dispatched=10, not_crashed=9, not_needed_human=8,
        converged=7, merged_pr=6, dod_evidenced=5,
    )
    d = _counts_to_dict(counts)
    assert set(d.keys()) == {
        "dispatched", "not_crashed", "not_needed_human",
        "converged", "merged_pr", "dod_evidenced", "autonomous",
    }


def test_counts_from_dict_all_zeros_on_empty() -> None:
    counts = _counts_from_dict({})
    assert counts.dispatched == 0
    assert counts.dod_evidenced == 0
    assert counts.autonomous == 0


def test_leak_from_dict_empty() -> None:
    leak = _leak_from_dict({})
    assert leak.crash_reasons == {}
    assert leak.convergence_kinds == {}


def test_leak_from_dict_non_dict_values() -> None:
    leak = _leak_from_dict({"crash_reasons": "invalid", "convergence_kinds": None})
    assert leak.crash_reasons == {}
    assert leak.convergence_kinds == {}


def _build_success_task(*, team: str = "implementation", evidenced: bool = True) -> Any:
    from alan.workspace import Task, TaskDoDClause, TaskStatus

    clause = TaskDoDClause(text="Feature merged", evidence="PR #1" if evidenced else None)
    return Task(id="t1", team=team, status=TaskStatus.COMPLETED, dod_clauses=[clause])


def _make_build_ws(
    ws_root: Path,
    slug: str,
    status: Any,
    *,
    team: str = "implementation",
    evidenced: bool = True,
) -> Any:
    from alan.workspace import create as create_workspace
    from alan.workspace import read as read_workspace
    from alan.workspace import transition

    ws = create_workspace(
        slug, "d", root=ws_root, tasks=[_build_success_task(team=team, evidenced=evidenced)]
    )
    transition(ws, status)
    return read_workspace(ws.task_id, root=ws_root)


def test_build_success_rate_archive_neutral(ws_root: Path) -> None:
    from alan.routes import infra as infra_mod
    from alan.workspace import Status

    unarchived_ws = _make_build_ws(ws_root, "build-done", Status.COMPLETED)
    archived_ws = _make_build_ws(ws_root, "build-done-archived", Status.ARCHIVED)
    rate_unarchived, _ = infra_mod._compute_build_success_rate([unarchived_ws])  # noqa: SLF001
    rate_archived, _ = infra_mod._compute_build_success_rate([archived_ws])  # noqa: SLF001
    assert rate_unarchived == pytest.approx(1.0)
    assert rate_archived == pytest.approx(1.0)


def test_build_success_rate_excludes_non_build(ws_root: Path) -> None:
    from alan.routes import infra as infra_mod
    from alan.workspace import Status

    research_ws = _make_build_ws(ws_root, "research-only", Status.COMPLETED, team="research")
    rate, in_flight = infra_mod._compute_build_success_rate([research_ws])  # noqa: SLF001
    assert rate is None
    assert in_flight == 0


def test_build_success_rate_terminal_only(ws_root: Path) -> None:
    from alan.routes import infra as infra_mod
    from alan.workspace import Status

    active_ws = _make_build_ws(ws_root, "build-live", Status.ACTIVE)
    rate, in_flight = infra_mod._compute_build_success_rate([active_ws])  # noqa: SLF001
    assert rate is None
    assert in_flight == 1


