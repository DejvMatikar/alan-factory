from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"

_DISPATCHER_SUPPORT_SUBMODULE_NAMES = frozenset({
    "_dispatcher_support_state",
    "_dispatcher_support_teams",
})
_DISPATCHER_SUPPORT_DOTTED_MODULES = frozenset(
    f"tests.{name}" for name in _DISPATCHER_SUPPORT_SUBMODULE_NAMES
)
_DISPATCHER_TARGETS = (
    ("alan.manager_summary", "trigger_manager_summary"),
    ("alan.proactive_rebase", "maybe_trigger_proactive_rebase"),
)

_VERIFICATION_SUPPORT_SUBMODULE_NAMES = frozenset({"_teams_verification_support"})
_VERIFICATION_SUPPORT_DOTTED_MODULES = frozenset(
    f"tests.{name}" for name in _VERIFICATION_SUPPORT_SUBMODULE_NAMES
)
_VERIFICATION_TARGETS = (
    ("alan.teams._pr_ready", "check_pr_verifier_blockers"),
    ("alan.teams._pr_post", "dismiss_stale_verifier_reviews"),
    ("alan.teams._pr_branch", "resolve_existing_branch"),
    ("alan.worktree", "implementation_worktree"),
)
_EXPECTED_VERIFICATION_SIBLINGS = frozenset({
    "tests.test_teams_verification_registration_and_prompts",
    "tests.test_teams_verification_run_and_retry",
    "tests.test_teams_verification_review_post",
    "tests.test_teams_verification_manifest_and_scenarios",
    "tests.test_teams_verification_judge_and_suggestions",
    "tests.test_teams_verification_external_gates",
    "tests.test_teams_verification_gate_format_and_routing",
    "tests.test_teams_verification_gate_path_mode",
    "tests.test_teams_verification_inline_and_isolation",
    "tests.test_teams_verification_settle_and_scoped_dod",
    "tests.test_teams_verification_grounded_checkout",
    "tests.test_teams_verification_review_post_option_effects",
    "tests.test_teams_review_context",
    "tests.test_clause_audit_wiring",
    "tests.test_clause_audit_retry_grounding",
    "tests.test_verification_verdict_composition",
    "tests.test_verification_clause_stall_routing",
    "tests.test_verification_thread_resolution_wiring",
    "tests.test_verification_ratchet_block",
    "tests.test_verification_merged_guard",
})


def _imports_support_module(
    path: Path, support_dotted_modules: frozenset[str], support_submodule_names: frozenset[str]
) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module in support_dotted_modules:
                return True
            if node.module == "tests" and any(
                alias.name in support_submodule_names for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name in support_dotted_modules for alias in node.names):
                return True
    return False


def _discover_sibling_modules(
    support_dotted_modules: frozenset[str], support_submodule_names: frozenset[str]
) -> list[str]:
    return [
        f"tests.{path.stem}"
        for path in sorted(_TESTS_ROOT.glob("test_*.py"))
        if _imports_support_module(path, support_dotted_modules, support_submodule_names)
    ]


def _is_autouse_fixture(obj: Any) -> bool:
    marker = getattr(obj, "_fixture_function_marker", None)
    return marker is not None and bool(marker.autouse)


def _current_target_callables(targets: tuple[tuple[str, str], ...]) -> dict[str, object]:
    return {
        f"{mod_name}.{attr}": getattr(importlib.import_module(mod_name), attr)
        for mod_name, attr in targets
    }


def _check_module(
    module_name: str,
    targets: tuple[tuple[str, str], ...],
    failures: list[str],
    *,
    tmp_path: Path | None = None,
) -> None:
    module = importlib.import_module(module_name)
    autouse_fixtures = [obj for obj in vars(module).values() if _is_autouse_fixture(obj)]
    if not autouse_fixtures:
        failures.append(
            f"{module_name}: imports a support module but declares no autouse fixture"
        )
        return

    before = _current_target_callables(targets)
    mp = pytest.MonkeyPatch()
    try:
        for fixture_obj in autouse_fixtures:
            func = fixture_obj._fixture_function  # noqa: SLF001
            params = inspect.signature(func).parameters
            kwargs: dict[str, object] = {}
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = mp
            if "tmp_path" in params and tmp_path is not None:
                kwargs["tmp_path"] = tmp_path
            func(**kwargs)
        after = _current_target_callables(targets)
        for key in before:
            if after[key] is before[key]:
                failures.append(
                    f"{module_name}: does not actually patch {key} -- an autouse "
                    "fixture is present but its target is stale or wrong"
                )
    finally:
        mp.undo()


def test_every_dispatcher_sibling_patches_both_thread_spawners() -> None:
    discovered = _discover_sibling_modules(
        _DISPATCHER_SUPPORT_DOTTED_MODULES, _DISPATCHER_SUPPORT_SUBMODULE_NAMES
    )
    assert len(discovered) == 18, discovered

    failures: list[str] = []
    for module_name in discovered:
        _check_module(module_name, _DISPATCHER_TARGETS, failures)
    assert not failures, "\n".join(failures)


def test_every_teams_verification_sibling_patches_all_external_gates(tmp_path: Path) -> None:
    discovered = _discover_sibling_modules(
        _VERIFICATION_SUPPORT_DOTTED_MODULES, _VERIFICATION_SUPPORT_SUBMODULE_NAMES
    )
    assert set(discovered) == _EXPECTED_VERIFICATION_SIBLINGS, discovered

    failures: list[str] = []
    for module_name in discovered:
        _check_module(module_name, _VERIFICATION_TARGETS, failures, tmp_path=tmp_path)
    assert not failures, "\n".join(failures)


def test_teams_verification_support_declares_no_fixtures() -> None:
    tree = ast.parse(
        (_REPO_ROOT / "tests" / "_teams_verification_support.py").read_text(encoding="utf-8")
    )
    decorator_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute):
                    decorator_names.append(target.attr)
                elif isinstance(target, ast.Name):
                    decorator_names.append(target.id)
    assert "fixture" not in decorator_names, decorator_names
