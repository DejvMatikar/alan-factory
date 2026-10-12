from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from alan import default_profile, roots
from alan.default_profile import Source
from tests.conftest import _SHARED_TEST_DIR  # pyright: ignore[reportPrivateUsage]

_CONFTEST = Path(__file__).with_name("conftest.py")
_TEST_PROFILE = Path(__file__).parent / "fixtures" / "default_profile_test.yaml"
_ALLOWED_BEFORE_THE_PROFILE_IS_WRITTEN = {"alan.roots", "tests.gh_guard", "tests.gh_read_baseline"}


def _imported_modules(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return [node.module]
    return []


def _profile_copy_line(tree: ast.Module) -> int:
    lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "shutil.copyfile"
        and "default_profile" in ast.unparse(node.value)
    ]
    assert len(lines) == 1
    return lines[0]


def _module_imports(tree: ast.Module) -> list[tuple[int, str]]:
    return [(node.lineno, name) for node in tree.body for name in _imported_modules(node)]


def test_the_default_profile_the_process_resolved_is_the_test_profile() -> None:
    installed = default_profile.resolve(roots.engines_root(), Path("absent-owner-profile.yaml"))
    assert installed.source is Source.profile
    resolved = default_profile.resolved()
    assert resolved.path is not None
    assert resolved.path.read_bytes() == _TEST_PROFILE.read_bytes()


def test_the_session_resolves_as_the_owner_install_unless_a_test_says_otherwise() -> None:
    assert default_profile.resolved().source is Source.owner


def test_the_test_profile_was_written_into_the_redirected_engines_root() -> None:
    resolved = default_profile.resolved()
    assert resolved.path == _SHARED_TEST_DIR / "engines" / "default_profile.yaml"


def test_the_process_resolved_the_default_from_the_redirected_root_not_the_real_one() -> None:
    real_state = Path.home() / ".alan"
    resolved = default_profile.resolved()
    assert resolved.path is not None
    assert not resolved.path.is_relative_to(real_state)


@pytest.mark.parametrize("token", sorted(roots.STATE_ROOT_SUBDIRS))
def test_no_state_isolation_root_points_into_the_real_alan_state_tree(token: str) -> None:
    assert not Path(os.environ[token]).is_relative_to(Path.home() / ".alan")


def test_conftest_writes_the_test_profile_before_importing_any_resolving_module() -> None:
    tree = ast.parse(_CONFTEST.read_text())
    copy_line = _profile_copy_line(tree)
    early = {name for line, name in _module_imports(tree) if line < copy_line}
    late = {name for line, name in _module_imports(tree) if line > copy_line}
    resolving = {name for name in early if name.startswith(("alan", "tests.fakes"))}
    assert resolving == {"alan.roots"}
    assert early >= {"alan.roots", "tests.gh_guard"}
    assert {"alan.engine_config", "alan.config_files", "tests.fakes.forge"} <= late


def test_conftest_redirects_every_state_root_before_writing_the_test_profile() -> None:
    tree = ast.parse(_CONFTEST.read_text())
    copy_line = _profile_copy_line(tree)
    redirect_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.For) and "STATE_ROOT_SUBDIRS" in ast.unparse(node.iter)
    ]
    assert len(redirect_lines) == 1
    assert redirect_lines[0] < copy_line


def test_every_module_imported_before_the_profile_is_written_is_leaf_level() -> None:
    tree = ast.parse(_CONFTEST.read_text())
    copy_line = _profile_copy_line(tree)
    early = {name for line, name in _module_imports(tree) if line < copy_line}
    project_imports = {name for name in early if name.startswith(("alan", "tests"))}
    assert project_imports <= _ALLOWED_BEFORE_THE_PROFILE_IS_WRITTEN
