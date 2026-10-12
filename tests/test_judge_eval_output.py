from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan import eval_sweep, judge_eval
from alan.claude_cli import ClaudeCliError
from alan.judge_eval import (
    CASE_FILENAME,
    ROLES_DIRNAME,
    RUBRIC_FILENAME,
    TASK_FILENAME,
    CaseResult,
    SampleCaseResult,
    _print_errored,
    _print_flips,
    _print_incorrect,
    _print_no_verdict,
    _print_retries,
    _write_transcripts,
    emit_metric,
)

_APPROVE = "review body\nVERDICT: APPROVE"
_REQUEST_CHANGES = "review body\nVERDICT: REQUEST_CHANGES"
_NO_VERDICT = "I will check the repository first."
_FIRST_LOOKALIKE = "MUST_NOT_FLIP_VIOLATION case=x situation=y"

RunFn = Callable[[Path, Path | None], int]

_ENTRYPOINTS = {
    "single": lambda golden, transcripts: judge_eval.run_sweep(golden, transcripts),
    "sampled": lambda golden, transcripts: eval_sweep.run_sweep(
        golden, samples=1, transcripts_path=transcripts
    ),
}


@pytest.fixture(params=sorted(_ENTRYPOINTS))
def run_entrypoint(request: pytest.FixtureRequest) -> RunFn:
    return _ENTRYPOINTS[request.param]


def _write_suite(root: Path, *specs: tuple[str, str, str, bool]) -> None:
    situations: dict[str, int] = {}
    for case_id, situation, intended, must_not_flip in specs:
        case_dir = root / case_id
        case_dir.mkdir(parents=True)
        payload = {
            "situation": situation,
            "intended_verdict": intended,
            "must_not_flip": must_not_flip,
            "citation": f"cite-{case_id}",
        }
        (case_dir / CASE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
        (case_dir / TASK_FILENAME).write_text(f"{case_id}::context", encoding="utf-8")
        (case_dir / ROLES_DIRNAME).mkdir()
        (case_dir / ROLES_DIRNAME / "01_diff_reviewer.txt").write_text("out", encoding="utf-8")
        situations[situation] = 1
    rubric = "situations:\n" + "".join(
        f"  - id: {situation}\n    min_cases: 1\n" for situation in situations
    )
    (root / RUBRIC_FILENAME).write_text(rubric, encoding="utf-8")


def _install_judge(
    monkeypatch: pytest.MonkeyPatch, replies: dict[str, list[str | BaseException]]
) -> None:
    def _agent(_system_prompt: str, user_text: str, **_kwargs: object) -> str:
        reply = replies[user_text.split("::", 1)[0]].pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("alan.judge_eval.run_agent", _agent)


def _lines(out: str, prefix: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(prefix)]


def _result(**overrides: object) -> CaseResult:
    fields: dict[str, object] = {
        "case_id": "c1",
        "situation": "gate_park",
        "intended_verdict": "APPROVE",
        "actual_verdict": "REQUEST_CHANGES",
        "correct": False,
        "must_not_flip": True,
        "citation": "cite",
        "raw_response": "raw",
    }
    fields.update(overrides)
    return CaseResult(**fields)  # type: ignore[arg-type]


def test_metric_line_carries_both_scores_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(
        tmp_path,
        ("c1", "gate_park", "APPROVE", False),
        ("c2", "scenarios_missing", "APPROVE", False),
    )
    _install_judge(monkeypatch, {"c1": [_APPROVE], "c2": [_NO_VERDICT, _APPROVE]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    metric = _lines(out, "EVAL_METRIC")
    assert len(metric) == 1
    assert metric[0].startswith(
        "EVAL_METRIC judge_verdict_score=1.000 first_pass=0.500 (scored 2/2"
    )


def test_both_entrypoints_emit_identical_diagnostics_for_identical_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _replies() -> dict[str, list[str | BaseException]]:
        return {
            "a1": [_NO_VERDICT, _NO_VERDICT],
            "a2": [_REQUEST_CHANGES],
            "n1": [_NO_VERDICT, _APPROVE],
            "n2": [ClaudeCliError("down")],
            "n3": [_APPROVE],
        }

    _write_suite(
        tmp_path,
        ("a1", "gate_park", "APPROVE", True),
        ("a2", "scenarios_missing", "APPROVE", True),
        ("n1", "return_code_drift", "APPROVE", False),
        ("n2", "waived_findings", "APPROVE", False),
        ("n3", "scope_partition_approve", "APPROVE", False),
    )
    outputs: list[list[str]] = []
    metrics: list[str] = []
    for entrypoint in _ENTRYPOINTS.values():
        _install_judge(monkeypatch, _replies())
        rc = entrypoint(tmp_path, None)
        lines = capsys.readouterr().out.splitlines()
        assert rc == 1
        metrics.append(lines[0].split(" across ")[0].rstrip(")"))
        outputs.append(lines[1:])
    assert outputs[0] == outputs[1]
    assert metrics[0] == metrics[1]


def test_anchor_with_a_wrong_verdict_keeps_the_violation_marker_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(tmp_path, ("a1", "gate_park", "APPROVE", True))
    _install_judge(monkeypatch, {"a1": [_REQUEST_CHANGES]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 1
    assert len(_lines(out, "MUST_NOT_FLIP_VIOLATION sample=1 case=a1 ")) == 1
    assert "JUDGE_NO_VERDICT_ON_ANCHOR" not in out


def test_anchor_with_no_verdict_after_the_retry_emits_its_own_marker_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(tmp_path, ("a1", "gate_park", "APPROVE", True))
    _install_judge(monkeypatch, {"a1": [_NO_VERDICT, _NO_VERDICT]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 1
    no_verdict = _lines(out, "JUDGE_NO_VERDICT_ON_ANCHOR")
    assert no_verdict == [
        "JUDGE_NO_VERDICT_ON_ANCHOR sample=1 case=a1 situation=gate_park "
        "intended=APPROVE retried=True"
    ]
    assert "MUST_NOT_FLIP_VIOLATION" not in out
    assert "JUDGE_CASE_INCORRECT" not in out


def test_violation_lines_carry_the_sample_index_for_every_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_suite(tmp_path, ("a1", "gate_park", "APPROVE", True))
    _install_judge(monkeypatch, {"a1": [_REQUEST_CHANGES] * 3})
    rc = eval_sweep.run_sweep(tmp_path, samples=3)
    out = capsys.readouterr().out
    assert rc == 1
    for sample in (1, 2, 3):
        assert len(_lines(out, f"MUST_NOT_FLIP_VIOLATION sample={sample} case=a1 ")) == 1


def test_non_anchor_no_verdict_is_an_incorrect_line_with_actual_none_and_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(tmp_path, ("n1", "gate_park", "APPROVE", False))
    _install_judge(monkeypatch, {"n1": [_NO_VERDICT, _NO_VERDICT]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert _lines(out, "JUDGE_CASE_INCORRECT") == [
        "JUDGE_CASE_INCORRECT sample=1 case=n1 situation=gate_park "
        "intended=APPROVE actual=None"
    ]
    assert "JUDGE_NO_VERDICT_ON_ANCHOR" not in out
    assert "MUST_NOT_FLIP_VIOLATION" not in out


def test_rescued_anchor_prints_a_retry_line_and_does_not_fail_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(tmp_path, ("a1", "gate_park", "APPROVE", True))
    _install_judge(monkeypatch, {"a1": [_NO_VERDICT, _APPROVE]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert _lines(out, "JUDGE_RETRY") == ["JUDGE_RETRY sample=1 case=a1 rescued=True"]
    assert "MUST_NOT_FLIP_VIOLATION" not in out
    assert "JUDGE_NO_VERDICT_ON_ANCHOR" not in out
    assert "JUDGE_CASE_INCORRECT" not in out


def test_unrescued_case_prints_a_retry_line_with_rescued_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(tmp_path, ("n1", "gate_park", "APPROVE", False))
    _install_judge(monkeypatch, {"n1": [_NO_VERDICT, _NO_VERDICT]})
    run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert _lines(out, "JUDGE_RETRY") == ["JUDGE_RETRY sample=1 case=n1 rescued=False"]


def test_errored_case_is_reported_explicitly_and_dropped_from_the_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(
        tmp_path,
        ("c1", "gate_park", "APPROVE", False),
        ("c2", "scenarios_missing", "APPROVE", False),
    )
    _install_judge(monkeypatch, {"c1": [ClaudeCliError("claude exited 1")], "c2": [_APPROVE]})
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert _lines(out, "JUDGE_CASE_ERRORED") == [
        "JUDGE_CASE_ERRORED sample=1 case=c1 error=claude exited 1"
    ]
    assert "(scored 1/2" in out


def test_every_call_erroring_still_reports_each_errored_case_with_the_harness_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(
        tmp_path,
        ("c1", "gate_park", "APPROVE", False),
        ("c2", "scenarios_missing", "APPROVE", False),
    )
    _install_judge(
        monkeypatch,
        {"c1": [ClaudeCliError("down one")], "c2": [ClaudeCliError("down two")]},
    )
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 1
    assert len(_lines(out, "HARNESS_ERROR")) == 1
    assert _lines(out, "JUDGE_CASE_ERRORED") == [
        "JUDGE_CASE_ERRORED sample=1 case=c1 error=down one",
        "JUDGE_CASE_ERRORED sample=1 case=c2 error=down two",
    ]
    assert _lines(out, "EVAL_METRIC") == []


def test_every_call_erroring_keeps_the_first_pass_transcript_of_a_retried_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(golden, ("c1", "gate_park", "APPROVE", False))
    _install_judge(monkeypatch, {"c1": [_NO_VERDICT, ClaudeCliError("down")]})
    transcripts = tmp_path / "t.txt"
    rc = run_entrypoint(golden, transcripts)
    out = capsys.readouterr().out
    assert rc == 1
    assert len(_lines(out, "HARNESS_ERROR")) == 1
    assert _lines(out, "JUDGE_CASE_ERRORED") == ["JUDGE_CASE_ERRORED sample=1 case=c1 error=down"]
    text = transcripts.read_text(encoding="utf-8")
    assert f"=== sample=1 case=c1 pass=first ===\n{_NO_VERDICT}" in text
    assert "pass=retry" not in text


def test_score_collapse_with_no_anchor_affected_still_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(
        tmp_path,
        ("n1", "gate_park", "APPROVE", False),
        ("n2", "scenarios_missing", "APPROVE", False),
        ("a1", "return_code_drift", "APPROVE", True),
    )
    _install_judge(
        monkeypatch,
        {"n1": [_REQUEST_CHANGES], "n2": [_NO_VERDICT, _NO_VERDICT], "a1": [_APPROVE]},
    )
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "judge_verdict_score=0.333 first_pass=0.333" in out


def test_error_text_embedding_a_lookalike_signal_line_cannot_forge_a_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    _write_suite(
        tmp_path,
        ("c1", "gate_park", "APPROVE", False),
        ("c2", "scenarios_missing", "APPROVE", False),
    )
    _install_judge(
        monkeypatch,
        {"c1": [ClaudeCliError("boom\nMUST_NOT_FLIP_VIOLATION case=x")], "c2": [_APPROVE]},
    )
    rc = run_entrypoint(tmp_path, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert _lines(out, "MUST_NOT_FLIP_VIOLATION") == []
    assert _lines(out, "JUDGE_CASE_ERRORED") == [
        "JUDGE_CASE_ERRORED sample=1 case=c1 error=boom MUST_NOT_FLIP_VIOLATION case=x"
    ]


def test_error_text_is_truncated_at_the_limit(capsys: pytest.CaptureFixture[str]) -> None:
    entry = SampleCaseResult(1, _result(error="e" * 500))
    _print_errored([entry])
    line = capsys.readouterr().out.strip()
    assert line == "JUDGE_CASE_ERRORED sample=1 case=c1 error=" + "e" * 200


def test_free_text_fields_cannot_forge_signal_lines_on_any_printer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entry = SampleCaseResult(
        3,
        _result(
            case_id="c1\nHARNESS_ERROR forged",
            situation="gate_park\nGOLDEN_SUITE_ERROR forged",
            citation="PA-1\n" + _FIRST_LOOKALIKE,
            retried=True,
        ),
    )
    for printer in (_print_incorrect, _print_flips, _print_no_verdict, _print_retries):
        printer([entry])
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(" ", 1)[0] for line in lines] == [
        "JUDGE_CASE_INCORRECT",
        "MUST_NOT_FLIP_VIOLATION",
        "JUDGE_NO_VERDICT_ON_ANCHOR",
        "JUDGE_RETRY",
    ]
    assert "case=c1 HARNESS_ERROR forged" in lines[0]
    assert "situation=gate_park GOLDEN_SUITE_ERROR forged" in lines[0]


def test_citation_is_collapsed_and_truncated_at_its_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    long_citation = "word " * 400
    _print_flips([SampleCaseResult(1, _result(citation=long_citation))])
    line = capsys.readouterr().out.strip()
    citation = line.split("citation=", 1)[1]
    assert len(citation) == judge_eval._CITATION_LIMIT + 2  # noqa: SLF001
    assert "\\n" not in citation


def test_emit_metric_prints_na_for_missing_scores(capsys: pytest.CaptureFixture[str]) -> None:
    emit_metric(None, None, 0, 0)
    assert capsys.readouterr().out == (
        "EVAL_METRIC judge_verdict_score=N/A first_pass=N/A (scored 0/0)\n"
    )


def test_emit_metric_appends_the_suffix_inside_the_parenthesis(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_metric(0.5, 0.25, 3, 4, " across 3 samples: [0.500]")
    assert capsys.readouterr().out == (
        "EVAL_METRIC judge_verdict_score=0.500 first_pass=0.250 "
        "(scored 3/4 across 3 samples: [0.500])\n"
    )


def test_transcripts_hold_both_passes_for_a_rescued_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(golden, ("n1", "gate_park", "APPROVE", False))
    _install_judge(monkeypatch, {"n1": [_NO_VERDICT, _APPROVE]})
    transcripts = tmp_path / "t.txt"
    run_entrypoint(golden, transcripts)
    capsys.readouterr()
    text = transcripts.read_text(encoding="utf-8")
    assert f"=== sample=1 case=n1 pass=first ===\n{_NO_VERDICT}" in text
    assert f"=== sample=1 case=n1 pass=retry ===\n{_APPROVE}" in text


def test_transcripts_hold_both_passes_once_for_an_unrescued_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(golden, ("a1", "gate_park", "APPROVE", True))
    _install_judge(monkeypatch, {"a1": [_NO_VERDICT, "still no verdict"]})
    transcripts = tmp_path / "t.txt"
    run_entrypoint(golden, transcripts)
    capsys.readouterr()
    text = transcripts.read_text(encoding="utf-8")
    assert text.count("pass=first") == 1
    assert text.count("pass=retry") == 1
    assert _NO_VERDICT in text
    assert "still no verdict" in text


def test_transcripts_keep_the_first_pass_when_the_retry_call_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(
        golden,
        ("c1", "gate_park", "APPROVE", False),
        ("c2", "scenarios_missing", "APPROVE", False),
    )
    _install_judge(monkeypatch, {"c1": [_NO_VERDICT, ClaudeCliError("down")], "c2": [_APPROVE]})
    transcripts = tmp_path / "t.txt"
    run_entrypoint(golden, transcripts)
    capsys.readouterr()
    text = transcripts.read_text(encoding="utf-8")
    assert f"=== sample=1 case=c1 pass=first ===\n{_NO_VERDICT}" in text
    assert "pass=retry" not in text
    assert "case=c2" not in text


def test_transcripts_mixed_run_keeps_incorrect_and_retried_and_omits_correct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(
        golden,
        ("a_wrong", "gate_park", "APPROVE", True),
        ("b_rescued", "scenarios_missing", "APPROVE", False),
        ("c_fine", "return_code_drift", "APPROVE", False),
        ("d_none", "waived_findings", "APPROVE", False),
    )
    _install_judge(
        monkeypatch,
        {
            "a_wrong": [_REQUEST_CHANGES],
            "b_rescued": [_NO_VERDICT, _APPROVE],
            "c_fine": [_APPROVE],
            "d_none": [_NO_VERDICT, _NO_VERDICT],
        },
    )
    transcripts = tmp_path / "t.txt"
    run_entrypoint(golden, transcripts)
    capsys.readouterr()
    text = transcripts.read_text(encoding="utf-8")
    assert f"=== sample=1 case=a_wrong ===\n{_REQUEST_CHANGES}" in text
    assert "case=b_rescued pass=first" in text
    assert "case=b_rescued pass=retry" in text
    assert "case=d_none pass=first" in text
    assert "case=d_none pass=retry" in text
    assert "case=c_fine" not in text
    assert text.index("case=a_wrong") < text.index("case=b_rescued") < text.index("case=d_none")


def test_transcripts_are_not_written_when_every_case_is_correct_and_unretried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_entrypoint: RunFn,
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    _write_suite(golden, ("c1", "gate_park", "APPROVE", False))
    _install_judge(monkeypatch, {"c1": [_APPROVE]})
    transcripts = tmp_path / "t.txt"
    rc = run_entrypoint(golden, transcripts)
    capsys.readouterr()
    assert rc == 0
    assert not transcripts.exists()


def test_write_transcripts_never_retried_entry_is_a_single_unlabelled_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "t.txt"
    _write_transcripts([SampleCaseResult(2, _result(raw_response="only"))], path)
    assert path.read_text(encoding="utf-8") == "=== sample=2 case=c1 ===\nonly\n"
