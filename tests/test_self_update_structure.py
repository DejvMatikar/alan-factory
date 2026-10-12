from __future__ import annotations

import ast
import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

from tests.forge_argv import (
    ADAPTER_MODULES,
    RECORDED_ARGV_MODULES,
    builds_gh_argv,
    literal_prefix,
    transport_call_nodes,
)

_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = "alan/self_update"
GITHUB = "alan/self_update_github.py"
PACKAGE_FILES = frozenset({
    f"{PACKAGE_DIR}/__init__.py",
    f"{PACKAGE_DIR}/__main__.py",
    f"{PACKAGE_DIR}/_cli.py",
    f"{PACKAGE_DIR}/_idle.py",
    f"{PACKAGE_DIR}/_lock.py",
    f"{PACKAGE_DIR}/_journal.py",
    f"{PACKAGE_DIR}/_extract.py",
    f"{PACKAGE_DIR}/_ops.py",
    f"{PACKAGE_DIR}/_origin.py",
    f"{PACKAGE_DIR}/_rollback.py",
    f"{PACKAGE_DIR}/_soak.py",
    f"{PACKAGE_DIR}/_leftover.py",
    f"{PACKAGE_DIR}/_run.py",
    f"{PACKAGE_DIR}/_state.py",
})
_STATE_IMPORTER = "alan/teams/_triage_auto_update.py"
_RUNNER = "deploy/update_runner.sh"
_ENTRY_POINT_CALL = "_cli.main("
EXPECTED_CLOSURE = frozenset({
    "alan/__init__.py",
    GITHUB,
    "alan/deploy_refresh.py",
    "alan/deploy_restart.py",
    "alan/roots.py",
    "alan/store.py",
    "alan/process_runner.py",
} | PACKAGE_FILES)
FORBIDDEN_IN_CLOSURE = (
    "served_sha", "deploy_signal", "clock", "forge_gh", "deploy_observe", "queue",
)
GET_PREFIX = ("gh", "api", "--hostname", "github.com", "--method", "GET")
FIELD_OR_INPUT_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})
AUXILIARY_SUFFIXES = frozenset({".py", ".sh", ".plist", ".yml", ".yaml"})
FORBIDDEN_TEXT = (
    "if __name__", "__file__", "os.environ", "getenv", "ALAN_", "Path.home", "expanduser",
)
_SUBMODULE_IMPORT_RE = re.compile(r"\Aalan\.self_update\._\w+")


def parse(rel: str) -> ast.Module:
    return ast.parse((_ROOT / rel).read_text(encoding="utf-8"))


def imported_names(rel: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(parse(rel)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, rel
            assert node.module is not None
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def alan_files_and_externals(rel: str) -> tuple[set[str], set[str]]:
    alan_files: set[str] = set()
    externals: set[str] = set()
    for name in imported_names(rel):
        if name == "alan" or name.startswith("alan."):
            candidate = name.replace(".", "/") + ".py"
            if (_ROOT / candidate).is_file():
                alan_files.add(candidate)
        else:
            externals.add(name.split(".")[0])
    return alan_files, externals


def import_closure(starts: frozenset[str]) -> tuple[set[str], set[str]]:
    seen: set[str] = set()
    externals: set[str] = set()
    pending = [*starts, "alan/__init__.py"]
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        seen.add(rel)
        files, outside = alan_files_and_externals(rel)
        externals |= outside
        pending.extend(files - seen)
    return seen, externals


def package_files_on_disk() -> frozenset[str]:
    found: set[str] = set()
    for directory, dirnames, filenames in os.walk(_ROOT / PACKAGE_DIR):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        found.update(
            (Path(directory) / name).relative_to(_ROOT).as_posix()
            for name in filenames
            if name.endswith(".py")
        )
    return frozenset(found)


def backend_sources() -> list[str]:
    found: list[str] = []
    for directory, dirnames, filenames in os.walk(_ROOT / "alan"):
        dirnames[:] = [name for name in dirnames if name not in {"web", "__pycache__"}]
        found.extend(
            (Path(directory) / name).relative_to(_ROOT).as_posix()
            for name in filenames
            if name.endswith(".py")
        )
    return sorted(found)


def imported_dotted(rel: str) -> set[str]:
    package = ".".join(Path(rel).parts[:-1])
    found: set[str] = set()
    for node in ast.walk(parse(rel)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            anchor = ".".join(parts[: len(parts) - node.level + 1]) if node.level else ""
            base = ".".join(part for part in (anchor, node.module) if part)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def has_submodule_import(dotted: set[str]) -> bool:
    return any(_SUBMODULE_IMPORT_RE.match(name) for name in dotted)


def argv_problem(call: ast.Call) -> str | None:
    argv = call.args[0] if call.args else None
    if not isinstance(argv, ast.List):
        return "argv is not an inline list literal"
    if any(isinstance(element, ast.Starred) for element in argv.elts):
        return "argv unpacks another sequence"
    if literal_prefix(argv) != GET_PREFIX:
        return "argv does not start with the read-only GET prefix"
    if len(argv.elts) != len(GET_PREFIX) + 1:
        return "argv is not the prefix plus one path"
    if any(
        isinstance(element, ast.Constant) and element.value in FIELD_OR_INPUT_FLAGS
        for element in argv.elts
    ):
        return "argv carries a field or input flag"
    return None


def calls_in(source: str) -> list[ast.Call]:
    return transport_call_nodes(source)


def test_the_pinned_package_file_set_matches_what_is_on_disk() -> None:
    assert package_files_on_disk() == PACKAGE_FILES


def test_the_pinned_file_set_would_notice_a_file_missing_from_the_pin() -> None:
    assert package_files_on_disk() != (PACKAGE_FILES - {f"{PACKAGE_DIR}/_ops.py"})


def test_the_package_aware_import_closure_of_the_updater_is_exactly_the_documented_set() -> None:
    closure, _ = import_closure(PACKAGE_FILES)
    assert closure == EXPECTED_CLOSURE


def test_every_module_in_the_closure_imports_only_the_standard_library_outside_the_repo() -> None:
    closure, externals = import_closure(PACKAGE_FILES)
    assert closure == EXPECTED_CLOSURE
    assert externals
    assert externals <= set(sys.stdlib_module_names)


@pytest.mark.parametrize("name", FORBIDDEN_IN_CLOSURE)
def test_the_clock_served_commit_forge_observe_and_queue_layers_stay_out_of_the_closure(
    name: str,
) -> None:
    closure, _ = import_closure(PACKAGE_FILES)
    assert f"alan/{name}.py" not in closure
    assert not any(rel.startswith("alan/teams/") for rel in closure)


def test_the_closure_walk_itself_notices_a_module_that_is_pulled_in() -> None:
    closure, _ = import_closure(frozenset({"alan/deploy_signal.py"}))
    assert "alan/served_sha.py" in closure
    assert f"{PACKAGE_DIR}/__init__.py" not in closure


def test_no_other_backend_module_imports_the_updater() -> None:
    importers = {"alan.self_update": set[str](), "alan.self_update_github": set[str]()}
    for rel in backend_sources():
        dotted = imported_dotted(rel)
        for module, holders in importers.items():
            if module == "alan.self_update" and rel in PACKAGE_FILES:
                continue
            if module in dotted:
                holders.add(rel)
    assert importers["alan.self_update"] == set()
    assert importers["alan.self_update_github"] == {f"{PACKAGE_DIR}/__init__.py"}


def test_nothing_outside_the_package_imports_an_individual_submodule() -> None:
    offenders = [
        rel
        for rel in backend_sources()
        if rel not in PACKAGE_FILES
        and rel != _STATE_IMPORTER
        and has_submodule_import(imported_dotted(rel))
    ]
    assert offenders == []


def test_the_one_allowed_importer_touches_only_the_state_submodule() -> None:
    dotted = imported_dotted(_STATE_IMPORTER)
    submodule_imports = {name for name in dotted if _SUBMODULE_IMPORT_RE.match(name)}
    assert submodule_imports
    assert all(name.startswith("alan.self_update._state") for name in submodule_imports)


def test_the_submodule_import_detector_notices_a_direct_private_import() -> None:
    assert has_submodule_import({"alan.self_update._rollback"})
    assert not has_submodule_import({"alan.self_update", "alan.self_update.apply"})


def auxiliary_files() -> list[Path]:
    return [
        path
        for extra in ("scripts", "deploy")
        for path in sorted((_ROOT / extra).rglob("*"))
        if path.is_file() and path.suffix in AUXILIARY_SUFFIXES
    ]


def test_nothing_outside_the_package_mentions_the_updater_by_name() -> None:
    mentions = [
        rel
        for rel in backend_sources()
        if rel not in PACKAGE_FILES | {GITHUB, _STATE_IMPORTER}
        and "self_update" in (_ROOT / rel).read_text(encoding="utf-8")
    ]
    mentions += [
        path.relative_to(_ROOT).as_posix()
        for path in auxiliary_files()
        if path.relative_to(_ROOT).as_posix() != _RUNNER
        and "self_update" in path.read_text(encoding="utf-8")
    ]
    assert mentions == []


def test_the_runner_is_the_one_auxiliary_file_that_names_the_updater() -> None:
    assert "self_update" in (_ROOT / _RUNNER).read_text(encoding="utf-8")


def test_the_package_has_exactly_one_entry_point() -> None:
    callers = [
        rel
        for rel in sorted(PACKAGE_FILES)
        if _ENTRY_POINT_CALL in (_ROOT / rel).read_text(encoding="utf-8")
    ]
    assert callers == [f"{PACKAGE_DIR}/__main__.py"]


@pytest.mark.parametrize("rel", sorted(PACKAGE_FILES | {GITHUB}))
def test_the_updater_modules_have_no_entry_point_no_path_inference_and_no_environment_read(
    rel: str,
) -> None:
    source = (_ROOT / rel).read_text(encoding="utf-8")
    for fragment in FORBIDDEN_TEXT:
        assert fragment not in source, fragment


def test_only_the_github_module_builds_a_gh_command_line() -> None:
    for rel in sorted(PACKAGE_FILES):
        assert not builds_gh_argv((_ROOT / rel).read_text(encoding="utf-8")), rel
    assert builds_gh_argv((_ROOT / GITHUB).read_text(encoding="utf-8"))


def test_every_transport_call_in_the_github_module_is_an_inline_read_only_get_argv() -> None:
    calls = calls_in((_ROOT / GITHUB).read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert [argv_problem(call) for call in calls] == [None]
    argv = calls[0].args[0]
    assert isinstance(argv, ast.List)
    assert literal_prefix(argv) == GET_PREFIX
    assert len(argv.elts) == 7


@pytest.mark.parametrize(
    "source",
    [
        "run_subprocess(argv, cwd=None, timeout=1)",
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', method, path])",
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', 'POST', path])",
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', *rest])",
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', path, 'x'])",
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '-f'])",
        "run_subprocess(['gh', 'api', '--method', 'GET', '--hostname', 'github.com', path])",
        "run_subprocess(('gh', 'api', '--hostname', 'github.com', '--method', 'GET', path))",
        "run_subprocess()",
    ],
)
def test_the_read_only_get_predicate_rejects_non_literal_and_mutating_argvs(source: str) -> None:
    (call,) = calls_in(source)
    assert argv_problem(call) is not None


def test_the_read_only_get_predicate_accepts_the_one_literal_shape() -> None:
    (call,) = calls_in(
        "run_subprocess(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', path])"
    )
    assert argv_problem(call) is None


def test_the_updater_sits_below_turn_policy_and_the_forge_queue_and_team_layers() -> None:
    contracts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["importlinter"]["contracts"]
    layers = next(c["layers"] for c in contracts if c["name"] == "Layered architecture")
    index = layers.index
    assert index("turn_policy") < index("self_update") < index("self_update_github")
    assert index("self_update_github") < index("deploy_refresh")
    assert index("self_update") < index("deploy_restart")
    for above in ("forge_gh", "teams", "queue"):
        assert index(above) < index("self_update")
    for below in ("roots", "store", "process_runner"):
        assert index("self_update_github") < index(below)


def test_the_argv_enumeration_names_the_github_module_as_a_second_adapter() -> None:
    assert ADAPTER_MODULES == frozenset({"alan/forge_gh.py", GITHUB})
    assert RECORDED_ARGV_MODULES == frozenset({"alan/branch_protection_compat.py"})
