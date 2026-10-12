from __future__ import annotations

from pathlib import Path

from tests.merge_gate_supply_gate import find_missing_merge_gate_supply

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"

_IMPORT_ALIAS = "from alan import dispatch as dispatch_mod\n"
_IMPORT_BARE_CALL = "from alan.dispatch import dispatch_team_core\n"
_IMPORT_UNALIASED_DOTTED = "import alan.dispatch\n"


def test_reference_passed_to_threadpool_without_merge_gate_is_flagged() -> None:
    source = _IMPORT_ALIAS + "run_in_threadpool(dispatch_mod.dispatch_team_core, a=1)\n"
    assert len(find_missing_merge_gate_supply(source, "snippet.py")) == 1


def test_reference_passed_to_threadpool_with_merge_gate_is_not_flagged() -> None:
    source = (
        _IMPORT_ALIAS
        + "run_in_threadpool(dispatch_mod.dispatch_team_core, a=1, merge_gate=X)\n"
    )
    assert find_missing_merge_gate_supply(source, "snippet.py") == []


def test_direct_call_missing_merge_gate_is_flagged() -> None:
    source = _IMPORT_ALIAS + "dispatch_mod.dispatch_team_core(a=1)\n"
    assert len(find_missing_merge_gate_supply(source, "snippet.py")) == 1


def test_direct_call_with_merge_gate_is_not_flagged() -> None:
    source = _IMPORT_ALIAS + "dispatch_mod.dispatch_team_core(a=1, merge_gate=X)\n"
    assert find_missing_merge_gate_supply(source, "snippet.py") == []


def test_bare_name_import_missing_merge_gate_is_flagged() -> None:
    source = _IMPORT_BARE_CALL + "dispatch_team_core(a=1)\n"
    assert len(find_missing_merge_gate_supply(source, "snippet.py")) == 1


def test_bare_name_import_with_merge_gate_is_not_flagged() -> None:
    source = _IMPORT_BARE_CALL + "dispatch_team_core(a=1, merge_gate=X)\n"
    assert find_missing_merge_gate_supply(source, "snippet.py") == []


def test_unaliased_dotted_import_missing_merge_gate_is_flagged() -> None:
    source = _IMPORT_UNALIASED_DOTTED + "alan.dispatch.dispatch_team_core(a=1)\n"
    assert len(find_missing_merge_gate_supply(source, "snippet.py")) == 1


def test_unaliased_dotted_import_with_merge_gate_is_not_flagged() -> None:
    source = _IMPORT_UNALIASED_DOTTED + "alan.dispatch.dispatch_team_core(a=1, merge_gate=X)\n"
    assert find_missing_merge_gate_supply(source, "snippet.py") == []


def test_unrelated_call_attribute_without_dispatch_import_is_not_flagged() -> None:
    source = "import alan.project as project_mod\nproject_mod.dispatch_team_core(a=1)\n"
    assert find_missing_merge_gate_supply(source, "snippet.py") == []


def _alan_python_files() -> list[Path]:
    return sorted(_ALAN_SRC_ROOT.rglob("*.py"))


def test_real_tree_has_no_missing_merge_gate_supply() -> None:
    violations = [
        v
        for path in _alan_python_files()
        for v in find_missing_merge_gate_supply(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO_ROOT).as_posix()
        )
    ]
    assert violations == []
