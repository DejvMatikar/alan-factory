from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.judge_eval import (
    CASE_FILENAME,
    JUDGE_COVERAGE,
    ROLES_DIRNAME,
    TASK_FILENAME,
    GoldenSuiteError,
    _load_case,
)
from alan.teams._static_pipeline import _coverage_block, build_role_user_text
from alan.teams.role_output_fence import role_fence
from alan.teams.verification import ROLES

_CASE_NAME = "case_03"
_FIXED_NONCE = "0123456789abcdef"
_ONE_ROLE = {"01_diff_reviewer.txt": "reviewer out"}


def _make_case(
    tmp_path: Path,
    roles: dict[str, str] | None = None,
    task: str | None = "the task",
) -> Path:
    case_dir = tmp_path / _CASE_NAME
    case_dir.mkdir()
    payload = {
        "situation": "gate_park",
        "intended_verdict": "APPROVE",
        "must_not_flip": False,
        "citation": "cite",
    }
    (case_dir / CASE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    if task is not None:
        (case_dir / TASK_FILENAME).write_text(task, encoding="utf-8")
    if roles is not None:
        roles_dir = case_dir / ROLES_DIRNAME
        roles_dir.mkdir()
        for filename, text in roles.items():
            (roles_dir / filename).write_text(text, encoding="utf-8")
    return case_dir


def _fail_reads_of(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    original = Path.read_text

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == filename:
            raise OSError("boom")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _boom)


def test_missing_task_file_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, _ONE_ROLE, task=None)
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: task.txt: missing"):
        _load_case(case_dir)


def test_empty_task_file_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, _ONE_ROLE, task=" \n\t")
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: task.txt: empty"):
        _load_case(case_dir)


def test_unreadable_task_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = _make_case(tmp_path, _ONE_ROLE)
    _fail_reads_of(monkeypatch, TASK_FILENAME)
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: task.txt: unreadable"):
        _load_case(case_dir)


def test_undecodable_task_file_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, _ONE_ROLE)
    (case_dir / TASK_FILENAME).write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: task.txt: unreadable"):
        _load_case(case_dir)


def test_missing_roles_folder_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, roles=None)
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/: missing"):
        _load_case(case_dir)


def test_empty_roles_folder_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, roles={})
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/: empty"):
        _load_case(case_dir)


def test_empty_role_file_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"01_x.txt": "  \n"})
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/01_x.txt: empty"):
        _load_case(case_dir)


def test_unreadable_role_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = _make_case(tmp_path, {"01_x.txt": "text"})
    _fail_reads_of(monkeypatch, "01_x.txt")
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/01_x.txt: unreadable"):
        _load_case(case_dir)


@pytest.mark.parametrize(
    "filename",
    [
        "diff_reviewer.txt",
        "01diff_reviewer.txt",
        "01_Diff.txt",
        "01_dif-f.txt",
        "01_diff.md",
        "01_diff.txt.bak",
        "01_.txt",
        "01_9x.txt",
        "1a_x.txt",
        "-1_x.txt",
        "٣_x.txt",
        ".txt",
        ".DS_Store",
    ],
)
def test_role_file_name_outside_the_numbered_pattern_raises(
    tmp_path: Path, filename: str
) -> None:
    case_dir = _make_case(tmp_path, {"01_a.txt": "a", filename: "text"})
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/.*must match"):
        _load_case(case_dir)


def test_non_file_entry_with_a_valid_name_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"01_a.txt": "a"})
    (case_dir / ROLES_DIRNAME / "02_b.txt").mkdir()
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/02_b.txt: not a file"):
        _load_case(case_dir)


def test_subdirectory_entry_raises(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"01_a.txt": "a"})
    (case_dir / ROLES_DIRNAME / "nested").mkdir()
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/nested"):
        _load_case(case_dir)


def test_two_role_files_sharing_a_numeric_prefix_raise(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"01_a.txt": "a", "01_b.txt": "b"})
    with pytest.raises(GoldenSuiteError, match=f"{_CASE_NAME}: roles/01_b.txt: duplicate prefix"):
        _load_case(case_dir)


def test_prefixes_equal_across_zero_padding_raise(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"1_a.txt": "a", "01_b.txt": "b"})
    with pytest.raises(GoldenSuiteError, match="duplicate prefix"):
        _load_case(case_dir)


def test_role_files_are_ordered_numerically_not_lexicographically(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"2_b.txt": "B", "10_c.txt": "C", "1_a.txt": "A"})
    case = _load_case(case_dir)
    assert case.roles == (("a", "A"), ("b", "B"), ("c", "C"))


def test_role_name_is_whatever_follows_the_prefix(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, {"07_scenario_runner2.txt": "out"})
    assert _load_case(case_dir).roles == (("scenario_runner2", "out"),)


def test_case_context_is_one_rendered_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.teams.role_output_fence.secrets.token_hex", lambda _n: _FIXED_NONCE)
    case_dir = _make_case(tmp_path, {"01_a.txt": "A out", "02_b.txt": "B out"}, task="task body")
    case = _load_case(case_dir)
    assert isinstance(case.context, str)
    assert case.context == build_role_user_text("task body", list(case.roles), JUDGE_COVERAGE)
    assert case.context.startswith("task body\n\n## COVERAGE\n\n" + JUDGE_COVERAGE)
    assert role_fence("a", "A out", _FIXED_NONCE) in case.context
    assert role_fence("b", "B out", _FIXED_NONCE) in case.context


def test_case_roles_hold_the_whole_file_text(tmp_path: Path) -> None:
    padded = "\n  padded role text \n\n"
    case_dir = _make_case(tmp_path, {"01_a.txt": padded})
    assert _load_case(case_dir).roles == (("a", padded),)


def test_judge_coverage_is_the_verification_teams_own_judge_coverage() -> None:
    assert JUDGE_COVERAGE == _coverage_block("judge", ROLES)
    assert "diff_reviewer" in JUDGE_COVERAGE
    assert "scenario_runner" in JUDGE_COVERAGE
