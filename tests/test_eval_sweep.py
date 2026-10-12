from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.claude_cli import ClaudeCliError
from alan.eval_sweep import SampledJudgeScore, main, run_sweep, sample_judge_suite
from alan.judge_eval import (
    CASE_FILENAME,
    ROLES_DIRNAME,
    RUBRIC_FILENAME,
    TASK_FILENAME,
    GoldenCase,
    load_golden_cases,
)


def _case(
    case_id: str = "c1",
    situation: str = "gate_park",
    intended_verdict: str = "APPROVE",
    must_not_flip: bool = False,
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        situation=situation,
        context="ctx",
        intended_verdict=intended_verdict,  # type: ignore[arg-type]
        citation="cite",
        must_not_flip=must_not_flip,
    )


def _write_case(
    case_dir: Path,
    *,
    situation: str = "gate_park",
    intended_verdict: str = "APPROVE",
    must_not_flip: bool = False,
    citation: str = "cite",
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "situation": situation,
        "intended_verdict": intended_verdict,
        "must_not_flip": must_not_flip,
        "citation": citation,
    }
    (case_dir / CASE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    (case_dir / TASK_FILENAME).write_text("ctx", encoding="utf-8")
    (case_dir / ROLES_DIRNAME).mkdir(exist_ok=True)
    (case_dir / ROLES_DIRNAME / "01_diff_reviewer.txt").write_text("out", encoding="utf-8")


def test_sample_judge_suite_empty_cases() -> None:
    result = sample_judge_suite([], samples=3)
    assert result == SampledJudgeScore(
        sample_means=(), mean=None, scored_count=0, total_count=0, incorrect=(), flipped=()
    )


def test_sample_judge_suite_runs_n_independent_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _judge(_system: str, _user: str, **_kwargs: object) -> str:
        calls["n"] += 1
        return "VERDICT: APPROVE"

    monkeypatch.setattr("alan.judge_eval.run_agent", _judge)
    cases = [_case("c1", intended_verdict="APPROVE")]
    result = sample_judge_suite(cases, samples=3)
    assert calls["n"] == 3
    assert result.sample_means == (1.0, 1.0, 1.0)
    assert result.mean == 1.0
    assert result.scored_count == 3
    assert result.total_count == 3
    assert result.incorrect == ()
    assert result.flipped == ()


def test_sample_judge_suite_every_sample_sees_the_identical_rendered_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_case(tmp_path / "case_01")
    cases = load_golden_cases(tmp_path)
    seen: list[str] = []

    def _judge(_system: str, user: str, **_kwargs: object) -> str:
        seen.append(user)
        return "VERDICT: APPROVE"

    monkeypatch.setattr("alan.judge_eval.run_agent", _judge)
    sample_judge_suite(cases, samples=3)
    assert seen == [cases[0].context] * 3


def test_sample_judge_suite_averages_varying_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            "VERDICT: APPROVE",
            "VERDICT: REQUEST_CHANGES",
            "VERDICT: APPROVE",
        ]
    )

    def _judge(_system: str, _user: str, **_kwargs: object) -> str:
        return next(responses)

    monkeypatch.setattr("alan.judge_eval.run_agent", _judge)
    cases = [_case("c1", intended_verdict="APPROVE")]
    result = sample_judge_suite(cases, samples=3)
    assert result.sample_means == (1.0, 0.0, 1.0)
    assert result.mean == pytest.approx(2.0 / 3.0)


def test_sample_judge_suite_all_harness_failures_mean_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_system: str, _user: str, **_kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.judge_eval.run_agent", _boom)
    cases = [_case("c1")]
    result = sample_judge_suite(cases, samples=3)
    assert result.mean is None
    assert result.scored_count == 0
    assert result.total_count == 3


def test_sample_judge_suite_flat_list_accumulation(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(["VERDICT: REQUEST_CHANGES", "VERDICT: APPROVE"])

    def _judge(_system: str, _user: str, **_kwargs: object) -> str:
        return next(responses)

    monkeypatch.setattr("alan.judge_eval.run_agent", _judge)
    cases = [_case("c1", intended_verdict="APPROVE")]
    result = sample_judge_suite(cases, samples=2)
    assert len(result.incorrect) == 1
    assert result.incorrect[0].sample_index == 1
    assert result.incorrect[0].result.case_id == "c1"


def test_sample_judge_suite_reports_each_sample_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    cases = [_case("c1", intended_verdict="REQUEST_CHANGES", must_not_flip=True)]
    result = sample_judge_suite(cases, samples=3)
    assert [entry.sample_index for entry in result.flipped] == [1, 2, 3]
    assert all(entry.result.case_id == "c1" for entry in result.flipped)


def test_sample_judge_suite_flipped_is_must_not_flip_subset_of_incorrect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    cases = [
        _case("c1", intended_verdict="REQUEST_CHANGES", must_not_flip=True),
        _case("c2", intended_verdict="REQUEST_CHANGES", must_not_flip=False),
    ]
    result = sample_judge_suite(cases, samples=1)
    assert len(result.incorrect) == 2
    assert len(result.flipped) == 1
    assert result.flipped[0].result.case_id == "c1"


def test_run_sweep_no_fixtures_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run_sweep(tmp_path, samples=3)
    assert rc == 0
    assert "judge_verdict_score=N/A" in capsys.readouterr().out


def test_run_sweep_malformed_case_is_harness_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "case_01").mkdir()
    rc = run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR judge golden suite malformed" in out


def test_run_sweep_situation_shortfall_is_golden_suite_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 2\n", encoding="utf-8"
    )
    rc = run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GOLDEN_SUITE_ERROR situation=gate_park" in out


def test_run_sweep_all_samples_fail_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )

    def _boom(*args: object, **kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.judge_eval.run_agent", _boom)
    rc = run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR judge behavioral eval harness broken across 3 samples" in out


def test_run_sweep_success_reports_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park", intended_verdict="APPROVE")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 0
    assert "judge_verdict_score=1.000 first_pass=1.000" in out
    assert "across 3 samples: [1.000, 1.000, 1.000]" in out
    assert "MUST_NOT_FLIP_VIOLATION" not in out


def test_run_sweep_flip_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(
        tmp_path / "case_01",
        situation="hostile_diff_injection",
        intended_verdict="REQUEST_CHANGES",
        must_not_flip=True,
        citation="PA-11",
    )
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: hostile_diff_injection\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 1
    assert "MUST_NOT_FLIP_VIOLATION sample=1 case=case_01" in out
    assert "citation='PA-11'" in out
    assert "JUDGE_CASE_INCORRECT sample=1 case=case_01" in out
    assert "JUDGE_CASE_INCORRECT sample=2 case=case_01" in out
    assert "JUDGE_CASE_INCORRECT sample=3 case=case_01" in out


def test_run_sweep_writes_transcripts_only_when_incorrect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park", intended_verdict="APPROVE")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    transcripts_path = tmp_path / "transcripts.txt"
    rc = run_sweep(tmp_path, samples=2, transcripts_path=transcripts_path)
    capsys.readouterr()
    assert rc == 0
    assert not transcripts_path.exists()


def test_run_sweep_writes_transcripts_for_incorrect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_case(
        tmp_path / "case_01",
        situation="hostile_diff_injection",
        intended_verdict="REQUEST_CHANGES",
        must_not_flip=True,
    )
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: hostile_diff_injection\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    transcripts_path = tmp_path / "transcripts.txt"
    rc = run_sweep(tmp_path, samples=2, transcripts_path=transcripts_path)
    capsys.readouterr()
    assert rc == 1
    assert transcripts_path.exists()
    text = transcripts_path.read_text(encoding="utf-8")
    assert "=== sample=1 case=case_01 ===" in text
    assert "=== sample=2 case=case_01 ===" in text


def test_main_golden_dir_override_no_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--golden-dir", str(tmp_path)])
    assert rc == 0
    assert "N/A" in capsys.readouterr().out


def test_main_samples_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park", intended_verdict="APPROVE")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = main(["--golden-dir", str(tmp_path), "--samples", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "across 2 samples" in out


def test_main_transcripts_default_value(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run_sweep(
        golden_dir: Path, samples: int, transcripts_path: Path | None = None
    ) -> int:
        captured["transcripts_path"] = transcripts_path
        return 0

    monkeypatch.setattr("alan.eval_sweep.run_sweep", _fake_run_sweep)
    rc = main([])
    assert rc == 0
    assert captured["transcripts_path"] == Path("judge-eval-transcripts.txt")


def test_main_transcripts_explicit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_case(
        tmp_path / "case_01",
        situation="hostile_diff_injection",
        intended_verdict="REQUEST_CHANGES",
        must_not_flip=True,
    )
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: hostile_diff_injection\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    transcripts_path = tmp_path / "custom.txt"
    rc = main(
        [
            "--golden-dir", str(tmp_path),
            "--samples", "2",
            "--transcripts", str(transcripts_path),
        ]
    )
    capsys.readouterr()
    assert rc == 1
    assert transcripts_path.exists()


def test_main_default_golden_dir_uses_real_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = main([])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "scored 33/33" in out


def test_main_samples_zero_is_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--samples", "0"])
    assert exc_info.value.code == 2
    assert "--samples must be >= 1, got 0" in capsys.readouterr().err


def test_main_samples_negative_is_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--samples", "-1"])
    assert exc_info.value.code == 2
    assert "--samples must be >= 1, got -1" in capsys.readouterr().err
