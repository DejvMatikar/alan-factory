from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.claude_cli import ClaudeCliError
from alan.compactor_eval import (
    CASE_FILENAME,
    CONTEXT_FILENAME,
    GOLDEN_DIR,
    RUBRIC_FILENAME,
    CaseResult,
    CompactorScore,
    GoldenCase,
    GoldenSuiteError,
    _load_case,
    _min_cases_from_rubric,
    _require_str,
    _require_str_list,
    compactor_case,
    load_golden_cases,
    load_rubric,
    main,
    run_sweep,
    score_compactor_cases,
    validate_case_count,
)


def _case(
    case_id: str = "c1",
    context: str = "ctx with IDENT_A and IDENT_B",
    must_survive: tuple[str, ...] = ("IDENT_A", "IDENT_B"),
    citation: str = "cite",
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id, context=context, must_survive=must_survive, citation=citation
    )


def _write_case(
    case_dir: Path,
    *,
    must_survive: list[str] | None = None,
    citation: str = "cite",
    context: str | None = "ctx with IDENT_A",
    case_payload: dict[str, object] | None = None,
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = (
        case_payload
        if case_payload is not None
        else {"must_survive": must_survive or ["IDENT_A"], "citation": citation}
    )
    (case_dir / CASE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    if context is not None:
        (case_dir / CONTEXT_FILENAME).write_text(context, encoding="utf-8")


def test_load_rubric_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_rubric(tmp_path / "nope.yaml") == {}


def test_load_rubric_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("min_cases: [unclosed", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_non_dict_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "rubric.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert load_rubric(path) == {}


def test_load_rubric_valid_dict() -> None:
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert rubric.get("min_cases") == 5


def test_min_cases_from_rubric_missing_key_uses_default() -> None:
    assert _min_cases_from_rubric({}) == 5


def test_min_cases_from_rubric_bool_value_uses_default() -> None:
    assert _min_cases_from_rubric({"min_cases": True}) == 5


def test_min_cases_from_rubric_non_int_value_uses_default() -> None:
    assert _min_cases_from_rubric({"min_cases": "five"}) == 5


def test_min_cases_from_rubric_valid_int() -> None:
    assert _min_cases_from_rubric({"min_cases": 3}) == 3


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


def test_require_str_list_missing_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str_list({}, "must_survive", "case_x")


def test_require_str_list_non_list_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str_list({"must_survive": "nope"}, "must_survive", "case_x")


def test_require_str_list_empty_list_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str_list({"must_survive": []}, "must_survive", "case_x")


def test_require_str_list_non_str_item_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str_list({"must_survive": ["ok", 5]}, "must_survive", "case_x")


def test_require_str_list_blank_item_raises() -> None:
    with pytest.raises(GoldenSuiteError):
        _require_str_list({"must_survive": ["ok", "   "]}, "must_survive", "case_x")


def test_require_str_list_valid_returns_tuple() -> None:
    assert _require_str_list({"must_survive": ["a", "b"]}, "must_survive", "case_x") == ("a", "b")


def test_load_case_missing_case_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / CONTEXT_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="missing case.json"):
        _load_case(case_dir)


def test_load_case_missing_context_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, context=None)
    with pytest.raises(GoldenSuiteError, match="missing context.txt"):
        _load_case(case_dir)


def test_load_case_malformed_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / CASE_FILENAME).write_text("not-json", encoding="utf-8")
    (case_dir / CONTEXT_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="unreadable case.json"):
        _load_case(case_dir)


def test_load_case_non_dict_json_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    (case_dir / CASE_FILENAME).write_text("[1, 2]", encoding="utf-8")
    (case_dir / CONTEXT_FILENAME).write_text("ctx", encoding="utf-8")
    with pytest.raises(GoldenSuiteError, match="must be a JSON object"):
        _load_case(case_dir)


def test_load_case_missing_must_survive_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, case_payload={"citation": "cite"})
    with pytest.raises(GoldenSuiteError, match="must_survive"):
        _load_case(case_dir)


def test_load_case_missing_citation_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, case_payload={"must_survive": ["a"]})
    with pytest.raises(GoldenSuiteError, match="citation"):
        _load_case(case_dir)


def test_load_case_context_unreadable_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir)
    orig_read_text = Path.read_text

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == CONTEXT_FILENAME:
            raise OSError("boom")
        return orig_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(GoldenSuiteError, match="unreadable context.txt"):
        _load_case(case_dir)


def test_load_case_empty_context_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(case_dir, context="   ")
    with pytest.raises(GoldenSuiteError, match="empty context.txt"):
        _load_case(case_dir)


def test_load_case_success(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    _write_case(
        case_dir,
        must_survive=["ident-1", "ident-2"],
        citation="cite-x",
        context="the context with ident-1 and ident-2",
    )
    case = _load_case(case_dir)
    assert case.case_id == "case_x"
    assert case.must_survive == ("ident-1", "ident-2")
    assert case.citation == "cite-x"
    assert case.context == "the context with ident-1 and ident-2"


def test_load_golden_cases_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_golden_cases(tmp_path / "nope") == []


def test_load_golden_cases_ignores_files(tmp_path: Path) -> None:
    (tmp_path / "rubric.yaml").write_text("min_cases: 1", encoding="utf-8")
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
    assert len(cases) == 5
    assert len(cases) == len({c.case_id for c in cases})
    for case in cases:
        assert case.context.strip()
        assert case.citation.strip()
        assert case.must_survive
        for ident in case.must_survive:
            assert ident in case.context


def test_validate_case_count_real_fixtures_satisfied() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert validate_case_count(cases, rubric) == []


def test_validate_case_count_reports_shortfall() -> None:
    cases = [_case("c1")]
    violations = validate_case_count(cases, {"min_cases": 2})
    assert len(violations) == 1
    assert "has 1 case(s), need >= 2" in violations[0]


def test_validate_case_count_missing_rubric_uses_default_floor() -> None:
    cases = [_case("c1")]
    violations = validate_case_count(cases, {})
    assert "need >= 5" in violations[0]


def test_compactor_case_claude_cli_error_returns_none() -> None:
    def _boom(_context: str) -> str:
        raise ClaudeCliError("down")

    assert compactor_case(_case(), run_compact=_boom) is None


def test_compactor_case_all_survive() -> None:
    result = compactor_case(_case(), run_compact=lambda c: "IDENT_A and IDENT_B survived")
    assert result == CaseResult(
        case_id="c1",
        must_survive=("IDENT_A", "IDENT_B"),
        missing=(),
        survival_rate=1.0,
        citation="cite",
    )


def test_compactor_case_partial_survival() -> None:
    result = compactor_case(_case(), run_compact=lambda c: "only IDENT_A survived")
    assert result is not None
    assert result.missing == ("IDENT_B",)
    assert result.survival_rate == pytest.approx(0.5)


def test_compactor_case_none_survive() -> None:
    result = compactor_case(_case(), run_compact=lambda c: "nothing survived")
    assert result is not None
    assert result.missing == ("IDENT_A", "IDENT_B")
    assert result.survival_rate == 0.0


def test_compactor_case_uses_default_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake(context: str) -> str:
        calls.append(context)
        return context

    monkeypatch.setattr("alan.compactor_eval._call_compactor_llm", _fake)
    result = compactor_case(_case())
    assert result is not None
    assert result.missing == ()
    assert calls == [_case().context]


def test_score_compactor_cases_empty_list() -> None:
    score = score_compactor_cases([], run_compact=lambda c: c)
    assert score == CompactorScore(mean=None, case_results={}, scored_count=0, total_count=0)


def test_score_compactor_cases_all_survive() -> None:
    cases = [_case("c1"), _case("c2")]
    score = score_compactor_cases(cases, run_compact=lambda c: c)
    assert score.mean == 1.0
    assert score.scored_count == 2
    assert score.total_count == 2


def test_score_compactor_cases_partial_harness_failure() -> None:
    cases = [_case("c1"), _case("c2")]
    responses = iter([ClaudeCliError("down"), "IDENT_A and IDENT_B"])

    def _compact(context: str) -> str:
        item = next(responses)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return item

    score = score_compactor_cases(cases, run_compact=_compact)
    assert score.scored_count == 1
    assert score.total_count == 2
    assert score.mean == 1.0


def test_score_compactor_cases_all_harness_failure_mean_none() -> None:
    def _boom(_context: str) -> str:
        raise ClaudeCliError("down")

    score = score_compactor_cases([_case("c1")], run_compact=_boom)
    assert score.mean is None
    assert score.scored_count == 0
    assert score.total_count == 1


def test_run_sweep_no_fixtures_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run_sweep(tmp_path)
    assert rc == 0
    assert "compactor_identifier_survival_rate=N/A" in capsys.readouterr().out


def test_run_sweep_malformed_case_is_harness_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "case_01").mkdir()
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR compactor golden suite malformed" in out


def test_run_sweep_case_count_shortfall_is_golden_suite_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01")
    (tmp_path / RUBRIC_FILENAME).write_text("min_cases: 2\n", encoding="utf-8")
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "GOLDEN_SUITE_ERROR golden suite has 1 case(s), need >= 2" in out


def test_run_sweep_all_compact_calls_fail_is_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01")
    (tmp_path / RUBRIC_FILENAME).write_text("min_cases: 1\n", encoding="utf-8")

    def _boom(*args: object, **kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.compactor_eval._call_compactor_llm", _boom)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS_ERROR compactor behavioral eval harness broken" in out


def test_run_sweep_success_all_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(tmp_path / "case_01", must_survive=["IDENT_A"], context="ctx with IDENT_A")
    (tmp_path / RUBRIC_FILENAME).write_text("min_cases: 1\n", encoding="utf-8")
    monkeypatch.setattr("alan.compactor_eval._call_compactor_llm", lambda c: c)
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "compactor_identifier_survival_rate=1.000" in out
    assert "IDENTIFIER_DROPPED" not in out


def test_run_sweep_success_reports_dropped_identifiers_but_stays_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_case(
        tmp_path / "case_01",
        must_survive=["IDENT_A"],
        citation="PA-05",
        context="ctx with IDENT_A",
    )
    (tmp_path / RUBRIC_FILENAME).write_text("min_cases: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "alan.compactor_eval._call_compactor_llm", lambda c: "compacted, nothing kept"
    )
    rc = run_sweep(tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "compactor_identifier_survival_rate=0.000" in out
    assert "IDENTIFIER_DROPPED case=case_01" in out
    assert "citation='PA-05'" in out


def test_main_golden_dir_override_no_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--golden-dir", str(tmp_path)])
    assert rc == 0
    assert "N/A" in capsys.readouterr().out


def test_main_default_golden_dir_uses_real_fixtures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("alan.compactor_eval._call_compactor_llm", lambda c: c)
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "scored 5/5" in out
