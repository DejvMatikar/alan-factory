from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from alan import judge_eval

SAMPLE_COUNT = 3


@dataclass(frozen=True)
class SampledJudgeScore:
    sample_means: tuple[float, ...]
    mean: float | None
    scored_count: int
    total_count: int
    incorrect: tuple[judge_eval.SampleCaseResult, ...]
    flipped: tuple[judge_eval.SampleCaseResult, ...]
    retried: tuple[judge_eval.SampleCaseResult, ...] = ()
    errored: tuple[judge_eval.SampleCaseResult, ...] = ()
    no_verdict_anchors: tuple[judge_eval.SampleCaseResult, ...] = ()
    first_pass_mean: float | None = None


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _present(values: Iterable[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def _flatten(
    groups: Iterable[tuple[judge_eval.SampleCaseResult, ...]],
) -> tuple[judge_eval.SampleCaseResult, ...]:
    return tuple(entry for group in groups for entry in group)


def _only(
    entries: tuple[judge_eval.SampleCaseResult, ...],
    predicate: Callable[[judge_eval.CaseResult], bool],
) -> tuple[judge_eval.SampleCaseResult, ...]:
    return tuple(entry for entry in entries if predicate(entry.result))


def _sampled_entries(scores: list[judge_eval.JudgeScore]) -> judge_eval.SampleEntries:
    per_sample = [judge_eval.sample_entries(i, score) for i, score in enumerate(scores, start=1)]
    return judge_eval.SampleEntries(
        incorrect=_flatten(entries.incorrect for entries in per_sample),
        retried=_flatten(entries.retried for entries in per_sample),
        errored=_flatten(entries.errored for entries in per_sample),
    )


def sample_judge_suite(
    cases: list[judge_eval.GoldenCase], samples: int = SAMPLE_COUNT
) -> SampledJudgeScore:
    scores = [judge_eval.score_judge_cases(cases) for _ in range(samples)]
    entries = _sampled_entries(scores)
    sample_means = _present(score.mean for score in scores)
    return SampledJudgeScore(
        sample_means=tuple(sample_means),
        mean=_average(sample_means),
        scored_count=sum(score.scored_count for score in scores),
        total_count=len(cases) * samples,
        incorrect=entries.incorrect,
        flipped=_only(entries.incorrect, judge_eval.is_flip),
        retried=entries.retried,
        errored=entries.errored,
        no_verdict_anchors=_only(entries.incorrect, judge_eval.is_no_verdict_anchor),
        first_pass_mean=_average(_present(score.first_pass_mean for score in scores)),
    )


def _load_cases_or_report(golden_dir: Path) -> list[judge_eval.GoldenCase] | None:
    try:
        return judge_eval.load_golden_cases(golden_dir)
    except judge_eval.GoldenSuiteError as exc:
        print(f"HARNESS_ERROR judge golden suite malformed: {exc}")
        return None


def _print_violations(violations: list[str]) -> None:
    for violation in violations:
        print(f"GOLDEN_SUITE_ERROR {violation}")


def _emit_sampled_metric(result: SampledJudgeScore, samples: int) -> None:
    sample_str = ", ".join(f"{m:.3f}" for m in result.sample_means)
    judge_eval.emit_metric(
        result.mean,
        result.first_pass_mean,
        result.scored_count,
        result.total_count,
        f" across {samples} samples: [{sample_str}]",
    )


def run_sweep(
    golden_dir: Path = judge_eval.GOLDEN_DIR,
    samples: int = SAMPLE_COUNT,
    transcripts_path: Path | None = None,
) -> int:
    cases = _load_cases_or_report(golden_dir)
    if cases is None:
        return 1
    if not cases:
        print("EVAL_METRIC judge_verdict_score=N/A (no golden fixtures on disk)")
        return 0
    rubric = judge_eval.load_rubric(golden_dir / judge_eval.RUBRIC_FILENAME)
    violations = judge_eval.validate_situation_coverage(cases, rubric)
    if violations:
        _print_violations(violations)
        return 1
    result = sample_judge_suite(cases, samples)
    if result.mean is None:
        print(
            f"HARNESS_ERROR judge behavioral eval harness broken across {samples} samples: "
            f"0/{result.total_count} golden-case judgments scored"
        )
        judge_eval.emit_diagnostics(
            result.incorrect, result.retried, result.errored, transcripts_path
        )
        return 1
    _emit_sampled_metric(result, samples)
    judge_eval.emit_diagnostics(
        result.incorrect, result.retried, result.errored, transcripts_path
    )
    return 1 if result.flipped or result.no_verdict_anchors else 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--samples must be >= 1, got {parsed}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden-dir",
        default=str(judge_eval.GOLDEN_DIR),
        help="Directory of committed golden verification-judge cases",
    )
    parser.add_argument(
        "--samples",
        type=_positive_int,
        default=SAMPLE_COUNT,
        help="Number of independent judge passes to average per case",
    )
    parser.add_argument(
        "--transcripts",
        default="judge-eval-transcripts.txt",
        help="Path to write raw judge transcripts for incorrect and retried cases",
    )
    args = parser.parse_args(argv)
    return run_sweep(Path(args.golden_dir), args.samples, Path(args.transcripts))


if __name__ == "__main__":
    sys.exit(main())
