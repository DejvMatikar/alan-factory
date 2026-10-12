from __future__ import annotations

from pathlib import Path

from tests.logger_level_leaks import ALLOWLIST, REMEDY, unprotected_calls, violations_in
from tests.no_comments_policy import iter_python_files

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"


def test_no_unprotected_configure_logging_calls_in_test_tree() -> None:
    violations: list[str] = []
    for rel_path in iter_python_files(_TESTS_ROOT, _REPO_ROOT):
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        violations.extend(violations_in(source, rel_path, ALLOWLIST))
    assert not violations, "\n".join(violations)


def test_logger_level_allowlist_is_empty() -> None:
    assert len(ALLOWLIST) == 0


def test_bare_call_to_main_mod_main_is_flagged() -> None:
    source = "from alan import __main__ as main_mod\n\nmain_mod.main()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1
    assert hits[0][0] == 3


def test_bare_call_to_configure_logging_via_module_alias_is_flagged() -> None:
    source = "from alan import __main__ as main_mod\n\nmain_mod._configure_logging()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_direct_function_import_call_is_flagged() -> None:
    source = "from alan.__main__ import main\n\nmain()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_aliased_direct_function_import_call_is_flagged() -> None:
    source = "from alan.__main__ import _configure_logging as configure\n\nconfigure()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_module_import_with_dotted_alias_is_flagged() -> None:
    source = "import alan.__main__ as main_mod\n\nmain_mod.main()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_unaliased_dotted_import_with_nested_attribute_call_is_flagged() -> None:
    source = "import alan.__main__\n\nalan.__main__.main()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1
    assert hits[0][0] == 3


def test_unaliased_dotted_import_configure_logging_call_is_flagged() -> None:
    source = "import alan.__main__\n\nalan.__main__._configure_logging()\n"
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_call_inside_try_finally_is_not_flagged() -> None:
    source = (
        "from alan import __main__ as main_mod\n\n"
        "try:\n"
        "    main_mod.main()\n"
        "finally:\n"
        "    pass\n"
    )
    assert unprotected_calls(source) == []


def test_call_inside_bare_try_except_without_finally_is_still_flagged() -> None:
    source = (
        "from alan import __main__ as main_mod\n\n"
        "try:\n"
        "    main_mod.main()\n"
        "except Exception:\n"
        "    pass\n"
    )
    hits = unprotected_calls(source)
    assert len(hits) == 1


def test_call_in_except_handler_with_finally_is_not_flagged() -> None:
    source = (
        "from alan import __main__ as main_mod\n\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    main_mod.main()\n"
        "finally:\n"
        "    pass\n"
    )
    assert unprotected_calls(source) == []


def test_unrelated_function_call_is_not_flagged() -> None:
    source = "import uvicorn\n\nuvicorn.run('app', host='x', port=1)\n"
    assert unprotected_calls(source) == []


def test_attribute_call_via_unbound_name_is_not_flagged() -> None:
    source = "main_mod = object()\n\nmain_mod.main()\n"
    assert unprotected_calls(source) == []


def test_call_to_differently_named_function_via_module_alias_is_not_flagged() -> None:
    source = "from alan import __main__ as main_mod\n\nmain_mod.resolve_host()\n"
    assert unprotected_calls(source) == []


def test_remedy_states_the_gate_is_scoped_to_this_one_call_pair() -> None:
    assert "catches only this one call pair" in REMEDY
    assert "not order-dependence in general" in REMEDY


def test_module_carries_no_repo_structural_marker() -> None:
    source = (_REPO_ROOT / "tests" / "logger_level_leaks.py").read_text(encoding="utf-8")
    assert "repo_structural" not in source


def test_violations_in_fails_closed_on_unlisted_module() -> None:
    source = "from alan import __main__ as main_mod\n\nmain_mod.main()\n"
    violations = violations_in(source, "fake/module.py", frozenset())
    assert violations
    assert "fake/module.py:3" in violations[0]


def test_violations_in_passes_when_module_is_allowlisted() -> None:
    source = "from alan import __main__ as main_mod\n\nmain_mod.main()\n"
    violations = violations_in(source, "fake/module.py", frozenset({"fake/module.py"}))
    assert violations == []


def test_allowlist_entries_reference_existing_files() -> None:
    for rel_path in ALLOWLIST:
        assert (_REPO_ROOT / rel_path).is_file(), rel_path
