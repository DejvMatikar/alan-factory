from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.claude_cli import UNTRUSTED_CONTENT_NOTICE, ClaudeCliError
from alan.engine_config import EngineConfig, RepoIdentity, RoleConfig, default_engine_config
from alan.judge_eval import (
    _ERRORED_PREFIX,
    _INCORRECT_PREFIX,
    _NO_VERDICT_ANCHOR_PREFIX,
    _RETRY_PREFIX,
    CASE_FILENAME,
    GOLDEN_DIR,
    ROLES_DIRNAME,
    RUBRIC_FILENAME,
    SITUATIONS,
    TASK_FILENAME,
    CaseResult,
    GoldenCase,
    GoldenSuiteError,
    JudgeScore,
    SampleCaseResult,
    _load_case,
    _print_flips,
    _print_incorrect,
    _require_str,
    _resolve_judge_model,
    _situations_from_rubric,
    _write_transcripts,
    emit_diagnostics,
    judge_case,
    load_golden_cases,
    load_rubric,
    main,
    run_sweep,
    score_judge_cases,
    validate_situation_coverage,
)
from alan.teams._static_pipeline import assemble_role_system_prompt
from alan.teams.verification import _JUDGE_PROMPT, TEAM, judge_system_prompt


def _case(
    case_id: str = "c1",
    situation: str = "gate_park",
    intended_verdict: str = "APPROVE",
    must_not_flip: bool = False,
    context: str = "ctx",
    citation: str = "cite",
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        situation=situation,
        context=context,
        intended_verdict=intended_verdict,  # type: ignore[arg-type]
        citation=citation,
        must_not_flip=must_not_flip,
    )


def _result(
    case_id: str = "c1",
    situation: str = "gate_park",
    intended_verdict: str = "APPROVE",
    actual_verdict: str | None = "REQUEST_CHANGES",
    correct: bool = False,
    must_not_flip: bool = False,
    citation: str = "cite",
    raw_response: str = "raw",
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        situation=situation,
        intended_verdict=intended_verdict,  # type: ignore[arg-type]
        actual_verdict=actual_verdict,  # type: ignore[arg-type]
        correct=correct,
        must_not_flip=must_not_flip,
        citation=citation,
        raw_response=raw_response,
    )


def _write_case(
    case_dir: Path,
    *,
    situation: str = "gate_park",
    intended_verdict: str = "APPROVE",
    must_not_flip: bool = False,
    citation: str = "cite",
    task: str | None = "ctx",
    case_payload: dict[str, object] | None = None,
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = (
        case_payload
        if case_payload is not None
        else {
            "situation": situation,
            "intended_verdict": intended_verdict,
            "must_not_flip": must_not_flip,
            "citation": citation,
        }
    )
    (case_dir / CASE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    if task is not None:
        (case_dir / TASK_FILENAME).write_text(task, encoding="utf-8")
    roles_dir = case_dir / ROLES_DIRNAME
    roles_dir.mkdir(exist_ok=True)
    (roles_dir / "01_diff_reviewer.txt").write_text("reviewer out", encoding="utf-8")


def test_situations_has_six_entries() -> None:
    assert len(SITUATIONS) == 6
    assert len(set(SITUATIONS)) == 6


def test_resolve_judge_model_uses_registered_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_config = EngineConfig(
        repo=RepoIdentity(owner="o", repo="r"),
        roles={"judge": RoleConfig(model="fake-registered-model")},
    )
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: fake_config)
    assert _resolve_judge_model() == "fake-registered-model"


def test_resolve_judge_model_falls_back_when_nothing_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    assert _resolve_judge_model() == default_engine_config().roles["judge"].model


def test_load_rubric_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_rubric(tmp_path / "nope.yaml") == {}


def test_load_rubric_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("situations: [unclosed", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_non_dict_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_valid_dict() -> None:
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert isinstance(rubric.get("situations"), list)
    assert len(rubric["situations"]) == 6


def test_situations_from_rubric_non_list_returns_empty() -> None:
    assert _situations_from_rubric({"situations": "nope"}) == {}


def test_situations_from_rubric_skips_non_dict_entries() -> None:
    result = _situations_from_rubric({"situations": ["nope", {"id": "gate_park", "min_cases": 1}]})
    assert result == {"gate_park": 1}


def test_situations_from_rubric_skips_unknown_situation_id() -> None:
    result = _situations_from_rubric({"situations": [{"id": "not_real", "min_cases": 3}]})
    assert result == {}


def test_situations_from_rubric_skips_non_str_id() -> None:
    result = _situations_from_rubric({"situations": [{"id": 5, "min_cases": 1}]})
    assert result == {}


def test_situations_from_rubric_skips_non_int_min_cases() -> None:
    result = _situations_from_rubric(
        {"situations": [{"id": "gate_park", "min_cases": "two"}]}
    )
    assert result == {}


def test_situations_from_rubric_skips_bool_min_cases() -> None:
    result = _situations_from_rubric({"situations": [{"id": "gate_park", "min_cases": True}]})
    assert result == {}


def test_situations_from_rubric_collects_valid_entries() -> None:
    result = _situations_from_rubric(
        {
            "situations": [
                {"id": "gate_park", "min_cases": 1},
                {"id": "hostile_diff_injection", "min_cases": 2},
            ]
        }
    )
    assert result == {"gate_park": 1, "hostile_diff_injection": 2}


def test_require_str_missing_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str({}, "citation", "case_x")


def test_require_str_non_str_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str({"citation": 5}, "citation", "case_x")


def test_require_str_blank_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str({"citation": "   "}, "citation", "case_x")


def test_require_str_valid_returns_value() -> None:
    assert _require_str({"citation": "cite"}, "citation", "case_x") == "cite"


def test_load_case_missing_case_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / TASK_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="missing case.json"):
        _load_case(case_dir)


def test_load_case_missing_task_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, task=None)
    with pytest.raises(GoldenSuiteError, match="task.txt: missing"):
        _load_case(case_dir)


def test_load_case_malformed_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / CASE_FILENAME).write_text("not-json", encoding="utf-8")
    (case_dir / TASK_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="unreadable case.json"):
        _load_case(case_dir)


def test_load_case_non_dict_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / CASE_FILENAME).write_text("[1, 2]", encoding="utf-8")
    (case_dir / TASK_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="must be a JSON object"):
        _load_case(case_dir)


def test_load_case_unknown_situation_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, situation="not_a_real_situation")
    with pytest.raises(GoldenSuiteError, match="unknown situation"):
        _load_case(case_dir)


def test_load_case_invalid_intended_verdict_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(
        case_dir,
        case_payload={
            "situation": "gate_park",
            "intended_verdict": "MAYBE",
            "must_not_flip": False,
            "citation": "cite",
        },
    )
    with pytest.raises(GoldenSuiteError, match="invalid intended_verdict"):
        _load_case(case_dir)


def test_load_case_missing_citation_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(
        case_dir,
        case_payload={
            "situation": "gate_park",
            "intended_verdict": "APPROVE",
            "must_not_flip": False,
        },
    )
    with pytest.raises(GoldenSuiteError, match="citation"):
        _load_case(case_dir)


def test_load_case_non_bool_must_not_flip_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(
        case_dir,
        case_payload={
            "situation": "gate_park",
            "intended_verdict": "APPROVE",
            "must_not_flip": "yes",
            "citation": "cite",
        },
    )
    with pytest.raises(GoldenSuiteError, match="must_not_flip must be a bool"):
        _load_case(case_dir)


def test_load_case_task_unreadable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir)
    orig_read_text = Path.read_text

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == TASK_FILENAME:
            raise OSError("boom")
        return orig_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(GoldenSuiteError, match="task.txt: unreadable"):
        _load_case(case_dir)


def test_load_case_empty_task_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, task="   ")
    with pytest.raises(GoldenSuiteError, match="task.txt: empty"):
        _load_case(case_dir)


def test_load_case_success(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(
        case_dir,
        situation="hostile_diff_injection",
        intended_verdict="REQUEST_CHANGES",
        must_not_flip=True,
        citation="cite-x",
        task="the task",
    )
    case = _load_case(case_dir)
    assert case.case_id == "case_x"
    assert case.situation == "hostile_diff_injection"
    assert case.intended_verdict == "REQUEST_CHANGES"
    assert case.must_not_flip is True
    assert case.citation == "cite-x"
    assert case.context.startswith("the task")
    assert case.roles == (("diff_reviewer", "reviewer out"),)


def test_load_golden_cases_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_golden_cases(tmp_path / "nope") == []


def test_load_golden_cases_ignores_files(tmp_path: Path) -> None:
    (tmp_path / "rubric.yaml").write_text("situations: []", encoding="utf-8")
    _write_case(tmp_path / "case_01")
    cases = load_golden_cases(tmp_path)
    assert [c.case_id for c in cases] == ["case_01"]


def test_load_golden_cases_propagates_bad_case(tmp_path: Path) -> None:
    _write_case(tmp_path / "case_01")
    (tmp_path / "case_02").mkdir()
    with pytest.raises(GoldenSuiteError):
        load_golden_cases(tmp_path)


def test_load_golden_cases_real_fixtures() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    assert len(cases) == 11
    assert len(cases) == len({c.case_id for c in cases})
    for case in cases:
        assert case.context.strip()
        assert case.citation.strip()
        assert case.situation in SITUATIONS


def test_load_golden_cases_real_fixtures_situation_spread() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.situation] = counts.get(case.situation, 0) + 1
    assert counts["scope_partition_approve"] == 3
    assert counts["hostile_diff_injection"] == 2
    assert counts["gate_park"] == 1
    assert counts["scenarios_missing"] == 2
    assert counts["return_code_drift"] == 2
    assert counts["waived_findings"] == 1


def test_load_golden_cases_real_fixtures_must_not_flip() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    flip_ids = {c.case_id for c in cases if c.must_not_flip}
    flip_situations = {c.situation for c in cases if c.must_not_flip}
    assert flip_situations == {"hostile_diff_injection", "scope_partition_approve"}
    assert len(flip_ids) == 3


def test_validate_situation_coverage_real_fixtures_satisfied() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert validate_situation_coverage(cases, rubric) == []


def test_validate_situation_coverage_reports_shortfall() -> None:
    cases = [_case("c1", situation="gate_park")]
    rubric = {"situations": [{"id": "gate_park", "min_cases": 2}]}
    violations = validate_situation_coverage(cases, rubric)
    assert len(violations) == 1
    assert "situation=gate_park" in violations[0]
    assert "has 1 case(s), need >= 2" in violations[0]


def test_validate_situation_coverage_missing_rubric_uses_default_floor() -> None:
    cases = [_case("c1", situation="gate_park")]
    violations = validate_situation_coverage(cases, {})
    assert any("situation=scope_partition_approve" in v for v in violations)
    assert not any("situation=gate_park" in v for v in violations)


def test_judge_case_claude_cli_error_returns_errored_result() -> None:
    def _boom(_system: str, _user: str) -> str:
        raise ClaudeCliError("down")

    result = judge_case(_case(), run_judge=_boom)
    assert result.error == "down"
    assert result.actual_verdict is None
    assert result.correct is False


def test_judge_case_unparseable_response_is_incorrect() -> None:
    result = judge_case(
        _case("c1", intended_verdict="APPROVE"), run_judge=lambda s, u: "no verdict here"
    )
    assert result.actual_verdict is None
    assert result.correct is False
    assert result.raw_response == "no verdict here"


def test_judge_case_correct() -> None:
    result = judge_case(
        _case("c1", intended_verdict="APPROVE"), run_judge=lambda s, u: "VERDICT: APPROVE"
    )
    assert result == CaseResult(
        case_id="c1",
        situation="gate_park",
        intended_verdict="APPROVE",
        actual_verdict="APPROVE",
        correct=True,
        must_not_flip=False,
        citation="cite",
        raw_response="VERDICT: APPROVE",
        first_pass_response="VERDICT: APPROVE",
        first_pass_correct=True,
    )


def test_judge_case_incorrect() -> None:
    result = judge_case(
        _case("c1", intended_verdict="APPROVE"),
        run_judge=lambda s, u: "VERDICT: REQUEST_CHANGES",
    )
    assert result.correct is False
    assert result.actual_verdict == "REQUEST_CHANGES"


def test_judge_case_hands_composed_prompt_to_injected_judge_fn() -> None:
    calls: list[str] = []

    def _spy(system_prompt: str, user_text: str) -> str:
        calls.append(system_prompt)
        return "VERDICT: APPROVE"

    judge_case(_case("c1", intended_verdict="APPROVE"), run_judge=_spy)
    assert calls == [judge_system_prompt()]


def test_judge_case_uses_default_judge_and_composed_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_run_agent(system_prompt: str, user_text: str, **kwargs: object) -> str:
        calls.append((system_prompt, user_text))
        return "VERDICT: APPROVE"

    monkeypatch.setattr("alan.judge_eval.run_agent", _fake_run_agent)
    result = judge_case(_case("c1", intended_verdict="APPROVE"))
    assert result.correct is True
    assert calls
    system_prompt, user_text = calls[0]
    assert "judge on the verification team" in system_prompt
    assert _JUDGE_PROMPT in system_prompt
    assert UNTRUSTED_CONTENT_NOTICE in system_prompt
    assert user_text == "ctx"


def test_default_judge_denies_all_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_run_agent(system_prompt: str, user_text: str, **kwargs: object) -> str:
        calls.append(kwargs)
        return "VERDICT: APPROVE"

    monkeypatch.setattr("alan.judge_eval.run_agent", _fake_run_agent)
    judge_case(_case("c1", intended_verdict="APPROVE"))
    assert calls
    assert calls[0]["deny_all_tools"] is True


def test_judge_system_prompt_matches_non_terse_composition() -> None:
    assert judge_system_prompt() == assemble_role_system_prompt(_JUDGE_PROMPT, terse=False)


def test_judge_system_prompt_matches_team_role_terse_predicate() -> None:
    assert judge_system_prompt() == assemble_role_system_prompt(
        _JUDGE_PROMPT, terse=TEAM._role_terse("judge")
    )


def test_score_judge_cases_empty_list() -> None:
    score = score_judge_cases([], run_judge=lambda s, u: "VERDICT: APPROVE")
    assert score == JudgeScore(
        mean=None, case_results={}, scored_count=0, total_count=0, flipped_case_ids=()
    )


def test_score_judge_cases_all_correct() -> None:
    cases = [
        _case("c1", intended_verdict="APPROVE"),
        _case("c2", intended_verdict="APPROVE"),
    ]
    score = score_judge_cases(cases, run_judge=lambda s, u: "VERDICT: APPROVE")
    assert score.mean == 1.0
    assert score.scored_count == 2
    assert score.total_count == 2
    assert score.flipped_case_ids == ()


def test_score_judge_cases_partial_harness_failure() -> None:
    cases = [_case("c1"), _case("c2")]
    responses = iter([ClaudeCliError("down"), "VERDICT: APPROVE"])

    def _judge(_system: str, _user: str) -> str:
        item = next(responses)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item

    score = score_judge_cases(cases, run_judge=_judge)
    assert score.scored_count == 1
    assert score.total_count == 2
    assert score.mean == 1.0


def test_score_judge_cases_all_harness_failure_mean_none() -> None:
    def _boom(_system: str, _user: str) -> str:
        raise ClaudeCliError("down")

    score = score_judge_cases([_case("c1")], run_judge=_boom)
    assert score.mean is None
    assert score.scored_count == 0
    assert score.total_count == 1


def test_score_judge_cases_flags_must_not_flip_violation() -> None:
    cases = [_case("c1", intended_verdict="REQUEST_CHANGES", must_not_flip=True)]
    score = score_judge_cases(cases, run_judge=lambda s, u: "VERDICT: APPROVE")
    assert score.flipped_case_ids == ("c1",)
    assert score.case_results["c1"].correct is False


def test_score_judge_cases_must_not_flip_correct_is_not_flagged() -> None:
    cases = [_case("c1", intended_verdict="REQUEST_CHANGES", must_not_flip=True)]
    score = score_judge_cases(cases, run_judge=lambda s, u: "VERDICT: REQUEST_CHANGES")
    assert score.flipped_case_ids == ()


def test_incorrect_prefix_literal_and_no_collision() -> None:
    assert _INCORRECT_PREFIX == "JUDGE_CASE_INCORRECT"
    assert _NO_VERDICT_ANCHOR_PREFIX == "JUDGE_NO_VERDICT_ON_ANCHOR"
    assert _RETRY_PREFIX == "JUDGE_RETRY"
    assert _ERRORED_PREFIX == "JUDGE_CASE_ERRORED"
    signals = (
        "HARNESS_ERROR",
        "GOLDEN_SUITE_ERROR",
        "MUST_NOT_FLIP_VIOLATION",
        _INCORRECT_PREFIX,
        _NO_VERDICT_ANCHOR_PREFIX,
        _RETRY_PREFIX,
        _ERRORED_PREFIX,
    )
    for signal in signals:
        for other in signals:
            if signal != other:
                assert not signal.startswith(other)


def test_print_incorrect_formats_fields_including_none_actual(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = SampleCaseResult(
        2,
        _result(
            case_id="c7",
            situation="hostile_diff_injection",
            intended_verdict="REQUEST_CHANGES",
            actual_verdict=None,
        ),
    )
    _print_incorrect([entry])
    out = capsys.readouterr().out
    assert (
        "JUDGE_CASE_INCORRECT sample=2 case=c7 situation=hostile_diff_injection "
        "intended=REQUEST_CHANGES actual=None"
    ) in out


def test_print_flips_emits_must_not_flip_violation(capsys: pytest.CaptureFixture[str]) -> None:
    entry = SampleCaseResult(1, _result(case_id="c1", must_not_flip=True, citation="PA-11"))
    _print_flips([entry])
    out = capsys.readouterr().out
    assert "MUST_NOT_FLIP_VIOLATION sample=1 case=c1 " in out
    assert "citation='PA-11'" in out


def test_emit_diagnostics_incorrect_vs_flip_line_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    flip = SampleCaseResult(1, _result(case_id="c1", must_not_flip=True))
    merely_incorrect = SampleCaseResult(1, _result(case_id="c2", must_not_flip=False))
    emit_diagnostics([flip, merely_incorrect], [], [], None)
    out = capsys.readouterr().out
    assert out.count("JUDGE_CASE_INCORRECT") == 2
    assert out.count("MUST_NOT_FLIP_VIOLATION") == 1


def test_emit_diagnostics_silent_when_nothing_incorrect_retried_or_errored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_diagnostics([], [], [], None)
    assert capsys.readouterr().out == ""


def test_write_transcripts_noop_on_none_path() -> None:
    entry = SampleCaseResult(1, _result())
    _write_transcripts([entry], None)


def test_write_transcripts_noop_on_empty_results(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    _write_transcripts([], path)
    assert not path.exists()


def test_write_transcripts_writes_incorrect_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "out.txt"
    entry = SampleCaseResult(1, _result(case_id="c1", raw_response="the raw text"))
    _write_transcripts([entry], path)
    text = path.read_text(encoding="utf-8")
    assert "=== sample=1 case=c1 ===" in text
    assert "the raw text" in text


def test_write_transcripts_distinguishes_same_case_different_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "out.txt"
    entries = [
        SampleCaseResult(1, _result(case_id="c1", raw_response="first")),
        SampleCaseResult(2, _result(case_id="c1", raw_response="second")),
    ]
    _write_transcripts(entries, path)
    text = path.read_text(encoding="utf-8")
    assert "=== sample=1 case=c1 ===" in text
    assert "=== sample=2 case=c1 ===" in text
    assert "first" in text
    assert "second" in text


def test_write_transcripts_rejects_path_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "judge-eval-escape.txt"
    entry = SampleCaseResult(1, _result(case_id="c1", raw_response="the raw text"))
    _write_transcripts([entry], outside)
    assert not outside.exists()


def test_run_sweep_no_fixtures_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run_sweep(tmp_path)
    assert rc == 0
    assert "judge_verdict_score=N/A" in capsys.readouterr().out


def test_run_sweep_malformed_case_is_harness_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "case_01").mkdir()
    rc = run_sweep(tmp_path)
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
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GOLDEN_SUITE_ERROR situation=gate_park" in out


def test_run_sweep_all_judge_calls_fail_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )

    def _boom(*args: object, **kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.judge_eval.run_agent", _boom)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR judge behavioral eval harness broken" in out


def test_run_sweep_mean_none_reports_errors_but_no_incorrect_lines_or_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )

    def _boom(*args: object, **kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.judge_eval.run_agent", _boom)
    transcripts_path = tmp_path / "transcripts.txt"
    rc = run_sweep(tmp_path, transcripts_path=transcripts_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR" in out
    assert "JUDGE_CASE_INCORRECT" not in out
    assert not transcripts_path.exists()


def test_run_sweep_success_no_flips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", situation="gate_park", intended_verdict="APPROVE")
    (tmp_path / RUBRIC_FILENAME).write_text(
        "situations:\n  - id: gate_park\n    min_cases: 1\n", encoding="utf-8"
    )
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "judge_verdict_score=1.000 first_pass=1.000 (scored 1/1)" in out
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
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "MUST_NOT_FLIP_VIOLATION sample=1 case=case_01" in out
    assert "citation='PA-11'" in out
    assert "JUDGE_CASE_INCORRECT sample=1 case=case_01" in out


def test_run_sweep_writes_transcripts_for_incorrect_cases(
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
    rc = run_sweep(tmp_path, transcripts_path=transcripts_path)
    capsys.readouterr()
    assert rc == 1
    assert transcripts_path.exists()
    assert "=== sample=1 case=case_01 ===" in transcripts_path.read_text(encoding="utf-8")


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

    monkeypatch.setattr("alan.judge_eval.run_sweep", _fake_run_sweep)
    rc = main([])
    assert rc == 0
    assert captured["transcripts_path"] == Path("judge-eval-transcripts.txt")


def test_main_transcripts_threading(
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
    transcripts_path = tmp_path / "custom-transcripts.txt"
    rc = main(["--golden-dir", str(tmp_path), "--transcripts", str(transcripts_path)])
    capsys.readouterr()
    assert rc == 1
    assert transcripts_path.exists()
    assert "case_01" in transcripts_path.read_text(encoding="utf-8")


def test_main_default_golden_dir_uses_real_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("alan.judge_eval.run_agent", lambda *a, **k: "VERDICT: APPROVE")
    rc = main([])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "scored 11/11" in out
