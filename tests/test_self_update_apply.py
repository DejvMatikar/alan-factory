from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alan import deploy_restart, self_update
from alan.self_update import ApplyPath, Reason, Verdict
from tests.self_update_apply_fakes import (
    DOMAIN,
    HEALTH_URL,
    LABEL,
    SWAP_SCRIPT,
    ApplyScenario,
    build_apply_scenario,
)
from tests.self_update_fixtures import RUNS_NEEDLE, advance_origin, completed, git, push_run

APPLY_KEYS = ["path", "target_sha", "previous_sha", "previous_slot"]
NINE_KEYS = [
    "verdict", "reason", "fetched_sha", "head_sha", "checked_at",
    "path", "target_sha", "previous_sha", "previous_slot",
]


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def make(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: Any) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path, **kwargs)


def arrange_pending(scenario: ApplyScenario) -> None:
    scenario.base.set_runs()


def arrange_red(scenario: ApplyScenario) -> None:
    scenario.base.set_runs(push_run(scenario.target, conclusion="failure"))


def arrange_blocked(scenario: ApplyScenario) -> None:
    (scenario.repo / "untracked.txt").write_text("u\n", encoding="utf-8")


def arrange_error(scenario: ApplyScenario) -> None:
    scenario.base.router.respond(RUNS_NEEDLE, completed(returncode=1))


NON_GREEN: dict[str, tuple[Callable[[ApplyScenario], None], Verdict]] = {
    "blocked": (arrange_blocked, Verdict.BLOCKED),
    "pending": (arrange_pending, Verdict.PENDING),
    "red": (arrange_red, Verdict.RED),
    "error": (arrange_error, Verdict.ERROR),
}


@pytest.mark.parametrize(("arrange", "verdict"), NON_GREEN.values(), ids=NON_GREEN.keys())
def test_a_non_green_check_is_returned_and_recorded_unchanged_and_nothing_else_moves(
    scenario: ApplyScenario, arrange: Callable[[ApplyScenario], None], verdict: Verdict
) -> None:
    arrange(scenario)
    files_before = sorted(path.name for path in scenario.repo.iterdir())
    checked = self_update.check(scenario.repo)
    scenario.commands.calls.clear()
    record = scenario.apply()
    assert record.verdict is verdict
    assert {**record.as_json(), "checked_at": ""} == {**checked.as_json(), "checked_at": ""}
    assert [record.as_json()[key] for key in APPLY_KEYS] == [None] * 4
    assert scenario.stored() == record.as_json()
    assert scenario.commands.mutations() == []
    assert scenario.commands.launchctl_argvs() == []
    assert scenario.health.urls == []
    assert scenario.idle.calls == 0
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert sorted(path.name for path in scenario.repo.iterdir()) == files_before


def test_an_up_to_date_checkout_is_returned_unchanged_and_nothing_moves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = make(monkeypatch, tmp_path, changed=None)
    record = current.apply()
    assert record.verdict is Verdict.UP_TO_DATE
    assert record.reason is Reason.ALREADY_CURRENT
    assert list(record.as_json()) == NINE_KEYS
    assert current.commands.mutations() == []
    assert current.commands.launchctl_argvs() == []


def test_the_classifier_is_consulted_only_after_a_green_check(
    monkeypatch: pytest.MonkeyPatch, scenario: ApplyScenario
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(deploy_restart, "decide", lambda *args, **kwargs: seen.append("x"))
    scenario.base.set_runs()
    assert scenario.apply().verdict is Verdict.PENDING
    assert seen == []


def test_the_classifier_runs_exactly_once_against_the_gated_commit_and_the_untouched_head(
    monkeypatch: pytest.MonkeyPatch, scenario: ApplyScenario
) -> None:
    seen: list[tuple[Path, Path, bool, str, str]] = []
    real = deploy_restart.decide

    def spy(repo: Path, marker: Path, loaded: bool, *, rev: str = "HEAD") -> Any:
        seen.append((repo, marker, loaded, rev, git(repo, "rev-parse", "HEAD")))
        return real(repo, marker, loaded, rev=rev)

    monkeypatch.setattr(deploy_restart, "decide", spy)
    record = scenario.apply()
    assert seen == [(scenario.repo, scenario.marker, True, scenario.target, scenario.previous)]
    assert record.previous_sha == scenario.previous


def test_the_gated_commit_is_used_even_when_the_private_ref_advances_after_the_check(
    scenario: ApplyScenario,
) -> None:
    def advance_ref_during_classification() -> None:
        scenario.launchctl.on_print = None
        newer = advance_origin(scenario.base.pair, "later.txt")
        bare = str(scenario.base.pair.bare)
        git(scenario.repo, "fetch", "-q", bare, "+refs/heads/main:refs/deploy/target")
        assert git(scenario.repo, "rev-parse", "refs/deploy/target") == newer

    scenario.launchctl.on_print = advance_ref_during_classification
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert scenario.head() == scenario.target
    assert record.target_sha == scenario.target
    assert git(scenario.repo, "rev-parse", "refs/deploy/target") != scenario.target


PATH_CASES: dict[str, tuple[dict[str, Any], ApplyPath]] = {
    "safe_range_loaded_with_marker": (
        {"changed": "docs/note.md", "marker": "head", "loaded": True}, ApplyPath.RESTART_FREE,
    ),
    "hot_range": ({"changed": "upstream.txt", "marker": "head"}, ApplyPath.RESTART),
    "missing_marker": ({"changed": "docs/note.md", "marker": "absent"}, ApplyPath.RESTART),
    "unknown_marker": ({"changed": "docs/note.md", "marker": "unreachable"}, ApplyPath.RESTART),
    "unloaded_service": (
        {"changed": "docs/note.md", "marker": "head", "loaded": False}, ApplyPath.RESTART,
    ),
    "plist_changed": (
        {"changed": "deploy/ai.alan.plist", "marker": "head"}, ApplyPath.RESTART,
    ),
}


@pytest.mark.parametrize(("options", "expected"), PATH_CASES.values(), ids=PATH_CASES.keys())
def test_the_path_is_chosen_by_the_classifier_before_the_checkout_moves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, options: dict[str, Any], expected: ApplyPath
) -> None:
    chosen = make(monkeypatch, tmp_path, **options)
    record = chosen.apply()
    assert record.verdict is Verdict.APPLIED
    assert record.path is expected
    assert chosen.stored()["path"] == expected.value


def test_a_classifier_error_takes_the_restart_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("diff failed")

    monkeypatch.setattr(deploy_restart, "changed_paths", broken)
    chosen = make(monkeypatch, tmp_path, changed="docs/note.md")
    assert chosen.apply().path is ApplyPath.RESTART


def test_the_checkout_advances_by_fast_forward_only(scenario: ApplyScenario) -> None:
    scenario.apply()
    merges = [argv for argv in scenario.commands.argvs() if argv[0] == "git" and "merge" in argv]
    assert merges == [("git", "-C", str(scenario.repo), "merge", "--ff-only", scenario.target)]
    assert not scenario.commands.rolled_back()
    assert scenario.head() == scenario.target


def test_ignored_local_state_survives_the_apply(scenario: ApplyScenario) -> None:
    scenario.apply()
    assert (scenario.repo / ".venv" / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (scenario.web / "node_modules" / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (scenario.web / "dist.a" / "index.html").is_file()


def test_the_prepare_steps_run_the_exact_commands_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = make(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.apply()
    swap = (sys.executable, str(quiet.repo / "scripts" / "swap_web_dist.py"))
    web = str(quiet.web)
    assert quiet.commands.mutations() == [
        ("git", "-C", str(quiet.repo), "merge", "--ff-only", quiet.target),
        ("bash", str(quiet.repo / "deploy" / "render_plist.sh")),
        ("uv", "sync", "--locked"),
        (*swap, "--web-dir", web, "next-slot"),
        ("npm", "ci", "--quiet"),
        ("npm", "run", "build", "--", "--outDir", "dist.b"),
        (*swap, "--web-dir", web, "activate", "--slot", "dist.b", "--sha", quiet.target),
    ]


def test_the_prepare_commands_run_in_the_right_directories_with_a_time_limit(
    scenario: ApplyScenario,
) -> None:
    scenario.apply()
    directories = {
        call.argv[0:2]: call.cwd
        for call in scenario.commands.calls
        if call.argv[0] in {"uv", "npm"}
    }
    assert directories[("uv", "sync")] == str(scenario.repo)
    assert directories[("npm", "ci")] == str(scenario.web)
    assert directories[("npm", "run")] == str(scenario.web)
    external = [call for call in scenario.commands.calls if call.argv[0] != "gh"]
    assert all(0 < call.timeout <= 900 for call in external)


def test_the_web_build_is_activated_with_the_target_through_the_real_swap_script(
    scenario: ApplyScenario,
) -> None:
    scenario.apply()
    assert scenario.dist_link() == "dist.b"
    assert (scenario.web / "dist" / ".deploy_sha").read_text(encoding="utf-8") == scenario.target
    assert (scenario.web / "dist" / "index.html").is_file()
    assert SWAP_SCRIPT.is_file()


def test_a_stale_file_in_the_next_slot_is_removed_before_the_build(
    scenario: ApplyScenario,
) -> None:
    (scenario.web / "dist.b").mkdir()
    (scenario.web / "dist.b" / "stale.txt").write_text("stale\n", encoding="utf-8")
    scenario.apply()
    assert not (scenario.web / "dist.b" / "stale.txt").exists()
    assert (scenario.web / "dist.b" / "index.html").is_file()


def test_a_restart_apply_boots_out_enables_bootstraps_and_kickstarts_once_in_that_order(
    scenario: ApplyScenario,
) -> None:
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert record.reason is Reason.TARGET_VERIFIED
    assert scenario.commands.launchctl_mutations() == [
        ("launchctl", "bootout", LABEL),
        ("launchctl", "enable", LABEL),
        ("launchctl", "bootstrap", DOMAIN, str(scenario.plist_path)),
        ("launchctl", "kickstart", "-p", LABEL),
    ]
    assert scenario.commands.count(("launchctl", "kickstart")) == 1
    assert scenario.launchctl.loaded


def test_the_launch_configuration_is_rendered_before_any_teardown(scenario: ApplyScenario) -> None:
    scenario.apply()
    render = scenario.commands.index_of(("bash",))
    assert render < scenario.commands.index_of(("launchctl", "bootout"))
    assert render < scenario.commands.index_of(("launchctl", "bootstrap"))


def test_a_plist_change_still_renders_before_teardown_and_restarts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    changed = make(monkeypatch, tmp_path, changed="deploy/ai.alan.plist")
    record = changed.apply()
    assert record.path is ApplyPath.RESTART
    assert changed.commands.index_of(("bash",)) < changed.commands.index_of(
        ("launchctl", "bootout")
    )


def test_an_unloaded_service_is_bootstrapped_without_a_bootout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unloaded = make(monkeypatch, tmp_path, loaded=False)
    record = unloaded.apply()
    assert record.verdict is Verdict.APPLIED
    assert [argv[1] for argv in unloaded.commands.launchctl_mutations()] == [
        "enable", "bootstrap", "kickstart",
    ]


def test_a_restart_apply_is_verified_by_the_served_commit_at_the_health_url(
    scenario: ApplyScenario,
) -> None:
    scenario.apply()
    assert scenario.health.urls == [HEALTH_URL]
    assert scenario.health.timeouts == [5.0]


def test_the_marker_moves_to_the_target_only_after_a_verified_restart(
    scenario: ApplyScenario,
) -> None:
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    scenario.apply()
    assert scenario.marker_bytes() == f"{scenario.target}\n".encode()


def test_a_restart_free_apply_touches_neither_the_service_nor_the_health_url_nor_the_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = make(monkeypatch, tmp_path, changed="docs/note.md")
    record = quiet.apply()
    assert record.verdict is Verdict.APPLIED
    assert record.path is ApplyPath.RESTART_FREE
    assert quiet.commands.launchctl_mutations() == []
    assert quiet.health.urls == []
    assert quiet.idle.calls == 0
    assert quiet.marker_bytes() == f"{quiet.previous}\n".encode()
    assert quiet.head() == quiet.target
    assert quiet.dist_link() == "dist.b"


def test_a_restart_free_apply_is_verified_even_when_the_served_commit_is_something_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = make(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.health.respond_with(RuntimeError("must not be consulted"))
    assert quiet.apply().verdict is Verdict.APPLIED


def test_the_applied_record_names_the_path_the_target_and_the_previous_commit_and_slot(
    scenario: ApplyScenario,
) -> None:
    record = scenario.apply()
    assert record.as_json() == {
        "verdict": "applied",
        "reason": "target_verified",
        "fetched_sha": scenario.target,
        "head_sha": scenario.target,
        "checked_at": record.checked_at,
        "path": "restart",
        "target_sha": scenario.target,
        "previous_sha": scenario.previous,
        "previous_slot": "dist.a",
    }
    assert list(scenario.stored()) == NINE_KEYS
    assert scenario.stored() == record.as_json()


@pytest.mark.parametrize("dist", ["real", "missing"])
def test_a_web_directory_without_a_slot_link_records_no_previous_slot_and_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dist: str
) -> None:
    unlinked = make(monkeypatch, tmp_path, dist=dist)
    record = unlinked.apply()
    assert record.verdict is Verdict.APPLIED
    assert record.previous_slot is None
    assert unlinked.stored()["previous_slot"] is None
    assert unlinked.dist_link() == "dist.a"
    assert (unlinked.web / "dist" / ".deploy_sha").read_text(encoding="utf-8") == unlinked.target


def test_a_slot_link_pointing_outside_the_slot_set_records_no_previous_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    odd = make(monkeypatch, tmp_path, dist="missing")
    (odd.web / "elsewhere").mkdir()
    os.symlink("elsewhere", odd.web / "dist")
    assert odd.apply().previous_slot is None


def test_the_apply_needs_no_gh_beyond_the_check_reads(scenario: ApplyScenario) -> None:
    scenario.apply()
    gh = [argv for argv in scenario.commands.argvs() if argv[0] == "gh"]
    assert len(gh) == 2
