from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from alan import self_update
from alan.self_update import ApplyPath, Reason, Verdict
from tests.self_update_apply_fakes import (
    ApplyScenario,
    Argv,
    Fault,
    build_apply_scenario,
    exit_with,
    hang,
    reply,
    vanish,
)
from tests.self_update_fixtures import completed

BEFORE_TEARDOWN = frozenset({
    "fast_forward", "render", "sync", "next_slot", "npm_ci", "build", "activate",
})

STEPS: dict[str, tuple[Argv, Reason, bool]] = {
    "fast_forward": (("git", "merge"), Reason.FAST_FORWARD_FAILED, True),
    "render": (("bash",), Reason.RENDER_FAILED, True),
    "sync": (("uv", "sync"), Reason.DEPENDENCY_SYNC_FAILED, True),
    "next_slot": (("next-slot",), Reason.WEB_BUILD_FAILED, True),
    "npm_ci": (("npm", "ci"), Reason.WEB_BUILD_FAILED, True),
    "build": (("npm", "run", "build"), Reason.WEB_BUILD_FAILED, True),
    "activate": (("activate",), Reason.WEB_BUILD_FAILED, True),
    "bootout": (("launchctl", "bootout"), Reason.TEARDOWN_FAILED, False),
    "enable": (("launchctl", "enable"), Reason.BOOTSTRAP_FAILED, False),
    "bootstrap": (("launchctl", "bootstrap"), Reason.BOOTSTRAP_FAILED, True),
    "kickstart": (("launchctl", "kickstart"), Reason.KICKSTART_FAILED, True),
}
EXIT_MATTERS = {name: entry for name, entry in STEPS.items() if entry[2]}


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def assert_failed_without_rollback(
    scenario: ApplyScenario, record: self_update.StatusRecord, name: str
) -> None:
    tokens, reason, _ = STEPS[name]
    assert (record.verdict, record.reason) == (Verdict.FAILED, reason)
    assert record.path is ApplyPath.RESTART
    assert record.target_sha == scenario.target
    assert record.previous_sha == scenario.previous
    assert record.previous_slot == "dist.a"
    assert record.fetched_sha == scenario.target
    assert scenario.stored() == record.as_json()
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert not scenario.commands.rolled_back()
    assert set(tokens) <= set(scenario.commands.mutations()[-1])
    if name in BEFORE_TEARDOWN:
        assert scenario.commands.launchctl_mutations() == []
        assert scenario.dist_link() == "dist.a"
    assert scenario.head() == (scenario.previous if name == "fast_forward" else scenario.target)


@pytest.mark.parametrize("name", list(EXIT_MATTERS))
def test_a_nonzero_exit_at_a_step_records_a_failure_at_that_step(
    scenario: ApplyScenario, name: str
) -> None:
    scenario.commands.inject(STEPS[name][0], exit_with(1))
    assert_failed_without_rollback(scenario, scenario.apply(), name)


@pytest.mark.parametrize("name", list(STEPS))
def test_a_time_limit_hit_at_a_step_records_a_failure_at_that_step(
    scenario: ApplyScenario, name: str
) -> None:
    scenario.commands.inject(STEPS[name][0], hang)
    assert_failed_without_rollback(scenario, scenario.apply(), name)


@pytest.mark.parametrize("name", list(STEPS))
def test_a_command_that_cannot_be_started_at_a_step_records_a_failure_at_that_step(
    scenario: ApplyScenario, name: str
) -> None:
    scenario.commands.inject(STEPS[name][0], vanish)
    assert_failed_without_rollback(scenario, scenario.apply(), name)


def test_a_render_refusal_during_the_apply_records_a_render_failure_before_the_service_is_touched(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(STEPS["render"][0], exit_with(78))
    assert_failed_without_rollback(scenario, scenario.apply(), "render")


@pytest.mark.parametrize("name", ["bootout", "enable"])
def test_the_exit_code_of_bootout_and_enable_is_ignored_like_the_owner_pipeline(
    scenario: ApplyScenario, name: str
) -> None:
    def fail_after_doing_the_work(argv: Argv, timeout: float) -> Any:
        scenario.launchctl.loaded = False
        return completed(returncode=3)

    scenario.commands.inject(STEPS[name][0], fail_after_doing_the_work)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.APPLIED, Reason.TARGET_VERIFIED)


@pytest.mark.parametrize("name", ["bootout", "enable", "bootstrap", "kickstart"])
def test_a_failure_after_the_slot_flip_leaves_the_new_slot_and_the_target_in_place(
    scenario: ApplyScenario, name: str
) -> None:
    scenario.commands.inject(STEPS[name][0], hang)
    record = scenario.apply()
    assert record.verdict is Verdict.FAILED
    assert scenario.dist_link() == "dist.b"
    assert scenario.head() == scenario.target
    assert record.head_sha == scenario.target
    assert not scenario.commands.rolled_back()


def test_a_service_that_never_unloads_fails_the_teardown_and_never_bootstraps(
    scenario: ApplyScenario,
) -> None:
    scenario.launchctl.stuck = True
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.TEARDOWN_FAILED)
    assert scenario.commands.launchctl_mutations() == [
        argv for argv in scenario.commands.launchctl_argvs() if argv[1] == "bootout"
    ]
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert scenario.health.urls == []


def test_a_failed_probe_while_waiting_for_the_unload_fails_the_teardown(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("launchctl", "print"), hang, skip=2)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.TEARDOWN_FAILED)
    assert scenario.commands.count(("launchctl", "bootout")) == 1
    assert scenario.commands.count(("launchctl", "bootstrap")) == 0


def test_a_failed_probe_before_the_teardown_fails_the_teardown_without_a_bootout(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("launchctl", "print"), hang, skip=1)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.TEARDOWN_FAILED)
    assert scenario.commands.launchctl_mutations() == []


@pytest.mark.parametrize("fault", [hang, vanish], ids=["timeout", "missing_launchctl"])
def test_a_failed_service_probe_while_classifying_errors_before_anything_changes(
    scenario: ApplyScenario, fault: Fault
) -> None:
    scenario.commands.inject(("launchctl", "print"), fault)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.SERVICE_PROBE_FAILED)
    assert record.path is None
    assert record.target_sha == scenario.target
    assert record.previous_sha == scenario.previous
    assert record.previous_slot == "dist.a"
    assert scenario.commands.mutations() == []
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert scenario.stored() == record.as_json()


def test_an_out_of_set_slot_name_from_the_swap_script_fails_the_web_build(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("next-slot",), reply("dist.z\n"))
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.WEB_BUILD_FAILED)
    assert not scenario.commands.has(("npm",))
    assert scenario.dist_link() == "dist.a"


def test_a_next_slot_that_cannot_be_removed_fails_the_web_build_before_the_build_runs(
    scenario: ApplyScenario,
) -> None:
    os.symlink("nowhere", scenario.web / "dist.b")
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.WEB_BUILD_FAILED)
    assert scenario.commands.has(("npm", "ci"))
    assert not scenario.commands.has(("npm", "run", "build"))
    assert scenario.dist_link() == "dist.a"
    assert scenario.commands.launchctl_mutations() == []


def test_an_unexpected_error_before_the_checkout_moves_is_an_error_and_changes_nothing(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("git", "status"), exit_with(128), skip=1)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.GIT_FAILED)
    assert record.path is ApplyPath.RESTART
    assert scenario.commands.mutations() == []
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"


@pytest.mark.parametrize("failure", [OSError("probe unreachable"), RuntimeError("probe crashed")])
def test_an_idle_probe_that_fails_is_an_error_and_changes_nothing(
    scenario: ApplyScenario, failure: Exception
) -> None:
    def broken(call: int) -> None:
        raise failure

    scenario.idle.on_call = broken
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.GIT_FAILED)
    assert scenario.commands.mutations() == []
    assert scenario.head() == scenario.previous


def test_a_restart_free_apply_whose_head_did_not_move_is_a_verification_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("git", "merge"), reply())
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_MISMATCH)
    assert record.path is ApplyPath.RESTART_FREE
    assert record.previous_slot == "dist.a"
    assert quiet.head() == quiet.previous
    assert quiet.marker_bytes() == f"{quiet.previous}\n".encode()


def test_a_restart_free_apply_whose_active_slot_was_not_stamped_is_a_verification_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("activate",), reply())
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_MISMATCH)
    assert quiet.head() == quiet.target
    assert quiet.dist_link() == "dist.a"
    assert quiet.health.urls == []


def test_a_restart_free_apply_whose_active_slot_has_no_recorded_commit_is_a_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("activate",), reply())
    (quiet.web / "dist.a" / ".deploy_sha").unlink()
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_MISMATCH)


def test_a_restart_free_apply_whose_head_cannot_be_read_back_fails_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("git", "rev-parse", "HEAD"), hang, skip=2)
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_TIMEOUT)
    assert record.path is ApplyPath.RESTART_FREE


def test_a_failed_marker_write_propagates_and_leaves_no_status_record(
    scenario: ApplyScenario,
) -> None:
    scenario.marker.unlink()
    scenario.marker.mkdir()
    with pytest.raises(OSError):
        scenario.apply()
    assert not self_update.status_path().exists()
    assert scenario.head() == scenario.target


def test_a_failed_status_write_propagates_after_a_restart_free_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    self_update.status_path().parent.mkdir(parents=True, exist_ok=True)
    self_update.status_path().mkdir()
    with pytest.raises(OSError):
        quiet.apply()
    assert quiet.head() == quiet.target
