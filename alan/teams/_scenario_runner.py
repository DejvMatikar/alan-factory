from __future__ import annotations

import math
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

from alan import process_runner
from alan.engine_config import AGGREGATE_BUDGET_DEFAULT, SCENARIO_TIMEOUT_DEFAULT, EngineConfig
from alan.handover import TestScenario
from alan.teams import _fence
from alan.teams._base import UNTRUSTED_CONTENT_NOTICE

_TIMEOUT_ENV = "ALAN_SCENARIO_TIMEOUT_S"
_RESULTS_MAX_CHARS_ENV = "ALAN_SCENARIO_RESULTS_MAX_CHARS"
_RESULTS_MAX_CHARS_DEFAULT = 40_000
_TAIL_LEN = 2_000
_PYTEST_RC_NO_TESTS = 5
_PYTEST_RC_COLLECTION_ERROR = 2
_PYTEST_RC_NOT_FOUND = 4

ScenarioStatus = Literal[
    "PASS", "FAIL", "SKIPPED", "DOCUMENTED", "ADVISORY", "TIMEOUT", "BUDGET_EXHAUSTED"
]
NON_BLOCKING_STATUSES: frozenset[ScenarioStatus] = frozenset(
    {"SKIPPED", "DOCUMENTED", "ADVISORY", "BUDGET_EXHAUSTED"}
)

_FORBIDDEN_SHELL_OPS: frozenset[str] = frozenset({
    "&&", "||", ";", "|", ">", "<", "`",
})

_ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("uv", "run"),
    ("npm", "run"),
    ("npm", "--prefix"),
    ("npx",),
    ("git", "log"),
    ("git", "diff"),
    ("git", "status"),
    ("git", "show"),
    ("git", "blame"),
    ("git", "ls-files"),
    ("ls",),
    ("cat",),
    ("pwd",),
    ("head",),
    ("tail",),
    ("wc",),
    ("diff",),
    ("nl",),
    ("git", "rev-parse"),
    ("git", "merge-base"),
    ("git", "rev-list"),
    ("git", "ls-tree"),
    ("git", "cat-file"),
    ("git", "describe"),
    ("git", "diff-tree"),
)


def _allowed_prefixes_for(cfg: EngineConfig | None) -> tuple[tuple[str, ...], ...]:
    if cfg is None:
        return _ALLOWED_COMMAND_PREFIXES
    derived = (tuple(cmd[:2]) for cmd in cfg.gate_commands if cmd)
    configured = (tuple(prefix) for prefix in cfg.scenario.command_prefixes if prefix)
    return tuple(dict.fromkeys((*_ALLOWED_COMMAND_PREFIXES, *derived, *configured)))


def _default_command_for(cfg: EngineConfig | None, test_ref: str) -> str:
    if cfg is not None:
        for command in cfg.gate_commands:
            if command:
                return f"{shlex.join(command)} {shlex.quote(test_ref)}"
    return f"uv run --no-active pytest {shlex.quote(test_ref)}"


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    status: ScenarioStatus
    diagnostic: str
    stdout_tail: str
    stderr_tail: str
    returncode: int | None
    elapsed_s: float


_RESULTS_MAX_CHARS_MIN = 256


def _results_max_chars() -> int:
    raw = os.environ.get(_RESULTS_MAX_CHARS_ENV, "").strip()
    try:
        value = int(raw) if raw else _RESULTS_MAX_CHARS_DEFAULT
    except ValueError:
        return _RESULTS_MAX_CHARS_DEFAULT
    return max(value, _RESULTS_MAX_CHARS_MIN)


def _resolved_timeout_default(cfg: EngineConfig | None) -> float:
    if cfg is not None and cfg.scenario.timeout_s is not None:
        return cfg.scenario.timeout_s
    return SCENARIO_TIMEOUT_DEFAULT


def scenario_timeout(cfg: EngineConfig | None = None) -> float:
    raw = os.environ.get(_TIMEOUT_ENV, "").strip()
    if not raw:
        return _resolved_timeout_default(cfg)
    try:
        value = float(raw)
    except ValueError:
        return SCENARIO_TIMEOUT_DEFAULT
    if not math.isfinite(value) or value <= 0:
        return SCENARIO_TIMEOUT_DEFAULT
    return value


def resolve_aggregate_budget(cfg: EngineConfig | None = None) -> float:
    if cfg is not None and cfg.scenario.aggregate_budget_s is not None:
        return cfg.scenario.aggregate_budget_s
    return AGGREGATE_BUDGET_DEFAULT


def _check_forbidden_ops(when: str) -> str | None:
    for op in _FORBIDDEN_SHELL_OPS:
        if op in when:
            return f"forbidden shell operator {op!r} in when field"
    return None


def _rejected_prefix_str(
    tokens: list[str], prefixes: tuple[tuple[str, ...], ...]
) -> str:
    partial_bases = frozenset(p[0] for p in prefixes if len(p) > 1)
    if len(tokens) >= 2 and tokens[0] in partial_bases:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


def _check_command_prefix(
    tokens: list[str], prefixes: tuple[tuple[str, ...], ...]
) -> str | None:
    for prefix in prefixes:
        if len(tokens) >= len(prefix) and tuple(tokens[: len(prefix)]) == prefix:
            return None
    return (
        f"command prefix {_rejected_prefix_str(tokens, prefixes)!r} "
        "not in scenario runner allowlist"
    )


def _check_no_path_traversal(tokens: list[str]) -> str | None:
    for tok in tokens[1:]:
        if any(seg == ".." for seg in tok.replace("\\", "/").split("/")):
            return f"path traversal segment '..' in argument {tok!r}"
    return None


def _check_allowlist(when: str, cfg: EngineConfig | None = None) -> str | None:
    op_diag = _check_forbidden_ops(when)
    if op_diag is not None:
        return op_diag
    try:
        tokens = shlex.split(when)
    except ValueError as exc:
        return f"when field has unbalanced quotes: {exc}"
    if not tokens:
        return "when field is empty after shell parsing"
    prefix_diag = _check_command_prefix(tokens, _allowed_prefixes_for(cfg))
    if prefix_diag is not None:
        return prefix_diag
    return _check_no_path_traversal(tokens)


def _is_prose_rejection(when: str, cfg: EngineConfig | None = None) -> bool:
    if not when.strip():
        return False
    if _check_forbidden_ops(when) is not None:
        return False
    try:
        tokens = shlex.split(when)
    except ValueError:
        return False
    return _check_command_prefix(tokens, _allowed_prefixes_for(cfg)) is not None


def resolved_command(scenario: TestScenario, cfg: EngineConfig | None = None) -> str:
    if scenario.when.strip():
        return scenario.when
    if scenario.test_ref:
        return _default_command_for(cfg, scenario.test_ref)
    return scenario.when


def _fail_diagnostic(returncode: int | None, then: str) -> str:
    if returncode == _PYTEST_RC_NO_TESTS:
        return f"pytest collected no tests (rc={_PYTEST_RC_NO_TESTS})"
    if returncode != 0:
        return f"exit code {returncode}"
    return f"then predicate {then!r} not found in combined output"


def _run_outcome(
    returncode: int | None, then: str, combined: str
) -> tuple[ScenarioStatus, str]:
    if returncode != 0:
        return "FAIL", _fail_diagnostic(returncode, then)
    then_stripped = then.strip()
    if not then_stripped or then_stripped in combined:
        return "PASS", ""
    return "ADVISORY", _fail_diagnostic(returncode, then)


_COLLECTION_ERROR_SIGNATURES: tuple[str, ...] = (
    "errors during collection",
    "error during collection",
    "ERROR collecting",
)


def _collection_diagnostic(returncode: int | None, combined: str) -> str | None:
    if returncode != _PYTEST_RC_COLLECTION_ERROR:
        return None
    for signature in _COLLECTION_ERROR_SIGNATURES:
        if signature in combined:
            return (
                f"pytest collection error (rc={_PYTEST_RC_COLLECTION_ERROR}): "
                f"{signature}"
            )
    return None


_NOTFOUND_SIGNATURES: tuple[str, ...] = (
    "file or directory not found",
    "ERROR: not found:",
)


def _notfound_diagnostic(returncode: int | None, combined: str) -> str | None:
    if returncode != _PYTEST_RC_NOT_FOUND:
        return None
    for signature in _NOTFOUND_SIGNATURES:
        if signature in combined:
            return (
                f"pytest scenario not found (rc={_PYTEST_RC_NOT_FOUND}): "
                f"{signature}"
            )
    return None


def _post_run_result(
    scenario: TestScenario,
    result: subprocess.CompletedProcess[str],
    elapsed: float,
) -> ScenarioResult:
    combined = result.stdout + "\n" + result.stderr
    skip_diag = _collection_diagnostic(
        result.returncode, combined
    ) or _notfound_diagnostic(result.returncode, combined)
    if skip_diag is not None:
        status: ScenarioStatus = "SKIPPED"
        diag = skip_diag
    else:
        status, diag = _run_outcome(result.returncode, scenario.then, combined)
    return ScenarioResult(
        scenario_id=scenario.id,
        status=status,
        diagnostic=diag,
        stdout_tail=result.stdout[-_TAIL_LEN:],
        stderr_tail=result.stderr[-_TAIL_LEN:],
        returncode=result.returncode,
        elapsed_s=elapsed,
    )


def _execute_scenario(
    scenario: TestScenario, *, cwd: str, timeout: float
) -> ScenarioResult:
    try:
        tokens = shlex.split(scenario.when)
    except ValueError as exc:
        return ScenarioResult(
            scenario_id=scenario.id,
            status="FAIL",
            diagnostic=f"when field has unbalanced quotes: {exc}",
            stdout_tail="",
            stderr_tail="",
            returncode=None,
            elapsed_s=0.0,
        )
    start = time.monotonic()
    try:
        result = process_runner.run_subprocess(tokens, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ScenarioResult(
            scenario_id=scenario.id,
            status="TIMEOUT",
            diagnostic=f"timed out after {timeout:.0f}s",
            stdout_tail="",
            stderr_tail="",
            returncode=None,
            elapsed_s=time.monotonic() - start,
        )
    except OSError as exc:
        return ScenarioResult(
            scenario_id=scenario.id,
            status="FAIL",
            diagnostic=f"command not found: {exc}",
            stdout_tail="",
            stderr_tail="",
            returncode=None,
            elapsed_s=time.monotonic() - start,
        )
    return _post_run_result(scenario, result, time.monotonic() - start)


def _prose_diagnostic(has_test_ref: bool) -> str:
    suffix = (
        "test_ref present but when field takes precedence; command prefix not in allowlist"
        if has_test_ref
        else "no test_ref, command prefix not in allowlist"
    )
    return f"prose when field: documentation-only scenario ({suffix})"


def _run_scenario_inner(
    scenario: TestScenario, *, cwd: str, timeout: float, cfg: EngineConfig | None = None
) -> ScenarioResult:
    effective_when = resolved_command(scenario, cfg)
    if not effective_when.strip():
        return ScenarioResult(
            scenario_id=scenario.id,
            status="SKIPPED",
            diagnostic="when field is empty",
            stdout_tail="",
            stderr_tail="",
            returncode=None,
            elapsed_s=0.0,
        )
    security_diag = _check_allowlist(effective_when, cfg)
    if security_diag is None:
        effective_scenario = scenario.model_copy(update={"when": effective_when})
        return _execute_scenario(effective_scenario, cwd=cwd, timeout=timeout)
    if _is_prose_rejection(effective_when, cfg):
        return ScenarioResult(
            scenario_id=scenario.id,
            status="DOCUMENTED",
            diagnostic=_prose_diagnostic(bool(scenario.test_ref)),
            stdout_tail="",
            stderr_tail="",
            returncode=None,
            elapsed_s=0.0,
        )
    return ScenarioResult(
        scenario_id=scenario.id,
        status="SKIPPED",
        diagnostic=security_diag,
        stdout_tail="",
        stderr_tail="",
        returncode=None,
        elapsed_s=0.0,
    )


def _as_failure(result: ScenarioResult) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=result.scenario_id,
        status="FAIL",
        diagnostic=result.diagnostic,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
        returncode=result.returncode,
        elapsed_s=result.elapsed_s,
    )


def run_scenario(
    scenario: TestScenario,
    *,
    cwd: str,
    timeout: float,
    cfg: EngineConfig | None = None,
    strict: bool = False,
) -> ScenarioResult:
    result = _run_scenario_inner(scenario, cwd=cwd, timeout=timeout, cfg=cfg)
    if strict and result.status in ("SKIPPED", "DOCUMENTED"):
        return _as_failure(result)
    return result


def _budget_exhausted_result(scenario_id: str) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        status="BUDGET_EXHAUSTED",
        diagnostic="aggregate budget exhausted",
        stdout_tail="",
        stderr_tail="",
        returncode=None,
        elapsed_s=0.0,
    )


def _resolve_limits(
    cfg: EngineConfig | None,
    per_scenario_timeout: float | None,
    aggregate_budget: float | None,
) -> tuple[float, float]:
    timeout = (
        per_scenario_timeout if per_scenario_timeout is not None else scenario_timeout(cfg)
    )
    budget = (
        aggregate_budget if aggregate_budget is not None else resolve_aggregate_budget(cfg)
    )
    return timeout, budget


def run_scenarios(
    scenarios: list[TestScenario],
    *,
    cwd: str,
    per_scenario_timeout: float | None = None,
    aggregate_budget: float | None = None,
    cfg: EngineConfig | None = None,
    strict_ids: frozenset[str] = frozenset(),
) -> list[ScenarioResult]:
    timeout, budget = _resolve_limits(cfg, per_scenario_timeout, aggregate_budget)
    deadline = time.monotonic() + budget
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append(_budget_exhausted_result(scenario.id))
            continue
        results.append(
            run_scenario(
                scenario, cwd=cwd, timeout=min(timeout, remaining), cfg=cfg,
                strict=scenario.id in strict_ids,
            )
        )
    return results


def _format_single_result(idx: int, result: ScenarioResult) -> str:
    rc = result.returncode if result.returncode is not None else "N/A"
    lines = [
        f"\n### {idx}. {result.scenario_id} — {result.status}",
        f"- Exit code: {rc}",
        f"- Elapsed: {result.elapsed_s:.2f}s",
    ]
    if result.diagnostic:
        lines.append(f"- Diagnostic: {result.diagnostic}")
    if result.stdout_tail:
        lines.append(f"- stdout:\n{_fence.fence(result.stdout_tail)}")
    if result.stderr_tail:
        lines.append(f"- stderr:\n{_fence.fence(result.stderr_tail)}")
    return "\n".join(lines)


def format_results_block(results: list[ScenarioResult]) -> str:
    if not results:
        return ""
    parts: list[str] = ["## Scenario execution results", UNTRUSTED_CONTENT_NOTICE]
    for idx, result in enumerate(results, start=1):
        parts.append(_format_single_result(idx, result))
    raw = "\n".join(parts)
    max_chars = _results_max_chars()
    if len(raw) <= max_chars:
        return raw
    suffix = f"\n\n[truncated — exceeded {max_chars} chars cap]"
    return raw[: max_chars - len(suffix)] + suffix


__all__ = [
    "AGGREGATE_BUDGET_DEFAULT",
    "SCENARIO_TIMEOUT_DEFAULT",
    "ScenarioResult",
    "ScenarioStatus",
    "_PYTEST_RC_COLLECTION_ERROR",
    "_PYTEST_RC_NO_TESTS",
    "_PYTEST_RC_NOT_FOUND",
    "_allowed_prefixes_for",
    "_check_allowlist",
    "_check_command_prefix",
    "_collection_diagnostic",
    "_default_command_for",
    "_fail_diagnostic",
    "_is_prose_rejection",
    "_notfound_diagnostic",
    "NON_BLOCKING_STATUSES",
    "format_results_block",
    "resolve_aggregate_budget",
    "resolved_command",
    "run_scenario",
    "run_scenarios",
    "scenario_timeout",
]
