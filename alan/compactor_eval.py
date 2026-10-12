from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, cast

import yaml

from alan.claude_cli import ClaudeCliError
from alan.compression import _call_compactor_llm

_REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = _REPO_ROOT / "tests" / "eval" / "golden" / "compactor"
RUBRIC_FILENAME = "rubric.yaml"
CASE_FILENAME = "case.json"
CONTEXT_FILENAME = "context.txt"

_DEFAULT_MIN_CASES = 5

CompactFn: TypeAlias = Callable[[str], str]
_DictStrAny: TypeAlias = dict[str, Any]
_ListObject: TypeAlias = list[object]


class GoldenSuiteError(ValueError):
    pass


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    context: str
    must_survive: tuple[str, ...]
    citation: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    must_survive: tuple[str, ...]
    missing: tuple[str, ...]
    survival_rate: float
    citation: str


@dataclass(frozen=True)
class CompactorScore:
    mean: float | None
    case_results: dict[str, CaseResult]
    scored_count: int
    total_count: int


def _default_compact(context: str) -> str:
    return _call_compactor_llm(context)


def _validated_path(path: Path) -> Path | None:
    resolved = path.resolve()
    return resolved if resolved.is_file() else None


def load_rubric(path: Path) -> dict[str, Any]:
    resolved = _validated_path(path)
    if resolved is None:
        return {}
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return cast(_DictStrAny, raw) if isinstance(raw, dict) else {}


def _min_cases_from_rubric(rubric: dict[str, Any]) -> int:
    value = rubric.get("min_cases")
    if isinstance(value, bool) or not isinstance(value, int):
        return _DEFAULT_MIN_CASES
    return value


def _require_str(data: _DictStrAny, key: str, case_name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoldenSuiteError(f"{case_name}: missing or empty {key!r}")
    return value


def _require_str_list(data: _DictStrAny, key: str, case_name: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise GoldenSuiteError(f"{case_name}: missing or empty {key!r}")
    items = cast(_ListObject, value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise GoldenSuiteError(f"{case_name}: {key!r} must be a list of non-empty strings")
    return tuple(cast("list[str]", items))


def _read_case_json(case_dir: Path, name: str) -> _DictStrAny:
    case_path = case_dir / CASE_FILENAME
    if not case_path.is_file():
        raise GoldenSuiteError(f"{name}: missing {CASE_FILENAME}")
    try:
        raw = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldenSuiteError(f"{name}: unreadable {CASE_FILENAME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GoldenSuiteError(f"{name}: {CASE_FILENAME} must be a JSON object")
    return cast(_DictStrAny, raw)


def _read_context(case_dir: Path, name: str) -> str:
    context_path = case_dir / CONTEXT_FILENAME
    if not context_path.is_file():
        raise GoldenSuiteError(f"{name}: missing {CONTEXT_FILENAME}")
    try:
        context = context_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldenSuiteError(f"{name}: unreadable {CONTEXT_FILENAME}: {exc}") from exc
    if not context.strip():
        raise GoldenSuiteError(f"{name}: empty {CONTEXT_FILENAME}")
    return context


def _load_case(case_dir: Path) -> GoldenCase:
    name = case_dir.name
    data = _read_case_json(case_dir, name)
    must_survive = _require_str_list(data, "must_survive", name)
    citation = _require_str(data, "citation", name)
    context = _read_context(case_dir, name)
    return GoldenCase(case_id=name, context=context, must_survive=must_survive, citation=citation)


def load_golden_cases(golden_dir: Path) -> list[GoldenCase]:
    if not golden_dir.is_dir():
        return []
    case_dirs = sorted(p for p in golden_dir.iterdir() if p.is_dir())
    return [_load_case(d) for d in case_dirs]


def validate_case_count(cases: list[GoldenCase], rubric: dict[str, Any]) -> list[str]:
    min_cases = _min_cases_from_rubric(rubric)
    if len(cases) < min_cases:
        return [f"golden suite has {len(cases)} case(s), need >= {min_cases}"]
    return []


def compactor_case(
    case: GoldenCase, run_compact: CompactFn = _default_compact
) -> CaseResult | None:
    try:
        compacted = run_compact(case.context)
    except ClaudeCliError:
        return None
    missing = tuple(ident for ident in case.must_survive if ident not in compacted)
    survived = len(case.must_survive) - len(missing)
    return CaseResult(
        case_id=case.case_id,
        must_survive=case.must_survive,
        missing=missing,
        survival_rate=survived / len(case.must_survive),
        citation=case.citation,
    )


def _mean_survival(scored: list[CaseResult]) -> float | None:
    if not scored:
        return None
    return sum(r.survival_rate for r in scored) / len(scored)


def score_compactor_cases(
    cases: list[GoldenCase], run_compact: CompactFn = _default_compact
) -> CompactorScore:
    case_results: dict[str, CaseResult] = {}
    for case in cases:
        result = compactor_case(case, run_compact)
        if result is not None:
            case_results[result.case_id] = result
    scored = list(case_results.values())
    return CompactorScore(
        mean=_mean_survival(scored),
        case_results=case_results,
        scored_count=len(scored),
        total_count=len(cases),
    )


def _load_cases_or_report(golden_dir: Path) -> list[GoldenCase] | None:
    try:
        return load_golden_cases(golden_dir)
    except GoldenSuiteError as exc:
        print(f"HARNESS_ERROR compactor golden suite malformed: {exc}")
        return None


def _print_violations(violations: list[str]) -> None:
    for violation in violations:
        print(f"GOLDEN_SUITE_ERROR {violation}")


def _print_missing(score: CompactorScore) -> None:
    for result in score.case_results.values():
        if result.missing:
            print(
                f"IDENTIFIER_DROPPED case={result.case_id} missing={list(result.missing)!r} "
                f"citation={result.citation!r}"
            )


def run_sweep(golden_dir: Path = GOLDEN_DIR) -> int:
    cases = _load_cases_or_report(golden_dir)
    if cases is None:
        return 1
    if not cases:
        print("EVAL_METRIC compactor_identifier_survival_rate=N/A (no golden fixtures on disk)")
        return 0
    rubric = load_rubric(golden_dir / RUBRIC_FILENAME)
    violations = validate_case_count(cases, rubric)
    if violations:
        _print_violations(violations)
        return 1
    score = score_compactor_cases(cases)
    if score.mean is None:
        print(
            "HARNESS_ERROR compactor behavioral eval harness broken: "
            f"0/{score.total_count} golden cases scored"
        )
        return 1
    print(
        f"EVAL_METRIC compactor_identifier_survival_rate={score.mean:.3f} "
        f"(scored {score.scored_count}/{score.total_count})"
    )
    _print_missing(score)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden-dir",
        default=str(GOLDEN_DIR),
        help="Directory of committed golden compactor cases",
    )
    args = parser.parse_args(argv)
    return run_sweep(Path(args.golden_dir))


if __name__ == "__main__":
    sys.exit(main())
