from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.teams import _pr_branch, _pr_merge_post_update, _pr_state_paths
from alan.teams.pr_merge import PrMergeOutcome


def _prior_handover(task_id: str, *, pr_number: int = 42, iteration: int = 3) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_prior",
        handover_type="forward",
        task_id=task_id,
        from_team="pr_merge",
        from_invocation_id="inv_prior",
        to_team_suggestions=[],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="prior synthesis",
        pr_number=pr_number,
        dependency_bot_authorized=False,
    )


def _outcome(
    *, pr_number: int | None = 42, reason: str = "timeout: not settling",
) -> PrMergeOutcome:
    return PrMergeOutcome(kind="timeout", pr_number=pr_number, reason=reason)


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def test_is_system_authored_head_false_when_no_marker() -> None:
    assert _pr_merge_post_update.is_system_authored_head(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET
    ) is False


def test_is_system_authored_head_true_when_marker_matches_and_not_forwarded() -> None:
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    assert _pr_merge_post_update.is_system_authored_head(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET
    ) is True


def test_is_system_authored_head_false_when_sha_mismatch() -> None:
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    assert _pr_merge_post_update.is_system_authored_head(
        42, "sha-2", forge_target=DEFAULT_FORGE_TARGET
    ) is False


def test_is_system_authored_head_false_when_already_forwarded() -> None:
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    _pr_state_paths.mark_system_head_forwarded(42, DEFAULT_FORGE_TARGET)
    assert _pr_merge_post_update.is_system_authored_head(
        42, "sha-1", forge_target=DEFAULT_FORGE_TARGET
    ) is False


def test_forward_if_system_head_returns_none_when_prior_is_none(
    tmp_path: Path,
) -> None:
    ws = workspace_mod.create("pmpu-no-prior", "d", root=tmp_path / "ws")
    result = _pr_merge_post_update.forward_if_system_head(ws, None, "inv_1", _outcome())
    assert result is None


def test_forward_if_system_head_returns_none_when_outcome_pr_number_missing(
    tmp_path: Path,
) -> None:
    ws = workspace_mod.create("pmpu-no-pr", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    result = _pr_merge_post_update.forward_if_system_head(
        ws, prior, "inv_1", _outcome(pr_number=None)
    )
    assert result is None


def test_forward_if_system_head_returns_none_when_head_sha_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-fetch-fails", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is None


def test_forward_if_system_head_returns_none_when_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-no-marker", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is None


def test_forward_if_system_head_returns_none_when_marker_sha_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-stale-marker", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(42, "sha-old", "behind_plus_transient", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is None


def test_forward_if_system_head_returns_none_when_already_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-already-fwd", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    _pr_state_paths.mark_system_head_forwarded(42, DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is None


def test_forward_if_system_head_returns_handover_and_marks_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-happy-path", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id, iteration=5)
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is not None
    assert result.handover_type == "forward"
    assert result.to_team_suggestions == ["pr_merge"]
    assert result.from_team == "pr_merge"
    assert result.from_invocation_id == "inv_1"
    assert result.pr_number == 42
    assert result.iteration == 6
    assert result.revisit_of == prior.handover_id
    assert result.handover_chain_id == prior.handover_chain_id
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert marker.forwarded is True
    assert marker.head_sha == "sha-1"


def test_forward_if_system_head_full_synthesis_names_sha_and_rung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-synthesis", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(
        42, "sha-abc123", "stale_measured_baseline", DEFAULT_FORGE_TARGET
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-abc123"
    )
    outcome = _outcome(reason="timeout: a required check never settled")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", outcome)
    assert result is not None
    assert "timeout: a required check never settled" in result.full_synthesis
    assert "sha-abc123" in result.full_synthesis
    assert "stale_measured_baseline" in result.full_synthesis


def test_forward_if_system_head_carries_dependency_bot_flag_and_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.workspace import TestScenario

    ws = workspace_mod.create("pmpu-carry", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id).model_copy(
        update={
            "dependency_bot_authorized": True,
            "test_scenarios": [
                TestScenario(id="s1", description="d1", given="g", when="w", then="t")
            ],
        }
    )
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    result = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert result is not None
    assert result.dependency_bot_authorized is True
    assert [s.id for s in result.test_scenarios] == ["s1"]


def test_build_handover_forwards_to_merge_lane_when_head_is_system_authored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.teams import pr_merge as pr_merge_mod

    ws = workspace_mod.create("pmpu-integration-forward", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    result = pr_merge_mod._build_handover(ws, prior, "inv_1", _outcome())
    assert result.handover_type == "forward"
    assert result.to_team_suggestions == ["pr_merge"]


def test_build_handover_falls_back_to_await_human_when_head_not_system_authored(
    tmp_path: Path,
) -> None:
    from alan.teams import pr_merge as pr_merge_mod

    ws = workspace_mod.create("pmpu-integration-fallback", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    result = pr_merge_mod._build_handover(ws, prior, "inv_1", _outcome())
    assert result.open_questions
    assert result.open_questions[0].resolver_role == "human"


def test_forward_if_system_head_is_one_shot_per_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = workspace_mod.create("pmpu-one-shot", "d", root=tmp_path / "ws")
    prior = _prior_handover(ws.task_id)
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", DEFAULT_FORGE_TARGET)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-1")
    first = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_1", _outcome())
    assert first is not None
    second = _pr_merge_post_update.forward_if_system_head(ws, prior, "inv_2", _outcome())
    assert second is None
