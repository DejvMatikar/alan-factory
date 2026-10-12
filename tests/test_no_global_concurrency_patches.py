from __future__ import annotations

from pathlib import Path

from tests.no_comments_policy import iter_python_files
from tests.no_global_concurrency_patches import (
    ALLOWLIST,
    REMEDY,
    violating_calls,
    violations_in,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"


def test_no_global_concurrency_patches_in_test_tree() -> None:
    violations: list[str] = []
    for rel_path in iter_python_files(_TESTS_ROOT, _REPO_ROOT):
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        violations.extend(violations_in(source, rel_path, ALLOWLIST))
    assert not violations, "\n".join(violations)


def test_global_concurrency_allowlist_is_empty() -> None:
    assert len(ALLOWLIST) == 0


def test_class_attribute_setattr_on_condition_wait_is_flagged() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(threading.Condition, 'wait', fake_wait)\n"
    )
    hits = violating_calls(source)
    assert len(hits) == 1
    assert hits[0][0] == 3


def test_aliased_module_import_condition_patch_is_flagged() -> None:
    source = (
        "import threading as t\n\n"
        "monkeypatch.setattr(t.Condition, 'wait', fake_wait)\n"
    )
    hits = violating_calls(source)
    assert len(hits) == 1


def test_from_import_condition_patch_is_flagged() -> None:
    source = (
        "from threading import Condition\n\n"
        "monkeypatch.setattr(Condition, 'wait', fake_wait)\n"
    )
    hits = violating_calls(source)
    assert len(hits) == 1


def test_aliased_from_import_condition_patch_is_flagged() -> None:
    source = (
        "from threading import Condition as Cond\n\n"
        "monkeypatch.setattr(Cond, 'wait', fake_wait)\n"
    )
    hits = violating_calls(source)
    assert len(hits) == 1


def test_bare_setattr_call_on_stdlib_queue_class_is_flagged() -> None:
    source = (
        "import queue\n\n"
        "setattr(queue.Queue, 'put', fake_put)\n"
    )
    hits = violating_calls(source)
    assert len(hits) == 1


def test_instance_scoped_condition_patch_is_not_flagged() -> None:
    source = (
        "import threading\n\n"
        "pool = SlotPool()\n"
        "monkeypatch.setattr(pool._cond, 'wait', fake_wait)\n"
    )
    assert violating_calls(source) == []


def test_thread_join_patch_is_not_flagged() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(threading.Thread, 'join', recording_join)\n"
    )
    assert violating_calls(source) == []


def test_alan_queue_class_import_is_not_confused_with_stdlib_queue_class() -> None:
    source = (
        "from alan.queue import Queue\n\n"
        "monkeypatch.setattr(Queue, 'mark_interrupted_and_fail', explode)\n"
    )
    assert violating_calls(source) == []


def test_from_import_of_non_primitive_threading_member_is_not_bound() -> None:
    source = (
        "from threading import current_thread\n\n"
        "monkeypatch.setattr(current_thread, 'x', y)\n"
    )
    assert violating_calls(source) == []


def test_bare_module_attribute_replacement_is_not_flagged() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(threading, 'Condition', FakeCondition)\n"
    )
    assert violating_calls(source) == []


def test_from_import_receiver_with_extra_attribute_is_not_flagged() -> None:
    source = (
        "from threading import Condition\n\n"
        "monkeypatch.setattr(Condition.something, 'x', y)\n"
    )
    assert violating_calls(source) == []


def test_non_dotted_receiver_expression_is_not_flagged() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(get_condition_class(), 'wait', fake_wait)\n"
    )
    assert violating_calls(source) == []


def test_unrelated_import_is_not_flagged() -> None:
    source = "import os\n\nos.path.join('a', 'b')\n"
    assert violating_calls(source) == []


def test_unrelated_setattr_style_call_is_not_flagged() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.delattr(threading.Condition, 'wait')\n"
    )
    assert violating_calls(source) == []


def test_unrelated_method_call_is_not_flagged() -> None:
    source = "pool.reset_all({})\n"
    assert violating_calls(source) == []


def test_remedy_states_the_gate_is_scoped_to_this_one_shape() -> None:
    assert "this one pollution shape" in REMEDY
    assert "not order-dependence in general" in REMEDY
    assert "426s" in REMEDY
    assert "107s" in REMEDY


def test_violations_in_fails_closed_on_unlisted_module() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(threading.Condition, 'wait', fake_wait)\n"
    )
    violations = violations_in(source, "fake/module.py", frozenset())
    assert violations
    assert "fake/module.py:3" in violations[0]


def test_violations_in_passes_when_module_is_allowlisted() -> None:
    source = (
        "import threading\n\n"
        "monkeypatch.setattr(threading.Condition, 'wait', fake_wait)\n"
    )
    violations = violations_in(source, "fake/module.py", frozenset({"fake/module.py"}))
    assert violations == []


def test_allowlist_entries_reference_existing_files() -> None:
    for rel_path in ALLOWLIST:
        assert (_REPO_ROOT / rel_path).is_file(), rel_path


def test_module_carries_no_repo_structural_marker() -> None:
    source = (_REPO_ROOT / "tests" / "no_global_concurrency_patches.py").read_text(
        encoding="utf-8"
    )
    assert "repo_structural" not in source
