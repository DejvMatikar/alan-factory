from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.behavioral_eval import (
    ANCHOR_CASE_ID,
    ANCHOR_CEILING,
    BASELINE_FILENAME,
    BRIEF_FILENAME,
    GOLDEN_DIR,
    REGRESSION_MARGIN,
    RUBRIC_FILENAME,
    SYNTHESIZER_RESULT_FILENAME,
    BehavioralScore,
    GoldenCase,
    _case_detail_line,
    _check_anchor,
    _load_case,
    _parse_score,
    _rubric_criterion_ids,
    _rubric_text,
    _single_line,
    _write_transcripts,
    anchor_regression,
    apply_score,
    is_regression,
    judge_case,
    load_baseline,
    load_baseline_score,
    load_golden_cases,
    load_rubric,
    main,
    run_sweep,
    score_planner_cases,
)
from alan.claude_cli import ClaudeCliError
from alan.learning_metrics import LearningMetrics


def _case(case_id: str = "c1", brief: str = "brief", result: str = "plan") -> GoldenCase:
    return GoldenCase(case_id=case_id, brief=brief, synthesizer_result=result)


def _write_case(case_dir: Path, brief: str, result_text: str | None) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / BRIEF_FILENAME).write_text(brief, encoding="utf-8")
    if result_text is not None:
        (case_dir / SYNTHESIZER_RESULT_FILENAME).write_text(
            json.dumps({"result_text": result_text}), encoding="utf-8"
        )


def _resp(*verdicts: str) -> str:
    return "\n".join(f"c{i}: {v} — reason{i}" for i, v in enumerate(verdicts))


def test_load_rubric_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_rubric(tmp_path / "nope.yaml") == {}


def test_load_rubric_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("criteria: [unclosed", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_non_dict_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_valid_dict() -> None:
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert isinstance(rubric.get("criteria"), list)
    assert len(rubric["criteria"]) == 4
    assert rubric.get("scale") == "0.0-1.0"


def test_rubric_text_missing_criteria_returns_empty() -> None:
    assert _rubric_text({}) == ""


def test_rubric_text_non_list_criteria_returns_empty() -> None:
    assert _rubric_text({"criteria": "not-a-list"}) == ""


def test_rubric_text_skips_non_dict_entries() -> None:
    text = _rubric_text({"criteria": ["not-a-dict", {"id": "x", "description": "y"}]})
    assert text == "- x: y"


def test_rubric_text_joins_multiple_entries() -> None:
    text = _rubric_text(
        {"criteria": [{"id": "a", "description": "d1"}, {"id": "b", "description": "d2"}]}
    )
    assert text == "- a: d1\n- b: d2"


def test_rubric_text_includes_scale_when_present() -> None:
    text = _rubric_text({"scale": "0.0-1.0", "criteria": [{"id": "a", "description": "d"}]})
    assert text == "Scale: 0.0-1.0\n- a: d"


def test_rubric_text_skips_non_string_scale() -> None:
    text = _rubric_text({"scale": 5, "criteria": [{"id": "a", "description": "d"}]})
    assert text == "- a: d"


def test_rubric_text_skips_blank_scale() -> None:
    text = _rubric_text({"scale": "   ", "criteria": [{"id": "a", "description": "d"}]})
    assert text == "- a: d"


def test_rubric_text_real_fixture_includes_scale_line() -> None:
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    text = _rubric_text(rubric)
    assert text.startswith("Scale: 0.0-1.0\n")


def test_rubric_criterion_ids_missing_criteria_returns_empty() -> None:
    assert _rubric_criterion_ids({}) == ()


def test_rubric_criterion_ids_non_list_returns_empty() -> None:
    assert _rubric_criterion_ids({"criteria": "nope"}) == ()


def test_rubric_criterion_ids_skips_non_dict_entries() -> None:
    ids = _rubric_criterion_ids({"criteria": ["not-a-dict", {"id": "a"}]})
    assert ids == ("a",)


def test_rubric_criterion_ids_skips_non_string_id() -> None:
    ids = _rubric_criterion_ids({"criteria": [{"id": 5}, {"id": "b"}]})
    assert ids == ("b",)


def test_rubric_criterion_ids_real_fixture_returns_four() -> None:
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert len(_rubric_criterion_ids(rubric)) == 4


def test_single_line_collapses_whitespace() -> None:
    assert _single_line("a\n  b\tc  ") == "a b c"


def test_single_line_truncates_to_limit() -> None:
    assert _single_line("a" * 300, limit=10) == "a" * 10


def test_load_case_missing_brief_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / SYNTHESIZER_RESULT_FILENAME).write_text(
        json.dumps({"result_text": "plan"}), encoding="utf-8"
    )
    assert _load_case(case_dir) is None


def test_load_case_missing_result_file_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / BRIEF_FILENAME).write_text("brief", encoding="utf-8")
    assert _load_case(case_dir) is None


def test_load_case_malformed_json_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / BRIEF_FILENAME).write_text("brief", encoding="utf-8")
    (case_dir / SYNTHESIZER_RESULT_FILENAME).write_text("not-json", encoding="utf-8")
    assert _load_case(case_dir) is None


def test_load_case_non_dict_json_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / BRIEF_FILENAME).write_text("brief", encoding="utf-8")
    (case_dir / SYNTHESIZER_RESULT_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert _load_case(case_dir) is None


def test_load_case_missing_result_text_key_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / BRIEF_FILENAME).write_text("brief", encoding="utf-8")
    (case_dir / SYNTHESIZER_RESULT_FILENAME).write_text(json.dumps({}), encoding="utf-8")
    assert _load_case(case_dir) is None


def test_load_case_empty_result_text_returns_none(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, "brief", "   ")
    assert _load_case(case_dir) is None


def test_load_case_success(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, "the brief", "the plan")
    case = _load_case(case_dir)
    assert case is not None
    assert case.case_id == "case_x"
    assert case.brief == "the brief"
    assert case.synthesizer_result == "the plan"


def test_load_golden_cases_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_golden_cases(tmp_path / "nope") == []


def test_load_golden_cases_mixes_files_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "rubric.yaml").write_text("criteria: []", encoding="utf-8")
    _write_case(tmp_path / "case_01", "b1", "p1")
    (tmp_path / "case_02").mkdir()
    cases = load_golden_cases(tmp_path)
    assert [c.case_id for c in cases] == ["case_01"]


def test_load_golden_cases_real_fixtures() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    assert [c.case_id for c in cases] == ["case_01", "case_02", "case_03", "case_04_anchor"]
    for case in cases:
        assert case.brief.strip()
        assert case.synthesizer_result.strip()


def test_parse_score_no_criteria_lines_returns_none() -> None:
    assert _parse_score("I refuse to answer") is None


def test_parse_score_all_pass_is_one() -> None:
    tally = _parse_score(_resp("PASS", "PASS"))
    assert tally is not None
    assert tally.passed == 2
    assert tally.matched == 2
    assert tally.failed_ids == ()


def test_parse_score_all_fail_is_zero() -> None:
    tally = _parse_score(_resp("FAIL", "FAIL"))
    assert tally is not None
    assert tally.passed == 0
    assert tally.matched == 2


def test_parse_score_partial_pass_is_fraction() -> None:
    tally = _parse_score(_resp("PASS", "FAIL", "PASS", "PASS"))
    assert tally is not None
    assert tally.passed == 3
    assert tally.matched == 4


def test_parse_score_case_insensitive_verdict() -> None:
    tally = _parse_score("a: pass — ok\nb: Fail — no")
    assert tally is not None
    assert tally.passed == 1
    assert tally.matched == 2


def test_parse_score_line_without_reason_is_ignored() -> None:
    tally = _parse_score("a: PASS\nb: PASS — has a reason")
    assert tally is not None
    assert tally.passed == 1
    assert tally.matched == 1


def test_parse_score_ignores_surrounding_prose() -> None:
    text = "Here is my evaluation:\na: PASS — good\nThanks for reading!"
    tally = _parse_score(text)
    assert tally is not None
    assert tally.passed == 1
    assert tally.matched == 1


def test_parse_score_accepts_en_dash_separator() -> None:
    tally = _parse_score("a: PASS – ok\nb: FAIL – no")
    assert tally is not None
    assert tally.passed == 1
    assert tally.matched == 2


def test_parse_score_matched_and_failed_ids_preserve_order() -> None:
    tally = _parse_score("a: PASS — ok\nb: FAIL — no\nc: FAIL — no\nd: PASS — ok")
    assert tally is not None
    assert tally.matched_ids == ("a", "b", "c", "d")
    assert tally.failed_ids == ("b", "c")


def test_parse_score_duplicate_ids_preserved() -> None:
    tally = _parse_score("a: PASS — ok\na: FAIL — no")
    assert tally is not None
    assert tally.matched_ids == ("a", "a")
    assert tally.failed_ids == ("a",)


def test_anchor_regression_none_score_returns_false() -> None:
    assert anchor_regression(None) is False


def test_anchor_regression_below_ceiling_returns_false() -> None:
    assert anchor_regression(0.2) is False


def test_anchor_regression_at_ceiling_boundary_returns_false() -> None:
    assert anchor_regression(ANCHOR_CEILING) is False


def test_anchor_regression_above_ceiling_returns_true() -> None:
    assert anchor_regression(0.4) is True


def test_anchor_regression_default_ceiling_is_module_constant() -> None:
    assert anchor_regression(0.5) == (0.5 > ANCHOR_CEILING)


def test_judge_case_claude_cli_error_returns_error_outcome() -> None:
    def _boom(_system: str, _user: str) -> str:
        raise ClaudeCliError("down")

    outcome = judge_case(_case(), {}, run_judge=_boom)
    assert outcome.score is None
    assert outcome.tally is None
    assert outcome.error == "judge_call_failed"
    assert outcome.error_detail == "down"
    assert outcome.raw_response == ""


def test_judge_case_malformed_response_returns_error_outcome() -> None:
    outcome = judge_case(_case(), {}, run_judge=lambda s, u: "no criteria here")
    assert outcome.score is None
    assert outcome.tally is None
    assert outcome.error == "unparseable_response"
    assert outcome.error_detail == "no criteria here"
    assert outcome.raw_response == "no criteria here"


def test_judge_case_error_detail_collapses_multiline_message() -> None:
    def _boom(_system: str, _user: str) -> str:
        raise ClaudeCliError("line one\nline two REGRESSION should not leak")

    outcome = judge_case(_case(), {}, run_judge=_boom)
    assert "\n" not in outcome.error_detail
    assert outcome.error_detail == "line one line two REGRESSION should not leak"


def test_judge_case_success() -> None:
    response = _resp("PASS", "PASS", "PASS", "FAIL")
    result = judge_case(_case("c1"), {}, run_judge=lambda s, u: response)
    assert result.case_id == "c1"
    assert result.score == 0.75
    assert result.error is None
    assert result.error_detail == ""
    assert result.raw_response == response
    assert result.tally is not None
    assert result.tally.passed == 3
    assert result.tally.matched == 4


def test_judge_case_uses_default_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_run_agent(system_prompt: str, user_text: str, **kwargs: object) -> str:
        calls.append((system_prompt, user_text))
        return _resp("PASS", "FAIL")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _fake_run_agent)
    result = judge_case(_case("c1"), {"criteria": []})
    assert result.score == 0.5
    assert calls


def test_score_planner_cases_empty_list() -> None:
    score = score_planner_cases([], {}, run_judge=lambda s, u: _resp("PASS"))
    assert score.mean is None
    assert score.case_scores == {}
    assert score.case_results == {}
    assert score.scored_count == 0
    assert score.total_count == 0


def test_score_planner_cases_all_succeed() -> None:
    cases = [_case("c1"), _case("c2")]

    def _judge(_system: str, _user: str) -> str:
        return _resp("PASS", "PASS", "PASS", "FAIL")

    score = score_planner_cases(cases, {}, run_judge=_judge)
    assert score.mean == pytest.approx(0.75)
    assert score.scored_count == 2
    assert score.total_count == 2
    assert set(score.case_results) == {"c1", "c2"}


def test_score_planner_cases_partial_failure() -> None:
    cases = [_case("c1"), _case("c2")]
    responses = iter([_resp("PASS", "PASS", "PASS", "FAIL", "FAIL"), "garbage"])

    def _judge(_system: str, _user: str) -> str:
        return next(responses)

    score = score_planner_cases(cases, {}, run_judge=_judge)
    assert score.mean == pytest.approx(0.6)
    assert score.scored_count == 1
    assert score.total_count == 2
    assert score.case_scores == {"c1": 0.6}
    assert score.case_results["c2"].error == "unparseable_response"


def test_score_planner_cases_all_fail_mean_none() -> None:
    cases = [_case("c1")]
    score = score_planner_cases(cases, {}, run_judge=lambda s, u: "garbage")
    assert score.mean is None
    assert score.scored_count == 0
    assert score.total_count == 1
    assert score.case_results["c1"].error == "unparseable_response"


def test_load_baseline_score_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_baseline_score(tmp_path / "nope.json") is None


def test_load_baseline_score_malformed_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_baseline_score(path) is None


def test_load_baseline_score_non_dict_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_baseline_score(path) is None


def test_load_baseline_score_bool_mean_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mean": True}), encoding="utf-8")
    assert load_baseline_score(path) is None


def test_load_baseline_score_non_numeric_mean_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mean": "high"}), encoding="utf-8")
    assert load_baseline_score(path) is None


def test_load_baseline_score_int_mean_becomes_float(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mean": 1}), encoding="utf-8")
    result = load_baseline_score(path)
    assert result == 1.0
    assert isinstance(result, float)


def test_load_baseline_score_real_fixture() -> None:
    assert load_baseline_score(GOLDEN_DIR / BASELINE_FILENAME) == 0.833


def test_load_baseline_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "nope.json") is None


def test_load_baseline_malformed_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("not-json", encoding="utf-8")
    assert load_baseline(path) is None


def test_load_baseline_missing_case_scores_defaults_empty(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mean": 0.5}), encoding="utf-8")
    baseline = load_baseline(path)
    assert baseline is not None
    assert baseline.mean == 0.5
    assert baseline.case_scores == {}


def test_load_baseline_non_dict_case_scores_defaults_empty(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mean": 0.5, "case_scores": "nope"}), encoding="utf-8")
    baseline = load_baseline(path)
    assert baseline is not None
    assert baseline.case_scores == {}


def test_load_baseline_drops_non_numeric_case_score_entries(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "mean": 0.5,
                "case_scores": {"c1": 0.5, "c2": "high", "c3": True, "c4": 1},
            }
        ),
        encoding="utf-8",
    )
    baseline = load_baseline(path)
    assert baseline is not None
    assert baseline.case_scores == {"c1": 0.5, "c4": 1.0}


def test_load_baseline_real_fixture_includes_case_scores() -> None:
    baseline = load_baseline(GOLDEN_DIR / BASELINE_FILENAME)
    assert baseline is not None
    assert baseline.mean == 0.833
    assert baseline.case_scores == {"case_01": 0.75, "case_02": 0.75, "case_03": 1.0}


def test_is_regression_current_none_false() -> None:
    assert is_regression(None, 0.8) is False


def test_is_regression_baseline_none_false() -> None:
    assert is_regression(0.5, None) is False


def test_is_regression_below_margin_true() -> None:
    assert is_regression(0.5, 0.8, margin=0.1) is True


def test_is_regression_at_boundary_false() -> None:
    baseline, margin = 0.8, 0.1
    current = baseline - margin
    assert is_regression(current, baseline, margin=margin) is False


def test_is_regression_above_baseline_false() -> None:
    assert is_regression(0.9, 0.8, margin=0.1) is False


def test_is_regression_default_margin_is_module_constant() -> None:
    assert is_regression(0.5, 0.8) == (0.5 < 0.8 - REGRESSION_MARGIN)


def test_apply_score_sets_fields_when_regressed() -> None:
    base = LearningMetrics(
        first_pass_approval_rate=None, mean_fix_cycles_to_merge=None, sample_size=0
    )
    score = BehavioralScore(
        mean=0.5, case_scores={"c1": 0.5}, case_results={}, scored_count=1, total_count=1
    )
    updated = apply_score(base, score, baseline=0.8)
    assert updated.planner_behavioral_score == 0.5
    assert updated.planner_behavioral_baseline == 0.8
    assert updated.planner_behavioral_regression is True


def test_apply_score_no_regression() -> None:
    base = LearningMetrics(
        first_pass_approval_rate=None, mean_fix_cycles_to_merge=None, sample_size=0
    )
    score = BehavioralScore(
        mean=0.9, case_scores={"c1": 0.9}, case_results={}, scored_count=1, total_count=1
    )
    updated = apply_score(base, score, baseline=0.8)
    assert updated.planner_behavioral_regression is False


def test_case_detail_line_scored_with_blessed() -> None:
    outcome = judge_case(_case("c1"), {}, run_judge=lambda s, u: _resp("PASS", "FAIL"))
    line = _case_detail_line("c1", outcome, blessed=0.5, rubric_count=2)
    assert line.startswith("EVAL_CASE case=c1 score=0.500 ")
    assert "passed=1/2" in line
    assert "rubric=2" in line
    assert "matched_ids=c0,c1" in line
    assert "failed_ids=c1" in line
    assert "blessed=0.500 delta=+0.000" in line


def test_case_detail_line_scored_without_blessed() -> None:
    outcome = judge_case(_case("c1"), {}, run_judge=lambda s, u: _resp("PASS", "PASS"))
    line = _case_detail_line("c1", outcome, blessed=None, rubric_count=2)
    assert "failed_ids=none" in line
    assert "blessed=n/a delta=n/a" in line


def test_case_detail_line_negative_delta() -> None:
    outcome = judge_case(_case("c1"), {}, run_judge=lambda s, u: _resp("FAIL", "FAIL"))
    line = _case_detail_line("c1", outcome, blessed=0.8, rubric_count=2)
    assert "delta=-0.800" in line


def test_case_detail_line_error_outcome() -> None:
    outcome = judge_case(_case("c1"), {}, run_judge=lambda s, u: "garbage")
    line = _case_detail_line("c1", outcome, blessed=0.6, rubric_count=2)
    assert line == (
        "EVAL_CASE case=c1 score=n/a reason=unparseable_response detail=garbage "
        "blessed=0.600 delta=n/a"
    )


def test_case_detail_line_outcome_absent() -> None:
    line = _case_detail_line("c1", None, blessed=0.6, rubric_count=2)
    assert line == "EVAL_CASE case=c1 score=n/a reason=not_run blessed=0.600 delta=n/a"


def test_case_detail_line_distinguishes_shrunk_denominator_from_failed_criterion() -> None:
    shrunk = judge_case(_case("c1"), {}, run_judge=lambda s, u: _resp("PASS", "PASS", "PASS"))
    shrunk_line = _case_detail_line("c1", shrunk, blessed=None, rubric_count=4)
    assert "passed=3/3 rubric=4" in shrunk_line

    failed = judge_case(
        _case("c1"), {}, run_judge=lambda s, u: _resp("PASS", "PASS", "PASS", "FAIL")
    )
    failed_line = _case_detail_line("c1", failed, blessed=None, rubric_count=4)
    assert "passed=3/4 rubric=4" in failed_line

    assert shrunk_line != failed_line


def test_write_transcripts_noop_on_none_path() -> None:
    score = score_planner_cases([_case("c1")], {}, run_judge=lambda s, u: _resp("PASS"))
    _write_transcripts(None, score)


def test_write_transcripts_noop_on_empty_case_results(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    score = score_planner_cases([], {}, run_judge=lambda s, u: _resp("PASS"))
    _write_transcripts(path, score)
    assert not path.exists()


def test_write_transcripts_writes_every_case_including_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "out.txt"
    responses = iter([_resp("PASS", "PASS"), "garbage"])
    score = score_planner_cases(
        [_case("c1"), _case("c2")], {}, run_judge=lambda s, u: next(responses)
    )
    _write_transcripts(path, score)
    text = path.read_text(encoding="utf-8")
    assert "=== case=c1 ===" in text
    assert "=== case=c2 ===" in text
    assert "garbage" in text


def test_write_transcripts_rejects_path_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "behavioral-eval-escape.txt"
    score = score_planner_cases([_case("c1")], {}, run_judge=lambda s, u: _resp("PASS"))
    _write_transcripts(outside, score)
    assert not outside.exists()


def test_write_transcripts_rejects_existing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "out.txt"
    directory.mkdir()
    score = score_planner_cases([_case("c1")], {}, run_judge=lambda s, u: _resp("PASS"))
    _write_transcripts(directory, score)
    assert directory.is_dir()
    assert list(directory.iterdir()) == []


def test_check_anchor_case_absent_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert _check_anchor([_case("other")], {}) == 0
    assert capsys.readouterr().out == ""


def test_check_anchor_judge_failure_prints_diagnostic_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: "garbage")
    cases = [_case(ANCHOR_CASE_ID)]
    assert _check_anchor(cases, {}) == 0
    out = capsys.readouterr().out
    assert "EVAL_ANCHOR_UNSCORED reason=unparseable_response detail=garbage" in out


def test_check_anchor_claude_cli_error_prints_diagnostic_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(*a: object, **k: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _boom)
    cases = [_case(ANCHOR_CASE_ID)]
    assert _check_anchor(cases, {}) == 0
    out = capsys.readouterr().out
    assert "EVAL_ANCHOR_UNSCORED reason=judge_call_failed detail=down" in out


def test_check_anchor_healthy_prints_metric_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "alan.behavioral_eval.run_agent", lambda *a, **k: _resp("FAIL", "FAIL")
    )
    cases = [_case(ANCHOR_CASE_ID)]
    assert _check_anchor(cases, {}) == 0
    out = capsys.readouterr().out
    assert "EVAL_METRIC planner_behavioral_anchor_score=0.000" in out
    assert "REGRESSION" not in out


def test_check_anchor_regression_prints_and_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS")
    )
    cases = [_case(ANCHOR_CASE_ID)]
    assert _check_anchor(cases, {}) == 1
    out = capsys.readouterr().out
    assert "EVAL_METRIC planner_behavioral_anchor_score=1.000" in out
    assert (
        f"REGRESSION planner_behavioral_anchor_score=1.000 "
        f"ceiling={ANCHOR_CEILING:.3f} case={ANCHOR_CASE_ID}" in out
    )


def test_run_sweep_no_fixtures_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run_sweep(tmp_path)
    assert rc == 0
    assert "planner_behavioral_score=N/A" in capsys.readouterr().out


def test_run_sweep_all_judge_calls_fail_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: "garbage")
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR" in out


def test_run_sweep_emits_case_detail_even_on_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: "garbage")
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "EVAL_CASE case=case_01 score=n/a reason=unparseable_response" in out


def test_run_sweep_no_baseline_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "planner_behavioral_score=1.000" in out
    assert "REGRESSION" not in out
    assert "anchor" not in out


def test_run_sweep_with_baseline_no_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    (tmp_path / BASELINE_FILENAME).write_text(json.dumps({"mean": 0.85}), encoding="utf-8")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "REGRESSION" not in out


def test_run_sweep_regression_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    (tmp_path / BASELINE_FILENAME).write_text(json.dumps({"mean": 0.9}), encoding="utf-8")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("FAIL", "FAIL"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "REGRESSION planner_behavioral_score=0.000 baseline=0.900" in out


def test_run_sweep_case_detail_reports_blessed_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    (tmp_path / BASELINE_FILENAME).write_text(
        json.dumps({"mean": 0.9, "case_scores": {"case_01": 0.9}}), encoding="utf-8"
    )
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "EVAL_CASE case=case_01 score=1.000" in out
    assert "blessed=0.900 delta=+0.100" in out


def test_run_sweep_case_detail_includes_baseline_only_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    (tmp_path / BASELINE_FILENAME).write_text(
        json.dumps({"mean": 0.9, "case_scores": {"case_01": 0.9, "case_99": 0.5}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "EVAL_CASE case=case_99 score=n/a reason=not_run blessed=0.500 delta=n/a" in out


def test_run_sweep_writes_transcripts_for_every_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_case(tmp_path / "case_01", "b1", "p1")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    transcripts_path = tmp_path / "transcripts.txt"
    rc = run_sweep(tmp_path, transcripts_path=transcripts_path)
    capsys.readouterr()
    assert rc == 0
    assert transcripts_path.exists()
    assert "=== case=case_01 ===" in transcripts_path.read_text(encoding="utf-8")


def test_run_sweep_anchor_healthy_excluded_from_mean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    _write_case(tmp_path / ANCHOR_CASE_ID, "anchor brief", "anchor plan")

    def _judge(_system: str, user_text: str, **_kwargs: object) -> str:
        if "anchor plan" in user_text:
            return _resp("FAIL", "FAIL")
        return _resp("PASS", "PASS")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _judge)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "planner_behavioral_score=1.000 (scored 1/1)" in out
    assert "EVAL_METRIC planner_behavioral_anchor_score=0.000" in out
    assert "REGRESSION" not in out


def test_run_sweep_anchor_regression_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    _write_case(tmp_path / ANCHOR_CASE_ID, "anchor brief", "anchor plan")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert (
        f"REGRESSION planner_behavioral_anchor_score=1.000 "
        f"ceiling={ANCHOR_CEILING:.3f} case={ANCHOR_CASE_ID}" in out
    )


def test_run_sweep_anchor_unscored_diagnostic_does_not_collide_with_gate_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")
    _write_case(tmp_path / ANCHOR_CASE_ID, "anchor brief", "anchor plan")

    def _judge(_system: str, user_text: str, **_kwargs: object) -> str:
        if "anchor plan" in user_text:
            raise ClaudeCliError("failed\nmultiline REGRESSION lookalike")
        return _resp("PASS", "PASS")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _judge)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    assert not any(line.startswith("HARNESS_ERROR") for line in lines)
    assert not any(line.startswith("REGRESSION") for line in lines)
    anchor_lines = [line for line in lines if line.startswith("EVAL_ANCHOR_UNSCORED")]
    assert len(anchor_lines) == 1
    assert "REGRESSION" in anchor_lines[0]


def test_run_sweep_case_detail_diagnostic_does_not_collide_with_gate_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", "b1", "p1")

    def _boom(*a: object, **k: object) -> str:
        raise ClaudeCliError("boom\nHARNESS_ERROR lookalike")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _boom)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    lines = out.splitlines()
    harness_lines = [line for line in lines if line.startswith("HARNESS_ERROR")]
    assert len(harness_lines) == 1
    assert harness_lines[0] == (
        "HARNESS_ERROR planner behavioral eval harness broken: 0/1 golden cases scored"
    )


def test_main_golden_dir_override_no_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--golden-dir", str(tmp_path)])
    assert rc == 0
    assert "N/A" in capsys.readouterr().out


def test_main_transcripts_default_value(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run_sweep(golden_dir: Path, transcripts_path: Path | None = None) -> int:
        captured["golden_dir"] = golden_dir
        captured["transcripts_path"] = transcripts_path
        return 0

    monkeypatch.setattr("alan.behavioral_eval.run_sweep", _fake_run_sweep)
    rc = main([])
    assert rc == 0
    assert captured["transcripts_path"] == Path("planner-eval-transcripts.txt")


def test_main_transcripts_threading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_case(tmp_path / "case_01", "b1", "p1")
    monkeypatch.setattr("alan.behavioral_eval.run_agent", lambda *a, **k: _resp("PASS", "PASS"))
    transcripts_path = tmp_path / "custom-transcripts.txt"
    rc = main(["--golden-dir", str(tmp_path), "--transcripts", str(transcripts_path)])
    capsys.readouterr()
    assert rc == 0
    assert transcripts_path.exists()
    assert "case_01" in transcripts_path.read_text(encoding="utf-8")


def test_main_default_golden_dir_uses_real_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def _judge(_system: str, user_text: str, **_kwargs: object) -> str:
        if "GET /api/version" in user_text:
            return _resp("FAIL", "FAIL")
        return _resp("PASS", "PASS")

    monkeypatch.setattr("alan.behavioral_eval.run_agent", _judge)
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "scored 3/3" in out
