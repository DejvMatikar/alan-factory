from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.dispatch_provenance import DispatchSurface
from alan.engine_config import DEFAULT_FORGE_TARGET, Posture
from alan.handover import list_handovers
from alan.queue import JobStatus, Queue
from alan.teams.dep_advisory import Ledger, LedgerEntry, load_ledger, save_ledger
from alan.workspace import Status
from tests.dep_advisory_support import (
    actions,
    audit_row,
    pending_teams,
    prepare,
    run_check,
    sec_workspaces,
    vuln_entry,
)
from tests.fakes.forge import FakeForge

_NO_FIX = audit_row("pyjwt", "2.12.1", vuln_entry("PYSEC-2099-1"))
_FIXABLE = audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.8.0"))
_NOW_FIXABLE = audit_row("pyjwt", "2.12.1", vuln_entry("PYSEC-2099-1", "2.13.0"))


def test_an_advisory_with_no_fix_escalates_straight_away(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)

    summary, audit = run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    assert ws.meta.title == "sec/pyjwt: PYSEC-2099-1 (no fix)"
    assert ws.meta.status == Status.ESCALATED
    assert ws.meta.team == "dependency_sweep"
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.DEP_ADVISORY_FIX
    assert actions(audit) == ["vuln_no_fix_workspace_opened"]
    assert summary == "advisory check complete: 1 workspace(s) opened, 0 already covered"


def test_the_escalation_message_names_advisory_package_version_and_missing_fix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    (handover,) = list_handovers(ws)

    assert handover.handover_type == "terminal"
    assert "PYSEC-2099-1" in handover.full_synthesis
    assert "`pyjwt`" in handover.full_synthesis
    assert "installed 2.12.1" in handover.full_synthesis
    assert "no fixed release exists" in handover.full_synthesis


def test_a_no_fix_workspace_never_enqueues_an_implementation_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)

    assert pending_teams(queue, ws) == []
    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.RUNNING) == []


def test_a_mixed_run_opens_one_fix_workspace_and_one_no_fix_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE, _NO_FIX)

    _, audit = run_check(queue, ws_root)

    by_title = {ws.meta.title: ws for ws in sec_workspaces(ws_root)}
    assert set(by_title) == {"sec/urllib3: GHSA-aaaa", "sec/pyjwt: PYSEC-2099-1 (no fix)"}
    assert by_title["sec/urllib3: GHSA-aaaa"].meta.status == Status.ACTIVE
    assert pending_teams(queue, by_title["sec/urllib3: GHSA-aaaa"]) == ["implementation"]
    assert by_title["sec/pyjwt: PYSEC-2099-1 (no fix)"].meta.status == Status.ESCALATED
    assert sorted(actions(audit)) == ["vuln_fix_workspace_opened", "vuln_no_fix_workspace_opened"]


def test_several_no_fix_advisories_share_one_escalated_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    other = audit_row("fastar", "0.9.0", vuln_entry("MAL-2099-2"))
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX, other)

    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    (handover,) = list_handovers(ws)
    assert ws.meta.title == "sec/fastar: MAL-2099-2 (+1 more) (no fix)"
    assert "PYSEC-2099-1" in handover.full_synthesis
    assert "MAL-2099-2" in handover.full_synthesis


def test_a_repeated_no_fix_advisory_does_not_re_escalate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)

    _, audit = run_check(queue, ws_root)

    assert len(sec_workspaces(ws_root)) == 1
    assert actions(audit) == ["vuln_already_covered"]


def test_a_no_fix_advisory_that_becomes_fixable_gets_a_fix_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NOW_FIXABLE)

    _, audit = run_check(queue, ws_root)

    titles = sorted(ws.meta.title for ws in sec_workspaces(ws_root))
    assert titles == ["sec/pyjwt: PYSEC-2099-1", "sec/pyjwt: PYSEC-2099-1 (no fix)"]
    assert actions(audit) == ["vuln_fix_workspace_opened"]


def test_an_archived_no_fix_workspace_escalates_again_while_main_stays_red(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)
    (first,) = sec_workspaces(ws_root)
    workspace_mod.transition(first, Status.ARCHIVED)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_no_fix_workspace_opened"]
    assert len(sec_workspaces(ws_root)) == 2


def test_the_ledger_records_a_no_fix_entry_per_advisory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)
    run_check(queue, ws_root)

    (ws,) = sec_workspaces(ws_root)
    entry = load_ledger(DEFAULT_FORGE_TARGET).entries["PYSEC-2099-1"]

    assert (entry.kind, entry.task_id) == ("no_fix", ws.task_id)


def test_no_fix_escalation_does_not_depend_on_the_engine_posture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _NO_FIX)

    _, audit = run_check(queue, ws_root, posture=Posture.observe)

    assert actions(audit) == ["vuln_no_fix_workspace_opened"]


def test_a_deferred_fix_does_not_hold_back_the_no_fix_escalation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE, _NO_FIX)
    busy = workspace_mod.create("sec/zlib: X-1", "d", team="dependency_sweep", root=ws_root)
    entry = LedgerEntry(
        advisory_id="X-1", task_id=busy.task_id, kind="fix",
        recorded_at=datetime.now(UTC).timestamp(),
    )
    save_ledger(DEFAULT_FORGE_TARGET, Ledger(entries={"X-1": entry}))

    _, audit = run_check(queue, ws_root)

    assert sorted(actions(audit)) == ["vuln_fix_deferred", "vuln_no_fix_workspace_opened"]
