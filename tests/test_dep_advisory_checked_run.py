from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.queue import Queue
from alan.teams import dep_advisory
from alan.teams.dep_advisory import (
    Ledger,
    LedgerEntry,
    checked_run_key,
    pending_run_key,
    save_ledger,
)
from alan.workspace import Status
from tests.dep_advisory_support import (
    actions,
    audit_row,
    prepare,
    run_check,
    vuln_entry,
)
from tests.fakes.forge import FakeForge

_SLUG = DEFAULT_FORGE_TARGET.repo.slug
_FIXABLE = audit_row("urllib3", "2.7.0", vuln_entry("GHSA-aaaa", "2.8.0"))
_NO_FIX = audit_row("pyjwt", "2.12.1", vuln_entry("PYSEC-2099-1"))


def _pend_run(queue: Queue) -> None:
    queue.set_meta(pending_run_key(_SLUG), "4242")


def _checked(queue: Queue) -> str | None:
    return queue.get_meta(checked_run_key(_SLUG))


def _seed_busy_fix(ws_root: Path) -> str:
    ws = workspace_mod.create("sec/zlib: GHSA-old", "d", team="dependency_sweep", root=ws_root)
    entry = LedgerEntry(
        advisory_id="GHSA-old",
        task_id=ws.task_id,
        kind="fix",
        recorded_at=datetime.now(UTC).timestamp(),
    )
    save_ledger(DEFAULT_FORGE_TARGET, Ledger(entries={"GHSA-old": entry}))
    return ws.task_id


def _boom(*_args: object, **_kwargs: object) -> None:
    raise OSError("boom")


def test_a_deferred_fix_leaves_the_red_run_unchecked_until_a_later_check_opens_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    busy = _seed_busy_fix(ws_root)
    _pend_run(queue)

    _, first = run_check(queue, ws_root)

    assert actions(first) == ["vuln_fix_deferred"]
    assert _checked(queue) is None

    workspace_mod.transition(workspace_mod.read(busy, root=ws_root), Status.ESCALATED)
    _, second = run_check(queue, ws_root)

    assert actions(second) == ["vuln_fix_workspace_opened"]
    assert _checked(queue) == "4242"


def test_a_deferred_fix_beside_an_opened_no_fix_workspace_still_leaves_the_run_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE, _NO_FIX)
    _seed_busy_fix(ws_root)
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert sorted(actions(audit)) == ["vuln_fix_deferred", "vuln_no_fix_workspace_opened"]
    assert _checked(queue) is None


def test_a_workspace_creation_failure_leaves_the_red_run_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    monkeypatch.setattr(workspace_mod, "create", _boom)
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_workspace_failed"]
    assert _checked(queue) is None


def test_an_enqueue_failure_leaves_the_red_run_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    monkeypatch.setattr(queue, "enqueue", _boom)
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_workspace_failed"]
    assert _checked(queue) is None


def test_a_ledger_write_failure_leaves_the_red_run_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    monkeypatch.setattr(dep_advisory, "save_ledger", _boom)
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_ledger_write_failed"]
    assert _checked(queue) is None


def test_a_dry_run_leaves_the_red_run_unchecked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    monkeypatch.setenv("ALAN_DEP_SWEEP_DRY_RUN", "1")
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_workspace_dry_run"]
    assert _checked(queue) is None


def test_a_run_whose_advisories_were_already_covered_is_recorded_as_checked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queue: Queue,
    ws_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    prepare(monkeypatch, tmp_path, _active_fake_forge, _FIXABLE)
    run_check(queue, ws_root)
    _pend_run(queue)

    _, audit = run_check(queue, ws_root)

    assert actions(audit) == ["vuln_already_covered"]
    assert _checked(queue) == "4242"
