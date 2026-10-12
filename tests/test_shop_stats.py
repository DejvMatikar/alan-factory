from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.health import (
    _MERGE_GATE_RECOVERY_META_KEY,  # pyright: ignore[reportPrivateUsage]
    HealthFinding,
    HealthState,
    load_merge_gate_recovery,
    save_health_state,
    save_learning_metrics,
)
from alan.learning_metrics import LearningMetrics
from alan.queue import Queue
from alan.shop_stats import assemble_shop_stats_block

_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
_NOW_ISO = _NOW.isoformat()


def _finding(kind: str, description: str) -> HealthFinding:
    return HealthFinding(
        finding_id=kind,
        kind=kind,
        severity="critical",
        description=description,
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="dispatch_remediation",
    )


def test_all_blobs_missing_yields_exactly_four_lines(queue: Queue) -> None:
    block = assemble_shop_stats_block(queue)
    lines = block.splitlines()
    assert len(lines) == 4
    assert all("not enough data yet" in line for line in lines)


def test_all_blobs_missing_never_leaks_none_literal(queue: Queue) -> None:
    block = assemble_shop_stats_block(queue)
    assert "None" not in block
    assert "none" not in block.lower().replace("not enough data yet", "")


def test_learning_metrics_full_data_formats_percentages(queue: Queue) -> None:
    save_learning_metrics(
        queue,
        LearningMetrics(
            first_pass_approval_rate=0.62,
            mean_fix_cycles_to_merge=0.12,
            sample_size=41,
            mean_fix_cycles_per_merged_pr=0.12,
            pct_merged_prs_needing_fix=0.14,
            mean_fix_cycles_when_fixed=1.21,
        ),
        _NOW,
    )
    block = assemble_shop_stats_block(queue)
    assert f"as of {_NOW_ISO}" in block
    assert "sample=41" in block
    assert "62.0%" in block
    assert "0.12" in block
    assert "14.0%" in block
    assert "1.21" in block
    assert "None" not in block


def test_learning_metrics_partial_none_fields_degrade_honestly(queue: Queue) -> None:
    save_learning_metrics(
        queue,
        LearningMetrics(
            first_pass_approval_rate=None,
            mean_fix_cycles_to_merge=None,
            sample_size=0,
            mean_fix_cycles_per_merged_pr=None,
            pct_merged_prs_needing_fix=None,
            mean_fix_cycles_when_fixed=None,
        ),
        _NOW,
    )
    block = assemble_shop_stats_block(queue)
    assert "sample=0" in block
    assert "not enough data yet" in block
    assert "None" not in block


def test_learning_metrics_missing_computed_at_falls_back_to_unknown(queue: Queue) -> None:
    queue.set_meta(
        "learning_metrics_v1",
        json.dumps({"sample_size": 5, "first_pass_approval_rate": 0.5}),
    )
    block = assemble_shop_stats_block(queue)
    assert "as of unknown" in block
    assert "None" not in block


def test_health_defaults_ci_and_deploy_to_green_when_no_findings(queue: Queue) -> None:
    save_health_state(
        queue,
        HealthState(
            verdict="healthy", blocked_count=0, in_flight_count=0, findings=[],
            computed_at=_NOW_ISO,
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Verdict: healthy" in block
    assert "Main CI: green" in block
    assert "Deploy: green" in block


def test_health_cites_main_ci_and_deploy_finding_descriptions(queue: Queue) -> None:
    save_health_state(
        queue,
        HealthState(
            verdict="degraded",
            blocked_count=2,
            in_flight_count=1,
            findings=[
                _finding("main_ci", "Main branch CI is failing (failure)"),
                _finding("deploy", "Main branch deploy is failing (failure) — live build is stale"),
            ],
            computed_at=_NOW_ISO,
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Verdict: degraded" in block
    assert "2 blocked surface(s)" in block
    assert "1 in flight" in block
    assert "Main CI: Main branch CI is failing (failure)" in block
    assert "Deploy: Main branch deploy is failing (failure) — live build is stale" in block


def test_health_missing_computed_at_falls_back_to_unknown(queue: Queue) -> None:
    save_health_state(
        queue,
        HealthState(
            verdict="healthy", blocked_count=0, in_flight_count=0, findings=[], computed_at="",
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "as of unknown" in block


def test_merge_gate_recovery_missing_blob_is_not_enough_data(queue: Queue) -> None:
    block = assemble_shop_stats_block(queue)
    assert "Merge-gate recovery: not enough data yet" in block


def test_merge_gate_recovery_empty_rates_is_not_enough_data(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY, json.dumps({"rates": {}, "computed_at": _NOW_ISO})
    )
    block = assemble_shop_stats_block(queue)
    assert "Merge-gate recovery: not enough data yet" in block


def test_merge_gate_recovery_computes_overall_and_per_class_rates(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {
                    "stale_review": {"recovered": 3, "total": 6},
                    "behind_plus_transient": {"recovered": 9, "total": 10},
                },
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert f"as of {_NOW_ISO}" in block
    assert "Overall: 75.0% recovered (12/16)" in block
    assert "behind_plus_transient: 90.0% (9/10)" in block
    assert "stale_review: 50.0% (3/6)" in block
    behind_idx = block.index("behind_plus_transient")
    stale_idx = block.index("stale_review:")
    assert behind_idx < stale_idx


def test_merge_gate_recovery_header_states_window_when_present(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"genuine": {"recovered": 5, "total": 5}},
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Merge-gate recovery (last 30d, as of" in block


def test_merge_gate_recovery_header_falls_back_to_all_history_when_window_absent(
    queue: Queue,
) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"genuine": {"recovered": 5, "total": 5}},
                "computed_at": _NOW_ISO,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Merge-gate recovery (all history, as of" in block


def test_merge_gate_recovery_class_with_no_in_window_rows_shows_no_measurement(
    queue: Queue,
) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"sonar_hotspot": {"recovered": 0, "total": 0, "lifetime_total": 186}},
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "sonar_hotspot: no measurement in last 30d (lifetime 186)" in block
    assert "0.0%" not in block


def test_merge_gate_recovery_below_min_samples_shows_raw_counts_not_percentage(
    queue: Queue,
) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"stale_review": {"recovered": 1, "total": 4, "lifetime_total": 4}},
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "stale_review: 1/4 (below minimum sample size)" in block
    class_line = next(line for line in block.splitlines() if "stale_review:" in line)
    assert "%" not in class_line


def test_merge_gate_recovery_at_min_samples_shows_percentage(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"stale_review": {"recovered": 1, "total": 5, "lifetime_total": 5}},
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "stale_review: 20.0% (1/5)" in block


def test_merge_gate_recovery_overall_line_keeps_percentage_when_class_suppressed(
    queue: Queue,
) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"stale_review": {"recovered": 1, "total": 4, "lifetime_total": 4}},
                "computed_at": _NOW_ISO,
                "window_hours": 720.0,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Overall: 25.0% recovered (1/4)" in block
    assert "stale_review: 1/4 (below minimum sample size)" in block


def test_merge_gate_recovery_old_shape_blob_renders_without_keyerror(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps(
            {
                "rates": {"genuine": {"recovered": 2, "total": 9}},
                "computed_at": _NOW_ISO,
            }
        ),
    )
    block = assemble_shop_stats_block(queue)
    assert "Merge-gate recovery (all history, as of" in block
    assert "genuine: 22.2% (2/9)" in block


def test_load_merge_gate_recovery_tolerates_corrupt_json(queue: Queue) -> None:
    queue.set_meta(_MERGE_GATE_RECOVERY_META_KEY, "not-valid-json")
    assert load_merge_gate_recovery(queue) is None
    assert "Merge-gate recovery: not enough data yet" in assemble_shop_stats_block(queue)


def test_load_merge_gate_recovery_tolerates_non_dict_json(queue: Queue) -> None:
    queue.set_meta(_MERGE_GATE_RECOVERY_META_KEY, json.dumps([1, 2, 3]))
    assert load_merge_gate_recovery(queue) is None
    assert "Merge-gate recovery: not enough data yet" in assemble_shop_stats_block(queue)


def test_merge_gate_recovery_malformed_class_entry_defaults_to_zero(queue: Queue) -> None:
    queue.set_meta(
        _MERGE_GATE_RECOVERY_META_KEY,
        json.dumps({"rates": {"weird": "not-a-dict"}, "computed_at": _NOW_ISO}),
    )
    block = assemble_shop_stats_block(queue)
    assert "weird: no measurement in all history (lifetime 0)" in block
    assert "None" not in block


def test_assemble_never_spawns_subprocesses(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("shop_stats must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    save_learning_metrics(
        queue,
        LearningMetrics(
            first_pass_approval_rate=0.5, mean_fix_cycles_to_merge=0.1, sample_size=10
        ),
        _NOW,
    )
    save_health_state(
        queue,
        HealthState(
            verdict="healthy", blocked_count=0, in_flight_count=0, findings=[],
            computed_at=_NOW_ISO,
        ),
    )
    result = assemble_shop_stats_block(queue)
    assert isinstance(result, str) and result


def test_assemble_never_reads_files(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    def _boom(*args: object, **kwargs: object) -> str:
        raise AssertionError("shop_stats must never read a file")

    monkeypatch.setattr(Path, "read_text", _boom)
    result = assemble_shop_stats_block(queue)
    assert isinstance(result, str) and result
