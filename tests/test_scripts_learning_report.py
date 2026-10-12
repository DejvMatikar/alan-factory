from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import learning_report as report  # type: ignore[import-not-found]  # noqa: E402


def _write_gate_audit(logs_dir: Path, rows: list[dict[str, object]]) -> None:
    audit_file = logs_dir / "merge_gate_audit.jsonl"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row) for row in rows]
    audit_file.write_text("\n".join(lines) + "\n")


def test_merge_gate_recovery_rates_returns_none_for_missing_file(
    tmp_path: Path,
) -> None:
    assert report._merge_gate_recovery_rates(tmp_path) is None  # noqa: SLF001


def test_merge_gate_recovery_rates_returns_none_for_empty_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "merge_gate_audit.jsonl").write_text("")
    assert report._merge_gate_recovery_rates(tmp_path) is None  # noqa: SLF001


def test_merge_gate_recovery_rates_counts_per_class(tmp_path: Path) -> None:
    _write_gate_audit(
        tmp_path,
        [
            {"block_class": "genuine", "recovered": False, "outcome": "implementation_fix"},
            {"block_class": "genuine", "recovered": False, "outcome": "implementation_fix"},
            {"block_class": "behind_plus_transient", "recovered": True, "outcome": "pr_merge"},
            {"block_class": "still_computing", "recovered": False, "outcome": "pr_merge"},
        ],
    )
    rates = report._merge_gate_recovery_rates(tmp_path)  # noqa: SLF001
    assert rates is not None
    assert rates["genuine"] == {"recovered": 0, "total": 2}
    assert rates["behind_plus_transient"] == {"recovered": 1, "total": 1}
    assert rates["still_computing"] == {"recovered": 0, "total": 1}


def test_merge_gate_recovery_rates_skips_invalid_json(tmp_path: Path) -> None:
    audit_file = tmp_path / "merge_gate_audit.jsonl"
    audit_file.write_text('{"block_class": "genuine", "recovered": false}\nnot-json\n')
    rates = report._merge_gate_recovery_rates(tmp_path)  # noqa: SLF001
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_skips_non_dict_rows(tmp_path: Path) -> None:
    audit_file = tmp_path / "merge_gate_audit.jsonl"
    audit_file.write_text(
        '{"block_class": "genuine", "recovered": false}\n[1, 2, 3]\n'
    )
    rates = report._merge_gate_recovery_rates(tmp_path)  # noqa: SLF001
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_skips_blank_lines(tmp_path: Path) -> None:
    audit_file = tmp_path / "merge_gate_audit.jsonl"
    audit_file.write_text(
        '\n{"block_class": "genuine", "recovered": false}\n\n'
    )
    rates = report._merge_gate_recovery_rates(tmp_path)  # noqa: SLF001
    assert rates is not None
    assert rates["genuine"]["total"] == 1


def test_merge_gate_recovery_rates_uses_unknown_for_missing_block_class(
    tmp_path: Path,
) -> None:
    _write_gate_audit(tmp_path, [{"recovered": False}])
    rates = report._merge_gate_recovery_rates(tmp_path)  # noqa: SLF001
    assert rates is not None
    assert "unknown" in rates


def test_print_merge_gate_rates_overall_and_per_class(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rates = {
        "genuine": {"recovered": 0, "total": 2},
        "behind_plus_transient": {"recovered": 1, "total": 1},
    }
    report._print_merge_gate_rates(rates)  # noqa: SLF001
    out = capsys.readouterr().out
    assert "merge_gate_overall_recovery_rate 0.3333 (1/3)" in out
    assert "merge_gate_recovery:behind_plus_transient 1.0000 (1/1)" in out
    assert "merge_gate_recovery:genuine 0.0000 (0/2)" in out


def test_print_merge_gate_rates_zero_total_safe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rates: dict[str, dict[str, int]] = {"empty": {"recovered": 0, "total": 0}}
    report._print_merge_gate_rates(rates)  # noqa: SLF001
    out = capsys.readouterr().out
    assert "merge_gate_recovery:empty 0.0000 (0/0)" in out


def test_main_prints_na_when_no_merge_gate_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = report.main(["--logs-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "merge_gate_overall_recovery_rate n/a" in out


def test_main_mean_fix_cycles_na_message_uses_per_pr_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "store.jsonl"
    store.write_text(
        json.dumps({"task_id": "t1", "pr_number": 1, "team": "implementation", "reward": False})
        + "\n"
    )
    rc = report.main(["--store", str(store), "--logs-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mean_fix_cycles_to_merge n/a (no merged-and-survived PRs)" in out


def test_main_prints_rates_when_merge_gate_file_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_gate_audit(
        tmp_path,
        [
            {"block_class": "genuine", "recovered": False, "outcome": "implementation_fix"},
            {"block_class": "behind_plus_transient", "recovered": True, "outcome": "pr_merge"},
        ],
    )
    rc = report.main(["--logs-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "merge_gate_overall_recovery_rate" in out
    assert "merge_gate_recovery:genuine" in out
    assert "merge_gate_recovery:behind_plus_transient" in out


def test_store_record_to_typed_missing_task_id_returns_none() -> None:
    assert report._store_record_to_typed({"team": "implementation"}) is None  # noqa: SLF001


def test_store_record_to_typed_non_str_task_id_returns_none() -> None:
    assert report._store_record_to_typed({"task_id": 1, "team": "implementation"}) is None  # noqa: SLF001


def test_store_record_to_typed_missing_team_returns_none() -> None:
    assert report._store_record_to_typed({"task_id": "t1"}) is None  # noqa: SLF001


def test_store_record_to_typed_valid_minimal() -> None:
    rec = report._store_record_to_typed({"task_id": "t1", "team": "implementation"})  # noqa: SLF001
    assert rec is not None
    assert rec.task_id == "t1"
    assert rec.team == "implementation"
    assert rec.inv_id is None
    assert rec.pr_number is None
    assert rec.verdict is None
    assert rec.reward is None
    assert rec.fix_cycles == 0
    assert rec.revisits == 0
    assert rec.turns is None


def test_store_record_to_typed_full_fields() -> None:
    raw: dict[str, object] = {
        "task_id": "t1",
        "inv_id": "inv_001",
        "pr_number": 5,
        "team": "implementation_fix",
        "verdict": "APPROVE",
        "reward": True,
        "fix_cycles": 2,
        "revisits": 3,
        "turns": 10,
    }
    rec = report._store_record_to_typed(raw)  # noqa: SLF001
    assert rec is not None
    assert rec.task_id == "t1"
    assert rec.inv_id == "inv_001"
    assert rec.pr_number == 5
    assert rec.team == "implementation_fix"
    assert rec.verdict == "APPROVE"
    assert rec.reward is True
    assert rec.fix_cycles == 2
    assert rec.revisits == 3
    assert rec.turns == 10


def test_store_record_to_typed_bool_pr_number_becomes_none() -> None:
    raw: dict[str, object] = {"task_id": "t1", "team": "implementation", "pr_number": True}
    rec = report._store_record_to_typed(raw)  # noqa: SLF001
    assert rec is not None
    assert rec.pr_number is None


def test_run_behavioral_eval_delegates_to_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.behavioral_eval.run_sweep", lambda: 5)
    assert report._run_behavioral_eval() == 5  # noqa: SLF001


def test_main_without_flag_never_invokes_behavioral_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> int:
        raise AssertionError("behavioral eval must not run without --behavioral-eval")

    monkeypatch.setattr(report, "_run_behavioral_eval", _boom)
    rc = report.main(["--logs-root", str(tmp_path)])
    assert rc == 0


def test_main_with_flag_returns_behavioral_eval_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "_run_behavioral_eval", lambda: 1)
    rc = report.main(["--logs-root", str(tmp_path), "--behavioral-eval"])
    assert rc == 1


def test_main_store_parity_with_direct_metric_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import asdict

    from alan.learning_metrics import first_pass_approval_rate, mean_fix_cycles_to_merge
    from alan.trajectory import TrajectoryRecord

    typed_records = [
        TrajectoryRecord(
            task_id="t1", inv_id="inv_001", pr_number=1,
            team="implementation", verdict="APPROVE", reward=True,
            fix_cycles=0, revisits=0, turns=5,
        ),
        TrajectoryRecord(
            task_id="t2", inv_id="inv_001", pr_number=2,
            team="implementation", verdict="REQUEST_CHANGES", reward=False,
            fix_cycles=0, revisits=0, turns=8,
        ),
        TrajectoryRecord(
            task_id="t2", inv_id="inv_002", pr_number=2,
            team="implementation_fix", verdict="APPROVE", reward=True,
            fix_cycles=0, revisits=1, turns=4,
        ),
    ]

    store = tmp_path / "store.jsonl"
    store.write_text("\n".join(json.dumps(asdict(r)) for r in typed_records) + "\n")

    rc = report.main(["--store", str(store), "--logs-root", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out

    expected_fpar = first_pass_approval_rate(typed_records)
    expected_mfc = mean_fix_cycles_to_merge(typed_records)
    assert expected_fpar is not None
    assert expected_mfc is not None
    assert f"first_pass_approval_rate {expected_fpar:.4f}" in out
    assert f"mean_fix_cycles_to_merge {expected_mfc:.4f}" in out
