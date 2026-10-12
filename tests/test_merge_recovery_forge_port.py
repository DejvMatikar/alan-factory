from __future__ import annotations

from typing import Any, cast

import pytest

from alan import merge_recovery
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import ApiResult
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_branch, _pr_state_paths
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_TARGET = DEFAULT_FORGE_TARGET


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _recovery_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "c" * 40)
    monkeypatch.setattr(_pr_state_paths, "save_system_head", lambda *_a, **_k: None)
    monkeypatch.setattr(merge_recovery, "_audit_recovery", lambda *_a, **_k: None)


@pytest.mark.parametrize(
    ("attempt", "updated"),
    [
        (api_result(), True),
        (api_result(returncode=1, stderr="boom"), False),
        (api_result(returncode=1, stderr="merge conflict"), False),
    ],
    ids=["updated", "failed", "conflict"],
)
def test_behind_plus_transient_recovery_reports_the_branch_update_result(
    forge: ForgeScript, attempt: ApiResult, updated: bool
) -> None:
    forge.queue("request_branch_update", attempt)
    result = merge_recovery._recover_behind_plus_transient(42, "task-x", _TARGET)
    assert result[0] is updated
    assert result == (updated, MergeBlockClass.BEHIND_PLUS_TRANSIENT, None)
    assert forge.calls("request_branch_update") == [((42,), {"forge_target": _TARGET})]


def test_measured_baseline_recovery_still_reaches_the_outcome_helper_not_the_port(
    forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome",
        lambda *_a, **_k: _pr_branch.BranchUpdateOutcome.CONFLICT,
    )
    result = merge_recovery._recover_stale_measured_baseline(42, "task-x", _TARGET)
    assert result == (False, MergeBlockClass.STALE_MEASURED_BASELINE_CONFLICT, None)
    assert forge.fake.calls == []


def test_attempt_mechanical_recovery_rejects_a_positional_forge_target() -> None:
    recover = cast(Any, merge_recovery.attempt_mechanical_recovery)
    with pytest.raises(TypeError):
        recover(42, "task-x", None, (), _TARGET)


def test_attempt_mechanical_recovery_requires_the_forge_target() -> None:
    recover = cast(Any, merge_recovery.attempt_mechanical_recovery)
    with pytest.raises(TypeError):
        recover(42, "task-x", None)
