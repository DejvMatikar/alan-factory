from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan.roots import STATE_ROOT_SUBDIRS

REPO_ROOT = Path(__file__).resolve().parents[1]
_ALAN_DIR = REPO_ROOT / "alan"
_OTHER_REPO_PROFILE = Path(__file__).parent / "fixtures" / "default_profile_other_repo.yaml"
_BOUND_NAMES = frozenset({"_DEFAULT", "DEFAULT_FORGE_TARGET"})
_ENGINE_CONFIG_MODULE = "alan.engine_config"
_PROBE = (
    "import importlib, json, sys\n"
    "module = importlib.import_module(sys.argv[1])\n"
    "print(json.dumps({name: getattr(module, name).repo.slug for name in sys.argv[2:]}))\n"
)


def _module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    for node in body:
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for field in ("body", "orelse", "finalbody"):
                yield from _module_level(getattr(node, field, []))
            for handler in getattr(node, "handlers", []):
                yield from _module_level(handler.body)


def _dotted_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(relative.parts)


def _imported_bindings(node: ast.stmt) -> list[str]:
    if not isinstance(node, ast.ImportFrom) or node.module != _ENGINE_CONFIG_MODULE:
        return []
    return [alias.asname or alias.name for alias in node.names if alias.name in _BOUND_NAMES]


def _assigned_bindings(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id] if node.target.id in _BOUND_NAMES else []
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name) and t.id in _BOUND_NAMES]
    return []


def _bindings_in(path: Path) -> list[str]:
    module = _dotted_name(path)
    found: list[str] = []
    for node in _module_level(ast.parse(path.read_text()).body):
        found.extend(_imported_bindings(node))
        if module == _ENGINE_CONFIG_MODULE:
            found.extend(_assigned_bindings(node))
    return found


def _all_bindings() -> dict[str, list[str]]:
    by_module: dict[str, list[str]] = {}
    for path in sorted(_ALAN_DIR.rglob("*.py")):
        names = _bindings_in(path)
        if names:
            by_module[_dotted_name(path)] = names
    return by_module


_BINDINGS = _all_bindings()


def _child_env(home: Path, profile: Path = _OTHER_REPO_PROFILE) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ALAN_", "COV_CORE_")) and key != "PYTHONHASHSEED"
    }
    env["HOME"] = str(home)
    for token in STATE_ROOT_SUBDIRS:
        root = home / token.lower()
        root.mkdir()
        env[token] = str(root)
    shutil.copyfile(profile, Path(env["ALAN_ENGINES_ROOT"]) / "default_profile.yaml")
    return env


def _slugs_in_fresh_process(tmp_path: Path, module: str, names: list[str]) -> dict[str, str]:
    try:
        done = subprocess.run(
            [sys.executable, "-c", _PROBE, module, *names],
            cwd=REPO_ROOT,
            env=_child_env(tmp_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(exc.stderr)
    slugs: dict[str, str] = json.loads(done.stdout.splitlines()[-1])
    return slugs


def test_a_malformed_profile_fails_a_fresh_process_at_import_naming_the_file_and_the_problem(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("owner: acme\nrepo: widget\nposture: CANARYVALUE77\n")
    home = tmp_path / "home"
    home.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", "import alan.engine_config"],
        cwd=REPO_ROOT,
        env=_child_env(home, malformed),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    named = "DefaultProfileError: alan_engines_root/default_profile.yaml: posture: Input should be"
    assert named in done.stderr
    assert "CANARYVALUE77" not in done.stderr
    assert "CANARYVALUE77" not in done.stdout


def test_the_walk_finds_the_engine_config_definitions_and_the_multi_line_import() -> None:
    assert sorted(_BINDINGS[_ENGINE_CONFIG_MODULE]) == ["DEFAULT_FORGE_TARGET", "_DEFAULT"]
    assert sorted(_BINDINGS["alan.teams.triage_pregate"]) == [
        "DEFAULT_FORGE_TARGET",
        "_ENGINE_DEFAULT",
    ]


def test_the_walk_finds_an_aliased_import() -> None:
    assert _BINDINGS["alan.router"] == ["_ENGINE_DEFAULT"]


def test_the_walk_finds_every_module_level_binder() -> None:
    assert len(_BINDINGS) >= 25


def test_the_walk_ignores_bindings_made_inside_functions(tmp_path: Path) -> None:
    source = tmp_path / "late.py"
    source.write_text(
        "def late():\n    from alan.engine_config import _DEFAULT\n    return _DEFAULT\n"
    )
    found = [
        name
        for node in _module_level(ast.parse(source.read_text()).body)
        for name in _imported_bindings(node)
    ]
    assert found == []


def test_the_walk_follows_module_level_conditional_imports(tmp_path: Path) -> None:
    source = tmp_path / "guarded.py"
    source.write_text(
        "try:\n    from alan.engine_config import DEFAULT_FORGE_TARGET as T\n"
        "except ImportError:\n    from alan.engine_config import _DEFAULT as D\n"
        "else:\n    pass\n"
    )
    found = [
        name
        for node in _module_level(ast.parse(source.read_text()).body)
        for name in _imported_bindings(node)
    ]
    assert found == ["T", "D"]


@pytest.mark.parametrize(
    ("module", "names"), sorted(_BINDINGS.items()), ids=[m for m, _ in sorted(_BINDINGS.items())]
)
def test_every_module_level_binding_resolves_to_the_profile_repository_in_a_fresh_process(
    tmp_path: Path, module: str, names: list[str]
) -> None:
    slugs = _slugs_in_fresh_process(tmp_path, module, names)
    assert slugs == {name: "acme-labs/widget-app" for name in names}
