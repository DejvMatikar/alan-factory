from __future__ import annotations

import pytest

from alan.compactor_eval import (
    GOLDEN_DIR,
    RUBRIC_FILENAME,
    load_golden_cases,
    load_rubric,
    score_compactor_cases,
    validate_case_count,
)


@pytest.mark.eval
def test_compactor_golden_suite_meets_min_case_floor() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    rubric = load_rubric(GOLDEN_DIR / RUBRIC_FILENAME)
    assert validate_case_count(cases, rubric) == []


@pytest.mark.eval
def test_compactor_identifiers_survive_real_compaction() -> None:
    cases = load_golden_cases(GOLDEN_DIR)
    score = score_compactor_cases(cases)
    for case_id, result in score.case_results.items():
        print(f"EVAL_METRIC compactor_identifier_survival_{case_id}={result.survival_rate:.3f}")
        assert not result.missing, (
            f"{case_id}: compaction dropped load-bearing identifiers: {result.missing} "
            f"(citation: {result.citation})"
        )
    assert score.mean is not None, "harness broken: 0 cases scored against the real compactor"
    print(f"EVAL_METRIC compactor_identifier_survival_mean={score.mean:.3f}")
