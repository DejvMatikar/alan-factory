from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import (
    _forge_errors,
    _pr_merge_checks,
    _pr_merge_conflict,
    _pr_merge_escalation,
    _pr_ready,
)
from alan.teams import pr_merge as pr_merge_mod
from alan.teams.pr_merge import TEAM
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read
from tests.test_teams_pr_merge import _approve_prior, _patch_poll_intervals, _pr_view_stdout


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-conflict-task", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_mergeable_merge_states_excludes_dirty() -> None:
    assert _pr_merge_conflict.MERGEABLE_MERGE_STATES == frozenset(
        {"CLEAN", "HAS_HOOKS", "UNSTABLE", "BEHIND"}
    )


def test_gh_pr_state_and_merge_status_parses_both_fields(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(
        ("state", "mergeStateStatus"), pr_read({"state": "open", "mergeStateStatus": "dirty"}),
    )
    assert _pr_merge_conflict.gh_pr_state_and_merge_status(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == ("OPEN", "DIRTY")


def test_gh_pr_state_and_merge_status_non_dict_json_returns_empty(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(("state", "mergeStateStatus"), pr_read(fields={}))
    assert _pr_merge_conflict.gh_pr_state_and_merge_status(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == ("", "")


def test_gh_pr_state_and_merge_status_unparseable_stdout_returns_empty(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(("state", "mergeStateStatus"), pr_read(fields={}))
    assert _pr_merge_conflict.gh_pr_state_and_merge_status(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == ("", "")


def test_gh_pr_state_and_merge_status_network_failure_raises(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(
        ("state", "mergeStateStatus"),
        pr_read(returncode=1, stderr="could not resolve host: api.github.com"),
    )
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_merge_conflict.gh_pr_state_and_merge_status(42, forge_target=DEFAULT_FORGE_TARGET)


def test_gh_pr_state_and_merge_status_unknown_failure_keeps_empty(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(
        ("state", "mergeStateStatus"), pr_read(returncode=1, stderr="odd failure"),
    )
    assert _pr_merge_conflict.gh_pr_state_and_merge_status(
        42, forge_target=DEFAULT_FORGE_TARGET
    ) == ("", "")


def test_gh_pr_state_and_merge_status_timeout_raises_forge_transient(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(
        ("state", "mergeStateStatus"), subprocess.TimeoutExpired(cmd="gh", timeout=60.0),
    )
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_merge_conflict.gh_pr_state_and_merge_status(42, forge_target=DEFAULT_FORGE_TARGET)


def test_gh_pr_state_and_merge_status_missing_binary_raises(
    forge: ForgeScript,
) -> None:
    forge.queue_read_pr(("state", "mergeStateStatus"), FileNotFoundError("gh"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _pr_merge_conflict.gh_pr_state_and_merge_status(42, forge_target=DEFAULT_FORGE_TARGET)


def test_confirm_ready_kind_pending_when_readiness_reports_dirty(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=False, merge_state_status="DIRTY", review_decision="",
        ),
    )
    kind, head_sha = _pr_merge_checks.confirm_ready_kind(
        workspace, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    )
    assert kind == "pending"
    assert head_sha == ""


def test_confirm_ready_kind_ready_when_readiness_reports_mergeable_state(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_merge_readiness",
        lambda *_a, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="",
            check_count=1, passing_checks=("ci",), head_sha="deadbeef",
        ),
    )
    kind, head_sha = _pr_merge_checks.confirm_ready_kind(
        workspace, 42, "ready", forge_target=DEFAULT_FORGE_TARGET
    )
    assert kind == "ready"
    assert head_sha == "deadbeef"


def test_poll_until_done_returns_conflict_outcome_before_evaluating_checks(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_poll_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.delenv("ALAN_PR_MERGE_AUDIT_DISABLE", raising=False)
    forge.queue_read_pr(("state", "mergeStateStatus"), _pr_view_stdout("OPEN", "DIRTY"))

    def boom(*_a: object, **_kw: object) -> Any:
        raise AssertionError("checks must not be evaluated once mergeStateStatus is DIRTY")

    monkeypatch.setattr(pr_merge_mod, "_evaluate_checks", boom)
    handover = seed_and_run(
        TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None
    )
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == []
    assert handover.open_questions
    assert "DIRTY" in handover.full_synthesis
    assert "not a check-settling timeout" in handover.full_synthesis
    assert "not mergeable after" not in handover.full_synthesis
    assert forge.calls("read_checks") == []
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "pr_merge_audit.jsonl").read_text().splitlines()
    ]
    assert any(e.get("action") == "merge_gate_conflict_detected" for e in entries)


def test_poll_until_done_ignores_dirty_after_terminal_state(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_poll_intervals(monkeypatch)
    forge.queue_read_pr(("state", "mergeStateStatus"), _pr_view_stdout("MERGED", "DIRTY"))
    handover = seed_and_run(
        TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None
    )
    assert handover.handover_type == "terminal"


def test_merge_block_options_conflict_uses_conflict_recovery_recipe() -> None:
    outcome = pr_merge_mod.PrMergeOutcome(kind="conflict", pr_number=42, reason="x")
    options, recommended, resolvers = _pr_merge_escalation.merge_block_options(outcome.kind)
    assert options == list(_pr_merge_conflict.CONFLICT_OPTIONS)
    assert recommended == _pr_merge_conflict.CONFLICT_OPTIONS[0]
    assert resolvers == {}
