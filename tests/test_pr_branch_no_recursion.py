from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import active_forge, set_active_forge
from alan.forge_gh import GhForge
from alan.teams import _pr_branch
from tests._pr_branch_support import _audit_entries

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",  # noqa: S106  # nosec B106
)


class _Counter:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""

    def __call__(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=self.stderr,
        )


@pytest.fixture
def real_adapter() -> Iterator[GhForge]:
    saved = active_forge()
    adapter = GhForge()
    set_active_forge(adapter)
    try:
        yield adapter
    finally:
        set_active_forge(saved)


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Counter:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fake = _Counter()
    monkeypatch.setattr(process_runner, "run_subprocess", fake)
    return fake


@pytest.mark.usefixtures("real_adapter")
def test_update_branch_success_ends_in_one_process_call_and_one_audit_row(
    counter: _Counter, tmp_path: Path,
) -> None:
    outcome = _pr_branch.update_pr_branch_outcome(7, "task-x", forge_target=_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.UPDATED
    assert counter.calls == [["gh", "pr", "update-branch", "7", "--repo", "acme/widget"]]
    assert [e["action"] for e in _audit_entries(tmp_path)] == ["pr_update_branch_ok"]


@pytest.mark.usefixtures("real_adapter")
def test_update_branch_conflict_ends_in_one_process_call_and_one_audit_row(
    counter: _Counter, tmp_path: Path,
) -> None:
    counter.returncode = 1
    counter.stderr = "not mergeable: merge conflict detected"
    outcome = _pr_branch.update_pr_branch_outcome(7, "task-x", forge_target=_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.CONFLICT
    assert len(counter.calls) == 1
    assert [e["action"] for e in _audit_entries(tmp_path)] == ["pr_update_branch_failed"]


@pytest.mark.usefixtures("real_adapter")
def test_update_branch_failure_ends_in_one_process_call_and_one_audit_row(
    counter: _Counter, tmp_path: Path,
) -> None:
    counter.returncode = 1
    counter.stderr = "permission denied"
    outcome = _pr_branch.update_pr_branch_outcome(7, "task-x", forge_target=_TARGET)
    assert outcome is _pr_branch.BranchUpdateOutcome.FAILED
    assert len(counter.calls) == 1
    assert [e["action"] for e in _audit_entries(tmp_path)] == ["pr_update_branch_failed"]


@pytest.mark.usefixtures("real_adapter")
def test_head_sha_ends_in_one_process_call(counter: _Counter) -> None:
    counter.stdout = "abc123\n"
    assert GhForge().head_sha(7, forge_target=_TARGET).stdout == "abc123\n"
    assert len(counter.calls) == 1


@pytest.mark.usefixtures("real_adapter")
def test_changed_files_ends_in_one_process_call(counter: _Counter) -> None:
    counter.stdout = "alan/forge.py\n"
    assert GhForge().changed_files(7, forge_target=_TARGET) == frozenset({"alan/forge.py"})
    assert len(counter.calls) == 1


@pytest.mark.usefixtures("real_adapter")
def test_flake_guard_head_sha_ends_in_one_process_call(counter: _Counter) -> None:
    counter.stdout = "abc123\n"
    assert _pr_branch.fetch_flake_guard_head_sha(7, "task-x", forge_target=_TARGET) == "abc123"
    assert len(counter.calls) == 1


@pytest.mark.usefixtures("real_adapter")
def test_fetch_diff_files_ends_in_one_process_call(counter: _Counter) -> None:
    counter.stdout = "alan/forge.py\n"
    assert _pr_branch.fetch_diff_files(7, forge_target=_TARGET) == frozenset({"alan/forge.py"})
    assert len(counter.calls) == 1
