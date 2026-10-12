from __future__ import annotations

from pathlib import Path

from tests.gh_guard import (
    GH_READ_REMEDY,
    GhDecision,
    StubbedRead,
    append_baseline_key,
    baseline_key,
    gh_read_ratchet_failure,
)

_KEY = "tests/test_x.py::test_y"
_READ = StubbedRead(
    GhDecision(True, ("pr", "list"), "recognised read-only subcommand", ("--json",)),
    _KEY,
    "MainThread",
)
_THREAD_READ = StubbedRead(
    GhDecision(True, ("pr", "diff"), "recognised read-only subcommand"),
    _KEY,
    "AnyIO worker thread",
)


def test_unlisted_test_that_stubbed_a_read_fails_with_remedy() -> None:
    message = gh_read_ratchet_failure(_KEY, [_READ, _THREAD_READ], listed=False, expected=False)
    assert message is not None
    assert message.startswith(_KEY)
    assert "2 call(s)" in message
    assert "gh pr list [MainThread]" in message
    assert "gh pr diff [AnyIO worker thread]" in message
    assert "alan.collision" in message
    assert "check_collision" in message
    assert "alan.predispatch_scan.list_open_prs" in message
    assert "alan.process_runner" in message
    assert "run_subprocess" in message
    assert "tests/gh_read_baseline.py" in message
    assert "expect_gh_read_stub" in message


def test_listed_test_that_stubbed_a_read_passes() -> None:
    assert gh_read_ratchet_failure(_KEY, [_READ], listed=True, expected=False) is None


def test_expected_stub_passes_without_a_baseline_entry() -> None:
    assert gh_read_ratchet_failure(_KEY, [_READ], listed=False, expected=True) is None


def test_expected_stub_that_never_happened_fails() -> None:
    message = gh_read_ratchet_failure(_KEY, [], listed=False, expected=True)
    assert message is not None
    assert _KEY in message
    assert "expect_gh_read_stub" in message


def test_no_reads_is_silent_for_an_unlisted_test() -> None:
    assert gh_read_ratchet_failure(_KEY, [], listed=False, expected=False) is None


def test_remedy_never_advises_adding_to_the_baseline() -> None:
    assert "Never add an entry to the baseline" in GH_READ_REMEDY


def test_baseline_key_strips_parametrization_suffix() -> None:
    assert baseline_key("tests/t.py::C::test_a[x-[y]]") == "tests/t.py::C::test_a"
    assert baseline_key("tests/t.py::test_plain") == "tests/t.py::test_plain"


def test_append_baseline_key_appends_one_line_per_call(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "gh-reads.txt"
    append_baseline_key(target, "tests/a.py::test_one")
    append_baseline_key(target, "tests/b.py::test_two")
    assert target.read_text(encoding="utf-8") == "tests/a.py::test_one\ntests/b.py::test_two\n"
