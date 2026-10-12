from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from alan.claude_cli import TurnCeilingReached
from alan.teams import _pr_handover as ph
from alan.worktree import WorktreeHandle


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)  # noqa: S603
    (repo / "README.md").write_text("seed\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "initial"], cwd=str(repo), check=True, capture_output=True
    )


@pytest.mark.parametrize(
    "path",
    [
        "__pycache__/x.pyc",
        "alan/__pycache__/x.pyc",
        ".pytest_cache/v/cache/lastfailed",
        ".mypy_cache/3.12/alan/x.json",
        ".ruff_cache/0.5/x",
        ".DS_Store",
        "alan/.DS_Store",
        "DerivedData/Build/x",
        ".xcresult/Info.plist",
        "htmlcov/index.html",
        ".coverage",
        "alan/web/node_modules/pkg/index.js",
        ".venv/lib/x.py",
        ".tox/py312/x",
    ],
)
def test_is_artifact_path_matches_known_components(path: str) -> None:
    assert ph._is_artifact_path(path) is True  # noqa: SLF001


@pytest.mark.parametrize(
    "path",
    [
        "alan/store.py",
        "tests/test_store.py",
        "docs/architecture/README.md",
        "alan/web/src/App.tsx",
        "not_venv/x.py",
        "myhtmlcov/index.html",
    ],
)
def test_is_artifact_path_leaves_non_artifact_paths_alone(path: str) -> None:
    assert ph._is_artifact_path(path) is False  # noqa: SLF001


def _porcelain_z(*entries: str) -> str:
    return "".join(f"{entry}\0" for entry in entries)


def test_partition_porcelain_empty_stream_is_clean() -> None:
    assert ph._partition_porcelain("") == ([], [])  # noqa: SLF001


def test_partition_porcelain_skips_untracked_artifact() -> None:
    stdout = _porcelain_z("?? __pycache__/x.pyc")
    staged, skipped = ph._partition_porcelain(stdout)  # noqa: SLF001
    assert staged == []
    assert skipped == ["__pycache__/x.pyc"]


def test_partition_porcelain_stages_untracked_non_artifact() -> None:
    stdout = _porcelain_z("?? alan/new_module.py")
    staged, skipped = ph._partition_porcelain(stdout)  # noqa: SLF001
    assert staged == ["alan/new_module.py"]
    assert skipped == []


def test_partition_porcelain_mixed_stages_only_the_non_artifact() -> None:
    stdout = _porcelain_z("?? alan/new_module.py", "?? .mypy_cache/x.json")
    staged, skipped = ph._partition_porcelain(stdout)  # noqa: SLF001
    assert staged == ["alan/new_module.py"]
    assert skipped == [".mypy_cache/x.json"]


@pytest.mark.parametrize("status", [" M", "M ", "R ", " D", "A ", "MM"])
def test_partition_porcelain_tracked_modification_always_staged(status: str) -> None:
    stdout = _porcelain_z(f"{status} .venv/pinned_but_tracked.py")
    staged, skipped = ph._partition_porcelain(stdout)  # noqa: SLF001
    assert staged == [".venv/pinned_but_tracked.py"]
    assert skipped == []


def test_partition_porcelain_rename_entry_consumes_two_fields() -> None:
    stdout = _porcelain_z("R  new_name.py", "old_name.py") + _porcelain_z("?? next.py")
    staged, skipped = ph._partition_porcelain(stdout)  # noqa: SLF001
    assert staged == ["new_name.py", "next.py"]
    assert skipped == []


def test_partition_porcelain_malformed_entry_raises_value_error() -> None:
    with pytest.raises(ValueError, match="malformed"):
        ph._partition_porcelain("XY\0")  # noqa: SLF001


def test_partition_porcelain_tolerates_a_stream_without_a_trailing_nul() -> None:
    staged, skipped = ph._partition_porcelain("?? foo.py")  # noqa: SLF001
    assert staged == ["foo.py"]
    assert skipped == []


def test_status_partition_none_on_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise FileNotFoundError("git missing")

    monkeypatch.setattr(subprocess, "run", boom)
    assert ph._status_partition(tmp_path) is None  # noqa: SLF001


def test_status_partition_none_on_a_successful_but_malformed_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="XY\0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ph._status_partition(tmp_path) is None  # noqa: SLF001


def test_salvage_uncommitted_wip_detail_fails_on_malformed_porcelain_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = WorktreeHandle(path=tmp_path / "wt", base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="XY\0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ph.salvage_uncommitted_wip_detail(handle, "task-1", "implementation")

    assert result.outcome == "failed"
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "wip_salvage_commit")
    assert row["ok"] is False
    assert row["staged"] == []


def test_salvage_uncommitted_wip_detail_artifact_only_tree_is_clean_with_skipped_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_text("junk\n")
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    result = ph.salvage_uncommitted_wip_detail(handle, "task-1", "implementation")

    assert result.outcome == "clean"
    assert result.skipped_count == 1
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "wip_salvage_artifact_only_clean")
    assert row["skipped_count"] == 1
    assert row["team"] == "implementation"


def test_salvage_uncommitted_wip_detail_distinguishes_from_genuinely_clean_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    result = ph.salvage_uncommitted_wip_detail(handle, "task-1", "implementation")

    assert result.outcome == "clean"
    assert result.skipped_count == 0
    assert not (tmp_path / "logs" / "impl_audit.jsonl").exists()


def test_salvage_uncommitted_wip_detail_mixed_tree_salvages_only_source_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "src.py").write_text("x = 1\n")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_text("junk\n")
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    result = ph.salvage_uncommitted_wip_detail(handle, "task-1", "implementation")

    assert result.outcome == "salvaged"
    assert result.skipped_count == 1
    show = subprocess.run(  # noqa: S603,S607
        ["git", "show", "--stat", "--format=", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    assert "src.py" in show
    assert "__pycache__" not in show
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "wip_salvage_commit")
    assert row["staged"] == ["src.py"]
    assert row["skipped_count"] == 1


def test_stage_and_commit_returns_false_and_commits_nothing_when_git_add_fails(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")

    ok = ph._stage_and_commit(handle, "implementation", ["does-not-exist.py"])  # noqa: SLF001

    assert ok is False
    log = subprocess.run(  # noqa: S603,S607
        ["git", "log", "--oneline"], cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    assert log.count("\n") == 1


def test_with_salvage_notice_interpolates_skipped_count_when_salvaged() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "salvaged", skipped_count=3)
    assert "3 untracked path(s)" in result


def test_with_salvage_notice_omits_skipped_suffix_when_zero() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "salvaged", skipped_count=0)
    assert "untracked path(s)" not in result


def test_with_salvage_notice_does_not_interpolate_for_failed_outcome() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "failed", skipped_count=2)
    assert "untracked path(s)" not in result


def test_salvage_on_ceiling_artifact_only_tree_reraises_and_parks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_text("junk\n")
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    try:
        raise TurnCeilingReached("ceiling")
    except TurnCeilingReached as exc:
        with pytest.raises(TurnCeilingReached):
            ph.salvage_on_ceiling(handle, "task-1", "implementation", exc)

    show = subprocess.run(  # noqa: S603,S607
        ["git", "log", "--oneline", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    assert show.count("\n") == 1
