from __future__ import annotations

from pathlib import Path

from tests.dispatch_provenance_gate import (
    find_missing_provenance_supply,
    find_provenance_mutation_writes,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"

_IMPORT_ALIAS = "from alan import workspace as workspace_mod\n"
_IMPORT_BARE_CREATE = "from alan.workspace import create\n"
_IMPORT_UNALIASED_DOTTED = "import alan.workspace\n"


def test_reference_passed_to_threadpool_without_provenance_is_flagged() -> None:
    source = _IMPORT_ALIAS + 'run_in_threadpool(workspace_mod.create, "t", "d")\n'
    assert len(find_missing_provenance_supply(source, "snippet.py")) == 1


def test_reference_passed_to_threadpool_with_provenance_is_not_flagged() -> None:
    source = _IMPORT_ALIAS + 'run_in_threadpool(workspace_mod.create, "t", "d", provenance=X)\n'
    assert find_missing_provenance_supply(source, "snippet.py") == []


def test_direct_call_missing_provenance_is_flagged() -> None:
    source = _IMPORT_ALIAS + 'workspace_mod.create("t", "d")\n'
    assert len(find_missing_provenance_supply(source, "snippet.py")) == 1


def test_direct_call_with_provenance_is_not_flagged() -> None:
    source = _IMPORT_ALIAS + 'workspace_mod.create("t", "d", provenance=X)\n'
    assert find_missing_provenance_supply(source, "snippet.py") == []


def test_bare_name_import_missing_provenance_is_flagged() -> None:
    source = _IMPORT_BARE_CREATE + 'create("t", "d")\n'
    assert len(find_missing_provenance_supply(source, "snippet.py")) == 1


def test_bare_name_import_with_provenance_is_not_flagged() -> None:
    source = _IMPORT_BARE_CREATE + 'create("t", "d", provenance=X)\n'
    assert find_missing_provenance_supply(source, "snippet.py") == []


def test_unaliased_dotted_import_missing_provenance_is_flagged() -> None:
    source = _IMPORT_UNALIASED_DOTTED + 'alan.workspace.create("t", "d")\n'
    assert len(find_missing_provenance_supply(source, "snippet.py")) == 1


def test_unaliased_dotted_import_with_provenance_is_not_flagged() -> None:
    source = _IMPORT_UNALIASED_DOTTED + 'alan.workspace.create("t", "d", provenance=X)\n'
    assert find_missing_provenance_supply(source, "snippet.py") == []


def test_unrelated_create_attribute_without_workspace_import_is_not_flagged() -> None:
    source = "import alan.project as project_mod\nproject_mod.create(title)\n"
    assert find_missing_provenance_supply(source, "snippet.py") == []


def test_dict_literal_writing_dispatch_provenance_is_flagged() -> None:
    source = 'x.model_copy(update={"dispatch_provenance": X})\n'
    assert len(find_provenance_mutation_writes(source, "snippet.py")) == 1


def test_subscript_assignment_writing_dispatch_provenance_is_flagged() -> None:
    source = 'update["dispatch_provenance"] = x\n'
    assert len(find_provenance_mutation_writes(source, "snippet.py")) == 1


def test_dict_literal_writing_unrelated_field_is_not_flagged() -> None:
    source = 'x.model_copy(update={"description": X})\n'
    assert find_provenance_mutation_writes(source, "snippet.py") == []


def test_call_keyword_writing_dispatch_provenance_outside_exemption_is_flagged() -> None:
    source = "SomeModel(dispatch_provenance=X)\n"
    assert len(find_provenance_mutation_writes(source, "somewhere.py")) == 1


def test_call_keyword_writing_dispatch_provenance_inside_meta_exemption_is_not_flagged() -> None:
    source = "Meta(dispatch_provenance=X)\n"
    assert find_provenance_mutation_writes(source, "alan/workspace.py") == []


def _alan_python_files() -> list[Path]:
    return sorted(_ALAN_SRC_ROOT.rglob("*.py"))


def test_real_tree_has_no_missing_provenance_supply() -> None:
    violations = [
        v
        for path in _alan_python_files()
        for v in find_missing_provenance_supply(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO_ROOT).as_posix()
        )
    ]
    assert violations == []


def test_real_tree_has_no_provenance_mutation_writes() -> None:
    violations = [
        v
        for path in _alan_python_files()
        for v in find_provenance_mutation_writes(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO_ROOT).as_posix()
        )
    ]
    assert violations == []
