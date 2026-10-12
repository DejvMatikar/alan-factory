from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from alan import workspace as workspace_mod
from alan.dep_advisory_seed import seed_advisory_checks
from alan.engine_config import EngineConfig
from alan.handover import list_handovers
from alan.queue import JobStatus, Queue
from alan.teams import triage as triage_mod
from alan.teams._base import TeamEvent
from alan.teams.dep_advisory import DEP_ADVISORY_TITLE, checked_run_key
from alan.teams.dependency_sweep import DependencySweepTeam
from alan.teams.triage import TriageTeam
from alan.teams.triage_pregate import gather_ci_surfaces_for
from alan.workspace import Status, Workspace
from tests.dep_advisory_support import (
    PYPROJECT,
    SpawnStub,
    audit_row,
    audit_stdout,
    install_spawns,
    isolate_state,
    register_engine,
    serve_repo,
    vuln_entry,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import rows_read

_SLUG = "acme/widget"
_FIXABLE = audit_row("pkg", "2.2.0", vuln_entry("GHSA-aaaa", "2.2.5", "2.3.0"))
_NO_FIX = audit_row("pyjwt", "2.12.1", vuln_entry("PYSEC-2099-1"))


def _emit(event: TeamEvent) -> None:
    del event


class _World:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        queue: Queue,
        ws_root: Path,
        fake: FakeForge,
    ) -> None:
        isolate_state(monkeypatch, tmp_path)
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        self.live_pyproject = checkout / "pyproject.toml"
        self.live_pyproject.write_text(PYPROJECT, encoding="utf-8")
        monkeypatch.chdir(checkout)
        self.digest = self._digest()
        self.queue = queue
        self.ws_root = ws_root
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.config: EngineConfig = register_engine()
        self.run_id = 900
        serve_repo(fake)
        fake.stub("latest_branch_run", lambda *_a, **_k: rows_read([self._red_run()]))
        config = self.config
        monkeypatch.setattr(
            triage_mod, "gather_all", lambda *_a, **_k: gather_ci_surfaces_for(config)
        )
        self.spawns = install_spawns(
            monkeypatch, SpawnStub(stdout=audit_stdout(_FIXABLE), audit_rc=1)
        )
        self.triage = TriageTeam(
            queue=queue,
            workspaces_root=ws_root,
            on_main_ci_red=partial(seed_advisory_checks, queue, ws_root),
        )
        self.dispatched: list[str] = []

    def _digest(self) -> str:
        return hashlib.sha256(self.live_pyproject.read_bytes()).hexdigest()

    def _red_run(self) -> dict[str, object]:
        return {
            "databaseId": self.run_id,
            "status": "completed",
            "conclusion": "failure",
            "headSha": "deadbeef",
        }

    def use_audit(self, stub: SpawnStub) -> None:
        self.spawns = install_spawns(self.monkeypatch, stub)

    def sweep(self) -> None:
        self.triage._run_sensors()  # noqa: SLF001

    def drain(self) -> None:
        team = DependencySweepTeam(queue=self.queue, workspaces_root=self.ws_root)
        while (job := self.queue.claim()) is not None:
            if job.team != "dependency_sweep":
                self.dispatched.append(job.team)
                continue
            ws = workspace_mod.read(job.task_id, root=self.ws_root)
            team.run(ws, None, _emit, inv_id=job.triggered_by_invocation_id or "")
            self.queue.complete(job.id)
            workspace_mod.transition(ws, Status.COMPLETED)

    def advisory_workspaces(self) -> list[Workspace]:
        return [
            ws for ws in workspace_mod.list_workspaces(root=self.ws_root)
            if ws.meta.title == DEP_ADVISORY_TITLE
        ]

    def sec_workspaces(self) -> list[Workspace]:
        return [
            ws for ws in workspace_mod.list_workspaces(root=self.ws_root)
            if ws.meta.title.startswith("sec/")
        ]

    def audit_entries(self) -> list[dict[str, Any]]:
        path = self.tmp_path / "logs" / "dep_sweep_audit.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [entry for row in rows for entry in row.get("entries", [])]

    def actions(self) -> list[str]:
        return [entry["action"] for entry in self.audit_entries()]

    def assert_invariants(self) -> None:
        assert self.queue.get_meta(f"dep_sweep_next_run:{_SLUG}") is None
        assert self._digest() == self.digest
        assert not any(argv[0] == "gh" for argv, _cwd in self.spawns.calls)


@pytest.fixture
def world(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> _World:
    return _World(monkeypatch, tmp_path, queue, ws_root, _active_fake_forge)


def test_a_red_main_run_opens_one_fix_workspace_with_the_lowest_clearing_version(
    world: _World,
) -> None:
    world.sweep()
    (advisory,) = world.advisory_workspaces()
    assert len(world.queue.list_jobs(task_id=advisory.task_id, status=JobStatus.PENDING)) == 1
    assert advisory.meta.engine_key == _SLUG

    world.drain()

    (fix,) = world.sec_workspaces()
    brief = list_handovers(fix)[0].plan_synthesis or ""
    assert fix.meta.title == "sec/pkg: GHSA-aaaa"
    assert "pkg==2.2.5" in brief
    assert "pkg==2.3.0" not in brief
    assert [job.team for job in world.queue.list_jobs(task_id=fix.task_id)] == ["implementation"]
    assert world.actions() == ["vuln_fix_workspace_opened"]
    world.assert_invariants()


def test_a_second_sweep_over_the_same_run_id_enqueues_nothing(world: _World) -> None:
    world.sweep()
    world.drain()
    assert world.queue.get_meta(checked_run_key(_SLUG)) == "900"
    audits_before = len(world.audit_entries())

    world.sweep()
    world.drain()

    (advisory,) = world.advisory_workspaces()
    assert world.queue.list_jobs(task_id=advisory.task_id, status=JobStatus.PENDING) == []
    assert len(world.audit_entries()) == audits_before
    assert len(world.sec_workspaces()) == 1
    world.assert_invariants()


def test_a_new_run_id_audits_again_and_finds_the_advisory_already_covered(
    world: _World,
) -> None:
    world.sweep()
    world.drain()

    world.run_id = 901
    world.sweep()
    world.drain()

    assert world.actions() == ["vuln_fix_workspace_opened", "vuln_already_covered"]
    assert len(world.sec_workspaces()) == 1
    assert len(world.advisory_workspaces()) == 1
    assert world.queue.get_meta(checked_run_key(_SLUG)) == "901"
    world.assert_invariants()


def test_a_no_fix_advisory_ends_escalated_with_nothing_dispatched(world: _World) -> None:
    world.use_audit(SpawnStub(stdout=audit_stdout(_NO_FIX), audit_rc=1))

    world.sweep()
    world.drain()

    (ws,) = world.sec_workspaces()
    assert ws.meta.status == Status.ESCALATED
    assert ws.meta.title == "sec/pyjwt: PYSEC-2099-1 (no fix)"
    assert world.dispatched == []
    assert world.actions() == ["vuln_no_fix_workspace_opened"]
    world.assert_invariants()


def test_an_audit_that_fails_to_start_is_recorded_and_retried_on_the_next_sweep(
    world: _World,
) -> None:
    world.use_audit(SpawnStub(raises=FileNotFoundError("uv")))
    world.sweep()
    world.drain()
    assert world.actions() == ["vuln_audit_failed"]
    assert world.queue.get_meta(checked_run_key(_SLUG)) is None
    assert world.sec_workspaces() == []

    world.use_audit(SpawnStub(stdout=audit_stdout(_FIXABLE), audit_rc=1))
    world.sweep()
    world.drain()

    assert world.actions() == ["vuln_audit_failed", "vuln_fix_workspace_opened"]
    assert len(world.sec_workspaces()) == 1
    assert world.queue.get_meta(checked_run_key(_SLUG)) == "900"
    world.assert_invariants()


def test_a_green_main_run_triggers_no_audit(
    world: _World, _active_fake_forge: FakeForge
) -> None:
    _active_fake_forge.stub(
        "latest_branch_run",
        lambda *_a, **_k: rows_read(
            [{"databaseId": 9, "status": "completed", "conclusion": "success", "headSha": "x"}]
        ),
    )

    world.sweep()

    assert world.advisory_workspaces() == []
    assert world.spawns.calls == []
    world.assert_invariants()
