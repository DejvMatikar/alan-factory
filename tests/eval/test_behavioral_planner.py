from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from alan.behavioral_eval import (
    ANCHOR_CASE_ID,
    BASELINE_FILENAME,
    GOLDEN_DIR,
    RUBRIC_FILENAME,
    is_regression,
    load_baseline_score,
    load_golden_cases,
    load_rubric,
    score_planner_cases,
)


def _claude_result_stdout(text: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "result": text})


@pytest.mark.eval
def test_behavioral_eval_scores_real_golden_cases(
    patch_subprocess: Callable[..., None],
) -> None:
    patch_subprocess(
        stdout=_claude_result_stdout(
            "a: PASS — ok\nb: PASS — ok\nc: PASS — ok\nd: PASS — ok"
        )
    )
    cases = load_golden_cases(GOLDEN_DIR)
    assert len(cases) == 4
    regular_cases = [c for c in cases if c.case_id != ANCHOR_CASE_ID]
    assert len(regular_cases) == 3
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    score = score_planner_cases(regular_cases, rubric)
    print(
        f"EVAL_METRIC planner_behavioral_score={score.mean:.3f} "
        f"(scored {score.scored_count}/{score.total_count})"
    )
    assert score.scored_count == 3
    assert score.mean == pytest.approx(1.0)


@pytest.mark.eval
def test_behavioral_eval_ratchet_against_committed_baseline(
    patch_subprocess: Callable[..., None],
) -> None:
    patch_subprocess(
        stdout=_claude_result_stdout(
            "a: FAIL — no\nb: FAIL — no\nc: FAIL — no\nd: FAIL — no"
        )
    )
    cases = load_golden_cases(GOLDEN_DIR)
    regular_cases = [c for c in cases if c.case_id != ANCHOR_CASE_ID]
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    score = score_planner_cases(regular_cases, rubric)
    baseline = load_baseline_score(GOLDEN_DIR / BASELINE_FILENAME)
    print(
        f"EVAL_METRIC planner_behavioral_score_regression_check "
        f"mean={score.mean} baseline={baseline}"
    )
    assert baseline is not None
    assert is_regression(score.mean, baseline) is True
