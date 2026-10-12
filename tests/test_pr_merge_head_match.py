from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import pr_merge as pr_merge_mod
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, merge_attempt
from tests.test_teams_pr_merge import _readiness_read

_HEAD_MODIFIED_STDERR = "Head branch was modified. Review and try the merge again."


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-head-match", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_head_mismatch_aborts_without_merge_call(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_pr", _readiness_read(head_sha="sha-new"))
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "sha-old", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome is None
    assert forge.calls("merge_pr") == []


def test_head_mismatch_writes_distinct_audit_action(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    monkeypatch.delenv("ALAN_PR_MERGE_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", _readiness_read(head_sha="sha-new"))
    pr_merge_mod._execute_merge(workspace, 42, "sha-old", forge_target=DEFAULT_FORGE_TARGET)
    gate_audit = tmp_path / "logs" / pr_merge_mod._MERGE_GATE_AUDIT_FILE
    rows = [json.loads(line) for line in gate_audit.read_text().splitlines()]
    row = next(r for r in rows if r["action"] == "merge_deferred_head_moved")
    assert row["expected_head_sha"] == "sha-old"
    assert row["observed_head_sha"] == "sha-new"
    assert row["pr_number"] == 42


def test_matching_head_proceeds_to_merge(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_pr", _readiness_read(head_sha="sha-same"))
    forge.queue("merge_pr", merge_attempt())
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "sha-same", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome is not None
    assert outcome.kind == "ready"
    assert len(forge.calls("merge_pr")) == 1


def test_empty_expected_head_sha_fails_closed_without_merging(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_pr", _readiness_read(head_sha="sha-whatever"))
    outcome = pr_merge_mod._execute_merge(workspace, 42, forge_target=DEFAULT_FORGE_TARGET)
    assert outcome is None
    assert forge.calls("merge_pr") == []


def test_is_head_mismatch_refusal_case_insensitive_match() -> None:
    hit = merge_attempt(returncode=1, stderr=_HEAD_MODIFIED_STDERR.upper())
    miss = merge_attempt(returncode=1, stderr="merge conflict")
    empty = merge_attempt(returncode=1)
    assert pr_merge_mod._is_head_mismatch_refusal(hit)
    assert not pr_merge_mod._is_head_mismatch_refusal(miss)
    assert not pr_merge_mod._is_head_mismatch_refusal(empty)


def test_merge_call_includes_match_head_commit_flag(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_pr", _readiness_read(head_sha="sha-same"))
    forge.queue("merge_pr", merge_attempt())
    pr_merge_mod._execute_merge(workspace, 42, "sha-same", forge_target=DEFAULT_FORGE_TARGET)
    [(args, kwargs)] = forge.calls("merge_pr")
    assert args == (42, "sha-same")
    assert kwargs["forge_target"] == DEFAULT_FORGE_TARGET


def test_merge_time_head_mismatch_refusal_retries_without_escalating(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_pr", _readiness_read(head_sha="sha-same"))
    forge.queue("merge_pr", merge_attempt(returncode=1, stderr=_HEAD_MODIFIED_STDERR))
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "sha-same", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome is None


def test_merge_time_head_mismatch_refusal_writes_distinct_audit_row(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    monkeypatch.delenv("ALAN_PR_MERGE_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", _readiness_read(head_sha="sha-same"))
    forge.queue("merge_pr", merge_attempt(returncode=1, stderr=_HEAD_MODIFIED_STDERR))
    pr_merge_mod._execute_merge(workspace, 42, "sha-same", forge_target=DEFAULT_FORGE_TARGET)
    gate_audit = tmp_path / "logs" / pr_merge_mod._MERGE_GATE_AUDIT_FILE
    rows = [json.loads(line) for line in gate_audit.read_text().splitlines()]
    row = next(r for r in rows if r.get("stage") == "gh_pr_merge_refusal")
    assert row["action"] == "merge_deferred_head_moved"
    assert row["expected_head_sha"] == "sha-same"
    assert row["pr_number"] == 42
    merge_audit = tmp_path / "logs" / pr_merge_mod._AUDIT_FILE
    assert not merge_audit.exists()
