from __future__ import annotations

import pytest

from alan import judge_eval
from alan.claude_cli import ClaudeCliError
from alan.eval_sweep import sample_judge_suite
from alan.judge_eval import GoldenCase, is_no_verdict_anchor, judge_case, score_judge_cases
from alan.teams import _verification_prompts
from alan.teams.verification import _JUDGE_RETRY_NUDGE, judge_system_prompt

_APPROVE = "review body\nVERDICT: APPROVE"
_REQUEST_CHANGES = "review body\nVERDICT: REQUEST_CHANGES"
_NO_VERDICT = "I will check the repository first."


def _case(
    case_id: str = "c1",
    intended_verdict: str = "APPROVE",
    must_not_flip: bool = False,
    context: str = "case context",
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        situation="gate_park",
        context=context,
        intended_verdict=intended_verdict,  # type: ignore[arg-type]
        citation="cite",
        must_not_flip=must_not_flip,
    )


class _Scripted:
    def __init__(self, *replies: str | BaseException) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system_prompt: str, user_text: str) -> str:
        self.calls.append((system_prompt, user_text))
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class _ByCase:
    def __init__(self, replies: dict[str, list[str | BaseException]]) -> None:
        self._replies = replies
        self.calls: list[str] = []

    def __call__(self, _system_prompt: str, user_text: str) -> str:
        case_id = user_text.split("::", 1)[0]
        self.calls.append(case_id)
        reply = self._replies[case_id].pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _keyed(case_id: str, intended: str = "APPROVE", must_not_flip: bool = False) -> GoldenCase:
    return _case(case_id, intended, must_not_flip, context=f"{case_id}::context")


def test_verdict_on_first_call_makes_exactly_one_call() -> None:
    judge = _Scripted(_APPROVE)
    result = judge_case(_case(), run_judge=judge)
    assert len(judge.calls) == 1
    assert result.retried is False
    assert result.correct is True
    assert result.first_pass_correct is True
    assert result.first_pass_response == _APPROVE
    assert result.raw_response == _APPROVE


def test_no_verdict_then_verdict_makes_two_calls_and_is_correct() -> None:
    judge = _Scripted(_NO_VERDICT, _APPROVE)
    result = judge_case(_case(), run_judge=judge)
    assert len(judge.calls) == 2
    assert result.retried is True
    assert result.correct is True
    assert result.first_pass_correct is False
    assert result.actual_verdict == "APPROVE"
    assert result.first_pass_response == _NO_VERDICT
    assert result.raw_response == _APPROVE
    assert result.error == ""


def test_no_verdict_twice_makes_exactly_two_calls_and_scores_no_verdict() -> None:
    judge = _Scripted(_NO_VERDICT, "still nothing")
    result = judge_case(_case(), run_judge=judge)
    assert len(judge.calls) == 2
    assert result.actual_verdict is None
    assert result.correct is False
    assert result.retried is True
    assert result.first_pass_response == _NO_VERDICT
    assert result.raw_response == "still nothing"
    assert result.error == ""


def test_retry_user_text_is_the_case_context_plus_the_production_nudge() -> None:
    judge = _Scripted(_NO_VERDICT, _APPROVE)
    case = _case(context="the original context")
    judge_case(case, run_judge=judge)
    assert judge.calls[0][1] == "the original context"
    assert judge.calls[1][1] == "the original context" + _JUDGE_RETRY_NUDGE


def test_retry_nudge_is_production_constant_by_identity_not_a_copy() -> None:
    assert vars(judge_eval)["_JUDGE_RETRY_NUDGE"] is _verification_prompts._JUDGE_RETRY_NUDGE  # noqa: SLF001
    assert vars(judge_eval)["_JUDGE_RETRY_NUDGE"] is _JUDGE_RETRY_NUDGE


def test_both_calls_use_the_identical_judge_system_prompt() -> None:
    judge = _Scripted(_NO_VERDICT, _APPROVE)
    judge_case(_case(), run_judge=judge)
    assert judge.calls[0][0] == judge.calls[1][0] == judge_system_prompt()


def test_wrong_but_parsable_first_verdict_does_not_retry() -> None:
    judge = _Scripted(_REQUEST_CHANGES)
    result = judge_case(_case(intended_verdict="APPROVE"), run_judge=judge)
    assert len(judge.calls) == 1
    assert result.retried is False
    assert result.actual_verdict == "REQUEST_CHANGES"
    assert result.correct is False


def test_wrong_verdict_from_the_retry_is_a_real_wrong_verdict() -> None:
    judge = _Scripted(_NO_VERDICT, _REQUEST_CHANGES)
    result = judge_case(_case(intended_verdict="APPROVE"), run_judge=judge)
    assert result.retried is True
    assert result.actual_verdict == "REQUEST_CHANGES"
    assert result.correct is False
    assert result.first_pass_correct is False


def test_cli_error_on_the_first_call_is_errored_without_retry() -> None:
    judge = _Scripted(ClaudeCliError("down"))
    result = judge_case(_case(), run_judge=judge)
    assert len(judge.calls) == 1
    assert result.error == "down"
    assert result.retried is False
    assert result.raw_response == ""
    assert result.first_pass_response == ""


def test_cli_error_on_the_retry_is_errored_and_keeps_the_first_pass() -> None:
    judge = _Scripted(_NO_VERDICT, ClaudeCliError("retry down"))
    result = judge_case(_case(), run_judge=judge)
    assert len(judge.calls) == 2
    assert result.error == "retry down"
    assert result.retried is True
    assert result.first_pass_response == _NO_VERDICT
    assert result.raw_response == ""
    assert result.first_pass_correct is False


def test_cli_error_with_empty_message_is_still_marked_errored() -> None:
    result = judge_case(_case(), run_judge=_Scripted(ClaudeCliError("")))
    assert result.error == "ClaudeCliError"


def test_errored_cases_are_excluded_from_the_mean_and_reported_separately() -> None:
    judge = _ByCase({"c1": [ClaudeCliError("down")], "c2": [_APPROVE]})
    score = score_judge_cases([_keyed("c1"), _keyed("c2")], run_judge=judge)
    assert score.mean == 1.0
    assert score.first_pass_mean == 1.0
    assert score.scored_count == 1
    assert score.total_count == 2
    assert list(score.case_results) == ["c2"]
    assert [r.case_id for r in score.errored] == ["c1"]


def test_first_pass_mean_uses_the_same_denominator_as_the_post_retry_mean() -> None:
    judge = _ByCase(
        {
            "c1": [_APPROVE],
            "c2": [_NO_VERDICT, _APPROVE],
            "c3": [ClaudeCliError("down")],
            "c4": [_NO_VERDICT, _NO_VERDICT],
        }
    )
    cases = [_keyed("c1"), _keyed("c2"), _keyed("c3"), _keyed("c4")]
    score = score_judge_cases(cases, run_judge=judge)
    assert score.scored_count == 3
    assert score.mean == pytest.approx(2 / 3)
    assert score.first_pass_mean == pytest.approx(1 / 3)


def test_first_pass_mean_is_none_when_nothing_scored() -> None:
    judge = _ByCase({"c1": [ClaudeCliError("down")]})
    score = score_judge_cases([_keyed("c1")], run_judge=judge)
    assert score.mean is None
    assert score.first_pass_mean is None


def test_judge_that_never_emits_a_verdict_is_asked_twice_per_case_and_never_loops() -> None:
    judge = _ByCase({"c1": [_NO_VERDICT] * 2, "c2": [_NO_VERDICT] * 2, "c3": [_NO_VERDICT] * 2})
    score_judge_cases([_keyed("c1"), _keyed("c2"), _keyed("c3")], run_judge=judge)
    assert judge.calls == ["c1", "c1", "c2", "c2", "c3", "c3"]


def test_no_verdict_anchor_ids_and_flipped_case_ids_are_disjoint() -> None:
    judge = _ByCase(
        {
            "a1": [_NO_VERDICT, _NO_VERDICT],
            "a2": [_REQUEST_CHANGES],
            "a3": [_APPROVE],
            "a4": [_NO_VERDICT, _APPROVE],
            "n1": [_NO_VERDICT, _NO_VERDICT],
        }
    )
    cases = [
        _keyed("a1", must_not_flip=True),
        _keyed("a2", must_not_flip=True),
        _keyed("a3", must_not_flip=True),
        _keyed("a4", must_not_flip=True),
        _keyed("n1"),
    ]
    score = score_judge_cases(cases, run_judge=judge)
    assert score.flipped_case_ids == ("a2",)
    assert score.no_verdict_anchor_ids == ("a1",)
    assert set(score.flipped_case_ids).isdisjoint(score.no_verdict_anchor_ids)


def test_errored_anchor_is_neither_a_flip_nor_a_no_verdict_anchor() -> None:
    judge = _ByCase({"a1": [ClaudeCliError("down")], "c2": [_APPROVE]})
    score = score_judge_cases([_keyed("a1", must_not_flip=True), _keyed("c2")], run_judge=judge)
    assert score.flipped_case_ids == ()
    assert score.no_verdict_anchor_ids == ()
    assert is_no_verdict_anchor(score.errored[0]) is False


def test_sampled_first_pass_mean_averages_only_samples_that_have_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[str | BaseException] = [
        _APPROVE,
        ClaudeCliError("down"),
        _NO_VERDICT,
        _APPROVE,
    ]

    def _agent(_system_prompt: str, _user_text: str, **_kwargs: object) -> str:
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("alan.judge_eval.run_agent", _agent)
    result = sample_judge_suite([_case()], samples=3)
    assert result.sample_means == (1.0, 1.0)
    assert result.mean == 1.0
    assert result.first_pass_mean == pytest.approx(0.5)
    assert [entry.sample_index for entry in result.errored] == [2]
    assert [entry.sample_index for entry in result.retried] == [3]


def test_sampled_first_pass_mean_is_none_when_every_sample_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> str:
        raise ClaudeCliError("down")

    monkeypatch.setattr("alan.judge_eval.run_agent", _boom)
    result = sample_judge_suite([_case()], samples=2)
    assert result.mean is None
    assert result.first_pass_mean is None
    assert len(result.errored) == 2


def test_sampled_no_verdict_anchor_is_separate_from_flipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {"a1": _NO_VERDICT, "a2": _APPROVE}

    def _agent(_system_prompt: str, user_text: str, **_kwargs: object) -> str:
        return responses[user_text.split("::", 1)[0]]

    monkeypatch.setattr("alan.judge_eval.run_agent", _agent)
    cases = [
        _keyed("a1", "REQUEST_CHANGES", must_not_flip=True),
        _keyed("a2", "REQUEST_CHANGES", must_not_flip=True),
    ]
    result = sample_judge_suite(cases, samples=2)
    assert [e.result.case_id for e in result.flipped] == ["a2", "a2"]
    assert [e.result.case_id for e in result.no_verdict_anchors] == ["a1", "a1"]
    assert len(result.incorrect) == 4
