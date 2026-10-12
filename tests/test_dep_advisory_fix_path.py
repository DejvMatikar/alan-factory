from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.app.workspace_creation import is_hidden_by_default
from alan.dep_sweep import Vulnerability
from alan.dispatch_provenance import DispatchSurface
from alan.engine_config import DEFAULT_FORGE_TARGET, Posture
from alan.handover import list_handovers
from alan.queue import JobStatus, Queue
from alan.teams import dep_advisory
from alan.teams._dep_advisory_audit import AuditResult
from alan.teams.dep_advisory import Ledger, LedgerEntry, load_ledger, save_ledger
from alan.workspace import Status
from tests.dep_advisory_support import (
    PYPROJECT,
    actions,
    audit_row,
    pending_teams,
    prepare,
    run_check,
    sec_workspaces,
    vuln_entry,
)
from tests.fakes.forge import FakeForge

_URLLIB3 = audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.7.5", "2.8.0", "2.9.0"))
_STARLETTE = audit_row("starlette", "1.7.0", vuln_entry("GHSA-cccc", "2.0.1"))
_OTHER = audit_row("idna", "3.20", vuln_entry("GHSA-dddd", "3.21"))


def test_a_fixable_advisory_opens_one_visible_workspace_that_enqueues_implementation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    summary, audit = run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    assert ws.meta.title == "sec/urllib3: GHSA-aaaa"
    assert ws.meta.team == "dependency_sweep"
    assert ws.meta.status == Status.ACTIVE
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.DEP_ADVISORY_FIX
    assert is_hidden_by_default(ws.meta.team, ws.meta.title) is False
    assert pending_teams(queue, ws) == ["implementation"]
    assert actions(audit) == ["vuln_fix_workspace_opened"]
    assert audit[0]["task_id"] == ws.task_id
    assert audit[0]["trigger"] == "main_ci_red"
    assert summary == "advisory check complete: 1 workspace(s) opened, 0 already covered"


def test_the_bootstrap_handover_carries_the_brief_and_the_dependency_bot_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    (handover,) = list_handovers(ws)
    job = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)[0]

    assert handover.handover_type == "forward"
    assert handover.from_team == "dependency_sweep"
    assert handover.dependency_bot_authorized is True
    assert handover.to_team_suggestions == []
    assert handover.files_to_edit == ["uv.lock", "pyproject.toml", "docs/architecture/dep_sweep.md"]
    assert handover.plan_synthesis is not None
    assert handover.plan_synthesis.startswith("## Implementation steps")
    assert job.parent_handover_id == handover.handover_id
    assert any("ignore-vuln" in action for action in handover.forbidden_actions)


def test_the_target_is_the_lowest_clearing_version_not_the_latest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    brief = list_handovers(ws)[0].plan_synthesis or ""

    assert "uv lock --upgrade-package urllib3==2.7.5" in brief
    assert "2.9.0" not in brief


def test_a_target_outside_the_declared_range_widens_it_in_the_brief(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _STARLETTE)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    brief = list_handovers(ws)[0].plan_synthesis or ""

    assert 'replace `"starlette>=1.7.0,<2"` with `"starlette>=1.7.0,<3"`' in brief
    assert "uv lock --upgrade-package starlette==2.0.1" in brief
    assert "## Advisory fix log" in brief


def test_several_advisories_in_one_run_share_one_workspace_and_one_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3, _STARLETTE, _OTHER)

    _, audit = run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    brief = list_handovers(ws)[0].plan_synthesis or ""
    assert ws.meta.title == "sec/idna: GHSA-dddd (+2 more)"
    assert pending_teams(queue, ws) == ["implementation"]
    for package in ("urllib3", "starlette", "idna"):
        assert f"uv lock --upgrade-package {package}==" in brief
    assert actions(audit) == ["vuln_fix_workspace_opened"]
    ledger = load_ledger(DEFAULT_FORGE_TARGET)
    assert {entry.task_id for entry in ledger.entries.values()} == {ws.task_id}
    assert set(ledger.entries) == {"GHSA-aaaa", "GHSA-cccc", "GHSA-dddd"}


def test_a_second_run_over_the_same_findings_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    run_check(queue, ws_root)

    summary, audit = run_check(queue, ws_root)

    assert len(sec_workspaces(ws_root)) == 1
    assert actions(audit) == ["vuln_already_covered"]
    assert audit[0]["advisory_ids"] == ["GHSA-aaaa"]
    assert summary == "advisory check complete: 0 workspace(s) opened, 1 already covered"


def test_a_merged_fix_that_is_recent_still_covers_the_advisory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    run_check(queue, ws_root)
    (ws,) = sec_workspaces(ws_root)
    workspace_mod.transition(ws, Status.COMPLETED)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_already_covered"]
    assert len(sec_workspaces(ws_root)) == 1


def test_an_archived_fix_lets_a_still_present_advisory_open_a_fresh_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    run_check(queue, ws_root)
    (first,) = sec_workspaces(ws_root)
    workspace_mod.transition(first, Status.ARCHIVED)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_fix_workspace_opened"]
    assert len(sec_workspaces(ws_root)) == 2


def test_concurrent_checks_over_the_same_findings_open_exactly_one_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge)
    barrier = threading.Barrier(2)
    result = AuditResult(
        "ok", (Vulnerability("GHSA-aaaa", "urllib3", "2.7.0", ("2.8.0",)),), (), PYPROJECT
    )

    def _audit(_forge: object) -> AuditResult:
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(dep_advisory, "audit_locked_dependencies", _audit)
    outcomes: list[list[str]] = []

    def _worker() -> None:
        _, audit = run_check(queue, ws_root)
        outcomes.append(actions(audit))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(sec_workspaces(ws_root)) == 1
    assert sorted(outcomes) == [["vuln_already_covered"], ["vuln_fix_workspace_opened"]]


def _seed_active_fix(ws_root: Path, *, age_s: float, advisory_id: str = "GHSA-old") -> str:
    ws = workspace_mod.create("sec/zlib: GHSA-old", "d", team="dependency_sweep", root=ws_root)
    entry = LedgerEntry(
        advisory_id=advisory_id,
        task_id=ws.task_id,
        kind="fix",
        recorded_at=datetime.now(UTC).timestamp() - age_s,
    )
    save_ledger(DEFAULT_FORGE_TARGET, Ledger(entries={advisory_id: entry}))
    return ws.task_id


def test_a_new_fixable_set_defers_while_another_fix_workspace_is_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    _seed_active_fix(ws_root, age_s=3600)

    summary, audit = run_check(queue, ws_root)

    assert [ws.meta.title for ws in sec_workspaces(ws_root)] == ["sec/zlib: GHSA-old"]
    assert actions(audit) == ["vuln_fix_deferred"]
    assert audit[0]["advisory_ids"] == ["GHSA-aaaa"]
    assert "GHSA-aaaa" not in load_ledger(DEFAULT_FORGE_TARGET).entries
    assert summary == "advisory check complete: 0 workspace(s) opened, 0 already covered"


def test_the_deferral_ends_after_twenty_four_hours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    _seed_active_fix(ws_root, age_s=dep_advisory.DEFER_WINDOW_S + 60)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_fix_workspace_opened"]
    assert len(sec_workspaces(ws_root)) == 2


def test_a_stuck_fix_workspace_does_not_defer_a_new_fixable_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    task_id = _seed_active_fix(ws_root, age_s=60)
    workspace_mod.transition(workspace_mod.read(task_id, root=ws_root), Status.ESCALATED)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_fix_workspace_opened"]


def test_an_enqueue_failure_rolls_the_workspace_back_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("queue down")

    monkeypatch.setattr(queue, "enqueue", _boom)

    summary, audit = run_check(queue, ws_root)

    assert sec_workspaces(ws_root) == []
    assert load_ledger(DEFAULT_FORGE_TARGET) == Ledger()
    assert actions(audit) == ["vuln_workspace_failed"]
    assert audit[0]["kind"] == "fix"
    assert audit[0]["advisory_ids"] == ["GHSA-aaaa"]
    assert summary == "advisory check complete: 0 workspace(s) opened, 0 already covered"


def test_the_next_check_retries_after_a_rolled_back_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    real_enqueue = queue.enqueue
    calls = {"n": 0}

    def _flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("queue down")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue", _flaky)
    run_check(queue, ws_root)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_fix_workspace_opened"]
    assert len(sec_workspaces(ws_root)) == 1


def test_a_workspace_creation_failure_is_recorded_without_aborting_the_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(workspace_mod, "create", _boom)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_workspace_failed"]
    assert load_ledger(DEFAULT_FORGE_TARGET) == Ledger()


def test_a_ledger_write_failure_removes_every_workspace_it_just_opened(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("read-only state dir")

    monkeypatch.setattr(dep_advisory, "save_ledger", _boom)

    _, audit = run_check(queue, ws_root)

    assert sec_workspaces(ws_root) == []
    assert actions(audit) == ["vuln_ledger_write_failed"]
    assert audit[0]["workspaces"] == 1


def test_a_failing_rollback_is_logged_and_the_check_still_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("queue down")

    def _stuck_delete(*_a: object, **_k: object) -> None:
        raise OSError("cannot delete")

    monkeypatch.setattr(queue, "enqueue", _boom)
    monkeypatch.setattr(workspace_mod, "delete", _stuck_delete)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_workspace_failed"]
    assert "rollback" in caplog.text


def test_an_engine_below_propose_gets_an_escalated_posture_workspace_not_a_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    _, audit = run_check(queue, ws_root, posture=Posture.observe)

    (ws,) = sec_workspaces(ws_root)
    (handover,) = list_handovers(ws)
    assert ws.meta.status == Status.ESCALATED
    assert pending_teams(queue, ws) == []
    assert actions(audit) == ["vuln_posture_workspace_opened"]
    assert "no fix was dispatched" in handover.full_synthesis
    assert "uv lock --upgrade-package urllib3==2.7.5" in handover.full_synthesis
    assert load_ledger(DEFAULT_FORGE_TARGET).entries["GHSA-aaaa"].kind == "posture"


def test_the_posture_workspace_is_not_deferred_by_an_active_fix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)
    _seed_active_fix(ws_root, age_s=60)

    _, audit = run_check(queue, ws_root, posture=Posture.observe)

    assert actions(audit) == ["vuln_posture_workspace_opened"]


def test_an_engine_at_propose_is_dispatched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    run_check(queue, ws_root, posture=Posture.propose)

    (ws,) = sec_workspaces(ws_root)
    assert pending_teams(queue, ws) == ["implementation"]


def test_a_workspace_is_tagged_with_the_engine_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _URLLIB3)

    run_check(queue, ws_root, engine_key="acme/widget")

    (ws,) = sec_workspaces(ws_root)
    assert ws.meta.engine_key == "acme/widget"
