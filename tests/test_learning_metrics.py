from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

from alan.learning_metrics import (
    MERGE_GATE_WINDOW_HOURS,
    LearningMetrics,
    first_pass_approval_rate,
    learning_metrics_from_dict,
    learning_metrics_to_dict,
    mean_fix_cycles_per_merged_pr,
    mean_fix_cycles_to_merge,
    mean_fix_cycles_when_fixed,
    merge_gate_recovery_rates,
    pct_merged_prs_needing_fix,
    sample_size,
)
from alan.trajectory import TrajectoryRecord

_MG_NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def _mg_row(
    block_class: str | None,
    *,
    recovered: bool,
    ts: datetime | None,
) -> dict[str, object]:
    row: dict[str, object] = {"block_class": block_class, "recovered": recovered}
    if ts is not None:
        row["timestamp_utc"] = ts.isoformat()
    return row


def _mg_text(rows: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _rec(
    task_id: str = "t1",
    inv_id: str | None = "inv_001",
    pr_number: int | None = None,
    team: str = "implementation",
    verdict: str | None = None,
    reward: bool | None = None,
    fix_cycles: int = 0,
    revisits: int = 0,
    turns: int | None = None,
) -> TrajectoryRecord:
    return TrajectoryRecord(
        task_id=task_id, inv_id=inv_id, pr_number=pr_number, team=team,
        verdict=verdict, reward=reward, fix_cycles=fix_cycles,
        revisits=revisits, turns=turns,
    )


def test_fpar_empty_records_returns_none() -> None:
    assert first_pass_approval_rate([]) is None


def test_fpar_no_implementation_records_returns_none() -> None:
    records = [_rec(team="implementation_fix", verdict="APPROVE")]
    assert first_pass_approval_rate(records) is None


def test_fpar_all_none_verdicts_returns_none() -> None:
    records = [_rec(team="implementation", verdict=None)]
    assert first_pass_approval_rate(records) is None


def test_fpar_single_approve() -> None:
    records = [_rec(team="implementation", verdict="APPROVE")]
    assert first_pass_approval_rate(records) == 1.0


def test_fpar_single_request_changes() -> None:
    records = [_rec(team="implementation", verdict="REQUEST_CHANGES")]
    assert first_pass_approval_rate(records) == 0.0


def test_fpar_mixed_verdicts() -> None:
    records = [
        _rec(task_id="t1", verdict="APPROVE"),
        _rec(task_id="t2", verdict="REQUEST_CHANGES"),
    ]
    result = first_pass_approval_rate(records)
    assert result == 0.5


def test_fpar_ignores_non_implementation_team() -> None:
    records = [
        _rec(task_id="t1", team="implementation", verdict="APPROVE"),
        _rec(task_id="t2", team="implementation_fix", verdict="APPROVE"),
    ]
    result = first_pass_approval_rate(records)
    assert result == 1.0


def test_fpar_ignores_none_verdict() -> None:
    records = [
        _rec(task_id="t1", verdict="APPROVE"),
        _rec(task_id="t2", verdict=None),
    ]
    result = first_pass_approval_rate(records)
    assert result == 1.0


def test_mfc_empty_records_returns_none() -> None:
    assert mean_fix_cycles_to_merge([]) is None


def test_mfc_no_reward_true_records_returns_none() -> None:
    records = [
        _rec(pr_number=1, team="implementation", reward=False),
        _rec(pr_number=1, team="implementation", reward=None),
    ]
    assert mean_fix_cycles_to_merge(records) is None


def test_mfc_first_pass_pr_contributes_zero() -> None:
    records = [_rec(pr_number=1, team="implementation", reward=True)]
    assert mean_fix_cycles_to_merge(records) == 0.0


def test_mfc_one_fix_cycle() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True),
    ]
    assert mean_fix_cycles_to_merge(records) == 1.0


def test_mfc_two_fix_cycles() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True),
    ]
    assert mean_fix_cycles_to_merge(records) == 2.0


def test_mfc_mean_over_distinct_prs() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation_fix", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation_fix", reward=True),
    ]
    assert mean_fix_cycles_to_merge(records) == 1.0


def test_mfc_ignores_records_with_reward_not_true() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=False),
    ]
    assert mean_fix_cycles_to_merge(records) == 0.0


def test_mfc_ignores_none_pr_number() -> None:
    records = [_rec(pr_number=None, team="implementation", reward=True)]
    assert mean_fix_cycles_to_merge(records) is None


def test_mfc_same_pr_number_different_task_ids_counted_separately() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True),
        _rec(task_id="t2", pr_number=1, team="implementation", reward=True),
    ]
    assert mean_fix_cycles_to_merge(records) == 0.5


def test_mfc_corrected_per_pr_grouping_not_per_record() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        *[
            _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True)
            for _ in range(10)
        ],
    ]
    result = mean_fix_cycles_to_merge(records)
    assert result == 10.0


def test_mean_fix_cycles_hidden_returns_none(db_path: Path, ws_root: Path) -> None:
    from datetime import UTC, datetime

    from alan.health import save_learning_metrics
    from alan.queue import Queue
    from alan.routes import infra as infra_mod

    q = Queue(db_path=db_path)
    metrics = LearningMetrics(
        first_pass_approval_rate=0.9, mean_fix_cycles_to_merge=1.2, sample_size=5
    )
    save_learning_metrics(q, metrics, datetime.now(UTC))
    try:
        result = infra_mod._build_quality_metrics(q, ws_root)  # noqa: SLF001
    finally:
        q.close()
    assert result.mean_fix_cycles_to_merge is None


def _mixed_merged_pr_records() -> list[TrajectoryRecord]:
    return [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation", reward=True),
        _rec(task_id="t3", pr_number=3, team="implementation", reward=True),
        _rec(task_id="t3", pr_number=3, team="implementation_fix", reward=True),
        _rec(task_id="t4", pr_number=4, team="implementation", reward=True),
        _rec(task_id="t4", pr_number=4, team="implementation_fix", reward=True),
        _rec(task_id="t4", pr_number=4, team="implementation_fix", reward=True),
        _rec(task_id="t4", pr_number=4, team="implementation_fix", reward=True),
    ]


def test_mfpmp_empty_records_returns_none() -> None:
    assert mean_fix_cycles_per_merged_pr([]) is None


def test_mfpmp_matches_mean_fix_cycles_to_merge_alias() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t1", pr_number=1, team="implementation_fix", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation", reward=True),
    ]
    assert mean_fix_cycles_per_merged_pr(records) == mean_fix_cycles_to_merge(records)
    assert mean_fix_cycles_per_merged_pr(records) == 0.5


def test_mfpmp_denominator_is_all_merged_prs_including_zero_fix() -> None:
    records = _mixed_merged_pr_records()
    assert mean_fix_cycles_per_merged_pr(records) == 1.0


def test_pct_needing_fix_empty_records_returns_none() -> None:
    assert pct_merged_prs_needing_fix([]) is None


def test_pct_needing_fix_returns_zero_when_no_pr_needed_a_fix() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation", reward=True),
    ]
    assert pct_merged_prs_needing_fix(records) == 0.0


def test_pct_needing_fix_numerator_is_prs_with_at_least_one_fix() -> None:
    records = _mixed_merged_pr_records()
    assert pct_merged_prs_needing_fix(records) == 0.5


def test_when_fixed_empty_records_returns_none() -> None:
    assert mean_fix_cycles_when_fixed([]) is None


def test_when_fixed_returns_none_when_no_pr_needed_a_fix() -> None:
    records = [
        _rec(task_id="t1", pr_number=1, team="implementation", reward=True),
        _rec(task_id="t2", pr_number=2, team="implementation", reward=True),
    ]
    assert mean_fix_cycles_when_fixed(records) is None


def test_when_fixed_mean_is_over_only_fixed_prs() -> None:
    records = _mixed_merged_pr_records()
    assert mean_fix_cycles_when_fixed(records) == 2.0


def test_invariant_per_merged_pr_equals_pct_needing_fix_times_when_fixed() -> None:
    records = _mixed_merged_pr_records()
    per_pr = mean_fix_cycles_per_merged_pr(records)
    pct_needing_fix = pct_merged_prs_needing_fix(records)
    when_fixed = mean_fix_cycles_when_fixed(records)
    assert per_pr is not None
    assert pct_needing_fix is not None
    assert when_fixed is not None
    assert per_pr == pct_needing_fix * when_fixed


def test_learning_metrics_new_fields_default_to_none() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5, mean_fix_cycles_to_merge=1.0, sample_size=2
    )
    assert m.mean_fix_cycles_per_merged_pr is None
    assert m.pct_merged_prs_needing_fix is None
    assert m.mean_fix_cycles_when_fixed is None


def test_learning_metrics_new_fields_explicit_values() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=4,
        mean_fix_cycles_per_merged_pr=1.0,
        pct_merged_prs_needing_fix=0.5,
        mean_fix_cycles_when_fixed=2.0,
    )
    assert m.mean_fix_cycles_to_merge == m.mean_fix_cycles_per_merged_pr
    assert m.pct_merged_prs_needing_fix == 0.5
    assert m.mean_fix_cycles_when_fixed == 2.0


def test_to_dict_emits_old_and_new_fix_cycle_keys() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=4,
        mean_fix_cycles_per_merged_pr=1.0,
        pct_merged_prs_needing_fix=0.5,
        mean_fix_cycles_when_fixed=2.0,
    )
    d = learning_metrics_to_dict(m)
    assert d["mean_fix_cycles_to_merge"] == 1.0
    assert d["mean_fix_cycles_per_merged_pr"] == 1.0
    assert d["pct_merged_prs_needing_fix"] == 0.5
    assert d["mean_fix_cycles_when_fixed"] == 2.0


def test_from_dict_missing_new_keys_degrades_to_none_without_raising() -> None:
    legacy_blob = {
        "first_pass_approval_rate": 0.9,
        "mean_fix_cycles_to_merge": 1.2,
        "sample_size": 5,
    }
    restored = learning_metrics_from_dict(legacy_blob)
    assert restored.mean_fix_cycles_to_merge == 1.2
    assert restored.mean_fix_cycles_per_merged_pr is None
    assert restored.pct_merged_prs_needing_fix is None
    assert restored.mean_fix_cycles_when_fixed is None


def test_serializer_round_trip_new_fields() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=4,
        mean_fix_cycles_per_merged_pr=1.0,
        pct_merged_prs_needing_fix=0.5,
        mean_fix_cycles_when_fixed=2.0,
    )
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_learning_metrics_behavioral_fields_default() -> None:
    m = LearningMetrics(first_pass_approval_rate=0.5, mean_fix_cycles_to_merge=1.0, sample_size=2)
    assert m.planner_behavioral_score is None
    assert m.planner_behavioral_baseline is None
    assert m.planner_behavioral_regression is False


def test_learning_metrics_behavioral_fields_explicit() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=2,
        planner_behavioral_score=0.6,
        planner_behavioral_baseline=0.8,
        planner_behavioral_regression=True,
    )
    assert m.planner_behavioral_score == 0.6
    assert m.planner_behavioral_baseline == 0.8
    assert m.planner_behavioral_regression is True


def test_to_dict_emits_behavioral_keys() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=2,
        planner_behavioral_score=0.6,
        planner_behavioral_baseline=0.8,
        planner_behavioral_regression=True,
    )
    d = learning_metrics_to_dict(m)
    assert d["planner_behavioral_score"] == 0.6
    assert d["planner_behavioral_baseline"] == 0.8
    assert d["planner_behavioral_regression"] is True


def test_from_dict_missing_behavioral_keys_degrades_without_raising() -> None:
    legacy_blob = {
        "first_pass_approval_rate": 0.9,
        "mean_fix_cycles_to_merge": 1.2,
        "sample_size": 5,
    }
    restored = learning_metrics_from_dict(legacy_blob)
    assert restored.planner_behavioral_score is None
    assert restored.planner_behavioral_baseline is None
    assert restored.planner_behavioral_regression is False


def test_from_dict_non_bool_regression_coerces_to_false() -> None:
    restored = learning_metrics_from_dict({"planner_behavioral_regression": "yes"})
    assert restored.planner_behavioral_regression is False


def test_serializer_round_trip_behavioral_fields_regression_false() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=4,
        planner_behavioral_score=0.72,
        planner_behavioral_baseline=0.83,
        planner_behavioral_regression=False,
    )
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_serializer_round_trip_behavioral_fields_regression_true() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.5,
        mean_fix_cycles_to_merge=1.0,
        sample_size=4,
        planner_behavioral_score=0.5,
        planner_behavioral_baseline=0.8,
        planner_behavioral_regression=True,
    )
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_merge_gate_recovery_rates_empty_text_returns_none() -> None:
    assert merge_gate_recovery_rates("", now=_MG_NOW) is None


def test_merge_gate_recovery_rates_counts_per_class() -> None:
    rows = [
        _mg_row("genuine", recovered=False, ts=_MG_NOW),
        _mg_row("genuine", recovered=False, ts=_MG_NOW),
        _mg_row("behind_plus_transient", recovered=True, ts=_MG_NOW),
        _mg_row("still_computing", recovered=False, ts=_MG_NOW),
    ]
    rates = merge_gate_recovery_rates(_mg_text(rows), now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"] == {"recovered": 0, "total": 2, "lifetime_total": 2}
    assert rates["behind_plus_transient"] == {"recovered": 1, "total": 1, "lifetime_total": 1}
    assert rates["still_computing"] == {"recovered": 0, "total": 1, "lifetime_total": 1}


def test_merge_gate_recovery_rates_skips_invalid_json() -> None:
    text = (
        json.dumps(_mg_row("genuine", recovered=False, ts=_MG_NOW)) + "\nnot-json\n"
    )
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_skips_non_dict_rows() -> None:
    text = (
        json.dumps(_mg_row("genuine", recovered=False, ts=_MG_NOW)) + "\n[1, 2, 3]\n"
    )
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_skips_blank_lines() -> None:
    text = "\n" + json.dumps(_mg_row("genuine", recovered=False, ts=_MG_NOW)) + "\n\n"
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_uses_unknown_for_missing_block_class() -> None:
    text = '{"recovered": false}\n'
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert "unknown" in rates


def test_merge_gate_recovery_rates_uses_unknown_for_null_block_class() -> None:
    text = '{"block_class": null, "recovered": false}\n'
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert "unknown" in rates
    assert "None" not in rates


def test_merge_gate_recovery_rates_uses_unknown_for_empty_block_class() -> None:
    text = '{"block_class": "", "recovered": false}\n'
    rates = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert rates is not None
    assert "unknown" in rates
    assert "" not in rates


def test_merge_gate_recovery_rates_out_of_window_excluded_but_bumps_lifetime() -> None:
    old_ts = _MG_NOW - timedelta(hours=MERGE_GATE_WINDOW_HOURS + 1)
    rows = [
        _mg_row("genuine", recovered=True, ts=_MG_NOW),
        _mg_row("genuine", recovered=True, ts=old_ts),
    ]
    rates = merge_gate_recovery_rates(_mg_text(rows), now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"] == {"recovered": 1, "total": 1, "lifetime_total": 2}


def test_merge_gate_recovery_rates_class_with_only_stale_rows_survives_with_zero_total() -> None:
    old_ts = _MG_NOW - timedelta(hours=MERGE_GATE_WINDOW_HOURS + 1)
    rows = [_mg_row("sonar_hotspot", recovered=True, ts=old_ts) for _ in range(3)]
    rates = merge_gate_recovery_rates(_mg_text(rows), now=_MG_NOW)
    assert rates is not None
    assert rates["sonar_hotspot"] == {"recovered": 0, "total": 0, "lifetime_total": 3}


def test_merge_gate_recovery_rates_all_rows_out_of_window_returns_dict_not_none() -> None:
    old_ts = _MG_NOW - timedelta(hours=MERGE_GATE_WINDOW_HOURS + 1)
    rows = [_mg_row("genuine", recovered=False, ts=old_ts)]
    rates = merge_gate_recovery_rates(_mg_text(rows), now=_MG_NOW)
    assert rates is not None
    assert rates == {"genuine": {"recovered": 0, "total": 0, "lifetime_total": 1}}


def test_merge_gate_recovery_rates_naive_timestamp_excluded_without_crashing() -> None:
    row = {
        "block_class": "genuine",
        "recovered": True,
        "timestamp_utc": "2026-08-30T12:00:00",
    }
    rates = merge_gate_recovery_rates(json.dumps(row) + "\n", now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"] == {"recovered": 0, "total": 0, "lifetime_total": 1}


def test_merge_gate_recovery_rates_deterministic_across_calls_with_fixed_now() -> None:
    rows = [
        _mg_row("genuine", recovered=True, ts=_MG_NOW),
        _mg_row("genuine", recovered=False, ts=_MG_NOW - timedelta(hours=1)),
    ]
    text = _mg_text(rows)
    first = merge_gate_recovery_rates(text, now=_MG_NOW)
    second = merge_gate_recovery_rates(text, now=_MG_NOW)
    assert first == second


def test_merge_gate_recovery_rates_ceiling_excludes_fixture_cluster_dates() -> None:
    fixture_ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    rows = [_mg_row("genuine", recovered=True, ts=fixture_ts)]
    rates = merge_gate_recovery_rates(_mg_text(rows), now=_MG_NOW)
    assert rates is not None
    assert rates["genuine"]["total"] == 0
    assert rates["genuine"]["lifetime_total"] == 1


def test_merge_gate_window_hours_is_thirty_days() -> None:
    assert MERGE_GATE_WINDOW_HOURS == 720.0


def test_sample_size_empty() -> None:
    assert sample_size([]) == 0


def test_sample_size_counts_implementation_with_verdict() -> None:
    records = [
        _rec(team="implementation", verdict="APPROVE"),
        _rec(team="implementation", verdict="REQUEST_CHANGES"),
    ]
    assert sample_size(records) == 2


def test_sample_size_excludes_none_verdict() -> None:
    records = [
        _rec(team="implementation", verdict="APPROVE"),
        _rec(team="implementation", verdict=None),
    ]
    assert sample_size(records) == 1


def test_sample_size_excludes_non_implementation_team() -> None:
    records = [
        _rec(team="implementation", verdict="APPROVE"),
        _rec(team="implementation_fix", verdict="APPROVE"),
    ]
    assert sample_size(records) == 1


def test_learning_metrics_frozen() -> None:
    import pytest as _pytest

    m = LearningMetrics(first_pass_approval_rate=0.5, mean_fix_cycles_to_merge=1.0, sample_size=2)
    with _pytest.raises((AttributeError, TypeError)):
        m.first_pass_approval_rate = 0.9  # type: ignore[misc]


def test_learning_metrics_none_fields() -> None:
    m = LearningMetrics(first_pass_approval_rate=None, mean_fix_cycles_to_merge=None, sample_size=0)
    assert m.first_pass_approval_rate is None
    assert m.mean_fix_cycles_to_merge is None
    assert m.sample_size == 0


def test_serializer_round_trip_none_fields() -> None:
    m = LearningMetrics(first_pass_approval_rate=None, mean_fix_cycles_to_merge=None, sample_size=0)
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_serializer_round_trip_float_fields() -> None:
    m = LearningMetrics(
        first_pass_approval_rate=0.75, mean_fix_cycles_to_merge=1.5, sample_size=8
    )
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_serializer_round_trip_zero_float() -> None:
    m = LearningMetrics(first_pass_approval_rate=0.0, mean_fix_cycles_to_merge=0.0, sample_size=3)
    d = learning_metrics_to_dict(m)
    restored = learning_metrics_from_dict(d)
    assert restored == m


def test_serializer_from_dict_non_dict_input_returns_default() -> None:
    result = learning_metrics_from_dict("not a dict")
    assert result.first_pass_approval_rate is None
    assert result.mean_fix_cycles_to_merge is None
    assert result.sample_size == 0


def test_serializer_from_dict_list_input_returns_default() -> None:
    result = learning_metrics_from_dict([1, 2, 3])
    assert result.first_pass_approval_rate is None


def test_serializer_from_dict_none_input_returns_default() -> None:
    result = learning_metrics_from_dict(None)
    assert result.first_pass_approval_rate is None
    assert result.sample_size == 0


def test_serializer_from_dict_string_fpar_coerced_to_none() -> None:
    result = learning_metrics_from_dict({"first_pass_approval_rate": "high"})
    assert result.first_pass_approval_rate is None


def test_serializer_from_dict_bool_fpar_coerced_to_none() -> None:
    result = learning_metrics_from_dict({"first_pass_approval_rate": True})
    assert result.first_pass_approval_rate is None


def test_serializer_from_dict_int_fpar_becomes_float() -> None:
    result = learning_metrics_from_dict({"first_pass_approval_rate": 1})
    assert result.first_pass_approval_rate == 1.0
    assert isinstance(result.first_pass_approval_rate, float)


def test_serializer_from_dict_bool_sample_size_coerced_to_zero() -> None:
    result = learning_metrics_from_dict({"sample_size": True})
    assert result.sample_size == 0


def test_serializer_from_dict_string_sample_size_coerced_to_zero() -> None:
    result = learning_metrics_from_dict({"sample_size": "big"})
    assert result.sample_size == 0


def test_serializer_from_dict_missing_keys_return_defaults() -> None:
    result = learning_metrics_from_dict({})
    assert result.first_pass_approval_rate is None
    assert result.mean_fix_cycles_to_merge is None
    assert result.sample_size == 0


def test_parity_with_offline_report_via_audit_logs(
    tmp_path: Path,
) -> None:
    import json

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import learning_report as report  # type: ignore[import-not-found]

    node_turns = tmp_path / "node_turns_audit.jsonl"
    verification = tmp_path / "verification_audit.jsonl"
    merge_audit = tmp_path / "pr_merge_audit.jsonl"

    node_turns.write_text(
        json.dumps({
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
            "task_id": "t1", "inv_id": "inv_001", "team": "implementation", "turns": 10,
        }) + "\n" +
        json.dumps({
            "timestamp_utc": "2026-01-01T01:00:00+00:00",
            "task_id": "t1", "inv_id": "inv_002", "team": "implementation_fix", "turns": 5,
        }) + "\n"
    )
    verification.write_text(
        json.dumps({
            "timestamp_utc": "2026-01-01T00:30:00+00:00",
            "task_id": "t1", "action": "review_ok", "pr_number": 1,
            "verdict": "REQUEST_CHANGES", "inv_id": "inv_001",
        }) + "\n" +
        json.dumps({
            "timestamp_utc": "2026-01-01T01:30:00+00:00",
            "task_id": "t1", "action": "review_ok", "pr_number": 1,
            "verdict": "APPROVE", "inv_id": "inv_002",
        }) + "\n"
    )
    merge_audit.write_text(
        json.dumps({
            "timestamp_utc": "2026-01-01T02:00:00+00:00",
            "task_id": "t1", "action": "merge_ok", "pr_number": 1,
        }) + "\n"
    )

    from alan.trajectory import derive_trajectories

    records = derive_trajectories(
        node_turns.read_text(),
        verification.read_text(),
        merge_audit.read_text(),
        "",
    )

    direct_fpar = first_pass_approval_rate(records)
    direct_mfc = mean_fix_cycles_to_merge(records)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        report.main(["--logs-root", str(tmp_path)])
    out = buf.getvalue()

    assert direct_fpar is not None
    assert direct_mfc is not None
    assert f"first_pass_approval_rate {direct_fpar:.4f}" in out
    assert f"mean_fix_cycles_to_merge {direct_mfc:.4f}" in out


def test_restart_recovery_outcomes_includes_never_started_variants() -> None:
    from alan.learning_metrics import _RESTART_RECOVERY_OUTCOMES  # noqa: SLF001

    assert "rerun_never_started" in _RESTART_RECOVERY_OUTCOMES
    assert "rerun_cap_exhausted" in _RESTART_RECOVERY_OUTCOMES


def test_restart_recovery_counts_tallies_never_started_variants() -> None:
    now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
    text = "\n".join([
        json.dumps({
            "outcome": "rerun_never_started", "team": "implementation_fix",
            "timestamp_utc": now.isoformat(),
        }),
        json.dumps({
            "outcome": "rerun_never_started", "team": "implementation_fix",
            "timestamp_utc": now.isoformat(),
        }),
        json.dumps({
            "outcome": "rerun_cap_exhausted", "team": "implementation_fix",
            "timestamp_utc": now.isoformat(),
        }),
    ])
    from alan.learning_metrics import restart_recovery_counts

    counts = restart_recovery_counts(text, now=now)
    assert counts is not None
    assert counts["implementation_fix"]["rerun_never_started"] == 2
    assert counts["implementation_fix"]["rerun_cap_exhausted"] == 1


def test_resume_outcome_literal_is_subset_of_restart_recovery_outcomes() -> None:
    from alan.learning_metrics import _RESTART_RECOVERY_OUTCOMES  # noqa: SLF001
    from alan.teams._resume_audit import ResumeOutcome

    assert set(get_args(ResumeOutcome)) <= set(_RESTART_RECOVERY_OUTCOMES)
