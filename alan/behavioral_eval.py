from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeAlias, cast

import yaml

from alan import agent_key
from alan.claude_cli import ClaudeCliError, run_agent
from alan.learning_metrics import LearningMetrics

_REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = _REPO_ROOT / "tests" / "eval" / "golden" / "planner"
RUBRIC_FILENAME = "rubric.yaml"
BASELINE_FILENAME = "baseline_score.json"
BRIEF_FILENAME = "brief.txt"
SYNTHESIZER_RESULT_FILENAME = "synthesizer_result.json"

# Provisional: no real judge-variance data exists yet (this is the harness's first
# PR). Re-calibrate from the first several weekly sweeps' score spread, per
# docs/architecture/eval_scaffolding.md.
REGRESSION_MARGIN = 0.15

# One-sided calibration control (PA-23): a deliberately-bad plan a well-calibrated
# judge should mark FAIL on most/all criteria. Scoring above the ceiling signals
# judge leniency drift, not planner quality, so it is kept out of the regular
# case mean/baseline — mixing it in would distort the REGRESSION_MARGIN ratchet.
ANCHOR_CASE_ID = "case_04_anchor"
ANCHOR_CEILING = 0.3

_CASE_DETAIL_PREFIX = "EVAL_CASE"
_ANCHOR_UNSCORED_PREFIX = "EVAL_ANCHOR_UNSCORED"

_JUDGE_MODEL = "haiku"
_CRITERION_LINE_RE = re.compile(
    r"^\s*(?P<id>[\w.-]+)\s*:\s*(?P<verdict>PASS|FAIL)\s*[-:—–]\s*(?P<reason>\S.*)$",
    re.IGNORECASE | re.MULTILINE,
)

JudgeFn: TypeAlias = Callable[[str, str], str]
_DictStrAny: TypeAlias = dict[str, Any]
_ListObject: TypeAlias = list[object]

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator of software implementation plans. For each "
    "rubric criterion, decide whether the candidate plan satisfies it. Respond "
    "with exactly one line per criterion, in the form `<criterion_id>: PASS — "
    "<brief reason>` or `<criterion_id>: FAIL — <brief reason>` — no other "
    "text, no preamble, no summary line, no overall score."
)


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    brief: str
    synthesizer_result: str


@dataclass(frozen=True)
class CriterionTally:
    passed: int
    matched: int
    matched_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    score: float | None
    tally: CriterionTally | None
    raw_response: str
    error: str | None
    error_detail: str


@dataclass(frozen=True)
class BehavioralScore:
    mean: float | None
    case_scores: dict[str, float]
    case_results: dict[str, CaseOutcome]
    scored_count: int
    total_count: int


@dataclass(frozen=True)
class Baseline:
    mean: float
    case_scores: dict[str, float]


def _default_judge(system_prompt: str, user_text: str) -> str:
    return run_agent(
        system_prompt, user_text, model=_JUDGE_MODEL, deny_all_tools=True,
        agent=agent_key.PLANNER_GRADER_EVAL)


def _validated_path(path: Path) -> Path | None:
    resolved = path.resolve()
    return resolved if resolved.is_file() else None


def _single_line(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


def load_rubric(path: Path) -> dict[str, Any]:
    resolved = _validated_path(path)
    if resolved is None:
        return {}
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return cast(_DictStrAny, raw) if isinstance(raw, dict) else {}


def _load_case(case_dir: Path) -> GoldenCase | None:
    brief_path = case_dir / BRIEF_FILENAME
    result_path = case_dir / SYNTHESIZER_RESULT_FILENAME
    if not brief_path.is_file() or not result_path.is_file():
        return None
    try:
        brief = brief_path.read_text(encoding="utf-8")
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    result_text = cast(_DictStrAny, raw).get("result_text")
    if not isinstance(result_text, str) or not result_text.strip():
        return None
    return GoldenCase(case_id=case_dir.name, brief=brief, synthesizer_result=result_text)


def load_golden_cases(golden_dir: Path) -> list[GoldenCase]:
    if not golden_dir.is_dir():
        return []
    case_dirs = sorted(p for p in golden_dir.iterdir() if p.is_dir())
    return [c for c in (_load_case(d) for d in case_dirs) if c is not None]


def _rubric_text(rubric: dict[str, Any]) -> str:
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list):
        return ""
    lines: list[str] = []
    scale = rubric.get("scale")
    if isinstance(scale, str) and scale.strip():
        lines.append(f"Scale: {scale}")
    for entry in cast(_ListObject, criteria):
        if not isinstance(entry, dict):
            continue
        entry_d = cast(_DictStrAny, entry)
        lines.append(f"- {entry_d.get('id', '')}: {entry_d.get('description', '')}")
    return "\n".join(lines)


def _rubric_criterion_ids(rubric: dict[str, Any]) -> tuple[str, ...]:
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list):
        return ()
    ids: list[str] = []
    for entry in cast(_ListObject, criteria):
        if not isinstance(entry, dict):
            continue
        cid = cast(_DictStrAny, entry).get("id")
        if isinstance(cid, str):
            ids.append(cid)
    return tuple(ids)


def _build_judge_user_text(case: GoldenCase, rubric: dict[str, Any]) -> str:
    return (
        f"## Rubric\n{_rubric_text(rubric)}\n\n"
        f"## Task brief\n{case.brief}\n\n"
        f"## Candidate implementation plan\n{case.synthesizer_result}\n\n"
        "For each rubric criterion above, judge the candidate plan PASS or "
        "FAIL and give a one-sentence reason. Respond with exactly one line "
        "per criterion: `<criterion_id>: PASS — <reason>` or "
        "`<criterion_id>: FAIL — <reason>`."
    )


def _parse_score(response: str) -> CriterionTally | None:
    matches = list(_CRITERION_LINE_RE.finditer(response))
    if not matches:
        return None
    matched_ids = tuple(m.group("id") for m in matches)
    failed_ids = tuple(m.group("id") for m in matches if m.group("verdict").upper() == "FAIL")
    return CriterionTally(
        passed=len(matches) - len(failed_ids),
        matched=len(matches),
        matched_ids=matched_ids,
        failed_ids=failed_ids,
    )


def anchor_regression(score: float | None, ceiling: float = ANCHOR_CEILING) -> bool:
    return score is not None and score > ceiling


def judge_case(
    case: GoldenCase, rubric: dict[str, Any], run_judge: JudgeFn = _default_judge
) -> CaseOutcome:
    try:
        response = run_judge(_JUDGE_SYSTEM_PROMPT, _build_judge_user_text(case, rubric))
    except ClaudeCliError as exc:
        return CaseOutcome(
            case_id=case.case_id,
            score=None,
            tally=None,
            raw_response="",
            error="judge_call_failed",
            error_detail=_single_line(str(exc)),
        )
    tally = _parse_score(response)
    if tally is None:
        return CaseOutcome(
            case_id=case.case_id,
            score=None,
            tally=None,
            raw_response=response,
            error="unparseable_response",
            error_detail=_single_line(response),
        )
    return CaseOutcome(
        case_id=case.case_id,
        score=tally.passed / tally.matched,
        tally=tally,
        raw_response=response,
        error=None,
        error_detail="",
    )


def score_planner_cases(
    cases: list[GoldenCase], rubric: dict[str, Any], run_judge: JudgeFn = _default_judge
) -> BehavioralScore:
    case_results = {case.case_id: judge_case(case, rubric, run_judge) for case in cases}
    case_scores = {
        case_id: outcome.score
        for case_id, outcome in case_results.items()
        if outcome.score is not None
    }
    mean = sum(case_scores.values()) / len(case_scores) if case_scores else None
    return BehavioralScore(
        mean=mean,
        case_scores=case_scores,
        case_results=case_results,
        scored_count=len(case_scores),
        total_count=len(cases),
    )


def _parse_case_scores(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    case_scores: dict[str, float] = {}
    for key, value in cast(_DictStrAny, raw).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        case_scores[key] = float(value)
    return case_scores


def load_baseline(path: Path) -> Baseline | None:
    resolved = _validated_path(path)
    if resolved is None:
        return None
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    raw_d = cast(_DictStrAny, raw)
    mean = raw_d.get("mean")
    if isinstance(mean, bool) or not isinstance(mean, (int, float)):
        return None
    return Baseline(mean=float(mean), case_scores=_parse_case_scores(raw_d.get("case_scores")))


def load_baseline_score(path: Path) -> float | None:
    baseline = load_baseline(path)
    return None if baseline is None else baseline.mean


def is_regression(
    current: float | None, baseline: float | None, margin: float = REGRESSION_MARGIN
) -> bool:
    if current is None or baseline is None:
        return False
    return current < baseline - margin


def apply_score(
    metrics: LearningMetrics, score: BehavioralScore, baseline: float | None
) -> LearningMetrics:
    updated = replace(
        metrics,
        planner_behavioral_score=score.mean,
        planner_behavioral_baseline=baseline,
        planner_behavioral_regression=is_regression(score.mean, baseline),
    )
    return cast(LearningMetrics, updated)  # type: ignore[redundant-cast]  # pyright: ignore[reportUnnecessaryCast]


def _blessed_delta_text(score: float | None, blessed: float | None) -> str:
    if blessed is None:
        return "blessed=n/a delta=n/a"
    if score is None:
        return f"blessed={blessed:.3f} delta=n/a"
    delta = score - blessed
    sign = "+" if delta >= 0 else ""
    return f"blessed={blessed:.3f} delta={sign}{delta:.3f}"


def _join_ids(ids: tuple[str, ...]) -> str:
    return ",".join(ids) if ids else "none"


def _case_detail_line(
    case_id: str, outcome: CaseOutcome | None, blessed: float | None, rubric_count: int
) -> str:
    blessed_delta = _blessed_delta_text(outcome.score if outcome is not None else None, blessed)
    if outcome is None:
        return f"{_CASE_DETAIL_PREFIX} case={case_id} score=n/a reason=not_run {blessed_delta}"
    if outcome.error is not None:
        return (
            f"{_CASE_DETAIL_PREFIX} case={case_id} score=n/a reason={outcome.error} "
            f"detail={outcome.error_detail} {blessed_delta}"
        )
    tally = cast(CriterionTally, outcome.tally)
    score = cast(float, outcome.score)
    return (
        f"{_CASE_DETAIL_PREFIX} case={case_id} score={score:.3f} "
        f"passed={tally.passed}/{tally.matched} rubric={rubric_count} "
        f"matched_ids={_join_ids(tally.matched_ids)} failed_ids={_join_ids(tally.failed_ids)} "
        f"{blessed_delta}"
    )


def _emit_case_details(
    score: BehavioralScore,
    baseline: Baseline | None,
    rubric_ids: tuple[str, ...],
    order: list[str],
) -> None:
    rubric_count = len(rubric_ids)
    baseline_scores = baseline.case_scores if baseline is not None else {}
    seen = set(order)
    baseline_only = sorted(cid for cid in baseline_scores if cid not in seen)
    for case_id in [*order, *baseline_only]:
        outcome = score.case_results.get(case_id)
        blessed = baseline_scores.get(case_id)
        print(_case_detail_line(case_id, outcome, blessed, rubric_count))


def _validated_write_path(path: Path, *, root: Path) -> Path | None:
    resolved = path.resolve()
    if resolved.is_dir():
        return None
    return resolved if resolved.is_relative_to(root.resolve()) else None


def _write_transcripts(path: Path | None, score: BehavioralScore) -> None:
    if path is None or not score.case_results:
        return
    resolved = _validated_write_path(path, root=Path.cwd())
    if resolved is None:
        return
    blocks = [
        f"=== case={case_id} ===\n{outcome.raw_response}"
        for case_id, outcome in score.case_results.items()
    ]
    resolved.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _check_anchor(cases: list[GoldenCase], rubric: dict[str, Any]) -> int:
    anchor_case = next((c for c in cases if c.case_id == ANCHOR_CASE_ID), None)
    if anchor_case is None:
        return 0
    outcome = judge_case(anchor_case, rubric)
    if outcome.error is not None:
        print(f"{_ANCHOR_UNSCORED_PREFIX} reason={outcome.error} detail={outcome.error_detail}")
        return 0
    print(f"EVAL_METRIC planner_behavioral_anchor_score={outcome.score:.3f}")
    if not anchor_regression(outcome.score):
        return 0
    print(
        f"REGRESSION planner_behavioral_anchor_score={outcome.score:.3f} "
        f"ceiling={ANCHOR_CEILING:.3f} case={ANCHOR_CASE_ID}"
    )
    return 1


def _print_baseline_regression(mean: float, baseline: Baseline | None) -> int:
    baseline_mean = baseline.mean if baseline is not None else None
    if baseline_mean is None or not is_regression(mean, baseline_mean, REGRESSION_MARGIN):
        return 0
    print(
        f"REGRESSION planner_behavioral_score={mean:.3f} "
        f"baseline={baseline_mean:.3f} margin={REGRESSION_MARGIN}"
    )
    return 1


def run_sweep(golden_dir: Path = GOLDEN_DIR, transcripts_path: Path | None = None) -> int:
    cases = load_golden_cases(golden_dir)
    if not cases:
        print("EVAL_METRIC planner_behavioral_score=N/A (no golden fixtures on disk)")
        return 0
    rubric = load_rubric(golden_dir / RUBRIC_FILENAME)
    regular_cases = [c for c in cases if c.case_id != ANCHOR_CASE_ID]
    regular_case_ids = [c.case_id for c in regular_cases]
    score = score_planner_cases(regular_cases, rubric)
    baseline = load_baseline(golden_dir / BASELINE_FILENAME)
    _emit_case_details(score, baseline, _rubric_criterion_ids(rubric), regular_case_ids)
    _write_transcripts(transcripts_path, score)
    mean = score.mean
    if mean is None:
        print(
            "HARNESS_ERROR planner behavioral eval harness broken: "
            f"0/{score.total_count} golden cases scored"
        )
        return 1
    print(
        f"EVAL_METRIC planner_behavioral_score={mean:.3f} "
        f"(scored {score.scored_count}/{score.total_count})"
    )
    exit_code = _print_baseline_regression(mean, baseline)
    return max(exit_code, _check_anchor(cases, rubric))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden-dir",
        default=str(GOLDEN_DIR),
        help="Directory of committed golden planner cases",
    )
    parser.add_argument(
        "--transcripts",
        default="planner-eval-transcripts.txt",
        help="Path to write raw judge transcripts for every scored case",
    )
    args = parser.parse_args(argv)
    return run_sweep(Path(args.golden_dir), Path(args.transcripts))


if __name__ == "__main__":
    sys.exit(main())
