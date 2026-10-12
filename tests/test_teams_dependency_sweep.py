from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alan import handover as handover_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.dep_sweep import (
    PeerCapBlock,
    Proposal,
    ProposalBatch,
    ProposalClass,
)
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    DependencySweepConfig,
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    RepoIdentity,
    register,
)
from alan.forge import JsonObject
from alan.queue import JobStatus, Queue
from alan.teams import _pr_branch, dep_advisory
from alan.teams._base import TeamEvent
from alan.teams._dep_advisory_audit import AuditResult
from alan.teams.dependency_sweep import (
    ROLE_SPEC,
    TEAM,
    DependencySweepStatus,
    DependencySweepTeam,
    SweepContext,
    _act_on_proposal,
    _detect_upstream_block,
    _dispatch_batch,
    _dispatch_proposal,
    _ensure_pr_workspace,
    _escalate_upstream_blocked,
    _escalation_message,
    _fetch_open_proposals,
    _fetch_pr_files,
    _find_workspace_for_pr,
    _is_in_flight,
    _lockfiles_in,
    _make_bootstrap_handover,
    _needs_human,
    _run_sweep,
    _team_specs,
    _to_proposals,
    _touches_npm_lockfile,
    _write_audit,
    load_dependency_sweep_status,
)
from alan.workspace import Status
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read

_ALAN_SWEEP_CTX = SweepContext(forge=DEFAULT_FORGE_TARGET, run_vuln_scan=True)


def _noop_emit(event: TeamEvent) -> None:
    pass


def _seed_dep_sweep_ws(
    tmp_path: Path, queue: Queue
) -> tuple[workspace_mod.Workspace, str]:
    ws = workspace_mod.create(
        "dep/1", "test dep sweep", team="dependency_sweep", root=tmp_path
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ROLE_SPEC], source_task_id=ws.task_id
    )
    return ws, inv_id


def _make_queue(tmp_path: Path) -> Queue:
    return Queue(db_path=tmp_path / "queue.db")


def _proposal(number: int, *, mergeable: str = "MERGEABLE") -> Proposal:
    return Proposal(number=number, state="open", mergeable=mergeable,
                    lockfiles=frozenset({"uv.lock"}))


_REAL_ERESOLVE = (
    "npm error code ERESOLVE\n"
    "npm error Conflicting peer dependency: typescript@6.0.3\n"
    'npm error   peer typescript@">=4.8.4 <6.1.0" from typescript-eslint@8.64.0\n'
    'npm error     dev typescript-eslint@"^8.64.0" from the root project'
)


@pytest.fixture(autouse=True)
def _neutralize_upstream_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *_a, **_k: [])


@pytest.fixture(autouse=True)
def _quiet_advisory_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(dep_advisory, "audit_locked_dependencies", lambda _forge: AuditResult("ok"))


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def test_team_singleton_is_dep_sweep_team() -> None:
    assert isinstance(TEAM, DependencySweepTeam)
    assert TEAM.name == "dependency_sweep"
    assert TEAM.supports_resume is False


def test_role_spec_fields() -> None:
    assert ROLE_SPEC.id == "sweep_runner"
    assert ROLE_SPEC.role == "sweep_runner"
    assert ROLE_SPEC.model == "none"


def test_plan_workflow_returns_role_spec(tmp_path: Path) -> None:
    ws = workspace_mod.create("t", "d", team="dependency_sweep", root=tmp_path)
    team = DependencySweepTeam()
    specs = team.plan_workflow(ws, None)
    assert specs == [ROLE_SPEC]


def test_run_rejects_resume_inv_id(tmp_path: Path) -> None:
    from alan.teams._base import ResumeUnsupportedError

    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    with pytest.raises(ResumeUnsupportedError):
        team.run(ws, None, _noop_emit, inv_id=inv_id, resume_inv_id="prev_inv")


def test_run_queue_not_wired_returns_terminal(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam()
    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.handover_type == "terminal"
    assert "not wired" in handover.full_synthesis


def test_run_happy_path_no_prs(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        handover = team.run(ws, None, _noop_emit, inv_id=inv_id)
    assert handover.handover_type == "terminal"
    assert "dep_sweep complete" in handover.full_synthesis
    pending = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(pending) == 1, "next sweep should be scheduled"


def test_run_dispatches_merge_ready_pr(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    raw = [{"number": 42, "mergeable": "MERGEABLE", "title": "Bump requests from 1 to 2"}]
    files_data = {"files": [{"path": "uv.lock"}]}

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files",
              return_value=["uv.lock"]),
        patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}),
    ):
        handover = team.run(ws, None, _noop_emit, inv_id=inv_id)

    del files_data
    assert handover.handover_type == "terminal"
    child_ws = _find_workspace_for_pr(42, tmp_path)
    assert child_ws is not None
    jobs = queue.list_jobs(task_id=child_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "pr_merge"


def test_run_dispatches_needs_rebase_pr(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    raw = [{"number": 55, "mergeable": "CONFLICTING", "title": "Bump fastapi 0.1 to 0.2"}]

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files", return_value=["uv.lock"]),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    child_ws = _find_workspace_for_pr(55, tmp_path)
    assert child_ws is not None
    jobs = queue.list_jobs(task_id=child_ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "implementation_fix"


def test_run_skips_merge_ready_when_switch_off(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    raw = [{"number": 77, "mergeable": "MERGEABLE", "title": "Bump lib 1 to 2"}]

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files", return_value=["uv.lock"]),
        patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "0"}),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert _find_workspace_for_pr(77, tmp_path) is None


def test_run_skips_in_flight_pr(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    raw = [{"number": 99, "mergeable": "MERGEABLE", "title": "Bump pkg 1 to 2"}]

    child_ws = workspace_mod.create(
        "dep/99", "dep/99", team="dependency_sweep", root=tmp_path
    )
    child_inv = workflow_mod.seed_pending_workflow(
        child_ws, team="pr_merge", tasks=[ROLE_SPEC]
    )
    queue.enqueue(child_ws.task_id, "pr_merge", triggered_by_invocation_id=child_inv)

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files", return_value=["uv.lock"]),
        patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    all_jobs = queue.list_jobs(task_id=child_ws.task_id, status=JobStatus.PENDING)
    assert len(all_jobs) == 1


def test_schedule_next_sweep_swallows_exception(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create("dep/sweep", "d", team="dependency_sweep", root=tmp_path)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    with patch.object(team, "_do_schedule_next_sweep", side_effect=RuntimeError("boom")):
        team._schedule_next_sweep(ws)  # noqa: SLF001 (test)


def test_do_schedule_next_sweep_skips_when_pending(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create("dep/sweep2", "d", team="dependency_sweep", root=tmp_path)
    inv = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ROLE_SPEC]
    )
    queue.enqueue(ws.task_id, "dependency_sweep", triggered_by_invocation_id=inv)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    before = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    team._do_schedule_next_sweep(ws)  # noqa: SLF001
    after = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(after) == len(before), "no new job should be added"


def test_do_schedule_next_sweep_queue_none(tmp_path: Path) -> None:
    ws = workspace_mod.create("dep/sweep3", "d", team="dependency_sweep", root=tmp_path)
    team = DependencySweepTeam()
    team._do_schedule_next_sweep(ws)  # noqa: SLF001 (no exception)


def test_fetch_open_proposals_returns_the_listing(forge: ForgeScript) -> None:
    listing: tuple[JsonObject, ...] = (
        {"number": 1, "mergeable": "MERGEABLE", "title": "Bump foo"},
    )
    forge.queue("open_prs_by_author", listing)
    assert _fetch_open_proposals(ctx=_ALAN_SWEEP_CTX) == listing


def test_fetch_open_proposals_returns_empty_when_the_port_lists_nothing(
    forge: ForgeScript,
) -> None:
    forge.queue("open_prs_by_author", ())
    assert _fetch_open_proposals(ctx=_ALAN_SWEEP_CTX) == ()


def test_fetch_pr_files_returns_paths(forge: ForgeScript) -> None:
    forge.queue(
        "read_pr",
        pr_read({"files": [{"path": "uv.lock"}, {"path": "alan/web/package-lock.json"}]}),
    )
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == ["uv.lock", "alan/web/package-lock.json"]


def test_fetch_pr_files_empty_on_error(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": [{"path": "uv.lock"}]}, returncode=1))
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == []


def test_fetch_pr_files_empty_on_non_list_files(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": "not-a-list"}))
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == []


def test_fetch_pr_files_skips_non_dict_items(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": ["not-a-dict", {"path": "uv.lock"}]}))
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == ["uv.lock"]


def test_fetch_pr_files_skips_non_string_path(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": [{"path": 42}, {"path": "uv.lock"}]}))
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == ["uv.lock"]


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("gh", 10), FileNotFoundError("gh not found")],
)
def test_fetch_pr_files_empty_when_the_read_raises(
    forge: ForgeScript, error: BaseException,
) -> None:
    forge.queue("read_pr", error)
    assert _fetch_pr_files(1, ctx=_ALAN_SWEEP_CTX) == []


def test_lockfiles_in_detects_known_files() -> None:
    paths = ["uv.lock", "alan/web/package-lock.json", "alan/other.py"]
    result = _lockfiles_in(paths)
    assert "uv.lock" in result
    assert "alan/web/package-lock.json" in result
    assert "alan/other.py" not in result


def test_lockfiles_in_empty_on_no_matches() -> None:
    assert _lockfiles_in(["setup.py", "requirements.txt"]) == frozenset()


def test_to_proposals_skips_invalid_number() -> None:
    raw: tuple[JsonObject, ...] = ({"number": "bad", "mergeable": "MERGEABLE", "title": "x"},)
    assert _to_proposals(raw, 10.0, ctx=_ALAN_SWEEP_CTX) == []


def test_to_proposals_normalizes_non_str_mergeable(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": []}))
    raw: tuple[JsonObject, ...] = ({"number": 5, "mergeable": None, "title": "x"},)
    proposals = _to_proposals(raw, 10.0, ctx=_ALAN_SWEEP_CTX)
    assert len(proposals) == 1
    assert proposals[0].mergeable == "UNKNOWN"


def test_to_proposals_normalizes_truthy_non_str_mergeable(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": []}))
    raw: tuple[JsonObject, ...] = ({"number": 6, "mergeable": 42, "title": "x"},)
    proposals = _to_proposals(raw, 10.0, ctx=_ALAN_SWEEP_CTX)
    assert len(proposals) == 1
    assert proposals[0].mergeable == "UNKNOWN"


def test_find_workspace_for_pr_returns_none_when_absent(tmp_path: Path) -> None:
    assert _find_workspace_for_pr(999, tmp_path) is None


def test_find_workspace_for_pr_returns_none_on_exception(tmp_path: Path) -> None:
    with patch.object(workspace_mod, "list_workspaces", side_effect=RuntimeError("io")):
        assert _find_workspace_for_pr(999, tmp_path) is None


def test_find_workspace_for_pr_returns_ws_when_present(tmp_path: Path) -> None:
    workspace_mod.create("dep/123", "d", team="dependency_sweep", root=tmp_path)
    result = _find_workspace_for_pr(123, tmp_path)
    assert result is not None
    assert result.meta.title == "dep/123"


def test_is_in_flight_returns_false_when_no_ws(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    assert not _is_in_flight(999, queue, tmp_path)


def test_is_in_flight_returns_false_when_no_jobs(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    workspace_mod.create("dep/77", "d", team="dependency_sweep", root=tmp_path)
    assert not _is_in_flight(77, queue, tmp_path)


def test_is_in_flight_returns_true_when_pending(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create("dep/88", "d", team="dependency_sweep", root=tmp_path)
    inv = workflow_mod.seed_pending_workflow(ws, team="pr_merge", tasks=[ROLE_SPEC])
    queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv)
    assert _is_in_flight(88, queue, tmp_path)


def test_needs_human_false_when_no_ws(tmp_path: Path) -> None:
    assert not _needs_human(999, tmp_path)


def test_needs_human_false_when_active(tmp_path: Path) -> None:
    workspace_mod.create("dep/501", "d", team="dependency_sweep", root=tmp_path)
    assert not _needs_human(501, tmp_path)


@pytest.mark.parametrize(
    "status",
    [Status.AWAITING_INPUT, Status.ESCALATED, Status.FAILED, Status.PAUSED],
)
def test_needs_human_true_for_terminal_status(tmp_path: Path, status: Status) -> None:
    ws = workspace_mod.create("dep/502", "d", team="dependency_sweep", root=tmp_path)
    workspace_mod.transition(ws, status)
    assert _needs_human(502, tmp_path)


def _uv_batch(*numbers: int) -> ProposalBatch:
    return ProposalBatch(lockfiles=frozenset({"uv.lock"}), proposal_numbers=numbers)


def test_dispatch_batch_skips_stuck_head_and_advances_to_sibling(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    stuck = workspace_mod.create("dep/450", "d", team="dependency_sweep", root=tmp_path)
    workspace_mod.transition(stuck, Status.AWAITING_INPUT)
    proposals = {450: _proposal(450), 451: _proposal(451)}
    audit: list[dict[str, Any]] = []

    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_batch(
            _uv_batch(450, 451), proposals, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX
        )

    assert [e["pr_number"] for e in audit] == [451]
    sibling = _find_workspace_for_pr(451, tmp_path)
    assert sibling is not None
    assert len(queue.list_jobs(task_id=sibling.task_id, status=JobStatus.PENDING)) == 1
    assert not queue.list_jobs(task_id=stuck.task_id, status=JobStatus.PENDING)


def test_dispatch_batch_dispatches_nothing_when_all_stuck(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    a = workspace_mod.create("dep/450", "d", team="dependency_sweep", root=tmp_path)
    workspace_mod.transition(a, Status.AWAITING_INPUT)
    b = workspace_mod.create("dep/451", "d", team="dependency_sweep", root=tmp_path)
    workspace_mod.transition(b, Status.ESCALATED)
    proposals = {450: _proposal(450), 451: _proposal(451)}
    audit: list[dict[str, Any]] = []

    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_batch(
            _uv_batch(450, 451), proposals, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX
        )

    assert audit == []
    assert not queue.list_jobs(task_id=a.task_id, status=JobStatus.PENDING)
    assert not queue.list_jobs(task_id=b.task_id, status=JobStatus.PENDING)


def test_dispatch_batch_skips_whole_batch_when_sibling_in_flight(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    stuck = workspace_mod.create("dep/450", "d", team="dependency_sweep", root=tmp_path)
    workspace_mod.transition(stuck, Status.AWAITING_INPUT)
    running = workspace_mod.create("dep/451", "d", team="dependency_sweep", root=tmp_path)
    inv = workflow_mod.seed_pending_workflow(running, team="pr_merge", tasks=[ROLE_SPEC])
    queue.enqueue(running.task_id, "pr_merge", triggered_by_invocation_id=inv)
    proposals = {450: _proposal(450), 451: _proposal(451)}
    audit: list[dict[str, Any]] = []

    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_batch(
            _uv_batch(450, 451), proposals, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX
        )

    assert audit == []
    assert not queue.list_jobs(task_id=stuck.task_id, status=JobStatus.PENDING)


def test_dispatch_batch_skips_missing_proposal_and_dispatches_next(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    proposals = {451: _proposal(451)}
    audit: list[dict[str, Any]] = []

    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_batch(
            _uv_batch(450, 451), proposals, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX
        )

    assert [e["pr_number"] for e in audit] == [451]
    assert _find_workspace_for_pr(450, tmp_path) is None
    assert _find_workspace_for_pr(451, tmp_path) is not None


def test_ensure_pr_workspace_creates_new(tmp_path: Path) -> None:
    ws = _ensure_pr_workspace(200, tmp_path)
    assert ws.meta.title == "dep/200"
    assert ws.meta.team == "dependency_sweep"


def test_ensure_pr_workspace_reuses_existing(tmp_path: Path) -> None:
    ws1 = workspace_mod.create("dep/201", "d", team="dependency_sweep", root=tmp_path)
    ws2 = _ensure_pr_workspace(201, tmp_path)
    assert ws1.task_id == ws2.task_id


def test_make_bootstrap_handover_fields(tmp_path: Path) -> None:
    ws = workspace_mod.create("dep/300", "d", team="dependency_sweep", root=tmp_path)
    h = _make_bootstrap_handover(ws, 300)
    assert h.pr_number == 300
    assert h.dependency_bot_authorized is True
    assert h.handover_type == "forward"
    assert h.to_team_suggestions == []
    assert h.task_id == ws.task_id


def test_team_specs_pr_merge() -> None:
    specs = _team_specs("pr_merge")
    assert len(specs) == 1
    assert specs[0].role == "pr_merge"


def test_team_specs_implementation_fix() -> None:
    specs = _team_specs("implementation_fix")
    assert len(specs) == 1
    assert specs[0].role == "implementer"


def test_dispatch_proposal_pr_merge(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_proposal(500, ProposalClass.MERGE_READY, queue, tmp_path)
    ws = _find_workspace_for_pr(500, tmp_path)
    assert ws is not None
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert jobs[0].team == "pr_merge"


def test_dispatch_proposal_implementation_fix_for_rebase(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    _dispatch_proposal(501, ProposalClass.NEEDS_REBASE, queue, tmp_path)
    ws = _find_workspace_for_pr(501, tmp_path)
    assert ws is not None
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert jobs[0].team == "implementation_fix"


def test_dispatch_proposal_skips_merge_ready_switch_off(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "0"}):
        _dispatch_proposal(502, ProposalClass.MERGE_READY, queue, tmp_path)
    assert _find_workspace_for_pr(502, tmp_path) is None


def test_dispatch_batch_skips_when_in_flight(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create("dep/600", "d", team="dependency_sweep", root=tmp_path)
    inv = workflow_mod.seed_pending_workflow(ws, team="pr_merge", tasks=[ROLE_SPEC])
    queue.enqueue(ws.task_id, "pr_merge", triggered_by_invocation_id=inv)

    proposal = _proposal(600)
    batch = ProposalBatch(lockfiles=frozenset({"uv.lock"}), proposal_numbers=(600,))
    proposals_by_num = {600: proposal}
    audit: list[dict[str, Any]] = []
    _dispatch_batch(batch, proposals_by_num, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX)
    assert audit == [], "in-flight PR must not generate new dispatch"


def test_dispatch_batch_skips_missing_proposal(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    batch = ProposalBatch(lockfiles=frozenset({"uv.lock"}), proposal_numbers=(700,))
    proposals_by_num: dict[int, Proposal] = {}
    audit: list[dict[str, Any]] = []
    _dispatch_batch(batch, proposals_by_num, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX)
    assert audit == []


def test_dispatch_batch_dispatches_first_ready(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    proposal = _proposal(800)
    batch = ProposalBatch(lockfiles=frozenset({"uv.lock"}), proposal_numbers=(800, 801))
    proposals_by_num = {800: proposal, 801: _proposal(801)}
    audit: list[dict[str, Any]] = []
    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_batch(batch, proposals_by_num, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX)
    assert len(audit) == 1
    assert audit[0]["pr_number"] == 800


_TITLE = "Security: CVE-9 in foo"


def test_run_sweep_returns_summary(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    audit: list[dict[str, Any]] = []
    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        summary = _run_sweep(queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX)
    assert "dep_sweep complete" in summary


def test_write_audit_writes_jsonl(tmp_path: Path) -> None:
    ws = workspace_mod.create("dep/audit", "d", team="dependency_sweep", root=tmp_path)
    audit = [{"action": "dispatch", "pr_number": 1}]
    with patch("alan.teams.dependency_sweep.logs_root", return_value=tmp_path):
        _write_audit(ws, "inv_001", audit, trigger="weekly")
    audit_file = tmp_path / "dep_sweep_audit.jsonl"
    assert audit_file.exists()
    line = json.loads(audit_file.read_text().splitlines()[0])
    assert line["task_id"] == ws.task_id
    assert line["entry_count"] == 1


def test_dependency_sweep_in_teams() -> None:
    import alan.teams as teams_mod

    assert "dependency_sweep" in teams_mod.TEAMS


def test_dependency_sweep_in_internal_only() -> None:
    import alan.teams as teams_mod

    assert "dependency_sweep" in teams_mod.INTERNAL_ONLY_TEAMS


def test_dependency_sweep_not_in_manually_dispatchable() -> None:
    import alan.teams as teams_mod

    assert "dependency_sweep" not in teams_mod.MANUALLY_DISPATCHABLE_TEAMS


def test_dependency_sweep_in_role_summaries() -> None:
    import alan.teams as teams_mod

    assert "dependency_sweep" in teams_mod.TEAM_ROLE_SUMMARIES


def test_detect_upstream_block_none_when_no_failing_job() -> None:
    assert _detect_upstream_block(42, 10.0, ctx=_ALAN_SWEEP_CTX) is None


def test_detect_upstream_block_scans_jobs_until_block_found(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *a, **k: [111, 222])
    logs = {111: "unrelated lint failure", 222: _REAL_ERESOLVE}
    monkeypatch.setattr(
        _pr_branch, "fetch_failed_log", lambda job_id, _t, **_k: logs[job_id]
    )
    block = _detect_upstream_block(42, 10.0, ctx=_ALAN_SWEEP_CTX)
    assert block is not None
    assert block.dependency == "typescript"
    assert block.capping_package == "typescript-eslint@8.64.0"


def test_detect_upstream_block_none_when_no_job_has_eresolve(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *a, **k: [111])
    monkeypatch.setattr(
        _pr_branch, "fetch_failed_log", lambda *a, **k: "some unrelated failure"
    )
    assert _detect_upstream_block(42, 10.0, ctx=_ALAN_SWEEP_CTX) is None


@pytest.mark.parametrize("lockfile", ["alan/web/package-lock.json", "alan/web/yarn.lock"])
def test_touches_npm_lockfile_true_for_npm_locks(lockfile: str) -> None:
    assert _touches_npm_lockfile([lockfile]) is True


def test_touches_npm_lockfile_false_for_python_locks() -> None:
    assert _touches_npm_lockfile(["uv.lock", "pyproject.toml"]) is False


def test_to_proposals_sets_upstream_block_for_npm_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.teams.dependency_sweep._fetch_pr_files",
                        lambda *a, **k: ["alan/web/package-lock.json"])
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", lambda *a, **k: [555])
    monkeypatch.setattr(_pr_branch, "fetch_failed_log", lambda *a, **k: _REAL_ERESOLVE)
    raw: tuple[JsonObject, ...] = (
        {"number": 698, "mergeable": "MERGEABLE", "title": "bump typescript"},
    )
    proposals = _to_proposals(raw, 10.0, ctx=_ALAN_SWEEP_CTX)
    assert len(proposals) == 1
    block = proposals[0].upstream_block
    assert block is not None
    assert block.dependency == "typescript"


def test_to_proposals_skips_block_probe_for_python_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.teams.dependency_sweep._fetch_pr_files",
                        lambda *a, **k: ["uv.lock"])
    probe = MagicMock()
    monkeypatch.setattr(_pr_branch, "fetch_failing_job_ids", probe)
    raw: tuple[JsonObject, ...] = (
        {"number": 5, "mergeable": "MERGEABLE", "title": "bump requests"},
    )
    proposals = _to_proposals(raw, 10.0, ctx=_ALAN_SWEEP_CTX)
    assert proposals[0].upstream_block is None
    probe.assert_not_called()


def test_escalation_message_contains_reason_and_ignore_snippet() -> None:
    block = PeerCapBlock("typescript", "typescript-eslint@8.64.0", ">=4.8.4 <6.1.0")
    msg = _escalation_message(698, block)
    assert "#698" in msg
    assert "typescript-eslint@8.64.0" in msg
    assert ">=4.8.4 <6.1.0" in msg
    assert 'dependency-name: "typescript"' in msg
    assert "version-update:semver-major" in msg
    assert "PR #402" in msg


def test_escalate_upstream_blocked_sets_escalated_with_diagnostic(tmp_path: Path) -> None:
    block = PeerCapBlock("typescript", "typescript-eslint@8.64.0", "<6.1.0")
    proposal = Proposal(number=698, state="open", mergeable="MERGEABLE",
                        lockfiles=frozenset({"alan/web/package-lock.json"}),
                        upstream_block=block)
    _escalate_upstream_blocked(698, proposal, tmp_path)
    ws = _find_workspace_for_pr(698, tmp_path)
    assert ws is not None
    assert ws.meta.status == Status.ESCALATED
    handovers = handover_mod.list_handovers(ws)
    assert handovers
    assert "typescript" in handovers[-1].full_synthesis
    assert handovers[-1].pr_number == 698


def test_act_on_proposal_escalates_blocked_and_returns_false(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    block = PeerCapBlock("typescript", "typescript-eslint@8.64.0", "<6.1.0")
    proposal = Proposal(number=698, state="open", mergeable="MERGEABLE",
                        lockfiles=frozenset(), upstream_block=block)
    audit: list[dict[str, Any]] = []
    consumed = _act_on_proposal(698, proposal, queue, tmp_path, audit)
    assert consumed is False
    assert audit == [{"action": "escalate_upstream_blocked", "pr_number": 698}]
    ws = _find_workspace_for_pr(698, tmp_path)
    assert ws is not None
    assert ws.meta.status == Status.ESCALATED
    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []


def test_act_on_proposal_dispatches_ready_and_returns_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
    queue = _make_queue(tmp_path)
    proposal = Proposal(number=50, state="open", mergeable="MERGEABLE", lockfiles=frozenset())
    audit: list[dict[str, Any]] = []
    consumed = _act_on_proposal(50, proposal, queue, tmp_path, audit)
    assert consumed is True
    assert audit == [{"action": "dispatch", "pr_number": 50, "proposal_class": "merge_ready"}]
    ws = _find_workspace_for_pr(50, tmp_path)
    assert ws is not None
    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].team == "pr_merge"


def test_dispatch_batch_escalates_blocked_then_dispatches_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEP_SWEEP_MERGE", "1")
    queue = _make_queue(tmp_path)
    npm = frozenset({"alan/web/package-lock.json"})
    block = PeerCapBlock("typescript", "typescript-eslint@8.64.0", "<6.1.0")
    blocked = Proposal(number=1, state="open", mergeable="MERGEABLE",
                       lockfiles=npm, upstream_block=block)
    ready = Proposal(number=2, state="open", mergeable="MERGEABLE", lockfiles=npm)
    batch = ProposalBatch(lockfiles=npm, proposal_numbers=(1, 2))
    audit: list[dict[str, Any]] = []
    _dispatch_batch(
        batch, {1: blocked, 2: ready}, queue, tmp_path, audit, ctx=_ALAN_SWEEP_CTX
    )
    ws1 = _find_workspace_for_pr(1, tmp_path)
    assert ws1 is not None
    assert ws1.meta.status == Status.ESCALATED
    ws2 = _find_workspace_for_pr(2, tmp_path)
    assert ws2 is not None
    jobs2 = queue.list_jobs(task_id=ws2.task_id, status=JobStatus.PENDING)
    assert len(jobs2) == 1
    assert jobs2[0].team == "pr_merge"
    assert {"action": "escalate_upstream_blocked", "pr_number": 1} in audit


def test_run_escalates_upstream_blocked_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _make_queue(tmp_path)
    ws, inv_id = _seed_dep_sweep_ws(tmp_path, queue)
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    raw = [{"number": 698, "mergeable": "MERGEABLE",
            "title": "bump typescript from 6.0.3 to 7.0.2"}]
    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files",
              return_value=["alan/web/package-lock.json"]),
        patch("alan.teams._pr_branch.fetch_failing_job_ids", return_value=[555]),
        patch("alan.teams._pr_branch.fetch_failed_log", return_value=_REAL_ERESOLVE),
        patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)
    child = _find_workspace_for_pr(698, tmp_path)
    assert child is not None
    assert child.meta.status == Status.ESCALATED
    assert queue.list_jobs(task_id=child.task_id, status=JobStatus.PENDING) == []


_OTHER_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
    credential_env_name="SPROCKET_GH_TOKEN",  # noqa: S106
    authoritative_checks=frozenset(),
)


def test_fetch_open_proposals_targets_ctx_repo(forge: ForgeScript) -> None:
    forge.queue("open_prs_by_author", ())
    ctx = SweepContext(forge=_OTHER_FORGE, run_vuln_scan=False)

    _fetch_open_proposals(ctx=ctx)

    ((_, kwargs),) = forge.calls("open_prs_by_author")
    assert kwargs["forge_target"] == _OTHER_FORGE


def test_fetch_pr_files_targets_ctx_repo(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"files": []}))
    ctx = SweepContext(forge=_OTHER_FORGE, run_vuln_scan=False)

    _fetch_pr_files(1, ctx=ctx)

    ((_, kwargs),) = forge.calls("read_pr")
    assert kwargs["forge_target"] == _OTHER_FORGE


def test_ensure_pr_workspace_tags_engine_key(tmp_path: Path) -> None:
    ws = _ensure_pr_workspace(900, tmp_path, engine_key="sprocket-co/sprocket")
    assert ws.meta.engine_key == "sprocket-co/sprocket"


def test_ensure_pr_workspace_no_engine_key_defaults_none(tmp_path: Path) -> None:
    ws = _ensure_pr_workspace(901, tmp_path)
    assert ws.meta.engine_key is None


def test_dispatch_proposal_tags_child_workspace_with_engine_key(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    with patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}):
        _dispatch_proposal(
            902, ProposalClass.MERGE_READY, queue, tmp_path, engine_key="sprocket-co/sprocket"
        )
    ws = _find_workspace_for_pr(902, tmp_path)
    assert ws is not None
    assert ws.meta.engine_key == "sprocket-co/sprocket"


def test_escalate_upstream_blocked_tags_engine_key(tmp_path: Path) -> None:
    block = PeerCapBlock("typescript", "typescript-eslint@8.64.0", "<6.1.0")
    proposal = Proposal(number=903, state="open", mergeable="MERGEABLE",
                        lockfiles=frozenset(), upstream_block=block)
    _escalate_upstream_blocked(903, proposal, tmp_path, engine_key="sprocket-co/sprocket")
    ws = _find_workspace_for_pr(903, tmp_path)
    assert ws is not None
    assert ws.meta.engine_key == "sprocket-co/sprocket"


def test_run_sweep_threads_engine_key_to_dispatched_child(tmp_path: Path) -> None:
    queue = _make_queue(tmp_path)
    audit: list[dict[str, Any]] = []
    raw = [{"number": 904, "mergeable": "MERGEABLE", "title": "Bump requests from 1 to 2"}]
    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=raw),
        patch("alan.teams.dependency_sweep._fetch_pr_files", return_value=["uv.lock"]),
        patch.dict("os.environ", {"ALAN_DEP_SWEEP_MERGE": "1"}),
    ):
        _run_sweep(queue, tmp_path, audit, engine_key="sprocket-co/sprocket", ctx=_ALAN_SWEEP_CTX)
    ws = _find_workspace_for_pr(904, tmp_path)
    assert ws is not None
    assert ws.meta.engine_key == "sprocket-co/sprocket"


def test_find_workspace_for_pr_scopes_by_engine_on_number_collision(tmp_path: Path) -> None:
    _register_sprocket_engine()
    alan_ws = _ensure_pr_workspace(950, tmp_path, engine_key=None)
    sprocket_ws = _ensure_pr_workspace(950, tmp_path, engine_key="sprocket-co/sprocket")
    assert alan_ws.task_id != sprocket_ws.task_id
    found_alan = _find_workspace_for_pr(950, tmp_path, engine_key=None)
    found_sprocket = _find_workspace_for_pr(950, tmp_path, engine_key="sprocket-co/sprocket")
    assert found_alan is not None and found_alan.task_id == alan_ws.task_id
    assert found_sprocket is not None and found_sprocket.task_id == sprocket_ws.task_id


def test_needs_human_scoped_per_engine_on_number_collision(tmp_path: Path) -> None:
    _register_sprocket_engine()
    alan_ws = _ensure_pr_workspace(951, tmp_path, engine_key=None)
    workspace_mod.transition(alan_ws, Status.ESCALATED)
    _ensure_pr_workspace(951, tmp_path, engine_key="sprocket-co/sprocket")
    assert _needs_human(951, tmp_path, engine_key=None)
    assert not _needs_human(951, tmp_path, engine_key="sprocket-co/sprocket")


def test_is_in_flight_scoped_per_engine_on_number_collision(tmp_path: Path) -> None:
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    alan_ws = _ensure_pr_workspace(952, tmp_path, engine_key=None)
    inv = workflow_mod.seed_pending_workflow(alan_ws, team="pr_merge", tasks=[ROLE_SPEC])
    queue.enqueue(alan_ws.task_id, "pr_merge", triggered_by_invocation_id=inv)
    _ensure_pr_workspace(952, tmp_path, engine_key="sprocket-co/sprocket")
    assert _is_in_flight(952, queue, tmp_path, engine_key=None)
    assert not _is_in_flight(952, queue, tmp_path, engine_key="sprocket-co/sprocket")


def _register_sprocket_engine(*, enable_python_vuln_scan: bool = False,
                              cadence_seconds: int = 3600, enabled: bool = True) -> None:
    register(EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
            credential_env_name="SPROCKET_GH_TOKEN",  # noqa: S106
        ),
        dependency_sweep=DependencySweepConfig(
            enabled=enabled,
            cadence_seconds=cadence_seconds,
            enable_python_vuln_scan=enable_python_vuln_scan,
        ),
    ), validate_env=False)


def test_sweep_resolves_ctx_from_workspace_engine(tmp_path: Path) -> None:
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)
    captured: dict[str, Any] = {}

    def _fake_run_sweep(
        q: Queue, root: Path | None, audit: list[dict[str, Any]], *,
        ctx: SweepContext, engine_key: str | None,
    ) -> str:
        del q, root, audit
        captured["ctx"] = ctx
        captured["engine_key"] = engine_key
        return "done"

    with patch("alan.teams.dependency_sweep._run_sweep", _fake_run_sweep):
        summary = team._sweep(ws, [])  # noqa: SLF001

    assert summary == "done"
    assert captured["engine_key"] == "sprocket-co/sprocket"
    assert captured["ctx"].forge.repo.slug == "sprocket-co/sprocket"
    assert captured["ctx"].run_vuln_scan is False


def test_sweep_not_wired_never_resolves_engine_config(tmp_path: Path) -> None:
    ws = workspace_mod.create("Dependency Sweep", "d", team="dependency_sweep", root=tmp_path)
    team = DependencySweepTeam()
    summary = team._sweep(ws, [])  # noqa: SLF001
    assert "not wired" in summary


def test_do_schedule_next_sweep_uses_cfg_cadence(tmp_path: Path) -> None:
    _register_sprocket_engine(cadence_seconds=3600)
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    before = time.time()
    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    jobs = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert len(jobs) == 1
    assert jobs[0].eligible_at is not None
    assert jobs[0].eligible_at == pytest.approx(before + 3600, abs=5)


def test_do_schedule_next_sweep_disabled_engine_does_not_reenqueue(tmp_path: Path) -> None:
    _register_sprocket_engine(enabled=False)
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta("dep_sweep_last_scheduled_at:sprocket-co/sprocket") is None


def test_run_disabled_engine_does_not_reschedule_next_sweep(tmp_path: Path) -> None:
    _register_sprocket_engine(enabled=False)
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    inv_id = workflow_mod.seed_pending_workflow(
        ws, team="dependency_sweep", tasks=[ROLE_SPEC], source_task_id=ws.task_id
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    with (
        patch("alan.teams.dependency_sweep._fetch_open_proposals", return_value=[]),
    ):
        team.run(ws, None, _noop_emit, inv_id=inv_id)

    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []


def test_do_schedule_next_sweep_namespaces_cadence_meta_key(tmp_path: Path) -> None:
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    assert queue.get_meta("dep_sweep_last_scheduled_at:sprocket-co/sprocket") is not None


def test_do_schedule_next_sweep_persists_dependency_sweep_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _register_sprocket_engine()
    queue = _make_queue(tmp_path)
    ws = workspace_mod.create(
        "Dependency Sweep", "d", team="dependency_sweep", root=tmp_path,
        engine_key="sprocket-co/sprocket",
    )
    team = DependencySweepTeam(queue=queue, workspaces_root=tmp_path)

    team._do_schedule_next_sweep(ws)  # noqa: SLF001

    status = load_dependency_sweep_status(_OTHER_FORGE)
    assert status is not None
    assert status.slug == "sprocket-co/sprocket"
    assert status.enabled is True
    assert status.last_run_at is not None
    assert status.next_run_at is not None


def test_load_dependency_sweep_status_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    assert load_dependency_sweep_status(_OTHER_FORGE) is None


def test_load_dependency_sweep_status_returns_none_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.dependency_sweep import _status_path  # noqa: SLF001

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    path = _status_path(_OTHER_FORGE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert load_dependency_sweep_status(_OTHER_FORGE) is None


def test_save_dependency_sweep_status_swallows_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams.dependency_sweep import _save_dependency_sweep_status  # noqa: SLF001

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("alan.teams.dependency_sweep.store.write_text_mkstemp", _raise)
    _save_dependency_sweep_status(
        _OTHER_FORGE,
        DependencySweepStatus(slug="sprocket-co/sprocket", enabled=True),
    )
