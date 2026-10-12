from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, cast

import yaml

from alan import agent_key, engine_config
from alan.claude_cli import ClaudeCliError, run_agent
from alan.teams._static_pipeline import (
    _coverage_block,  # pyright: ignore[reportPrivateUsage]
    build_role_user_text,
)
from alan.teams._verdict import Verdict, parse_verdict
from alan.teams.verification import (
    _JUDGE_RETRY_NUDGE,  # pyright: ignore[reportPrivateUsage]
    ROLES,
    judge_system_prompt,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = _REPO_ROOT / "tests" / "eval" / "golden" / "judge"
RUBRIC_FILENAME = "rubric.yaml"
CASE_FILENAME = "case.json"
TASK_FILENAME = "task.txt"
ROLES_DIRNAME = "roles"

# PA-23: the six situations minted from real production verdict artifacts
# (prompt-audit item PA1.1). Fixed here
# (not rubric-derived) — a case referencing anything outside this set is a
# suite-authoring bug, not a configurable choice.
SITUATIONS: tuple[str, ...] = (
    "scope_partition_approve",
    "gate_park",
    "scenarios_missing",
    "return_code_drift",
    "waived_findings",
    "hostile_diff_injection",
)
_SITUATIONS_SET = frozenset(SITUATIONS)
_DEFAULT_MIN_CASES = 1

_VALID_VERDICTS = frozenset({"APPROVE", "REQUEST_CHANGES"})

JUDGE_COVERAGE: str = _coverage_block("judge", ROLES)

_ROLE_NAME_FIRST = frozenset("abcdefghijklmnopqrstuvwxyz")
_ROLE_NAME_REST = _ROLE_NAME_FIRST | frozenset("0123456789_")
_ROLE_FILE_SUFFIX = ".txt"

JudgeFn: TypeAlias = Callable[[str, str], str]
_DictStrAny: TypeAlias = dict[str, Any]
_ListObject: TypeAlias = list[object]


class GoldenSuiteError(ValueError):
    pass


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    situation: str
    context: str
    intended_verdict: Verdict
    citation: str
    must_not_flip: bool
    roles: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    situation: str
    intended_verdict: Verdict
    actual_verdict: Verdict | None
    correct: bool
    must_not_flip: bool
    citation: str
    raw_response: str
    first_pass_response: str = ""
    retried: bool = False
    first_pass_correct: bool = False
    error: str = ""


@dataclass(frozen=True)
class JudgeScore:
    mean: float | None
    case_results: dict[str, CaseResult]
    scored_count: int
    total_count: int
    flipped_case_ids: tuple[str, ...]
    first_pass_mean: float | None = None
    errored: tuple[CaseResult, ...] = ()
    no_verdict_anchor_ids: tuple[str, ...] = ()


def _resolve_judge_model() -> str:
    # Resolved at call time (not cached at import) so this suite always scores
    # whatever engine is actually registered as default, not just the built-in
    # `_DEFAULT` — mirrors the alan/dispatcher.py:_pr_branch.py fallback pattern.
    config = engine_config.get_registered() or engine_config.default_engine_config()
    return config.roles["judge"].model


def _default_judge(system_prompt: str, user_text: str) -> str:
    return run_agent(
        system_prompt, user_text, model=_resolve_judge_model(), deny_all_tools=True,
        agent=agent_key.JUDGE_EVAL)


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


def _situation_entry(entry: object) -> tuple[str, int] | None:
    if not isinstance(entry, dict):
        return None
    entry_d = cast(_DictStrAny, entry)
    sid = entry_d.get("id")
    min_cases = entry_d.get("min_cases")
    if not isinstance(sid, str) or sid not in _SITUATIONS_SET:
        return None
    if not isinstance(min_cases, int) or isinstance(min_cases, bool):
        return None
    return sid, min_cases


def _situations_from_rubric(rubric: dict[str, Any]) -> dict[str, int]:
    situations = rubric.get("situations")
    if not isinstance(situations, list):
        return {}
    return {
        entry[0]: entry[1]
        for raw in cast(_ListObject, situations)
        if (entry := _situation_entry(raw)) is not None
    }


def _require_str(data: _DictStrAny, key: str, case_name: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoldenSuiteError(f"{case_name}: missing or empty {key!r}")
    return value


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


def _read_text_file(path: Path, name: str, label: str) -> str:
    if not path.is_file():
        raise GoldenSuiteError(f"{name}: {label}: missing")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GoldenSuiteError(f"{name}: {label}: unreadable: {exc}") from exc
    if not text.strip():
        raise GoldenSuiteError(f"{name}: {label}: empty")
    return text


def _read_task(case_dir: Path, name: str) -> str:
    return _read_text_file(case_dir / TASK_FILENAME, name, TASK_FILENAME)


def _role_entries(case_dir: Path, name: str) -> list[Path]:
    roles_dir = case_dir / ROLES_DIRNAME
    if not roles_dir.is_dir():
        raise GoldenSuiteError(f"{name}: {ROLES_DIRNAME}/: missing")
    entries = sorted(roles_dir.iterdir())
    if not entries:
        raise GoldenSuiteError(f"{name}: {ROLES_DIRNAME}/: empty")
    return entries


def _valid_role_name(role: str) -> bool:
    return role[:1] in _ROLE_NAME_FIRST and set(role) <= _ROLE_NAME_REST


def _parse_role_filename(filename: str) -> tuple[int, str] | None:
    path = Path(filename)
    prefix, separator, role = path.stem.partition("_")
    valid = (
        path.suffix == _ROLE_FILE_SUFFIX
        and separator == "_"
        and prefix.isascii()
        and prefix.isdigit()
        and _valid_role_name(role)
    )
    return (int(prefix), role) if valid else None


def _role_file(path: Path, name: str) -> tuple[int, str, Path]:
    label = f"{ROLES_DIRNAME}/{path.name}"
    parsed = _parse_role_filename(path.name)
    if parsed is None:
        raise GoldenSuiteError(f"{name}: {label}: name must match <digits>_<role>.txt")
    if not path.is_file():
        raise GoldenSuiteError(f"{name}: {label}: not a file")
    return parsed[0], parsed[1], path


def _reject_duplicate_prefixes(files: list[tuple[int, str, Path]], name: str) -> None:
    seen: dict[int, str] = {}
    for prefix, _role, path in files:
        if prefix in seen:
            raise GoldenSuiteError(
                f"{name}: {ROLES_DIRNAME}/{path.name}: duplicate prefix of {seen[prefix]}"
            )
        seen[prefix] = path.name


def _read_roles(case_dir: Path, name: str) -> tuple[tuple[str, str], ...]:
    files = [_role_file(path, name) for path in _role_entries(case_dir, name)]
    _reject_duplicate_prefixes(files, name)
    return tuple(
        (role, _read_text_file(path, name, f"{ROLES_DIRNAME}/{path.name}"))
        for _prefix, role, path in sorted(files, key=lambda entry: entry[0])
    )


def _render_context(task: str, roles: tuple[tuple[str, str], ...]) -> str:
    return build_role_user_text(task, list(roles), JUDGE_COVERAGE)


def _parse_situation_field(data: _DictStrAny, name: str) -> str:
    situation = _require_str(data, "situation", name)
    if situation not in _SITUATIONS_SET:
        raise GoldenSuiteError(f"{name}: unknown situation {situation!r} (not in SITUATIONS)")
    return situation


def _parse_verdict_field(data: _DictStrAny, name: str) -> Verdict:
    intended_verdict = data.get("intended_verdict")
    if intended_verdict not in _VALID_VERDICTS:
        raise GoldenSuiteError(f"{name}: invalid intended_verdict {intended_verdict!r}")
    return cast(Verdict, intended_verdict)


def _parse_must_not_flip_field(data: _DictStrAny, name: str) -> bool:
    must_not_flip = data.get("must_not_flip")
    if not isinstance(must_not_flip, bool):
        raise GoldenSuiteError(f"{name}: must_not_flip must be a bool")
    return must_not_flip


def _load_case(case_dir: Path) -> GoldenCase:
    name = case_dir.name
    data = _read_case_json(case_dir, name)
    situation = _parse_situation_field(data, name)
    intended_verdict = _parse_verdict_field(data, name)
    citation = _require_str(data, "citation", name)
    must_not_flip = _parse_must_not_flip_field(data, name)
    task = _read_task(case_dir, name)
    roles = _read_roles(case_dir, name)
    return GoldenCase(
        case_id=name,
        situation=situation,
        context=_render_context(task, roles),
        intended_verdict=intended_verdict,
        citation=citation,
        must_not_flip=must_not_flip,
        roles=roles,
    )


def load_golden_cases(golden_dir: Path) -> list[GoldenCase]:
    if not golden_dir.is_dir():
        return []
    case_dirs = sorted(p for p in golden_dir.iterdir() if p.is_dir())
    return [_load_case(d) for d in case_dirs]


def validate_situation_coverage(cases: list[GoldenCase], rubric: dict[str, Any]) -> list[str]:
    required = _situations_from_rubric(rubric)
    if not required:
        required = dict.fromkeys(SITUATIONS, _DEFAULT_MIN_CASES)
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.situation] = counts.get(case.situation, 0) + 1
    return [
        f"situation={sid} has {counts.get(sid, 0)} case(s), need >= {min_cases}"
        for sid, min_cases in sorted(required.items())
        if counts.get(sid, 0) < min_cases
    ]


def _case_result(
    case: GoldenCase, final: str, *, first_response: str, retried: bool, error: str = ""
) -> CaseResult:
    actual = parse_verdict(final)
    return CaseResult(
        case_id=case.case_id,
        situation=case.situation,
        intended_verdict=case.intended_verdict,
        actual_verdict=actual,
        correct=actual == case.intended_verdict,
        must_not_flip=case.must_not_flip,
        citation=case.citation,
        raw_response=final,
        first_pass_response=first_response,
        retried=retried,
        first_pass_correct=parse_verdict(first_response) == case.intended_verdict,
        error=error,
    )


def _errored_result(
    case: GoldenCase, exc: ClaudeCliError, *, first_response: str, retried: bool
) -> CaseResult:
    return _case_result(
        case, "", first_response=first_response, retried=retried,
        error=str(exc) or type(exc).__name__,
    )


def judge_case(case: GoldenCase, run_judge: JudgeFn = _default_judge) -> CaseResult:
    system_prompt = judge_system_prompt()
    try:
        first = run_judge(system_prompt, case.context)
    except ClaudeCliError as exc:
        return _errored_result(case, exc, first_response="", retried=False)
    if parse_verdict(first) is not None:
        return _case_result(case, first, first_response=first, retried=False)
    try:
        retry = run_judge(system_prompt, case.context + _JUDGE_RETRY_NUDGE)
    except ClaudeCliError as exc:
        return _errored_result(case, exc, first_response=first, retried=True)
    return _case_result(case, retry, first_response=first, retried=True)


def is_flip(result: CaseResult) -> bool:
    return result.must_not_flip and result.actual_verdict is not None and not result.correct


def is_no_verdict_anchor(result: CaseResult) -> bool:
    return result.must_not_flip and result.actual_verdict is None and result.error == ""


def _mean_correct(scored: list[CaseResult]) -> float | None:
    if not scored:
        return None
    return sum(1 for r in scored if r.correct) / len(scored)


def _first_pass_mean(scored: list[CaseResult]) -> float | None:
    if not scored:
        return None
    return sum(1 for r in scored if r.first_pass_correct) / len(scored)


def _case_ids(scored: list[CaseResult], predicate: Callable[[CaseResult], bool]) -> tuple[str, ...]:
    return tuple(r.case_id for r in scored if predicate(r))


def _aggregate(results: list[CaseResult], total_count: int) -> JudgeScore:
    scored = [r for r in results if r.error == ""]
    return JudgeScore(
        mean=_mean_correct(scored),
        case_results={r.case_id: r for r in scored},
        scored_count=len(scored),
        total_count=total_count,
        flipped_case_ids=_case_ids(scored, is_flip),
        first_pass_mean=_first_pass_mean(scored),
        errored=tuple(r for r in results if r.error != ""),
        no_verdict_anchor_ids=_case_ids(scored, is_no_verdict_anchor),
    )


def score_judge_cases(
    cases: list[GoldenCase], run_judge: JudgeFn = _default_judge
) -> JudgeScore:
    return _aggregate([judge_case(case, run_judge) for case in cases], len(cases))


def _load_cases_or_report(golden_dir: Path) -> list[GoldenCase] | None:
    try:
        return load_golden_cases(golden_dir)
    except GoldenSuiteError as exc:
        print(f"HARNESS_ERROR judge golden suite malformed: {exc}")
        return None


def _print_violations(violations: list[str]) -> None:
    for violation in violations:
        print(f"GOLDEN_SUITE_ERROR {violation}")


@dataclass(frozen=True)
class SampleCaseResult:
    sample_index: int
    result: CaseResult


@dataclass(frozen=True)
class SampleEntries:
    incorrect: tuple[SampleCaseResult, ...]
    retried: tuple[SampleCaseResult, ...]
    errored: tuple[SampleCaseResult, ...]


_INCORRECT_PREFIX = "JUDGE_CASE_INCORRECT"
_NO_VERDICT_ANCHOR_PREFIX = "JUDGE_NO_VERDICT_ON_ANCHOR"
_RETRY_PREFIX = "JUDGE_RETRY"
_ERRORED_PREFIX = "JUDGE_CASE_ERRORED"
_CITATION_LIMIT = 1000


def _label(entry: SampleCaseResult) -> str:
    return f"sample={entry.sample_index} case={_single_line(entry.result.case_id)}"


def _print_incorrect(results: Sequence[SampleCaseResult]) -> None:
    for entry in results:
        result = entry.result
        print(
            f"{_INCORRECT_PREFIX} {_label(entry)} "
            f"situation={_single_line(result.situation)} intended={result.intended_verdict} "
            f"actual={result.actual_verdict}"
        )


def _print_flips(results: Sequence[SampleCaseResult]) -> None:
    for entry in results:
        result = entry.result
        print(
            f"MUST_NOT_FLIP_VIOLATION {_label(entry)} "
            f"situation={_single_line(result.situation)} "
            f"intended={result.intended_verdict} actual={result.actual_verdict} "
            f"citation={_single_line(result.citation, _CITATION_LIMIT)!r}"
        )


def _print_no_verdict(results: Sequence[SampleCaseResult]) -> None:
    for entry in results:
        result = entry.result
        print(
            f"{_NO_VERDICT_ANCHOR_PREFIX} {_label(entry)} "
            f"situation={_single_line(result.situation)} intended={result.intended_verdict} "
            f"retried={result.retried}"
        )


def _print_retries(results: Sequence[SampleCaseResult]) -> None:
    for entry in results:
        print(f"{_RETRY_PREFIX} {_label(entry)} rescued={entry.result.actual_verdict is not None}")


def _print_errored(results: Sequence[SampleCaseResult]) -> None:
    for entry in results:
        print(f"{_ERRORED_PREFIX} {_label(entry)} error={_single_line(entry.result.error)}")


def _validated_write_path(path: Path, *, root: Path) -> Path | None:
    resolved = path.resolve()
    return resolved if resolved.is_relative_to(root.resolve()) else None


def _transcript_blocks(entry: SampleCaseResult) -> list[str]:
    result = entry.result
    header = f"=== {_label(entry)}"
    if not result.retried:
        return [f"{header} ===\n{result.raw_response}"]
    blocks = [f"{header} pass=first ===\n{result.first_pass_response}"]
    if result.error == "":
        blocks.append(f"{header} pass=retry ===\n{result.raw_response}")
    return blocks


def _transcript_entries(
    incorrect: Sequence[SampleCaseResult],
    retried: Sequence[SampleCaseResult],
    errored: Sequence[SampleCaseResult],
) -> list[SampleCaseResult]:
    keyed = {
        (entry.sample_index, entry.result.case_id): entry
        for entry in [*incorrect, *retried, *(e for e in errored if e.result.retried)]
    }
    return [keyed[key] for key in sorted(keyed)]


def _write_transcripts(results: Sequence[SampleCaseResult], path: Path | None) -> None:
    if path is None or not results:
        return
    resolved = _validated_write_path(path, root=Path.cwd())
    if resolved is None:
        return
    blocks = [block for entry in results for block in _transcript_blocks(entry)]
    resolved.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def sample_entries(sample_index: int, score: JudgeScore) -> SampleEntries:
    scored = [SampleCaseResult(sample_index, r) for r in score.case_results.values()]
    return SampleEntries(
        incorrect=tuple(entry for entry in scored if not entry.result.correct),
        retried=tuple(entry for entry in scored if entry.result.retried),
        errored=tuple(SampleCaseResult(sample_index, r) for r in score.errored),
    )


def _score_text(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def emit_metric(
    mean: float | None,
    first_pass_mean: float | None,
    scored_count: int,
    total_count: int,
    suffix: str = "",
) -> None:
    print(
        f"EVAL_METRIC judge_verdict_score={_score_text(mean)} "
        f"first_pass={_score_text(first_pass_mean)} "
        f"(scored {scored_count}/{total_count}{suffix})"
    )


def emit_diagnostics(
    incorrect: Sequence[SampleCaseResult],
    retried: Sequence[SampleCaseResult],
    errored: Sequence[SampleCaseResult],
    transcripts_path: Path | None,
) -> None:
    _print_incorrect([e for e in incorrect if not is_no_verdict_anchor(e.result)])
    _print_flips([e for e in incorrect if is_flip(e.result)])
    _print_no_verdict([e for e in incorrect if is_no_verdict_anchor(e.result)])
    _print_retries(retried)
    _print_errored(errored)
    _write_transcripts(_transcript_entries(incorrect, retried, errored), transcripts_path)


def run_sweep(golden_dir: Path = GOLDEN_DIR, transcripts_path: Path | None = None) -> int:
    cases = _load_cases_or_report(golden_dir)
    if cases is None:
        return 1
    if not cases:
        print("EVAL_METRIC judge_verdict_score=N/A (no golden fixtures on disk)")
        return 0
    rubric = load_rubric(golden_dir / RUBRIC_FILENAME)
    violations = validate_situation_coverage(cases, rubric)
    if violations:
        _print_violations(violations)
        return 1
    score = score_judge_cases(cases)
    entries = sample_entries(1, score)
    if score.mean is None:
        print(
            "HARNESS_ERROR judge behavioral eval harness broken: "
            f"0/{score.total_count} golden cases scored"
        )
        emit_diagnostics(entries.incorrect, entries.retried, entries.errored, transcripts_path)
        return 1
    emit_metric(score.mean, score.first_pass_mean, score.scored_count, score.total_count)
    emit_diagnostics(entries.incorrect, entries.retried, entries.errored, transcripts_path)
    return 1 if score.flipped_case_ids or score.no_verdict_anchor_ids else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden-dir",
        default=str(GOLDEN_DIR),
        help="Directory of committed golden verification-judge cases",
    )
    parser.add_argument(
        "--transcripts",
        default="judge-eval-transcripts.txt",
        help="Path to write raw judge transcripts for incorrect and retried cases",
    )
    args = parser.parse_args(argv)
    return run_sweep(Path(args.golden_dir), Path(args.transcripts))


if __name__ == "__main__":
    sys.exit(main())
