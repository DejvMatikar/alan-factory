from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from alan import dep_advisory_seed
from alan import reconciler as reconciler_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.app.workspace_creation import is_hidden_by_default
from alan.dep_sweep import PeerCapBlock, Proposal, ProposalBatch
from alan.engine_config import DEFAULT_FORGE_TARGET, Posture
from alan.queue import JobStatus, Queue
from alan.teams import dep_advisory
from alan.teams._base import TeamEvent
from alan.teams._dep_advisory_audit import AuditResult
from alan.teams.dep_advisory import (
    ADVISORY_SPEC,
    DEP_ADVISORY_TITLE,
    Ledger,
    LedgerEntry,
    is_advisory_workspace,
    save_ledger,
)
from alan.teams.dependency_sweep import (
    ROLE_SPEC,
    DependencySweepTeam,
    SweepContext,
    _dispatch_batch,  # noqa: SLF001
    _run_sweep,  # noqa: SLF001
    _write_audit,  # noqa: SLF001
    load_dependency_sweep_status,
)
from alan.workspace import Status, Workspace
from tests.dep_advisory_support import (
    audit_row,
    forge_target_of,
    isolate_state,
    prepare,
    register_engine,
    sec_workspaces,
    vuln_entry,
)
from tests.fakes.forge import FakeForge

_SLUG = "acme/widget"
_CTX = SweepContext(forge=DEFAULT_FORGE_TARGET, run_vuln_scan=True)


def _emit(event: TeamEvent) -> None:
    del event


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_state(monkeypatch, tmp_path)


def _advisory_ws(ws_root: Path) -> tuple[Workspace, str]:
    ws = workspace_mod.create(
        DEP_ADVISORY_TITLE, "d", team="dependency_sweep", root=ws_root, engine_key=_SLUG
    )
    inv = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ADVISORY_SPEC], source_task_id=ws.task_id
    )
    return ws, inv


def _weekly_ws(ws_root: Path) -> tuple[Workspace, str]:
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root, engine_key=_SLUG
    )
    inv = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ROLE_SPEC], source_task_id=ws.task_id
    )
    return ws, inv


def _clean_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dep_advisory, "audit_locked_dependencies", lambda _f: AuditResult("ok"))


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> str:
        calls.append(kwargs)
        return "spy"

    monkeypatch.setattr(dep_advisory, "run_advisory_check", _fake)
    return calls


def _audit_rows(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "logs" / "dep_sweep_audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_advisory_mode_never_lists_dependabot_proposals(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    register_engine()
    _clean_audit(monkeypatch)
    ws, inv = _advisory_ws(ws_root)
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    def _forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("the Dependabot half must not run in advisory mode")

    with patch("alan.teams.dependency_sweep._fetch_open_proposals", _forbidden):
        handover = team.run(ws, None, _emit, inv_id=inv)

    assert handover.handover_type == "terminal"
    assert handover.full_synthesis.startswith("advisory check complete")
    assert "open_prs_by_author" not in {name for name, _a, _k in _active_fake_forge.calls}


def test_advisory_mode_leaves_the_weekly_cadence_untouched(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path
) -> None:
    config = register_engine()
    _clean_audit(monkeypatch)
    queue.set_meta(f"dep_sweep_next_run:{_SLUG}", "1234.5")
    ws, inv = _advisory_ws(ws_root)
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    team.run(ws, None, _emit, inv_id=inv)

    assert queue.get_meta(f"dep_sweep_next_run:{_SLUG}") == "1234.5"
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_SLUG}") is None
    assert load_dependency_sweep_status(forge_target_of(config)) is None
    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []


def test_advisory_mode_audit_row_carries_the_event_trigger(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    register_engine()
    _clean_audit(monkeypatch)
    ws, inv = _advisory_ws(ws_root)

    DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    (row,) = [r for r in _audit_rows(tmp_path) if r["action"] == "sweep_complete"]
    assert row["trigger"] == "main_ci_red"
    assert [entry["action"] for entry in row["entries"]] == ["vuln_none_found"]
    assert row["entries"][0]["trigger"] == "main_ci_red"


def test_advisory_mode_runs_the_check_for_the_workspace_engine_with_its_live_posture(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path
) -> None:
    config = register_engine(posture=Posture.propose)
    calls = _spy(monkeypatch)
    ws, inv = _advisory_ws(ws_root)

    DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    (call,) = calls
    assert call["trigger"] == "main_ci_red"
    assert call["forge"] == forge_target_of(config)
    assert call["posture"] == Posture.propose
    assert call["engine_key"] == _SLUG
    assert call["workspaces_root"] == ws_root


def test_advisory_mode_skips_an_engine_that_has_not_opted_into_the_scan(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    register_engine(vuln_scan=False)
    calls = _spy(monkeypatch)
    ws, inv = _advisory_ws(ws_root)

    handover = DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(
        ws, None, _emit, inv_id=inv
    )

    assert calls == []
    assert "python vulnerability scan is not enabled" in handover.full_synthesis
    (row,) = [r for r in _audit_rows(tmp_path) if r["action"] == "sweep_complete"]
    assert row["entries"] == [{"action": "vuln_scan_disabled", "trigger": "main_ci_red"}]


def test_advisory_mode_end_to_end_opens_the_fix_workspace(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    config = register_engine()
    prepare(
        monkeypatch, tmp_path, _active_fake_forge,
        audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.8.0")),
    )
    ws, inv = _advisory_ws(ws_root)

    DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    (fix,) = sec_workspaces(ws_root)
    assert fix.meta.engine_key == _SLUG
    assert [job.team for job in queue.list_jobs(task_id=fix.task_id)] == ["implementation"]
    assert dep_advisory.load_ledger(forge_target_of(config)).entries["GHSA-aaaa"].task_id == (
        fix.task_id
    )


def test_weekly_mode_runs_both_halves_with_the_weekly_trigger_and_reschedules(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    config = register_engine()
    calls = _spy(monkeypatch)
    ws, inv = _weekly_ws(ws_root)
    team = DependencySweepTeam(queue=queue, workspaces_root=ws_root)

    with patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]) as listing:
        team.run(ws, None, _emit, inv_id=inv)

    listing.assert_called_once()
    (call,) = calls
    assert call["trigger"] == "weekly"
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_SLUG}") is not None
    assert load_dependency_sweep_status(forge_target_of(config)) is not None
    (row,) = [r for r in _audit_rows(tmp_path) if r["action"] == "sweep_complete"]
    assert row["trigger"] == "weekly"


def test_weekly_mode_stamps_the_trigger_on_every_proposal_entry(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path, tmp_path: Path
) -> None:
    register_engine()
    _spy(monkeypatch)
    ws, inv = _weekly_ws(ws_root)
    lockfiles = frozenset({"alan/web/package-lock.json"})
    blocked = PeerCapBlock(dependency="eslint", capping_package="plugin", peer_range="^8")
    proposals = [
        Proposal(7, "open", "MERGEABLE", lockfiles, upstream_block=blocked),
        Proposal(8, "open", "MERGEABLE", lockfiles),
    ]

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=()),
        patch("alan.teams.dependency_sweep._to_proposals", return_value=proposals),
    ):
        DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    (row,) = [r for r in _audit_rows(tmp_path) if r["action"] == "sweep_complete"]
    assert [entry["action"] for entry in row["entries"]] == [
        "escalate_upstream_blocked",
        "dispatch",
    ]
    assert {entry["trigger"] for entry in row["entries"]} == {"weekly"}
    assert row["entry_count"] == 2


def test_an_entry_that_already_carries_a_trigger_keeps_it_in_the_audit_row(
    ws_root: Path, tmp_path: Path
) -> None:
    ws, _ = _advisory_ws(ws_root)

    _write_audit(
        ws,
        "inv_001",
        [{"action": "vuln_none_found", "trigger": "main_ci_red"}, {"action": "dispatch"}],
        trigger="weekly",
    )

    (row,) = _audit_rows(tmp_path)
    assert [entry["trigger"] for entry in row["entries"]] == ["main_ci_red", "weekly"]


def test_weekly_mode_without_the_scan_never_runs_the_advisory_check(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path
) -> None:
    register_engine(vuln_scan=False)
    calls = _spy(monkeypatch)
    ws, inv = _weekly_ws(ws_root)

    with patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]):
        DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    assert calls == []


def test_weekly_mode_hands_the_engine_posture_to_the_check(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, ws_root: Path
) -> None:
    register_engine(posture=Posture.observe)
    calls = _spy(monkeypatch)
    ws, inv = _weekly_ws(ws_root)

    with patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]):
        DependencySweepTeam(queue=queue, workspaces_root=ws_root).run(ws, None, _emit, inv_id=inv)

    assert calls[0]["posture"] == Posture.observe


def _uv_batch() -> tuple[ProposalBatch, dict[int, Proposal]]:
    proposal = Proposal(
        number=7, state="open", mergeable="MERGEABLE", lockfiles=frozenset({"uv.lock"})
    )
    batch = ProposalBatch(lockfiles=frozenset({"uv.lock"}), proposal_numbers=(7,))
    return batch, {7: proposal}


def _seed_fix(ws_root: Path, status: Status) -> None:
    ws = workspace_mod.create("sec/zlib: GHSA-old", "d", team="dependency_sweep", root=ws_root)
    if status != Status.ACTIVE:
        workspace_mod.transition(ws, status)
    entry = LedgerEntry(
        advisory_id="GHSA-old",
        task_id=ws.task_id,
        kind="fix",
        recorded_at=datetime.now(UTC).timestamp(),
    )
    save_ledger(DEFAULT_FORGE_TARGET, Ledger(entries={"GHSA-old": entry}))


def test_the_uv_lock_batch_is_skipped_while_an_advisory_fix_is_in_flight(
    queue: Queue, ws_root: Path
) -> None:
    _seed_fix(ws_root, Status.ACTIVE)
    batch, proposals = _uv_batch()
    audit: list[dict[str, Any]] = []

    _dispatch_batch(batch, proposals, queue, ws_root, audit, ctx=_CTX)

    assert audit == []
    assert not any(ws.meta.title == "dep/7" for ws in workspace_mod.list_workspaces(root=ws_root))


def test_the_uv_lock_batch_dispatches_once_the_advisory_fix_is_no_longer_active(
    queue: Queue, ws_root: Path
) -> None:
    _seed_fix(ws_root, Status.ESCALATED)
    batch, proposals = _uv_batch()
    audit: list[dict[str, Any]] = []

    _dispatch_batch(batch, proposals, queue, ws_root, audit, ctx=_CTX)

    assert [entry["action"] for entry in audit] == ["dispatch"]


def test_a_fix_opened_by_the_same_weekly_run_holds_back_the_uv_lock_batch(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(
        monkeypatch, tmp_path, _active_fake_forge,
        audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.8.0")),
    )
    _, proposals = _uv_batch()
    audit: list[dict[str, Any]] = []

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=()),
        patch("alan.teams.dependency_sweep._to_proposals", return_value=list(proposals.values())),
    ):
        _run_sweep(queue, ws_root, audit, ctx=_CTX)

    assert [entry["action"] for entry in audit] == ["vuln_fix_workspace_opened"]
    assert len(sec_workspaces(ws_root)) == 1
    assert not any(ws.meta.title == "dep/7" for ws in workspace_mod.list_workspaces(root=ws_root))


def test_the_uv_lock_batch_still_dispatches_when_the_weekly_run_opens_no_fix(
    monkeypatch: pytest.MonkeyPatch,
    queue: Queue,
    ws_root: Path,
    tmp_path: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge)
    _, proposals = _uv_batch()
    audit: list[dict[str, Any]] = []

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=()),
        patch("alan.teams.dependency_sweep._to_proposals", return_value=list(proposals.values())),
    ):
        _run_sweep(queue, ws_root, audit, ctx=_CTX)

    assert [entry["action"] for entry in audit] == ["vuln_none_found", "dispatch"]


def test_an_npm_lock_batch_is_not_blocked_by_an_advisory_fix(
    queue: Queue, ws_root: Path
) -> None:
    _seed_fix(ws_root, Status.ACTIVE)
    proposal = Proposal(
        number=8, state="open", mergeable="MERGEABLE",
        lockfiles=frozenset({"alan/web/package-lock.json"}),
    )
    batch = ProposalBatch(lockfiles=proposal.lockfiles, proposal_numbers=(8,))
    audit: list[dict[str, Any]] = []

    _dispatch_batch(batch, {8: proposal}, queue, ws_root, audit, ctx=_CTX)

    assert [entry["action"] for entry in audit] == ["dispatch"]


def test_the_weekly_parent_lookup_ignores_the_advisory_workspace(ws_root: Path) -> None:
    config = register_engine()
    advisory, _ = _advisory_ws(ws_root)
    weekly, _ = _weekly_ws(ws_root)

    assert reconciler_mod._is_dep_sweep_parent_for(advisory, config) is False  # noqa: SLF001
    assert reconciler_mod._is_dep_sweep_parent_for(weekly, config) is True  # noqa: SLF001


def test_the_weekly_seed_creates_its_own_parent_when_only_an_advisory_workspace_exists(
    queue: Queue, ws_root: Path
) -> None:
    config = register_engine()
    advisory, _ = _advisory_ws(ws_root)

    created = reconciler_mod._dep_sweep_workspace_if_idle(queue, ws_root, config)  # noqa: SLF001

    assert created is not None
    assert created.task_id != advisory.task_id
    assert created.meta.title == "Dependency Sweep"


def test_the_advisory_lookup_ignores_the_weekly_parent(ws_root: Path) -> None:
    config = register_engine()
    _weekly_ws(ws_root)

    assert dep_advisory_seed._find_advisory_workspace(ws_root, config) is None  # noqa: SLF001


def test_the_advisory_lookup_is_scoped_to_one_engine(ws_root: Path) -> None:
    config = register_engine()
    other = register_engine("other-org", "gadget")
    advisory, _ = _advisory_ws(ws_root)

    found = dep_advisory_seed._find_advisory_workspace(ws_root, config)  # noqa: SLF001

    assert found is not None
    assert found.task_id == advisory.task_id
    assert dep_advisory_seed._find_advisory_workspace(ws_root, other) is None  # noqa: SLF001


def test_is_advisory_workspace_needs_both_the_team_and_the_title(ws_root: Path) -> None:
    advisory, _ = _advisory_ws(ws_root)
    wrong_team = workspace_mod.create(DEP_ADVISORY_TITLE, "d", team="triage", root=ws_root)
    wrong_title = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=ws_root
    )

    assert is_advisory_workspace(advisory) is True
    assert is_advisory_workspace(wrong_team) is False
    assert is_advisory_workspace(wrong_title) is False


def test_the_advisory_check_workspace_is_hidden_but_fix_workspaces_are_not() -> None:
    assert is_hidden_by_default("dependency_sweep", DEP_ADVISORY_TITLE) is True
    assert is_hidden_by_default("dependency_sweep", "sec/urllib3: GHSA-aaaa") is False
    assert is_hidden_by_default("dependency_sweep", "sec/urllib3: GHSA-aaaa (no fix)") is False
    assert is_hidden_by_default("implementation", DEP_ADVISORY_TITLE) is False
