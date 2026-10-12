from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from alan import process_runner, self_update
from alan.self_update import ApplyPath, Reason, Verdict, _journal, _ops, _rollback, _run
from tests.self_update_apply_fakes import (
    HEALTH_URL,
    ApplyScenario,
    build_apply_scenario,
    exit_with,
    reply,
)
from tests.self_update_fixtures import completed, git


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def failed_journal(scenario: ApplyScenario, record: self_update.StatusRecord) -> _journal.Journal:
    if record.target_sha is None or record.previous_sha is None:
        raise AssertionError("expected a failed record with both commits")
    return _journal.Journal(
        _journal.Phase.ROLLING_BACK,
        record.target_sha,
        record.previous_sha,
        record.previous_slot,
        record.path is ApplyPath.RESTART,
    )


def test_rollback_refuses_and_changes_nothing_when_the_tree_is_dirty(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    assert record.verdict is Verdict.FAILED
    scenario.commands.injections.clear()
    (scenario.repo / "rogue.txt").write_text("rogue\n", encoding="utf-8")
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert not ok
    assert scenario.head() == scenario.target
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLBACK_REFUSED_DIRTY
    assert self_update.read_rejected() == frozenset()


def test_rollback_skips_web_slot_restore_when_no_previous_slot_is_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_slot = build_apply_scenario(monkeypatch, tmp_path, dist="missing")
    missing_slot.commands.inject(("bash",), exit_with(1))
    record = missing_slot.apply()
    assert record.previous_slot is None
    missing_slot.commands.injections.clear()
    missing_slot.health.sha = missing_slot.previous
    journal = failed_journal(missing_slot, record)
    ok = _rollback.perform(
        missing_slot.repo, journal, plist_path=missing_slot.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert not missing_slot.commands.has(("activate",))


def test_a_rollback_restart_that_never_verifies_is_recorded_as_rollback_failed(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    scenario.commands.injections.clear()
    scenario.health.up = lambda: False
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert not ok
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLBACK_FAILED


def test_a_restart_free_rollback_never_touches_launchctl_or_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("activate",), reply())
    record = quiet.apply()
    assert record.path is ApplyPath.RESTART_FREE
    quiet.commands.injections.clear()
    quiet.commands.calls.clear()
    quiet.health.respond_with(RuntimeError("must not be consulted"))
    journal = failed_journal(quiet, record)
    ok = _rollback.perform(
        quiet.repo, journal, plist_path=quiet.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert quiet.commands.launchctl_argvs() == []
    assert quiet.health.urls == []


def test_rollback_does_not_duplicate_an_already_rejected_commit(scenario: ApplyScenario) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    if record.target_sha is None:
        raise AssertionError
    self_update.rejected_path().parent.mkdir(parents=True, exist_ok=True)
    self_update.rejected_path().write_text(f"{record.target_sha}\n", encoding="utf-8")
    scenario.commands.injections.clear()
    scenario.health.sha = scenario.previous
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert self_update.read_rejected() == frozenset({record.target_sha})


def test_rollback_recovers_from_an_unreadable_rejected_list(scenario: ApplyScenario) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    self_update.rejected_path().parent.mkdir(parents=True, exist_ok=True)
    self_update.rejected_path().write_text("not-a-sha\n", encoding="utf-8")
    scenario.commands.injections.clear()
    scenario.health.sha = scenario.previous
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert self_update.read_rejected() == frozenset({record.target_sha})


def test_a_rollback_that_fails_to_sync_dependencies_is_recorded_as_rollback_failed(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    scenario.commands.injections.clear()
    scenario.commands.inject(("uv", "sync"), exit_with(1))
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert not ok
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLBACK_FAILED


def test_a_rollback_with_an_unusable_origin_skips_the_renderer_and_still_restarts_the_service(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    assert record.verdict is Verdict.FAILED
    scenario.commands.injections.clear()
    scenario.commands.calls.clear()
    git(
        scenario.repo,
        "remote",
        "set-url",
        "origin",
        "https://GitHub.com/example-owner/example-repo.git",
    )
    scenario.health.sha = scenario.previous
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert not scenario.commands.has(("bash",))
    assert scenario.commands.has(("launchctl", "bootstrap"))
    assert scenario.commands.has(("launchctl", "kickstart"))
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLED_BACK
    assert self_update.read_rejected() == frozenset({record.target_sha})
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()


def test_a_rollback_whose_renderer_refuses_keeps_the_plist_and_still_restarts_the_service(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    scenario.commands.injections.clear()
    scenario.commands.calls.clear()
    scenario.commands.inject(("bash",), exit_with(_ops.RENDER_REFUSED_EXIT))
    scenario.health.sha = scenario.previous
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert scenario.commands.count(("bash",)) == 1
    order = [
        scenario.commands.index_of(tokens)
        for tokens in (
            ("bash",),
            ("launchctl", "bootout"),
            ("launchctl", "bootstrap"),
            ("launchctl", "kickstart"),
        )
    ]
    assert order == sorted(order)
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLED_BACK


def test_a_rollback_whose_renderer_fails_outright_is_recorded_as_rollback_failed(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    scenario.commands.calls.clear()
    journal = failed_journal(scenario, record)
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert not ok
    assert scenario.commands.launchctl_mutations() == []
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLBACK_FAILED


def test_a_tick_whose_renderer_refuses_ends_rolled_back_and_never_retries_the_commit(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(_ops.RENDER_REFUSED_EXIT))
    scenario.health.sha = scenario.previous

    def tick() -> self_update.StatusRecord | None:
        return _run.perform(
            scenario.repo,
            scheduled=False,
            plist_path=scenario.plist_path,
            health_url=HEALTH_URL,
            idle_probe=scenario.idle,
            wait_bound_s=30.0,
            detached=False,
        )

    first = tick()
    assert first is not None
    assert (first.verdict, first.reason) == (Verdict.FAILED, Reason.RENDER_FAILED)
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLED_BACK
    assert scenario.head() == scenario.previous
    assert self_update.read_rejected() == frozenset({scenario.target})

    renders_after_first_tick = scenario.commands.count(("bash",))
    second = tick()
    assert second is not None
    assert (second.verdict, second.reason) == (Verdict.BLOCKED, Reason.REJECTED_COMMIT)
    assert scenario.commands.count(("bash",)) == renders_after_first_tick
    assert scenario.head() == scenario.previous


def test_a_verified_rollback_reports_the_rejected_commit_as_blocked_on_the_next_check(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = scenario.apply()
    scenario.commands.injections.clear()
    scenario.health.sha = scenario.previous
    journal = failed_journal(scenario, record)
    assert _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    checked = self_update.check(scenario.repo)
    assert (checked.verdict, checked.reason) == (Verdict.BLOCKED, Reason.REJECTED_COMMIT)


def test_teardown_service_raises_when_the_service_never_unloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}

    def advance(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(_ops, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(_ops, "sleep", advance)
    monkeypatch.setattr(process_runner, "run_subprocess", lambda *a, **kw: completed())
    with pytest.raises(RuntimeError):
        _ops.teardown_service()


def test_bootstrap_service_raises_on_a_nonzero_launchctl_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", lambda *a, **kw: completed(returncode=5)
    )
    with pytest.raises(RuntimeError):
        _ops.bootstrap_service(tmp_path / "ai.alan.plist")


def test_kickstart_service_raises_on_a_nonzero_launchctl_kickstart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", lambda *a, **kw: completed(returncode=1)
    )
    with pytest.raises(RuntimeError):
        _ops.kickstart_service()


def test_activate_slot_raises_when_the_swap_script_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", lambda *a, **kw: completed(returncode=1)
    )
    with pytest.raises(RuntimeError):
        _ops.activate_slot(tmp_path, "dist.a", "a" * 40)


def test_sync_dependencies_raises_on_a_nonzero_uv_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", lambda *a, **kw: completed(returncode=1)
    )
    with pytest.raises(RuntimeError):
        _ops.sync_dependencies(tmp_path)


def test_git_raises_a_called_process_error_on_a_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        process_runner, "run_subprocess", lambda *a, **kw: completed(returncode=128)
    )
    with pytest.raises(subprocess.CalledProcessError):
        _ops.git(tmp_path, "status")


def test_previous_slot_returns_none_when_dist_is_not_a_symlink(tmp_path: Path) -> None:
    assert _ops.previous_slot(tmp_path) is None


def test_previous_slot_returns_the_slot_name_for_a_recognised_link(tmp_path: Path) -> None:
    (tmp_path / "dist.a").mkdir()
    os.symlink("dist.a", tmp_path / "dist")
    assert _ops.previous_slot(tmp_path) == "dist.a"


def test_previous_slot_returns_none_for_an_unrecognised_link_target(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    os.symlink("other", tmp_path / "dist")
    assert _ops.previous_slot(tmp_path) is None


def test_active_slot_sha_returns_none_when_the_file_is_missing(tmp_path: Path) -> None:
    assert _ops.active_slot_sha(tmp_path) is None


def test_active_slot_sha_returns_the_stripped_content_when_present(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / _ops.DEPLOY_SHA_FILE).write_text(f"{'a' * 40}\n", encoding="utf-8")
    assert _ops.active_slot_sha(tmp_path) == "a" * 40
