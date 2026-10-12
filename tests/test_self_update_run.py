from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

from alan import self_update
from alan.self_update import (
    ApplyPath,
    Reason,
    Verdict,
    _cli,
    _journal,
    _leftover,
    _lock,
    _run,
    _soak,
)
from tests.self_update_apply_fakes import (
    HEALTH_URL,
    ApplyScenario,
    build_apply_scenario,
    exit_with,
)
from tests.self_update_fixtures import advance_origin, commit, gate_check, git, push_run


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def perform(
    scenario: ApplyScenario, *, scheduled: bool = False
) -> self_update.StatusRecord | None:
    return _run.perform(
        scenario.repo,
        scheduled=scheduled,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
    )


def test_applied_shas_rejects_a_record_missing_either_commit() -> None:
    incomplete = self_update.StatusRecord(
        Verdict.ERROR, Reason.GIT_FAILED, None, None, "2026-01-01T00:00:00+00:00"
    )
    with pytest.raises(RuntimeError):
        _run._applied_shas(incomplete)


def test_trusted_commit_is_head_when_no_leftover_exists(scenario: ApplyScenario) -> None:
    commit(scenario.repo, "extra.txt")
    new_head = git(scenario.repo, "rev-parse", "HEAD")
    assert _run._trusted_commit(scenario.repo) == new_head


def test_trusted_commit_prefers_the_leftover_previous_sha_over_head(
    scenario: ApplyScenario,
) -> None:
    commit(scenario.repo, "extra.txt")
    new_head = git(scenario.repo, "rev-parse", "HEAD")
    assert new_head != scenario.previous
    journal = _journal.Journal(
        _journal.Phase.APPLYING, scenario.target, scenario.previous, "dist.a", True
    )
    _journal.write(journal)
    assert _run._trusted_commit(scenario.repo) == scenario.previous


def test_trampoline_returns_zero_without_extracting_when_the_lock_is_held(
    scenario: ApplyScenario,
) -> None:
    with _lock.acquire() as held:
        assert held
        result = _run.trampoline(
            scenario.repo, scheduled=False, plist_path=scenario.plist_path, health_url=HEALTH_URL
        )
    assert result == 0
    assert scenario.execs.calls == []


def test_trampoline_extracts_and_execs_when_the_lock_is_free(scenario: ApplyScenario) -> None:
    original_cwd = Path.cwd()
    try:
        result = _run.trampoline(
            scenario.repo, scheduled=True, plist_path=scenario.plist_path, health_url=HEALTH_URL
        )
    finally:
        os.chdir(original_cwd)
    assert result == 0
    assert len(scenario.execs.calls) == 1
    _python, argv = scenario.execs.calls[0]
    assert argv == [
        "-S", "-m", "alan.self_update", "apply",
        "--kit-repo", str(scenario.repo),
        "--plist-path", str(scenario.plist_path),
        "--health-url", HEALTH_URL,
        "--scheduled",
    ]


def test_trampoline_converts_relative_repo_and_plist_paths_to_absolute(
    scenario: ApplyScenario,
) -> None:
    original_cwd = Path.cwd()
    os.chdir(scenario.repo.parent)
    relative_repo = Path(scenario.repo.name)
    relative_plist = Path(os.path.relpath(scenario.plist_path, scenario.repo.parent))
    try:
        result = _run.trampoline(
            relative_repo, scheduled=False, plist_path=relative_plist, health_url=HEALTH_URL
        )
    finally:
        os.chdir(original_cwd)
    assert result == 0
    _python, argv = scenario.execs.calls[0]
    assert argv[argv.index("--kit-repo") + 1] == str(scenario.repo)
    assert argv[argv.index("--plist-path") + 1] == str(scenario.plist_path)


def test_perform_records_last_good_after_a_clean_restart_soak(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    record = perform(scenario)
    assert record is not None
    assert record.verdict is Verdict.APPLIED
    assert record.path is ApplyPath.RESTART
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD
    assert stored.target_sha == record.target_sha


def test_tick_journals_the_pending_apply_before_calling_apply_so_a_crash_is_recoverable(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(repo: Path, inputs: self_update.ApplyInputs) -> self_update.StatusRecord:
        stored = _journal.read()
        assert stored is not None
        assert stored.phase is _journal.Phase.APPLYING
        assert stored.target_sha == scenario.target
        assert stored.previous_sha == scenario.previous
        assert stored.restarted is True
        raise RuntimeError("crash mid-apply")

    monkeypatch.setattr(self_update, "apply", boom)
    with pytest.raises(RuntimeError):
        perform(scenario)


def test_pending_journal_targets_the_pin_not_the_fetched_tip(
    scenario: ApplyScenario,
) -> None:
    mid = advance_origin(scenario.base.pair, "mid.txt")
    scenario.base.fetched = advance_origin(scenario.base.pair, "tip.txt")
    scenario.base.set_runs(push_run(mid))
    scenario.base.set_checks(gate_check(mid))
    journal = _run._pending_journal(scenario.repo, pin=mid, force=False)
    assert journal is not None
    assert journal.target_sha == mid
    assert journal.previous_sha == scenario.previous


def test_pending_journal_is_built_for_a_forced_apply_that_ci_would_otherwise_block(
    scenario: ApplyScenario,
) -> None:
    scenario.base.set_runs(push_run(scenario.target, conclusion="failure"))
    journal = _run._pending_journal(scenario.repo, pin=None, force=True)
    assert journal is not None
    assert journal.target_sha == scenario.target
    assert journal.previous_sha == scenario.previous


def test_pending_journal_is_none_when_ci_blocks_and_force_is_not_set(
    scenario: ApplyScenario,
) -> None:
    scenario.base.set_runs(push_run(scenario.target, conclusion="failure"))
    assert _run._pending_journal(scenario.repo, pin=None, force=False) is None


def test_perform_rolls_back_when_the_soak_fails(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_soak(target: str, url: str) -> bool:
        scenario.health.sha = scenario.previous
        return False

    monkeypatch.setattr(_soak, "run", failing_soak)
    record = perform(scenario)
    assert record is not None
    assert record.verdict is Verdict.APPLIED
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLED_BACK
    assert scenario.head() == scenario.previous


def test_perform_records_last_good_immediately_for_a_restart_free_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    soak_calls: list[str] = []

    def fake_soak(target: str, url: str) -> bool:
        soak_calls.append(target)
        return True

    monkeypatch.setattr(_soak, "run", fake_soak)
    record = perform(quiet)
    assert record is not None
    assert record.path is ApplyPath.RESTART_FREE
    assert soak_calls == []
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD


def test_perform_rolls_back_a_failure_after_the_fast_forward(scenario: ApplyScenario) -> None:
    scenario.commands.inject(("bash",), exit_with(1))
    record = perform(scenario)
    assert record is not None
    assert record.verdict is Verdict.FAILED
    assert record.reason is Reason.RENDER_FAILED
    stored = _journal.read()
    assert stored is not None
    assert stored.phase in (_journal.Phase.ROLLED_BACK, _journal.Phase.ROLLBACK_FAILED)


def test_perform_never_rolls_back_a_fast_forward_failure(scenario: ApplyScenario) -> None:
    scenario.commands.inject(("git", "merge"), exit_with(1))
    record = perform(scenario)
    assert record is not None
    assert record.reason is Reason.FAST_FORWARD_FAILED
    assert _journal.read() is None


def test_perform_leaves_a_non_failed_verdict_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = build_apply_scenario(monkeypatch, tmp_path, changed=None)
    record = perform(current)
    assert record is not None
    assert record.verdict is Verdict.UP_TO_DATE
    assert _journal.read() is None


def test_perform_resolves_a_leftover_instead_of_ticking(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal.Journal(
        _journal.Phase.ROLLBACK_REFUSED_DIRTY, scenario.target, scenario.previous, "dist.a", True
    )
    _journal.write(journal)
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_leftover, "resolve", lambda repo, j, **kw: calls.append(j))
    result = perform(scenario)
    assert result is None
    assert len(calls) == 1


def test_main_apply_dispatches_to_the_trampoline_when_not_extracted(
    scenario: ApplyScenario,
) -> None:
    original_cwd = Path.cwd()
    try:
        result = _run.main_apply(
            scenario.repo,
            scheduled=False,
            extracted=False,
            plist_path=scenario.plist_path,
            health_url=HEALTH_URL,
            idle_probe=lambda: True,
            wait_bound_s=0.0,
            detached=True,
        )
    finally:
        os.chdir(original_cwd)
    assert result == 0
    assert len(scenario.execs.calls) == 1


def test_main_apply_dispatches_to_perform_when_extracted(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    result = _run.main_apply(
        scenario.repo,
        scheduled=False,
        extracted=True,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
    )
    assert result == 0
    assert scenario.execs.calls == []
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD


def test_cli_main_rejects_a_missing_or_unknown_subcommand() -> None:
    assert _cli.main([]) == 2
    assert _cli.main(["nope"]) == 2


def test_cli_main_defaults_repo_to_cwd_and_marks_the_run_as_not_extracted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_main_apply(repo: Path, **kwargs: object) -> int:
        calls.append({"repo": repo, **kwargs})
        return 0

    monkeypatch.setattr(_run, "main_apply", fake_main_apply)
    plist = tmp_path / "ai.alan.plist"
    assert _cli.main(["apply", "--plist-path", str(plist)]) == 0
    assert calls[0]["repo"] == Path.cwd()
    assert calls[0]["extracted"] is False
    assert calls[0]["scheduled"] is False
    assert calls[0]["detached"] is True
    assert calls[0]["plist_path"] == plist
    assert calls[0]["health_url"] == _cli._DEFAULT_HEALTH_URL


def test_cli_main_rejects_apply_missing_the_required_plist_path_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_main_apply(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(_run, "main_apply", fake_main_apply)
    assert _cli.main(["apply"]) == 2
    assert calls == []


def test_cli_main_returns_2_when_plist_path_is_the_last_token_with_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_main_apply(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(_run, "main_apply", fake_main_apply)
    assert _cli.main(["apply", "--plist-path"]) == 2
    assert calls == []


def test_cli_main_parses_kit_repo_scheduled_plist_and_health_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_main_apply(repo: Path, **kwargs: object) -> int:
        calls.append({"repo": repo, **kwargs})
        return 0

    monkeypatch.setattr(_run, "main_apply", fake_main_apply)
    plist = tmp_path / "custom.plist"
    result = _cli.main([
        "apply", "--kit-repo", str(tmp_path), "--scheduled",
        "--plist-path", str(plist), "--health-url", "http://x.invalid/health",
    ])
    assert result == 0
    call = calls[0]
    assert call["repo"] == tmp_path
    assert call["scheduled"] is True
    assert call["extracted"] is True
    assert call["plist_path"] == plist
    assert call["health_url"] == "http://x.invalid/health"


def test_cli_main_rejects_an_unrecognized_flag_token() -> None:
    assert _cli.main(["apply", "--plist-path", "x", "--bogus"]) == 2


def test_cli_main_treats_a_health_url_value_matching_a_flag_as_a_plain_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_main_apply(repo: Path, **kwargs: object) -> int:
        calls.append({"repo": repo, **kwargs})
        return 0

    monkeypatch.setattr(_run, "main_apply", fake_main_apply)
    plist = tmp_path / "custom.plist"
    result = _cli.main([
        "apply", "--plist-path", str(plist), "--health-url", "--scheduled",
    ])
    assert result == 0
    call = calls[0]
    assert call["health_url"] == "--scheduled"
    assert call["scheduled"] is False


def test_dunder_main_invokes_cli_main_and_exits_with_its_return_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cli, "main", lambda argv: 7)
    monkeypatch.setattr("sys.argv", ["alan.self_update", "apply"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("alan.self_update.__main__", run_name="__main__")
    assert exc.value.code == 7
