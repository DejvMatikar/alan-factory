from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import proactive_rebase as pr_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.handover import Handover, save_handover
from alan.queue import Queue
from alan.teams import _pr_branch, _pr_forge, _pr_state_paths
from alan.teams._pr_branch import BranchUpdateOutcome
from alan.workspace import Status, Workspace

_WIDGET_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)


def _make_pr_handover(ws: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"h_{ws.task_id}_{pr_number}",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="test",
        pr_number=pr_number,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture(autouse=True)
def _default_pr_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "pr_is_open", lambda *_a, **_kw: True)


def test_rebase_cap_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pr_mod._REBASE_CAP_ENV, raising=False)
    assert pr_mod._rebase_cap() == 5


def test_rebase_cap_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr_mod._REBASE_CAP_ENV, "3")
    assert pr_mod._rebase_cap() == 3


def test_rebase_cap_invalid_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr_mod._REBASE_CAP_ENV, "bad")
    assert pr_mod._rebase_cap() == 5


def test_rebase_cap_zero_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr_mod._REBASE_CAP_ENV, "0")
    assert pr_mod._rebase_cap() == 5


def test_latest_pr_number_exception(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    ws = workspace_mod.create("t1", "desc", root=ws_root)

    def boom(w: Workspace) -> list[Handover]:
        raise RuntimeError("disk error")

    monkeypatch.setattr("alan.proactive_rebase.list_handovers", boom)
    assert pr_mod._latest_pr_number(ws) is None


def test_latest_pr_number_empty_handovers(ws_root: Path) -> None:
    ws = workspace_mod.create("t2", "desc", root=ws_root)
    assert pr_mod._latest_pr_number(ws) is None


def test_latest_pr_number_found(ws_root: Path) -> None:
    ws = workspace_mod.create("t3", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 99))
    assert pr_mod._latest_pr_number(ws) == 99


def test_latest_pr_number_none_when_no_pr_in_handovers(ws_root: Path) -> None:
    ws = workspace_mod.create("t4", "desc", root=ws_root)
    h = Handover(
        handover_id="h_noPR",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="research",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="no pr",
        pr_number=None,
    )
    save_handover(ws, h)
    assert pr_mod._latest_pr_number(ws) is None


def test_ws_eligible_allows_each_parked_status(ws_root: Path, queue: Queue) -> None:
    statuses = [
        Status.ACTIVE,
        Status.AWAITING_INPUT,
        Status.ESCALATED,
        Status.PAUSED,
        Status.PARTIAL,
    ]
    for i, status in enumerate(statuses):
        ws = workspace_mod.create(f"elig{i}", "desc", root=ws_root)
        ws = workspace_mod.transition(ws, status)
        assert pr_mod._ws_eligible(ws, "skip", queue) is True


def test_ws_eligible_rejects_terminal_statuses(ws_root: Path, queue: Queue) -> None:
    for i, status in enumerate([Status.COMPLETED, Status.ARCHIVED, Status.FAILED]):
        ws = workspace_mod.create(f"term{i}", "desc", root=ws_root)
        ws = workspace_mod.transition(ws, status)
        assert pr_mod._ws_eligible(ws, "skip", queue) is False


def test_ws_eligible_excludes_pending_queue_work(ws_root: Path, queue: Queue) -> None:
    ws = workspace_mod.create("pending-job", "desc", root=ws_root)
    queue.enqueue(ws.task_id, "pr_merge")
    assert pr_mod._ws_eligible(ws, "other", queue) is False


def test_collect_targets_empty_root(tmp_path: Path, queue: Queue) -> None:
    targets = pr_mod._collect_targets("skip", tmp_path / "empty", queue, cap=5)
    assert targets == []


def test_collect_targets_skips_current_workspace(
    ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("current", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 1))
    targets = pr_mod._collect_targets(ws.workspace_id, ws_root, queue, cap=5)
    assert targets == []


def test_collect_targets_skips_active_queue_work(
    ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("busy", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 3))
    queue.enqueue(ws.task_id, "pr_merge")
    queue.claim()
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert targets == []


def test_collect_targets_skips_no_pr(ws_root: Path, queue: Queue) -> None:
    workspace_mod.create("nopr", "desc", root=ws_root)
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert targets == []


def test_collect_targets_honours_cap(ws_root: Path, queue: Queue) -> None:
    for i in range(3):
        ws = workspace_mod.create(f"ws{i}", f"desc{i}", root=ws_root)
        save_handover(ws, _make_pr_handover(ws, i + 10))
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=2)
    assert len(targets) == 2


def test_collect_targets_returns_active_pr(ws_root: Path, queue: Queue) -> None:
    ws = workspace_mod.create("active", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 42))
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert targets == [
        pr_mod._RebaseTarget(
            task_id=ws.task_id, pr_number=42, forge_target=DEFAULT_FORGE_TARGET,
        )
    ]


def test_collect_targets_active_before_parked_at_cap(
    ws_root: Path, queue: Queue
) -> None:
    parked1 = workspace_mod.create("parked1", "d", root=ws_root)
    save_handover(parked1, _make_pr_handover(parked1, 101))
    workspace_mod.transition(parked1, Status.PAUSED)

    parked2 = workspace_mod.create("parked2", "d", root=ws_root)
    save_handover(parked2, _make_pr_handover(parked2, 102))
    workspace_mod.transition(parked2, Status.AWAITING_INPUT)

    active1 = workspace_mod.create("active1", "d", root=ws_root)
    save_handover(active1, _make_pr_handover(active1, 103))

    active2 = workspace_mod.create("active2", "d", root=ws_root)
    save_handover(active2, _make_pr_handover(active2, 104))

    targets = pr_mod._collect_targets("other", ws_root, queue, cap=2)
    assert [t.task_id for t in targets] == [active1.task_id, active2.task_id]


def test_collect_targets_parked_tier_is_oldest_first(
    ws_root: Path, queue: Queue
) -> None:
    parked_old = workspace_mod.create("parked-old", "d", root=ws_root)
    save_handover(parked_old, _make_pr_handover(parked_old, 201))
    workspace_mod.transition(parked_old, Status.PAUSED)

    parked_new = workspace_mod.create("parked-new", "d", root=ws_root)
    save_handover(parked_new, _make_pr_handover(parked_new, 202))
    workspace_mod.transition(parked_new, Status.PAUSED)

    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert [t.task_id for t in targets] == [parked_old.task_id, parked_new.task_id]


def test_collect_targets_skips_closed_pr(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pr_branch, "pr_is_open", lambda *_a, **_kw: False)
    ws = workspace_mod.create("closed", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 55))
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert targets == []


def test_collect_targets_closed_pr_frees_cap_slot(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = workspace_mod.create("closed-first", "d", root=ws_root)
    save_handover(closed, _make_pr_handover(closed, 60))

    kept = workspace_mod.create("kept-second", "d", root=ws_root)
    save_handover(kept, _make_pr_handover(kept, 61))

    def fake_open(pr_number: int, _task_id: str, **_kw: object) -> bool | None:
        return pr_number != 60

    monkeypatch.setattr(_pr_branch, "pr_is_open", fake_open)
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=1)
    assert [t.task_id for t in targets] == [kept.task_id]


def test_collect_targets_pr_is_open_none_fails_open(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pr_branch, "pr_is_open", lambda *_a, **_kw: None)
    ws = workspace_mod.create("unknown-state", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 70))
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert [t.task_id for t in targets] == [ws.task_id]


def test_collect_targets_isolates_candidate_exception(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = workspace_mod.create("bad-candidate", "d", root=ws_root)
    save_handover(bad, _make_pr_handover(bad, 80))

    good = workspace_mod.create("good-candidate", "d", root=ws_root)
    save_handover(good, _make_pr_handover(good, 81))

    def fake_open(pr_number: int, _task_id: str, **_kw: object) -> bool | None:
        if pr_number == 80:
            raise RuntimeError("boom")
        return True

    monkeypatch.setattr(_pr_branch, "pr_is_open", fake_open)
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert [t.task_id for t in targets] == [good.task_id]


def test_collect_targets_resolves_forge_target_per_workspace(
    ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("secondary", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 90))
    monkeypatch.setattr(_pr_forge, "forge_target_for_ws", lambda _w: _WIDGET_FORGE)
    targets = pr_mod._collect_targets("other", ws_root, queue, cap=5)
    assert targets == [
        pr_mod._RebaseTarget(task_id=ws.task_id, pr_number=90, forge_target=_WIDGET_FORGE)
    ]


def test_do_fan_out_empty_targets(tmp_path: Path, queue: Queue) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("skip", tmp_path / "empty", queue, audit_dir, cap=5, triggered_by_pr=None)
    assert not (audit_dir / pr_mod._AUDIT_FILE).exists()


def test_do_fan_out_updated_writes_ok_action_and_system_head(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("fan-ok", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 42))

    calls: list[tuple[int, str]] = []

    def fake_outcome(
        pr_number: int, task_id: str, **_kw: object
    ) -> BranchUpdateOutcome:
        calls.append((pr_number, task_id))
        return BranchUpdateOutcome.UPDATED

    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", fake_outcome)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=55)

    assert calls == [(42, ws.task_id)]
    line = (audit_dir / pr_mod._AUDIT_FILE).read_text().strip()
    row = json.loads(line)
    assert row["action"] == "proactive_rebase_ok"
    assert row["repo"] == DEFAULT_FORGE_TARGET.repo.slug
    assert row["triggered_by_merge_of"] == 55
    marker = _pr_state_paths.load_system_head(42, DEFAULT_FORGE_TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (
        "sha-new", "proactive_rebase", False,
    )


def test_do_fan_out_conflict_writes_conflict_action(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("fan-conflict", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 65))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.CONFLICT
    )

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=10)

    line = (audit_dir / pr_mod._AUDIT_FILE).read_text().strip()
    row = json.loads(line)
    assert row["action"] == "proactive_rebase_conflict"


def test_do_fan_out_failed_writes_failed_action(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = workspace_mod.create("fan-fail", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 77))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.FAILED
    )

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=10)

    line = (audit_dir / pr_mod._AUDIT_FILE).read_text().strip()
    row = json.loads(line)
    assert row["action"] == "proactive_rebase_failed"
    assert row["triggered_by_merge_of"] == 10


def test_do_fan_out_collect_targets_exception_returns_early(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kw: object) -> list[pr_mod._RebaseTarget]:
        raise RuntimeError("I/O error listing workspaces")

    monkeypatch.setattr("alan.proactive_rebase._collect_targets", boom)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("skip", ws_root, queue, audit_dir, cap=5, triggered_by_pr=None)
    assert not (audit_dir / pr_mod._AUDIT_FILE).exists()


def test_do_fan_out_exception_writes_failed_action_and_continues(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws1 = workspace_mod.create("ex1", "d1", root=ws_root)
    ws2 = workspace_mod.create("ex2", "d2", root=ws_root)
    save_handover(ws1, _make_pr_handover(ws1, 11))
    save_handover(ws2, _make_pr_handover(ws2, 22))

    call_count = [0]

    def flaky(pr_number: int, task_id: str, **_kw: object) -> BranchUpdateOutcome:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("network blip")
        return BranchUpdateOutcome.UPDATED

    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", flaky)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=99)

    assert call_count[0] == 2
    lines = (audit_dir / pr_mod._AUDIT_FILE).read_text().strip().split("\n")
    actions = {json.loads(ln)["action"] for ln in lines}
    assert "proactive_rebase_failed" in actions
    assert "proactive_rebase_ok" in actions


def test_do_fan_out_uses_workspace_forge_target(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("secondary-fan", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 91))
    monkeypatch.setattr(_pr_forge, "forge_target_for_ws", lambda _w: _WIDGET_FORGE)

    seen_targets: list[object] = []

    def fake_outcome(
        _pr_number: int, _task_id: str, *, forge_target: object, **_kw: object
    ) -> BranchUpdateOutcome:
        seen_targets.append(forge_target)
        return BranchUpdateOutcome.UPDATED

    monkeypatch.setattr(_pr_branch, "update_pr_branch_outcome", fake_outcome)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-new")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=1)

    assert seen_targets == [_WIDGET_FORGE]
    row = json.loads((audit_dir / pr_mod._AUDIT_FILE).read_text().strip())
    assert row["repo"] == _WIDGET_FORGE.repo.slug


def test_do_fan_out_never_reads_merge_state(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_ready

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("must not read merge state")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", boom)

    ws = workspace_mod.create("no-merge-state", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 202))
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.UPDATED
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=1)

    row = json.loads((audit_dir / pr_mod._AUDIT_FILE).read_text().strip())
    assert row["action"] == "proactive_rebase_ok"


def test_do_fan_out_leaves_workspace_status_untouched(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    ws = workspace_mod.create("untouched", "desc", root=ws_root)
    save_handover(ws, _make_pr_handover(ws, 303))
    workspace_mod.transition(ws, Status.PAUSED)
    monkeypatch.setattr(
        _pr_branch, "update_pr_branch_outcome", lambda *_a, **_kw: BranchUpdateOutcome.UPDATED
    )
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: "sha-x")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    pr_mod._do_fan_out("other", ws_root, queue, audit_dir, cap=5, triggered_by_pr=1)

    reloaded = workspace_mod.read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.PAUSED
    assert not queue.has_active_work(ws.workspace_id)


def test_maybe_trigger_spawns_thread_and_runs(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = threading.Event()

    def fake_fan_out(*args: object, **kwargs: object) -> None:
        ran.set()

    monkeypatch.setattr("alan.proactive_rebase._do_fan_out", fake_fan_out)
    pr_mod.maybe_trigger_proactive_rebase(
        skip_workspace_id="skip",
        workspaces_root=ws_root,
        queue=queue,
        audit_dir=tmp_path / "audit",
        triggered_by_pr=7,
    )
    assert ran.wait(timeout=5.0), "daemon thread did not call _do_fan_out"
