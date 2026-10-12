from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.forge_argv import (
    ADAPTER_MODULES,
    MIGRATED_MODULES,
    RECORDED_ARGV_MODULES,
    builds_gh_argv,
    find_argv_modules,
    literal_prefix,
    transport_call_nodes,
    transport_calls,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RAW_SUBPROCESS_NAMES = frozenset(
    {"run", "Popen", "check_output", "call", "check_call", "getoutput", "getstatusoutput"},
)
_ISSUE_AND_TRIAGE_MODULES = ("alan/teams/triage_pregate.py", "alan/teams/unblock_issue.py")


def _subprocess_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    modules = {"subprocess"}
    operations = {name: name for name in _RAW_SUBPROCESS_NAMES}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            operations.update(
                {a.asname or a.name: a.name for a in node.names if a.name in _RAW_SUBPROCESS_NAMES},
            )
    return modules, operations


def raw_subprocess_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    modules, operations = _subprocess_bindings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in modules:
                found.append(f"subprocess.{func.attr}")
        elif isinstance(func, ast.Name) and func.id in operations:
            found.append(operations[func.id])
    return found


def test_recorded_set_equals_the_modules_that_assemble_a_gh_argv() -> None:
    found = find_argv_modules() - ADAPTER_MODULES
    unlisted = sorted(found - RECORDED_ARGV_MODULES)
    stale = sorted(RECORDED_ARGV_MODULES - found)
    assert not unlisted, f"unlisted module builds a gh argv: {unlisted}"
    assert not stale, (
        f"listed module no longer does — remove it from RECORDED_ARGV_MODULES: {stale}"
    )


def test_adapter_module_builds_a_gh_argv_and_is_named_as_exempt() -> None:
    assert ADAPTER_MODULES <= find_argv_modules()
    assert ADAPTER_MODULES.isdisjoint(RECORDED_ARGV_MODULES)


def test_recorded_modules_all_exist() -> None:
    assert all((_REPO_ROOT / rel).is_file() for rel in RECORDED_ARGV_MODULES)


@pytest.mark.parametrize("rel_path", sorted(MIGRATED_MODULES))
def test_migrated_module_is_absent_from_the_recorded_set(rel_path: str) -> None:
    assert rel_path not in RECORDED_ARGV_MODULES
    assert rel_path not in find_argv_modules()


@pytest.mark.parametrize("rel_path", sorted(MIGRATED_MODULES))
def test_migrated_module_calls_none_of_the_three_transport_entry_points(rel_path: str) -> None:
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert transport_calls(source) == []


def test_copilot_resolution_sends_only_git_command_lines_through_the_transport() -> None:
    source = (_REPO_ROOT / "alan/teams/_copilot_resolution.py").read_text(encoding="utf-8")
    calls = transport_call_nodes(source)
    assert calls
    assert all(
        call.args and literal_prefix(call.args[0])[:1] == ("git",) for call in calls
    )


@pytest.mark.parametrize(
    "source",
    [
        "argv = ['gh', 'api', 'graphql']",
        "argv = ('gh', 'pr', 'view')",
        "argv = [\n    'gh', 'api',\n    '--paginate',\n]",
        "def _argv(n):\n    return ['gh', 'api', f'/repos/{n}']",
        "argv = ['gh', *rest]",
        "run(['gh', 'api', 'user'], cwd=None)",
    ],
)
def test_predicate_flags_wrapped_helper_returned_and_nested_gh_argv(source: str) -> None:
    assert builds_gh_argv(source) is True


@pytest.mark.parametrize(
    "source",
    [
        "argv = ['git', 'push', '-u']",
        "argv = [binary, 'api']",
        "argv = ['ghost', 'api']",
        "argv = []",
        "name = 'gh'",
        "argv = [f'gh', 'api']",
    ],
)
def test_predicate_ignores_git_and_non_literal_first_elements(source: str) -> None:
    assert builds_gh_argv(source) is False


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("_pr_branch.run_subprocess(argv, cwd=None, timeout=1)", ["run_subprocess"]),
        ("run_gh_api_json_input(args, payload)", ["run_gh_api_json_input"]),
        ("_pr_branch.gh_checked(argv, context='x')", ["gh_checked"]),
        ("subprocess.run(argv)", []),
    ],
)
def test_transport_calls_finds_all_three_entry_points_by_call(
    source: str, expected: list[str],
) -> None:
    assert transport_calls(source) == expected


@pytest.mark.parametrize("rel_path", _ISSUE_AND_TRIAGE_MODULES)
def test_migrated_issue_and_triage_modules_make_no_raw_subprocess_call(rel_path: str) -> None:
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert raw_subprocess_calls(source) == []
    assert transport_calls(source) == []
    assert not builds_gh_argv(source)


@pytest.mark.parametrize("rel_path", _ISSUE_AND_TRIAGE_MODULES)
def test_issue_and_triage_modules_are_migrated_not_recorded(rel_path: str) -> None:
    assert rel_path in MIGRATED_MODULES
    assert rel_path not in RECORDED_ARGV_MODULES


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("subprocess.run(argv)", ["subprocess.run"]),
        ("subprocess.Popen(argv)", ["subprocess.Popen"]),
        ("subprocess.check_output(argv)", ["subprocess.check_output"]),
        ("run(argv)", ["run"]),
        ("Popen(argv)", ["Popen"]),
        ("check_output(argv)", ["check_output"]),
        ("call(argv)", ["call"]),
        ("check_call(argv)", ["check_call"]),
        ("getoutput(cmd)", ["getoutput"]),
        ("getstatusoutput(cmd)", ["getstatusoutput"]),
    ],
)
def test_raw_subprocess_walker_flags_module_qualified_and_bare_calls(
    source: str, expected: list[str],
) -> None:
    assert raw_subprocess_calls(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import subprocess as sp\nsp.run(argv)", ["subprocess.run"]),
        ("import subprocess as sp\nsp.Popen(argv)", ["subprocess.Popen"]),
        ("import os, subprocess as sp\nsp.check_output(argv)", ["subprocess.check_output"]),
        ("from subprocess import run as spawn\nspawn(argv)", ["run"]),
        (
            "from subprocess import Popen as launch, call\nlaunch(argv)\ncall(argv)",
            ["Popen", "call"],
        ),
        ("from subprocess import getoutput as shell\nshell(cmd)", ["getoutput"]),
    ],
)
def test_raw_subprocess_walker_resolves_aliased_module_and_imported_operations(
    source: str, expected: list[str],
) -> None:
    assert raw_subprocess_calls(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "import os as sp\nsp.run(argv)",
        "from other import run as spawn\nspawn(argv)",
        "from subprocess import TimeoutExpired as expired\nexpired(1, 2)",
        "from subprocess import PIPE as pipe\nuse(pipe)",
        "import subprocess as sp\nexcept_types = (sp.TimeoutExpired,)",
    ],
)
def test_raw_subprocess_walker_ignores_unrelated_aliases_and_non_spawning_imports(
    source: str,
) -> None:
    assert raw_subprocess_calls(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    pass\nexcept subprocess.TimeoutExpired:\n    pass",
        "except_types = (FileNotFoundError, subprocess.TimeoutExpired)",
        "active_forge().read_pr(1, ('number',))",
        "obj.run(argv)",
    ],
)
def test_raw_subprocess_walker_allows_exception_references_and_forge_calls(source: str) -> None:
    assert raw_subprocess_calls(source) == []
