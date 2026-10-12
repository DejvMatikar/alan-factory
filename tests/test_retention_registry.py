from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import retention_registry
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.engine_config import EngineConfig, RepoIdentity, register
from alan.handover import Handover, save_handover
from alan.retention_registry import (
    REGISTRY,
    RetentionRegistryError,
    _registry_capacity_findings,  # pyright: ignore[reportPrivateUsage]
    lookup,
    seed_recurring_workspace,
)
from alan.workflow import PendingTaskSpec
from alan.workspace import Status, create


def _seed_inv(ws: workspace_mod.Workspace) -> str:
    return workflow_mod.seed_pending_workflow(
        ws, team=ws.meta.team or "triage", tasks=[PendingTaskSpec(id="w", role="w", model="none")]
    )


def _audit_rows(logs_dir: Path, filename: str) -> list[dict[str, object]]:
    path = logs_dir / filename
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_registry_has_triage_and_dependency_sweep_entries() -> None:
    keys = {(e.team, e.title) for e in REGISTRY}
    assert ("triage", "Failure Triage Sweep") in keys
    assert ("dependency_sweep", "Dependency Sweep") in keys
    assert ("dependency_sweep", "Dependency Advisory Check") in keys


def test_advisory_check_entry_shares_the_dep_sweep_audit_file() -> None:
    entry = lookup("dependency_sweep", "Dependency Advisory Check")
    assert entry is not None
    assert entry.audit_file == "dep_sweep_audit.jsonl"
    assert entry.description == "Event-triggered Python advisory check on red main CI."


def test_lookup_returns_matching_entry() -> None:
    entry = lookup("triage", "Failure Triage Sweep")
    assert entry is not None
    assert entry.team == "triage"
    assert entry.audit_file == "triage_audit.jsonl"


def test_lookup_returns_none_for_unknown_title() -> None:
    assert lookup("triage", "Some Other Title") is None


def test_lookup_returns_none_for_unknown_team() -> None:
    assert lookup("not_a_team", "Failure Triage Sweep") is None


def test_lookup_returns_none_when_team_is_none() -> None:
    assert lookup(None, "Failure Triage Sweep") is None


def test_lookup_keys_by_team_and_title_together() -> None:
    assert lookup("dependency_sweep", "Failure Triage Sweep") is None


def test_seed_recurring_workspace_hard_fails_on_unregistered_workspace(
    ws_root: Path,
) -> None:
    ws = create("Some Unregistered Recurring Thing", "d", team="mystery_team", root=ws_root)
    with pytest.raises(RetentionRegistryError, match="mystery_team"):
        seed_recurring_workspace(ws)


def test_seed_recurring_workspace_soft_fails_when_hard_fail_false(
    ws_root: Path, tmp_path: Path
) -> None:
    ws = create("Some Unregistered Recurring Thing", "d", team="mystery_team", root=ws_root)
    seed_recurring_workspace(ws, hard_fail=False)
    assert not (tmp_path / "logs" / "triage_audit.jsonl").exists()


def test_seed_recurring_workspace_prunes_triage_invocation_slots(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(3):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    inv_dirs = sorted(p.name for p in (ws.path / "invocations").iterdir())
    assert len(inv_dirs) == 1
    rows = _audit_rows(tmp_path / "logs", "triage_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["removed"] == 2
    assert pruned[0]["task_id"] == ws.task_id


def test_seed_recurring_workspace_prunes_triage_handover_files(
    ws_root: Path, tmp_path: Path
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    save_handover(
        ws,
        Handover(
            handover_id=f"handover_{ws.task_id}_a",
            handover_type="forward",
            task_id=ws.task_id,
            from_team="triage",
            from_invocation_id="inv_001",
            to_team_suggestions=[],
            iteration=1,
            timestamp_utc=datetime.now(UTC),
            full_synthesis="[SENSOR_SWEEP] surfaces=1 blocked=0",
        ),
    )

    seed_recurring_workspace(ws)

    assert list((ws.path / "handovers").iterdir()) == []
    rows = _audit_rows(tmp_path / "logs", "triage_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "handover_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["removed"] == 1


def test_seed_recurring_workspace_prunes_triage_revisit_counts(
    ws_root: Path, tmp_path: Path
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    ws = workspace_mod.increment_revisit_count(ws, "triage", "chain-1")
    ws = workspace_mod.increment_revisit_count(ws, "research", "chain-1")

    seed_recurring_workspace(ws)

    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert all("|triage|" not in k for k in reloaded.meta.revisit_counts)
    assert any("|research|" in k for k in reloaded.meta.revisit_counts)
    rows = _audit_rows(tmp_path / "logs", "triage_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "revisit_keys_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["removed"] == 1


def test_seed_recurring_workspace_writes_no_audit_row_when_nothing_pruned(
    ws_root: Path, tmp_path: Path
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    seed_recurring_workspace(ws)
    assert not (tmp_path / "logs" / "triage_audit.jsonl").exists()


def test_seed_recurring_workspace_prunes_dependency_sweep_into_own_audit_file(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Dependency Sweep", "d", team="dependency_sweep", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(2):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    rows = _audit_rows(tmp_path / "logs", "dep_sweep_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["removed"] == 1
    assert not (tmp_path / "logs" / "triage_audit.jsonl").exists()


def test_seed_recurring_workspace_dep_sweep_audit_entry_carries_engine_slug(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    ws = create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root,
        engine_key="acme/widget",
    )
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(2):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    rows = _audit_rows(tmp_path / "logs", "dep_sweep_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["slug"] == "acme/widget"


def test_seed_recurring_workspace_dep_sweep_audit_entry_defaults_to_alan_slug(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Dependency Sweep", "d", team="dependency_sweep", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(2):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    rows = _audit_rows(tmp_path / "logs", "dep_sweep_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["slug"] == "DejvMatikar/alan"


def test_seed_recurring_workspace_triage_audit_entry_has_no_slug(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(3):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    rows = _audit_rows(tmp_path / "logs", "triage_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert "slug" not in pruned[0]


def test_seed_recurring_workspace_contains_prune_exception(
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)

    def _boom(_ws: workspace_mod.Workspace) -> int:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workflow_mod, "prune_invocation_dirs", _boom)
    with caplog.at_level(logging.ERROR, logger="alan.retention_registry"):
        seed_recurring_workspace(ws)
    assert any("prune failed" in r.message for r in caplog.records)


def test_registry_capacity_findings_empty_when_no_workspaces(ws_root: Path) -> None:
    assert _registry_capacity_findings(ws_root) == []


def test_registry_capacity_findings_empty_when_below_threshold(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 10)
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    for _ in range(8):
        _seed_inv(ws)
    assert _registry_capacity_findings(ws_root) == []


def test_registry_capacity_findings_flags_workspace_at_or_above_threshold(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 10)
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    for _ in range(9):
        _seed_inv(ws)

    findings = _registry_capacity_findings(ws_root)

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "retention_near_cap"
    assert f.is_blocked is True
    assert f.obs["task_id"] == ws.task_id
    assert f.obs["team"] == "triage"
    assert f.obs["title"] == "Failure Triage Sweep"
    assert f.obs["used"] == 9
    assert f.obs["cap"] == 10


def test_registry_capacity_findings_ignores_title_mismatch(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 2)
    ws = create("Failure Triage Sweep V2", "d", team="triage", root=ws_root)
    for _ in range(2):
        _seed_inv(ws)
    assert _registry_capacity_findings(ws_root) == []


def test_registry_capacity_findings_ignores_team_mismatch(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 2)
    ws = create("Failure Triage Sweep", "d", team="not_triage", root=ws_root)
    for _ in range(2):
        _seed_inv(ws)
    assert _registry_capacity_findings(ws_root) == []


def test_registry_capacity_findings_scans_workspaces_once(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    create("Dependency Sweep", "d", team="dependency_sweep", root=ws_root)
    calls: list[Path | None] = []
    original = workspace_mod.list_workspaces

    def _spy(*, root: Path | None = None) -> list[workspace_mod.Workspace]:
        calls.append(root)
        return original(root=root)

    monkeypatch.setattr(workspace_mod, "list_workspaces", _spy)
    _registry_capacity_findings(ws_root)
    assert len(calls) == 1


def test_registry_capacity_findings_counts_dirs_without_saved_workflow(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 2)
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    workflow_mod.create_invocation_dir(ws)
    workflow_mod.create_invocation_dir(ws)
    assert workflow_mod.list_invocations(ws) == []

    findings = _registry_capacity_findings(ws_root)

    assert len(findings) == 1
    assert findings[0].obs["used"] == 2


def test_registry_capacity_findings_counts_regardless_of_lifecycle_status(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention_registry, "_INVOCATION_SLOT_CAP", 2)
    ws = create("Failure Triage Sweep", "d", team="triage", root=ws_root)
    for _ in range(2):
        _seed_inv(ws)
    workspace_mod.transition(ws, Status.ARCHIVED)
    findings = _registry_capacity_findings(ws_root)
    assert len(findings) == 1


def test_near_cap_ratio_and_kind_constants() -> None:
    assert retention_registry._NEAR_CAP_RATIO == 0.9
    assert retention_registry._NEAR_CAP_KIND == "retention_near_cap"


def test_seed_recurring_workspace_prunes_advisory_check_into_dep_sweep_audit_file(
    ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Dependency Advisory Check", "d", team="dependency_sweep", root=ws_root)
    monkeypatch.setattr(workflow_mod, "_INV_SLOT_KEEP", 1)
    for _ in range(2):
        _seed_inv(ws)

    seed_recurring_workspace(ws)

    rows = _audit_rows(tmp_path / "logs", "dep_sweep_audit.jsonl")
    pruned = [r for r in rows if r["action"] == "invocation_slots_pruned"]
    assert len(pruned) == 1
    assert pruned[0]["slug"] == "DejvMatikar/alan"
