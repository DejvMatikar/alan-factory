from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from alan.deploy_verify import DeployObservation, GateRecord
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget, RepoIdentity, register
from alan.forge import JsonObject
from alan.handover import Handover
from alan.teams import TEAMS, _forge_errors, _pr_forge
from alan.teams import deploy_verify as deploy_verify_mod
from alan.teams._base import ResumeUnsupportedError
from alan.teams.deploy_verify import TEAM, DeployVerifyTeam
from alan.workflow import WorkflowDoc, WorkflowTask, create_invocation_dir, save_workflow
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read

_OTHER_REPO = RepoIdentity(owner="acme", repo="example-repo")


def _read_meta(workspace: Workspace) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads((workspace.path / "meta.json").read_text(encoding="utf-8"))
    return raw


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", "1")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("dv-task", "deploy gate test", root=tmp_path / "ws")


def _prior(workspace: Workspace, pr_number: int | None = 42) -> Handover:
    return Handover(
        handover_id="handover_pr_merge_001",
        handover_type="terminal",
        task_id=workspace.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="PR merged",
        pr_number=pr_number,
        decisions=[],
    )


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _quick_intervals(
    monkeypatch: pytest.MonkeyPatch, budget_s: int = 3600, race_budget_s: float = 0
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", str(budget_s))
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S", str(race_budget_s))


def _pr_view_fields(*, state: str | None, seconds_ago: float | None) -> JsonObject:
    merged_at = None
    if seconds_ago is not None:
        ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
        merged_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"state": state, "mergedAt": merged_at}


def _merged_at_fields(seconds_ago: float = 5.0) -> JsonObject:
    return _pr_view_fields(state="MERGED", seconds_ago=seconds_ago)


def _live_observation() -> DeployObservation:
    return DeployObservation(
        arm="restart_free",
        checkout_contains_merge=True,
        slot_contains_merge=True,
        served_contains_merge=False,
        health_ok=True,
    )


def _not_live_observation() -> DeployObservation:
    return DeployObservation(
        arm="restart_free",
        checkout_contains_merge=False,
        slot_contains_merge=False,
        served_contains_merge=False,
        health_ok=True,
    )


def test_team_registration() -> None:
    assert TEAMS["deploy_verify"] is TEAM
    assert isinstance(TEAM, DeployVerifyTeam)
    assert TEAM.name == "deploy_verify"
    assert TEAM.supports_resume is False
    assert TEAM.restart_rederivable is True


def test_plan_workflow_returns_single_deploy_verify_node() -> None:
    specs = TEAM.plan_workflow(None, None)  # type: ignore[arg-type]
    assert len(specs) == 1
    assert specs[0].id == "deploy_verify"
    assert specs[0].role == "deploy_verify"
    assert specs[0].model == "none"


def test_resume_raises(workspace: Workspace) -> None:
    prior = _prior(workspace)
    with pytest.raises(ResumeUnsupportedError):
        TEAM.run(workspace, prior, lambda e: None, inv_id="inv_001", resume_inv_id="inv_000")


def test_no_pr_number_produces_escalation_handover(workspace: Workspace) -> None:
    prior = _prior(workspace, pr_number=None)
    events: list[Any] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.handover_type == "escalation"
    assert "PR number" in handover.full_synthesis
    node_kinds = [e.kind for e in events]
    assert "node_started" in node_kinds
    assert "node_completed" in node_kinds


def test_no_pr_number_with_none_prior(workspace: Workspace) -> None:
    events: list[Any] = []
    handover = seed_and_run(TEAM, workspace, None, events.append)
    assert handover.handover_type == "escalation"
    assert handover.pr_number is None


def _ceiling_instructions(merge_sha: str = "a" * 40) -> str:
    return GateRecord(merge_sha=merge_sha, path="ceiling", deferred_at=None).to_json()


def test_confirmed_path(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _live_observation())

    prior = _prior(workspace)
    events: list[Any] = []
    handover = seed_and_run(
        TEAM, workspace, prior, events.append, instructions=_ceiling_instructions()
    )

    assert handover.handover_type == "terminal"
    assert handover.pr_number == 42
    node_kinds = [e.kind for e in events]
    assert "node_started" in node_kinds
    assert "node_completed" in node_kinds


def test_confirmed_path_marks_delivered_and_stamps_live_at(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _live_observation())

    prior = _prior(workspace)
    seed_and_run(TEAM, workspace, prior, lambda e: None, instructions=_ceiling_instructions())

    meta = _read_meta(workspace)
    assert meta["delivered_at"] is not None
    assert meta["live_at"] is not None


def test_escalate_on_never_live_budget_exhausted(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch, budget_s=0)
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _not_live_observation())
    monkeypatch.setattr(
        deploy_verify_mod, "_diagnostic_detail", lambda workspace: "no ci-deploy runs observed"
    )

    prior = _prior(workspace)
    events: list[Any] = []
    handover = seed_and_run(
        TEAM, workspace, prior, events.append, instructions=_ceiling_instructions()
    )

    assert handover.handover_type == "escalation"
    assert "never went live" in handover.full_synthesis
    assert "no ci-deploy runs observed" in handover.full_synthesis


def test_escalate_when_merged_at_unavailable(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(returncode=1))

    prior = _prior(workspace)
    events: list[Any] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)

    assert handover.handover_type == "escalation"
    assert "mergedAt" in handover.full_synthesis


def test_wait_then_confirmed_on_next_evaluation(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    merged = _merged_at_fields()
    forge.queue("read_pr", pr_read(merged), pr_read(merged))
    observations = [_not_live_observation(), _live_observation()]
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: observations.pop(0))

    merge_sha = "a" * 40
    first = deploy_verify_mod._evaluate_once(  # noqa: SLF001
        workspace, 42, _ceiling_instructions(merge_sha)
    )
    assert first.kind == "wait"
    assert first.deadline is not None
    record = GateRecord(
        merge_sha=merge_sha, path="ceiling", deferred_at=None, deadline=first.deadline
    )
    second = deploy_verify_mod._evaluate_once(  # noqa: SLF001
        workspace, 42, record.to_json()
    )

    assert second.kind == "confirmed"


def test_wait_merge_race_then_confirms_once_merged_at_resolves(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch, race_budget_s=10)
    forge.queue(
        "read_pr",
        pr_read(_pr_view_fields(state="MERGED", seconds_ago=None)),
        pr_read(_merged_at_fields()),
    )
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _live_observation())

    first = deploy_verify_mod._evaluate_once(workspace, 42, None)  # noqa: SLF001
    assert first.kind == "wait"
    assert first.race_deadline is not None
    record = GateRecord(
        merge_sha="a" * 40,
        path="ceiling",
        deferred_at=None,
        race_deadline=first.race_deadline,
    )
    second = deploy_verify_mod._evaluate_once(  # noqa: SLF001
        workspace, 42, record.to_json()
    )

    assert second.kind == "confirmed"


def test_closed_pr_escalates_without_burning_race_budget(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch, race_budget_s=9999)
    forge.queue("read_pr", pr_read(_pr_view_fields(state="CLOSED", seconds_ago=None)))

    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    assert handover.handover_type == "escalation"
    assert "closed" in handover.full_synthesis.lower()
    assert "PR #42" in handover.full_synthesis


def test_open_pr_escalates_without_burning_race_budget(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch, race_budget_s=9999)
    forge.queue("read_pr", pr_read(_pr_view_fields(state="OPEN", seconds_ago=None)))

    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    assert handover.handover_type == "escalation"
    assert "open" in handover.full_synthesis.lower()
    assert "PR #42" in handover.full_synthesis


def test_pr_read_file_not_found_raises_for_retry(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", FileNotFoundError("gh not found"))
    prior = _prior(workspace)
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, prior, lambda e: None)


def test_pr_read_timeout_raises_for_retry(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", subprocess.TimeoutExpired("gh", 60))
    prior = _prior(workspace)
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, prior, lambda e: None)


def test_merged_state_null_merged_at_escalates_when_race_budget_is_zero(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_pr_view_fields(state="MERGED", seconds_ago=None)))
    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert handover.handover_type == "escalation"
    assert "mergedAt" in handover.full_synthesis


def test_empty_pr_read_fields_escalate_as_a_failed_read_when_race_budget_is_zero(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read({}))
    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert handover.handover_type == "escalation"
    assert "merge/forge-race budget" in handover.full_synthesis
    assert "state=gh call failed" in handover.full_synthesis


def test_empty_pr_read_fields_take_the_merge_race_wait_path_while_budget_remains(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch, race_budget_s=10)
    forge.queue("read_pr", pr_read({}))
    outcome = deploy_verify_mod._evaluate_once(workspace, 42, None)  # noqa: SLF001
    assert outcome.kind == "wait"
    assert outcome.race_deadline is not None


def test_parse_iso_datetime_fractional_seconds() -> None:
    raw = "2024-01-15T10:05:30.123456Z"
    dt = deploy_verify_mod._parse_iso_datetime(raw)
    assert dt is not None
    assert dt.tzinfo is UTC


def test_parse_iso_datetime_no_fractional() -> None:
    raw = "2024-01-15T10:05:30Z"
    dt = deploy_verify_mod._parse_iso_datetime(raw)
    assert dt is not None
    assert dt.second == 30


def test_parse_iso_datetime_invalid_returns_none() -> None:
    assert deploy_verify_mod._parse_iso_datetime("not-a-date") is None


def test_health_url_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HEALTH_URL", "http://custom:9090/health")
    assert deploy_verify_mod._health_url() == "http://custom:9090/health"


def test_health_url_default_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_HEALTH_URL", raising=False)
    assert deploy_verify_mod._health_url() == "http://127.0.0.1:8585/api/health"


def test_health_url_default_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_HEALTH_URL", "   ")
    assert deploy_verify_mod._health_url() == "http://127.0.0.1:8585/api/health"


def test_audit_disabled_when_env_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", "1")
    assert deploy_verify_mod._audit_disabled() is True


def test_audit_disabled_false_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)
    assert deploy_verify_mod._audit_disabled() is False


def test_audit_writes_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    deploy_verify_mod._audit({"action": "test", "pr_number": 1})
    audit_file = tmp_path / "logs" / "deploy_verify_audit.jsonl"
    assert audit_file.exists()


def test_diagnostic_detail_reports_latest_run(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import deploy_runs as deploy_runs_mod
    from alan.deploy_verify import DeployRun

    run = DeployRun(
        run_id="1", created_at=datetime.now(UTC), status="completed", conclusion="failure"
    )
    monkeypatch.setattr(
        deploy_runs_mod, "fetch_deploy_runs", lambda forge_target, deploy_workflow: [run]
    )
    detail = deploy_verify_mod._diagnostic_detail(workspace)  # noqa: SLF001
    assert "status=completed" in detail
    assert "conclusion=failure" in detail


def test_diagnostic_detail_reports_no_runs_observed(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import deploy_runs as deploy_runs_mod

    monkeypatch.setattr(
        deploy_runs_mod, "fetch_deploy_runs", lambda forge_target, deploy_workflow: []
    )
    detail = deploy_verify_mod._diagnostic_detail(workspace)  # noqa: SLF001
    assert detail == "no ci-deploy runs observed"


def test_diagnostic_detail_fails_soft_on_fetch_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan import deploy_runs as deploy_runs_mod

    def _boom(forge_target: ForgeTarget, deploy_workflow: str | None) -> list[object]:
        raise RuntimeError("gh unreachable")

    monkeypatch.setattr(deploy_runs_mod, "fetch_deploy_runs", _boom)
    detail = deploy_verify_mod._diagnostic_detail(workspace)  # noqa: SLF001
    assert detail == "deploy-run diagnostics unavailable"


def test_diagnostic_detail_passes_the_workspaces_own_forge_target_not_a_slug_derived_one(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import deploy_runs as deploy_runs_mod

    captured: list[ForgeTarget] = []

    def _capture(forge_target: ForgeTarget, deploy_workflow: str | None) -> list[object]:
        captured.append(forge_target)
        return []

    monkeypatch.setattr(deploy_runs_mod, "fetch_deploy_runs", _capture)
    deploy_verify_mod._diagnostic_detail(workspace)  # noqa: SLF001
    assert captured == [_pr_forge.forge_target_for_ws(workspace)]


def test_never_live_outcome_audit_action_stable_with_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setattr(
        deploy_verify_mod, "_diagnostic_detail", lambda workspace: "no ci-deploy runs observed"
    )
    deploy_verify_mod._never_live_outcome(  # noqa: SLF001
        workspace, 42, 200.0, 100.0, _not_live_observation()
    )
    audit_file = tmp_path / "logs" / "deploy_verify_audit.jsonl"
    entry = json.loads(audit_file.read_text().strip().splitlines()[-1])
    assert entry["action"] == "deploy_escalate"
    assert entry["variant"] == "never_live"


def test_never_live_outcome_includes_arm_when_observation_present(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_verify_mod, "_diagnostic_detail", lambda workspace: "detail")
    outcome = deploy_verify_mod._never_live_outcome(  # noqa: SLF001
        workspace, 42, 200.0, 100.0, _not_live_observation()
    )
    assert "arm=restart_free" in outcome.reason


def test_never_live_outcome_omits_arm_when_observation_absent(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_verify_mod, "_diagnostic_detail", lambda workspace: "detail")
    outcome = deploy_verify_mod._never_live_outcome(  # noqa: SLF001
        workspace, 42, 200.0, 100.0, None
    )
    assert "arm=" not in outcome.reason


def test_escalate_no_merged_at_audit_action_stable_with_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    info = deploy_verify_mod._PrMergeInfo(gh_ok=True, state="CLOSED", merged_at=None)
    deploy_verify_mod._escalate_no_merged_at(workspace, 42, info, 0.0)
    audit_file = tmp_path / "logs" / "deploy_verify_audit.jsonl"
    entry = json.loads(audit_file.read_text().strip().splitlines()[-1])
    assert entry["action"] == "escalate_no_merged_at"
    assert entry["variant"] == "closed"


def test_merge_race_step_first_encounter_waits(workspace: Workspace) -> None:
    info = deploy_verify_mod._PrMergeInfo(gh_ok=False, state=None, merged_at=None)
    outcome = deploy_verify_mod._merge_race_step(workspace, 42, None, info)
    assert outcome.kind == "wait"
    assert outcome.race_deadline is not None


def test_merge_race_step_continues_waiting_within_budget(workspace: Workspace) -> None:
    race_deadline = time.time() + 299.0
    record = GateRecord(
        merge_sha=None, path="ceiling", deferred_at=None, race_deadline=race_deadline
    )
    info = deploy_verify_mod._PrMergeInfo(gh_ok=True, state="MERGED", merged_at=None)
    outcome = deploy_verify_mod._merge_race_step(workspace, 42, record, info)
    assert outcome.kind == "wait"
    assert outcome.race_deadline == race_deadline


def test_merge_race_step_budget_exhausted_escalates(workspace: Workspace) -> None:
    race_deadline = time.time() - 1.0
    record = GateRecord(
        merge_sha=None, path="ceiling", deferred_at=None, race_deadline=race_deadline
    )
    info = deploy_verify_mod._PrMergeInfo(gh_ok=False, state=None, merged_at=None)
    outcome = deploy_verify_mod._merge_race_step(workspace, 42, record, info)
    assert outcome.kind == "escalate"
    assert "race budget" in outcome.reason


def test_merge_race_step_closed_escalates_without_budget(workspace: Workspace) -> None:
    info = deploy_verify_mod._PrMergeInfo(gh_ok=True, state="CLOSED", merged_at=None)
    outcome = deploy_verify_mod._merge_race_step(workspace, 42, None, info)
    assert "closed" in outcome.reason.lower()


def test_merge_race_step_open_escalates_without_budget(workspace: Workspace) -> None:
    info = deploy_verify_mod._PrMergeInfo(gh_ok=True, state="OPEN", merged_at=None)
    outcome = deploy_verify_mod._merge_race_step(workspace, 42, None, info)
    assert "open" in outcome.reason.lower()


def test_merged_step_confirms_when_observation_is_live(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _live_observation())
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    record = GateRecord(merge_sha="a" * 40, path="ceiling", deferred_at=None)
    outcome = deploy_verify_mod._merged_step(workspace, 42, record, merged_at, "http://x")
    assert outcome.kind == "confirmed"


def test_merged_step_waits_when_within_budget(workspace: Workspace) -> None:
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    outcome = deploy_verify_mod._merged_step(workspace, 42, None, merged_at, "http://x")
    assert outcome.kind == "wait"
    assert outcome.deadline is not None
    assert outcome.merged_at == merged_at


def test_merged_step_treats_missing_merge_sha_as_unobservable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(sha: str, url: str) -> DeployObservation:
        raise AssertionError("_observe must not be called without a merge_sha")

    monkeypatch.setattr(deploy_verify_mod, "_observe", _fail_if_called)
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    outcome = deploy_verify_mod._merged_step(workspace, 42, None, merged_at, "http://x")
    assert outcome.kind == "wait"
    assert outcome.merge_sha is None


def test_merged_step_uses_hint_when_gate_record_has_no_merge_sha(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def _observe(sha: str, url: str) -> DeployObservation:
        seen.append(sha)
        return _live_observation()

    monkeypatch.setattr(deploy_verify_mod, "_observe", _observe)
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    record = GateRecord(merge_sha=None, path="awaiting_live", deferred_at=None)
    outcome = deploy_verify_mod._merged_step(
        workspace, 42, record, merged_at, "http://x", "b" * 40
    )
    assert outcome.kind == "confirmed"
    assert seen == ["b" * 40]


def test_merged_step_carries_recovered_merge_sha_into_wait_outcome(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _not_live_observation())
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    record = GateRecord(merge_sha=None, path="awaiting_live", deferred_at=None)
    outcome = deploy_verify_mod._merged_step(
        workspace, 42, record, merged_at, "http://x", "c" * 40
    )
    assert outcome.kind == "wait"
    assert outcome.merge_sha == "c" * 40


def test_merged_step_prefers_gate_record_merge_sha_over_hint(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def _observe(sha: str, url: str) -> DeployObservation:
        seen.append(sha)
        return _live_observation()

    monkeypatch.setattr(deploy_verify_mod, "_observe", _observe)
    merged_at = datetime.now(UTC) - timedelta(seconds=5)
    record = GateRecord(merge_sha="a" * 40, path="awaiting_live", deferred_at=None)
    deploy_verify_mod._merged_step(workspace, 42, record, merged_at, "http://x", "b" * 40)
    assert seen == ["a" * 40]


def test_merged_step_escalates_never_live_when_budget_exhausted(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _not_live_observation())
    monkeypatch.setattr(
        deploy_verify_mod, "_diagnostic_detail", lambda workspace: "no ci-deploy runs observed"
    )
    expired = GateRecord(
        merge_sha="a" * 40, path="ceiling", deferred_at=None, deadline=time.time() - 1.0
    )
    merged_at = datetime.now(UTC) - timedelta(seconds=200)
    outcome = deploy_verify_mod._merged_step(workspace, 42, expired, merged_at, "http://x")
    assert outcome.kind == "escalate"
    assert "never went live" in outcome.reason


def test_no_pr_with_prior_preserves_chain(workspace: Workspace) -> None:
    prior = _prior(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert handover.handover_type == "escalation"
    assert handover.iteration == prior.iteration + 1


def test_team_not_in_internal_only_teams() -> None:
    from alan.teams import INTERNAL_ONLY_TEAMS

    assert "deploy_verify" not in INTERNAL_ONLY_TEAMS


def test_team_not_in_initial_dispatchable_teams() -> None:
    from alan.teams import INITIAL_DISPATCHABLE_TEAMS

    assert "deploy_verify" not in INITIAL_DISPATCHABLE_TEAMS


def test_deploy_verify_in_transition_policy() -> None:
    from alan.router import TEAM_TRANSITION_POLICY

    assert "deploy_verify" in TEAM_TRANSITION_POLICY
    assert TEAM_TRANSITION_POLICY["deploy_verify"] == frozenset()


def test_pr_merge_can_route_to_deploy_verify() -> None:
    from alan.router import TEAM_TRANSITION_POLICY

    assert "deploy_verify" in TEAM_TRANSITION_POLICY.get("pr_merge", frozenset())


def test_dry_run_enabled_false_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_DRY_RUN", raising=False)
    assert deploy_verify_mod.dry_run_enabled() is False


def test_dry_run_enabled_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    assert deploy_verify_mod.dry_run_enabled() is True


def test_dry_run_produces_confirmed_handover(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert handover.handover_type == "terminal"
    assert handover.pr_number == 42
    assert "dry run" in handover.full_synthesis


def test_dry_run_does_not_mark_delivered(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    prior = _prior(workspace)
    seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert _read_meta(workspace)["delivered_at"] is None


def test_escalation_does_not_mark_delivered(workspace: Workspace) -> None:
    prior = _prior(workspace, pr_number=None)
    seed_and_run(TEAM, workspace, prior, lambda e: None)
    assert _read_meta(workspace)["delivered_at"] is None


def test_mark_delivered_settles_active_seconds_from_real_invocation(
    workspace: Workspace,
) -> None:
    inv_id = create_invocation_dir(workspace)
    started = datetime.now(UTC) - timedelta(seconds=90)
    completed = started + timedelta(seconds=30)
    doc = WorkflowDoc(
        id=inv_id,
        namespace=f"alan.workspace.{workspace.task_id}",
        team="implementation",
        status="success",
        started_at=started,
        completed_at=completed,
        tasks=[
            WorkflowTask(
                id="worker",
                role="worker",
                model="sonnet",
                status="success",
                started_at=started,
                completed_at=completed,
            )
        ],
    )
    save_workflow(workspace, doc)
    deploy_verify_mod._mark_delivered(workspace)
    meta = _read_meta(workspace)
    assert meta["delivered_at"] is not None
    assert meta["settled_active_seconds"] == pytest.approx(30.0)
    assert meta["live_at"] is None


def test_mark_delivered_is_idempotent_across_repeated_confirmations(
    workspace: Workspace,
) -> None:
    deploy_verify_mod._mark_delivered(workspace)
    first = _read_meta(workspace)
    deploy_verify_mod._mark_delivered(workspace, live_at=datetime.now(UTC))
    second = _read_meta(workspace)
    assert second["delivered_at"] == first["delivered_at"]
    assert second["settled_active_seconds"] == first["settled_active_seconds"]
    assert second["live_at"] == first["live_at"] is None


def test_evaluate_once_never_sleeps(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _live_observation())

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("_evaluate_once must never sleep")

    monkeypatch.setattr(time, "sleep", _boom)
    outcome = deploy_verify_mod._evaluate_once(workspace, 42, _ceiling_instructions())
    assert outcome.kind == "confirmed"


def test_evaluate_once_waits_when_observation_not_yet_live(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    monkeypatch.setattr(deploy_verify_mod, "_observe", lambda sha, url: _not_live_observation())
    outcome = deploy_verify_mod._evaluate_once(workspace, 42, _ceiling_instructions())
    assert outcome.kind == "wait"


def test_evaluate_once_reads_the_pr_from_the_default_repository_target(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_pr_view_fields(state="OPEN", seconds_ago=None)))
    deploy_verify_mod._evaluate_once(workspace, 42, None)  # noqa: SLF001
    (call,) = forge.calls("read_pr")
    target = cast(ForgeTarget, call[1]["forge_target"])
    assert target.repo.slug == deploy_verify_mod._default_repo_slug()  # noqa: SLF001


def test_evaluate_once_reads_the_pr_from_the_workspaces_own_repository_target(
    tmp_path: Path, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    _quick_intervals(monkeypatch)
    _register_other_repo()
    ws = create("dv-other-read", "d", root=tmp_path / "ws", engine_key=_OTHER_REPO.slug)
    monkeypatch.setattr(deploy_verify_mod, "_pipeline_available_for", lambda workspace: True)
    forge.queue("read_pr", pr_read(_pr_view_fields(state="OPEN", seconds_ago=None)))
    deploy_verify_mod._evaluate_once(ws, 42, None)  # noqa: SLF001
    (call,) = forge.calls("read_pr")
    slug = cast(ForgeTarget, call[1]["forge_target"]).repo.slug
    assert slug == _OTHER_REPO.slug
    assert slug != deploy_verify_mod._default_repo_slug()  # noqa: SLF001


def _register_other_repo() -> None:
    register(
        EngineConfig(
            repo=_OTHER_REPO,
            forge=ForgeConfig(repo=_OTHER_REPO, credential_env_name="OTHER_REPO_GH_TOKEN"),
        ),
        validate_env=False,
    )


def test_pipeline_unavailable_for_non_default_engine_completes_with_skip_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    _register_other_repo()
    ws = create(
        "dv-other-engine", "d", root=tmp_path / "ws", engine_key=_OTHER_REPO.slug
    )
    prior = _prior(ws)

    handover = seed_and_run(TEAM, ws, prior, lambda e: None)

    assert _active_fake_forge.calls == []
    assert handover.handover_type == "terminal"
    assert "deploy_verify_skipped_no_pipeline" in handover.full_synthesis
    assert _read_meta(ws)["delivered_at"] is None
    assert _read_meta(ws)["live_at"] is None


def test_pipeline_available_for_default_engine(workspace: Workspace) -> None:
    assert deploy_verify_mod._pipeline_available_for(workspace) is True  # noqa: SLF001


def test_pipeline_unavailable_records_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    _register_other_repo()
    ws = create(
        "dv-other-engine-audit", "d", root=tmp_path / "ws", engine_key=_OTHER_REPO.slug
    )
    deploy_verify_mod._no_pipeline_outcome(ws, 99)  # noqa: SLF001
    audit_file = tmp_path / "logs" / "deploy_verify_audit.jsonl"
    entry = json.loads(audit_file.read_text().strip().splitlines()[-1])
    assert entry["action"] == "deploy_verify_skipped_no_pipeline"
    assert entry["pr_number"] == 99
    assert entry["repo"] == _OTHER_REPO.slug
