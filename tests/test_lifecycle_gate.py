from __future__ import annotations

import ast
from pathlib import Path

from tests.dispatch_provenance_gate import _dotted_name  # pyright: ignore[reportPrivateUsage]
from tests.lifecycle_gate import (
    STATUS_WRITE_EXEMPTIONS,
    STATUS_WRITE_EXEMPTIONS_COUNT,
    find_status_write_violations,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALAN_SRC_ROOT = _REPO_ROOT / "alan"
_WORKSPACE = "alan/workspace.py"

_SEEDED_DIRECT_WRITE = (
    "def sneaky(ws):\n"
    "    meta = ws.meta.model_copy(update={'status': 'archived'})\n"
    "    _write_meta(ws.path, meta)\n"
)


def _alan_python_files() -> list[Path]:
    return sorted(_ALAN_SRC_ROOT.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _functions(source: str) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}


def test_exemption_set_first_entry_is_the_workspace_creation_birth_write() -> None:
    assert STATUS_WRITE_EXEMPTIONS[0] == (_WORKSPACE, "create")


def test_exemption_set_contents_are_pinned_by_a_count_literal() -> None:
    assert STATUS_WRITE_EXEMPTIONS_COUNT == 3
    assert len(STATUS_WRITE_EXEMPTIONS) == STATUS_WRITE_EXEMPTIONS_COUNT
    assert len(set(STATUS_WRITE_EXEMPTIONS)) == STATUS_WRITE_EXEMPTIONS_COUNT


def test_exemption_set_names_the_writer_and_the_excluded_per_task_lifecycle() -> None:
    assert STATUS_WRITE_EXEMPTIONS[1:] == (
        (_WORKSPACE, "transition"),
        (_WORKSPACE, "update_task_status"),
    )


def test_every_exempt_function_exists_in_its_file() -> None:
    for rel, function in STATUS_WRITE_EXEMPTIONS:
        assert function in _functions((_REPO_ROOT / rel).read_text(encoding="utf-8"))


def test_clause_one_without_exemptions_flags_exactly_the_exempt_functions() -> None:
    source = (_REPO_ROOT / _WORKSPACE).read_text(encoding="utf-8")
    flagged = find_status_write_violations(source, _WORKSPACE, frozenset())
    assert sorted(v.function for v in flagged) == sorted(f for _, f in STATUS_WRITE_EXEMPTIONS)


def test_seeded_direct_write_outside_the_exemption_set_is_flagged() -> None:
    violations = find_status_write_violations(_SEEDED_DIRECT_WRITE, "alan/other.py")
    assert {v.function for v in violations} == {"sneaky"}
    assert len(violations) == 2


def test_seeded_direct_write_in_an_unexempt_workspace_function_is_flagged() -> None:
    violations = find_status_write_violations(_SEEDED_DIRECT_WRITE, _WORKSPACE)
    assert len(violations) == 2


def test_seeded_direct_write_inside_the_exempt_birth_function_passes_clause_one() -> None:
    source = (
        "def create(ws):\n"
        "    meta = Meta(status='active')\n"
        "    _write_meta(ws.path, meta)\n"
    )
    assert find_status_write_violations(source, _WORKSPACE) == []
    assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_persisting_meta_without_naming_status_is_not_flagged() -> None:
    source = (
        "def update_notes(ws, notes):\n"
        "    meta = ws.meta.model_copy(update={'notes': notes})\n"
        "    _write_meta(ws.path, meta)\n"
    )
    assert find_status_write_violations(source, _WORKSPACE) == []


def test_write_meta_with_status_keyword_on_a_meta_call_is_flagged() -> None:
    source = "def rebirth(p):\n    _write_meta(p, Meta(status='paused'))\n"
    assert [v.function for v in find_status_write_violations(source, "alan/other.py")] == [
        "rebirth"
    ]


def test_module_level_write_naming_status_is_flagged_as_module() -> None:
    source = "_write_meta(p, Meta(status='paused'))\n"
    assert [v.function for v in find_status_write_violations(source, "alan/other.py")] == [
        "<module>"
    ]


def test_qualified_write_meta_call_is_flagged() -> None:
    source = "def sneaky(p):\n    workspace._write_meta(p, Meta(status='paused'))\n"
    assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_meta_copy_with_status_key_outside_any_function_is_flagged() -> None:
    source = "meta.model_copy(update={'status': 'paused'})\n"
    violations = find_status_write_violations(source, "alan/other.py")
    assert [v.function for v in violations] == ["<module>"]


def test_clause_two_resolves_a_local_dict_assigned_before_model_copy() -> None:
    source = (
        "def sneaky(ws):\n"
        "    update = {'status': 'paused'}\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_clause_two_resolves_an_annotated_local_dict() -> None:
    source = (
        "def sneaky(ws):\n"
        "    update: dict[str, object] = {'status': 'paused'}\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_clause_two_resolves_a_subscript_store_on_the_local_dict() -> None:
    source = (
        "def sneaky(ws):\n"
        "    update = {}\n"
        "    update['status'] = 'paused'\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_clause_two_ignores_a_local_dict_without_a_status_key() -> None:
    source = (
        "def notes(ws):\n"
        "    other = {'status': 'paused'}\n"
        "    update = {'notes': []}\n"
        "    other['notes'] = 1\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert find_status_write_violations(source, "alan/other.py") == []


def test_clause_two_ignores_an_assignment_to_a_different_name() -> None:
    source = (
        "def notes(ws):\n"
        "    other = {'status': 'paused'}\n"
        "    update = build()\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert find_status_write_violations(source, "alan/other.py") == []


def test_clause_two_ignores_a_model_copy_without_an_update_keyword() -> None:
    assert find_status_write_violations("ws.meta.model_copy(deep=True)\n", "alan/o.py") == []


def test_clause_two_ignores_a_subscript_load_of_a_status_key() -> None:
    source = (
        "def read(ws):\n"
        "    update = {}\n"
        "    seen = update['status']\n"
        "    return ws.meta.model_copy(update=update)\n"
    )
    assert find_status_write_violations(source, "alan/other.py") == []


def test_clause_two_ignores_a_non_meta_receiver() -> None:
    source = "def f(task):\n    return task.model_copy(update={'status': 'done'})\n"
    assert find_status_write_violations(source, "alan/other.py") == []


def test_clause_two_ignores_a_call_receiver_that_is_not_meta_shaped() -> None:
    source = "def f(x):\n    return build(x).model_copy(update={'status': 'done'})\n"
    assert find_status_write_violations(source, "alan/other.py") == []


def test_clause_two_flags_meta_named_and_meta_call_receivers() -> None:
    named = "def f(fresh_meta):\n    return fresh_meta.model_copy(update={'status': 1})\n"
    read = "def f(p):\n    return _read_meta(p).model_copy(update={'status': 1})\n"
    bare = "def f(meta):\n    return meta.model_copy(update={'status': 1})\n"
    for source in (named, read, bare):
        assert len(find_status_write_violations(source, "alan/other.py")) == 1


def test_a_lifecycle_derived_update_dict_is_not_flagged() -> None:
    source = (
        "def transition(ws, status):\n"
        "    update = {**lifecycle.transition(status), 'updated_at': now()}\n"
        "    meta = ws.meta.model_copy(update=update)\n"
        "    _write_meta(ws.path, meta)\n"
    )
    assert find_status_write_violations(source, _WORKSPACE) == []


def test_real_tree_has_no_status_writes_outside_the_exemption_set() -> None:
    violations = [
        v.message
        for path in _alan_python_files()
        for v in find_status_write_violations(path.read_text(encoding="utf-8"), _rel(path))
    ]
    assert violations == []


def test_workspace_writer_derives_its_update_from_lifecycle_transition() -> None:
    writer = _functions((_REPO_ROOT / _WORKSPACE).read_text(encoding="utf-8"))["transition"]
    called = {
        _dotted_name(node.func) for node in ast.walk(writer) if isinstance(node, ast.Call)
    }
    assert "lifecycle.transition" in called


def test_the_absorbed_status_writer_no_longer_exists_or_is_referenced() -> None:
    hits: list[str] = []
    for path in _alan_python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                getattr(node, "id", None),
                getattr(node, "attr", None),
                getattr(node, "name", None),
            )
            if "update_status" in names:
                hits.append(f"{_rel(path)}:{getattr(node, 'lineno', 0)}")
    assert hits == []
