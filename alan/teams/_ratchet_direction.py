from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, cast

from alan import base_ref

_GIT_SHOW_TIMEOUT_S = 30.0

_RATCHET_HEADER = "## Ratchet direction observation (advisory)"

_UNRESOLVED_BASE_BLOCK = (
    f"{_RATCHET_HEADER}\n\n"
    "The base version could not be resolved; this comparison was not "
    "made, and no direction is inferred from one side."
)

_DISCRIMINATOR = (
    "A legitimate raise happens in its own pull request whose only "
    "content is the raise, opened against the flake-quarantine issue and "
    "citing its rerun-rescued evidence across three distinct PRs. Is that "
    "what this PR is? If not, this is a ratchet being widened inside the "
    "pull request it is blocking."
)


class Loosening(NamedTuple):
    path: str
    detail: str


_RuleFn = Callable[[str, str], Loosening | None]


def base_content(checkout: Path, path: str, base: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "show", f"{base}:{path}"],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            timeout=_GIT_SHOW_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _head_text(checkout: Path, path: str) -> str | None:
    try:
        return (checkout / path).read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_json_pair(head: str, base: str) -> tuple[Any, Any] | None:
    try:
        return json.loads(head), json.loads(base)
    except json.JSONDecodeError:
        return None


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_dict(value: Any) -> dict[Any, Any] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[Any, Any], value)  # type: ignore[redundant-cast]


def _as_list(value: Any) -> list[Any] | None:
    if not isinstance(value, list):
        return None
    return cast(list[Any], value)  # type: ignore[redundant-cast]


def _string_members(value: Any) -> set[str] | None:
    items = _as_list(value)
    if items is None:
        return None
    return {v for v in items if isinstance(v, str)}


def _check_flaky_baseline(head: str, base: str) -> Loosening | None:
    parsed = _parse_json_pair(head, base)
    if parsed is None:
        return None
    head_specs = _string_members(parsed[0])
    base_specs = _string_members(parsed[1])
    if head_specs is None or base_specs is None:
        return None
    added = sorted(head_specs - base_specs)
    if not added:
        return None
    return Loosening(
        "alan/web/e2e/flaky-baseline.json",
        f"spec(s) added to the flake baseline: {', '.join(added)}",
    )


def _lowered_key(
    head_obj: dict[Any, Any], base_obj: dict[Any, Any], key: str,
) -> tuple[float, float] | None:
    head_n = _as_number(head_obj.get(key))
    base_n = _as_number(base_obj.get(key))
    if head_n is not None and base_n is not None and head_n < base_n:
        return base_n, head_n
    return None


def _check_stress_floor(head: str, base: str) -> Loosening | None:
    parsed = _parse_json_pair(head, base)
    if parsed is None:
        return None
    head_obj = _as_dict(parsed[0])
    base_obj = _as_dict(parsed[1])
    if head_obj is None or base_obj is None:
        return None
    for key in ("minExecutedTests", "minSpecFiles"):
        hit = _lowered_key(head_obj, base_obj, key)
        if hit is not None:
            base_n, head_n = hit
            return Loosening(
                "alan/web/e2e/stress-floor.json",
                f"{key} lowered from {base_n:g} to {head_n:g}",
            )
    return None


def _check_jscpd(head: str, base: str) -> Loosening | None:
    parsed = _parse_json_pair(head, base)
    if parsed is None:
        return None
    head_obj = _as_dict(parsed[0])
    base_obj = _as_dict(parsed[1])
    if head_obj is None or base_obj is None:
        return None
    head_n = _as_number(head_obj.get("threshold"))
    base_n = _as_number(base_obj.get("threshold"))
    if head_n is not None and base_n is not None and head_n > base_n:
        return Loosening(
            "alan/web/.jscpd.json",
            f"duplication threshold raised from {base_n:g} to {head_n:g}",
        )
    return None


def _raised_dimension(
    head_dims: dict[Any, Any], base_dims: dict[Any, Any],
) -> tuple[Any, float, float] | None:
    for dim, raw_head_n in head_dims.items():
        head_n = _as_number(raw_head_n)
        base_n = _as_number(base_dims.get(dim))
        if head_n is not None and base_n is not None and head_n > base_n:
            return dim, base_n, head_n
    return None


def _screen_finding(
    path: str, screen: Any, head_dims: Any, base_dims: Any,
) -> Loosening | None:
    head_dict = _as_dict(head_dims)
    base_dict = _as_dict(base_dims)
    if head_dict is None or base_dict is None:
        return None
    hit = _raised_dimension(head_dict, base_dict)
    if hit is None:
        return None
    dim, base_n, head_n = hit
    return Loosening(path, f"{screen}.{dim} raised from {base_n:g} to {head_n:g}")


def _json_object(source: str) -> dict[Any, Any] | None:
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError:
        return None
    return _as_dict(parsed)


_TS_OBJECT_RE = re.compile(r"=\s*(\{.*\})\s*;\s*\Z", re.DOTALL)
_TS_BARE_KEY_RE = re.compile(r"(?<=[{,\s])([A-Za-z_$][\w$]*)\s*:")
_TS_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _ts_object_literal(source: str) -> dict[Any, Any] | None:
    match = _TS_OBJECT_RE.search(source)
    if match is None:
        return None
    body = _TS_BARE_KEY_RE.sub(r'"\1":', match.group(1))
    body = _TS_TRAILING_COMMA_RE.sub(r"\1", body)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return _as_dict(parsed)


def _dimension_rule(path: str, parse: Callable[[str], dict[Any, Any] | None]) -> _RuleFn:
    def _check(head: str, base: str) -> Loosening | None:
        head_obj = parse(head)
        base_obj = parse(base)
        if head_obj is None or base_obj is None:
            return None
        for screen, head_dims in head_obj.items():
            finding = _screen_finding(path, screen, head_dims, base_obj.get(screen))
            if finding is not None:
                return finding
        return None
    return _check


def _audit_baseline_rule(path: str) -> _RuleFn:
    return _dimension_rule(path, _json_object)


def _pin_rule(path: str) -> _RuleFn:
    return _dimension_rule(path, _ts_object_literal)


def _assign_target_name(node: ast.stmt) -> str | None:
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _int_constant(value: ast.expr) -> int | None:
    if not isinstance(value, ast.Constant) or isinstance(value.value, bool):
        return None
    return value.value if isinstance(value.value, int) else None


def _int_assign(node: ast.stmt) -> tuple[str, int] | None:
    name = _assign_target_name(node)
    if name is None:
        return None
    value = _int_constant(cast(ast.Assign, node).value)
    if value is None:
        return None
    return name, value


def _int_assigns(source: str) -> dict[str, int]:
    tree = ast.parse(source)
    out: dict[str, int] = {}
    for node in tree.body:
        found = _int_assign(node)
        if found is not None:
            out[found[0]] = found[1]
    return out


_SIZE_BASELINE_PATH = "tests/size_baseline.py"

_SIZE_BASELINE_CAPS = (
    "TEST_FILE_CAP",
    "SOURCE_MODULE_CAP",
    "CLAUDE_MD_BYTE_CEILING",
)


def _check_size_baseline(head: str, base: str) -> Loosening | None:
    try:
        head_vals, base_vals = _int_assigns(head), _int_assigns(base)
    except SyntaxError:
        return None
    for name in _SIZE_BASELINE_CAPS:
        head_n, base_n = head_vals.get(name), base_vals.get(name)
        if head_n is not None and base_n is not None and head_n > base_n:
            return Loosening(
                _SIZE_BASELINE_PATH, f"{name} raised from {base_n} to {head_n}",
            )
    return None


def _lte_constant(node: ast.AST) -> ast.Constant | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    if not isinstance(node.ops[0], ast.LtE) or len(node.comparators) != 1:
        return None
    bound = node.comparators[0]
    return bound if isinstance(bound, ast.Constant) else None


def _int_literal(constant: ast.Constant) -> int | None:
    if isinstance(constant.value, bool) or not isinstance(constant.value, int):
        return None
    return constant.value


def _le_bound(node: ast.AST) -> tuple[str, int] | None:
    if not isinstance(node, ast.Compare):
        return None
    constant = _lte_constant(node)
    if constant is None:
        return None
    value = _int_literal(constant)
    if value is None:
        return None
    return ast.unparse(node.left), value


def _le_bounds_in(func: ast.FunctionDef) -> dict[tuple[str, str], int]:
    bounds: dict[tuple[str, str], int] = {}
    for node in ast.walk(func):
        found = _le_bound(node)
        if found is not None:
            bounds[(func.name, found[0])] = found[1]
    return bounds


def _pinned_le_bounds(source: str) -> dict[tuple[str, str], int]:
    tree = ast.parse(source)
    out: dict[tuple[str, str], int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_pinned_"):
            out.update(_le_bounds_in(node))
    return out


def _check_test_size_ratchets(head: str, base: str) -> Loosening | None:
    try:
        head_bounds, base_bounds = _pinned_le_bounds(head), _pinned_le_bounds(base)
    except SyntaxError:
        return None
    for key, head_n in head_bounds.items():
        base_n = base_bounds.get(key)
        if base_n is not None and head_n > base_n:
            func_name, expr = key
            return Loosening(
                "tests/test_size_ratchets.py",
                f"{func_name}: bound on `{expr}` raised from {base_n} to {head_n}",
            )
    return None


_MOBILE_AUDIT_BASELINE = "alan/web/e2e/mobile-audit/baseline.json"
_DESKTOP_AUDIT_BASELINE = "alan/web/e2e/desktop-audit/baseline.json"
_MOBILE_AUDIT_PIN = "alan/web/e2e/mobile-audit/baseline-high-water.ts"
_DESKTOP_AUDIT_PIN = "alan/web/e2e/desktop-audit/baseline-high-water.ts"
_GH_READ_BASELINE = "tests/gh_read_baseline.py"
_GH_READ_BASELINE_NAME = "GH_READ_STUBBED_TESTS"
_CI_DURATION_BUDGETS = ".github/ci-duration-budgets.json"


def _frozenset_call(value: ast.expr | None) -> ast.Call | None:
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
        return None
    if value.func.id != "frozenset" or value.keywords:
        return None
    return value


def _set_string_constants(argument: ast.expr) -> set[str] | None:
    if not isinstance(argument, ast.Set):
        return None
    members: set[str] = set()
    for element in argument.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        members.add(element.value)
    return members


def _frozenset_string_members(value: ast.expr | None) -> set[str] | None:
    call = _frozenset_call(value)
    if call is None:
        return None
    members: set[str] = set()
    for argument in call.args:
        constants = _set_string_constants(argument)
        if constants is None:
            return None
        members |= constants
    return members


def _gh_read_baseline_members(source: str) -> set[str] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == _GH_READ_BASELINE_NAME:
                return _frozenset_string_members(node.value)
    return None


def _check_gh_read_baseline(head: str, base: str) -> Loosening | None:
    head_keys = _gh_read_baseline_members(head)
    base_keys = _gh_read_baseline_members(base)
    if head_keys is None or base_keys is None:
        return None
    added = sorted(head_keys - base_keys)
    if not added:
        return None
    return Loosening(
        _GH_READ_BASELINE, f"test(s) added to the gh read baseline: {', '.join(added)}",
    )


_RULES: dict[str, _RuleFn] = {
    "alan/web/e2e/flaky-baseline.json": _check_flaky_baseline,
    "alan/web/e2e/stress-floor.json": _check_stress_floor,
    "alan/web/.jscpd.json": _check_jscpd,
    _MOBILE_AUDIT_BASELINE: _audit_baseline_rule(_MOBILE_AUDIT_BASELINE),
    _DESKTOP_AUDIT_BASELINE: _audit_baseline_rule(_DESKTOP_AUDIT_BASELINE),
    _MOBILE_AUDIT_PIN: _pin_rule(_MOBILE_AUDIT_PIN),
    _DESKTOP_AUDIT_PIN: _pin_rule(_DESKTOP_AUDIT_PIN),
    _SIZE_BASELINE_PATH: _check_size_baseline,
    "tests/test_size_ratchets.py": _check_test_size_ratchets,
    _GH_READ_BASELINE: _check_gh_read_baseline,
    _CI_DURATION_BUDGETS: _audit_baseline_rule(_CI_DURATION_BUDGETS),
}


def _finding_for(checkout: Path, base: str, path: str, rule: _RuleFn) -> Loosening | None:
    head_text = _head_text(checkout, path)
    if head_text is None:
        return None
    base_text = base_content(checkout, path, base)
    if base_text is None:
        return None
    return rule(head_text, base_text)


def detect_ratchet_loosening(checkout: Path) -> tuple[Loosening, ...] | None:
    base = base_ref.resolve(checkout)
    if base is None:
        return None
    findings: list[Loosening] = []
    for path, rule in _RULES.items():
        finding = _finding_for(checkout, base, path, rule)
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def render_ratchet_block(findings: tuple[Loosening, ...] | None) -> str | None:
    if findings is None:
        return _UNRESOLVED_BASE_BLOCK
    if not findings:
        return None
    lines = [_RATCHET_HEADER, ""]
    lines.extend(f"- `{f.path}`: {f.detail}" for f in findings)
    lines.append("")
    lines.append(
        "This observation is advisory only — the host applies no "
        "mechanical block."
    )
    lines.append("")
    lines.append(_DISCRIMINATOR)
    return "\n".join(lines)


__all__ = [
    "Loosening",
    "base_content",
    "detect_ratchet_loosening",
    "render_ratchet_block",
]
