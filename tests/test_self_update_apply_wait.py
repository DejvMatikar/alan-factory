from __future__ import annotations

import http.client
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from alan.self_update import ApplyPath, Reason, Verdict
from tests.self_update_apply_fakes import (
    BODY_LIMIT,
    ApplyScenario,
    FakeResponse,
    build_apply_scenario,
    health_body,
)
from tests.self_update_fixtures import commit, git


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def quiet_scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")


def test_no_idle_wait_happens_when_detached_workers_are_on(scenario: ApplyScenario) -> None:
    record = scenario.apply(detached=True)
    assert record.verdict is Verdict.APPLIED
    assert scenario.idle.calls == 0
    assert scenario.clock.sleeps == []


def test_no_idle_wait_happens_for_a_restart_free_apply_with_detached_workers_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = quiet_scenario(monkeypatch, tmp_path)
    record = quiet.apply(detached=False)
    assert record.path is ApplyPath.RESTART_FREE
    assert quiet.idle.calls == 0
    assert quiet.clock.sleeps == []


def test_an_idle_service_is_probed_once_and_nothing_sleeps(scenario: ApplyScenario) -> None:
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert scenario.idle.calls == 1
    assert scenario.clock.sleeps == []


def test_a_busy_service_is_polled_with_one_sleep_per_busy_probe(scenario: ApplyScenario) -> None:
    scenario.idle.busy = 3
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert scenario.idle.calls == 4
    assert scenario.clock.sleeps == [5.0, 5.0, 5.0]


def test_an_always_busy_service_is_waited_on_no_longer_than_the_bound_and_then_restarted(
    scenario: ApplyScenario,
) -> None:
    scenario.idle.busy = None
    record = scenario.apply(wait_bound_s=12.0)
    assert record.verdict is Verdict.APPLIED
    assert record.path is ApplyPath.RESTART
    assert scenario.clock.sleeps == [5.0, 5.0, 2.0]
    assert scenario.clock.slept <= 12.0
    assert scenario.commands.count(("launchctl", "kickstart")) == 1


def test_a_zero_bound_probes_once_sleeps_never_and_still_proceeds(scenario: ApplyScenario) -> None:
    scenario.idle.busy = None
    record = scenario.apply(wait_bound_s=0.0)
    assert record.verdict is Verdict.APPLIED
    assert scenario.idle.calls == 1
    assert scenario.clock.sleeps == []


def test_nothing_is_checked_out_installed_or_swapped_until_the_wait_ends(
    scenario: ApplyScenario,
) -> None:
    observed: list[tuple[int, str, str | None]] = []

    def watch(call: int) -> None:
        observed.append((len(scenario.commands.mutations()), scenario.head(), scenario.dist_link()))

    scenario.idle.busy = 3
    scenario.idle.on_call = watch
    scenario.apply()
    assert observed == [(0, scenario.previous, "dist.a")] * 4
    assert scenario.head() == scenario.target


def test_a_tree_that_turns_dirty_during_the_wait_is_blocked_and_nothing_changes(
    scenario: ApplyScenario,
) -> None:
    scenario.idle.busy = 2

    def dirty(call: int) -> None:
        (scenario.repo / "edited.txt").write_text("edit\n", encoding="utf-8")

    scenario.idle.on_call = dirty
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.DIRTY_TREE)
    assert record.path is ApplyPath.RESTART
    assert record.previous_sha == scenario.previous
    assert record.target_sha == scenario.target
    assert scenario.commands.mutations() == []
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert scenario.health.urls == []


def test_a_head_that_moves_during_the_wait_is_blocked_as_not_fast_forward(
    scenario: ApplyScenario,
) -> None:
    scenario.idle.busy = 1

    def advance(call: int) -> None:
        if call == 1:
            commit(scenario.repo, "local.txt")

    scenario.idle.on_call = advance
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NOT_FAST_FORWARD)
    assert scenario.commands.mutations() == []
    assert scenario.head() != scenario.target


def test_a_branch_switch_during_the_wait_is_blocked_as_not_on_main(
    scenario: ApplyScenario,
) -> None:
    scenario.idle.busy = 1

    def switch(call: int) -> None:
        if call == 1:
            git(scenario.repo, "checkout", "-q", "-b", "elsewhere")

    scenario.idle.on_call = switch
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert scenario.commands.mutations() == []


def test_a_restart_free_apply_is_rechecked_just_before_the_checkout_moves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = quiet_scenario(monkeypatch, tmp_path)

    def dirty() -> None:
        quiet.launchctl.on_print = None
        (quiet.repo / "edited.txt").write_text("edit\n", encoding="utf-8")

    quiet.launchctl.on_print = dirty
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.DIRTY_TREE)
    assert record.path is ApplyPath.RESTART_FREE
    assert quiet.commands.mutations() == []


def test_a_service_that_unloads_slowly_is_polled_each_second_until_it_is_gone(
    scenario: ApplyScenario,
) -> None:
    scenario.launchctl.unload_delay = 3
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert scenario.clock.sleeps == [1.0, 1.0, 1.0]


def test_a_service_that_never_unloads_is_polled_for_fifteen_seconds_and_no_longer(
    scenario: ApplyScenario,
) -> None:
    scenario.launchctl.stuck = True
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.TEARDOWN_FAILED)
    assert scenario.clock.sleeps == [1.0] * 15
    assert scenario.commands.count(("launchctl", "bootout")) == 1


def test_a_service_that_only_spawns_after_the_forced_start_is_verified_once_it_answers(
    scenario: ApplyScenario,
) -> None:
    scenario.health.up = lambda: (
        scenario.commands.has(("launchctl", "kickstart")) and len(scenario.clock.sleeps) >= 2
    )
    record = scenario.apply()
    assert record.verdict is Verdict.APPLIED
    assert scenario.clock.sleeps == [3.0, 3.0]
    assert len(scenario.health.urls) == 3
    assert scenario.commands.count(("launchctl", "kickstart")) == 1
    assert scenario.marker_bytes() == f"{scenario.target}\n".encode()


def test_a_service_that_serves_the_wrong_commit_times_out_within_the_bound(
    scenario: ApplyScenario,
) -> None:
    scenario.health.sha = "e" * 40
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_TIMEOUT)
    assert scenario.clock.slept <= 60.0
    assert scenario.clock.slept > 0
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert scenario.commands.count(("launchctl", "kickstart")) == 1


def padded(sha: str, size: int) -> bytes:
    base = health_body(sha, pad="")
    return health_body(sha, pad="x" * (size - len(base)))


def unhealthy_answers(target: str) -> dict[str, Any]:
    return {
        "ok_false": FakeResponse(200, health_body(target, ok=False)),
        "ok_not_boolean": FakeResponse(200, health_body(target, ok=1)),
        "non_200": FakeResponse(503, health_body(target)),
        "redirect_status": FakeResponse(204, health_body(target)),
        "malformed_json": FakeResponse(200, b"{not json"),
        "not_utf8": FakeResponse(200, b"\xff\xfe"),
        "json_list": FakeResponse(200, b"[]"),
        "no_served_sha": FakeResponse(200, b'{"ok": true}'),
        "null_served_sha": FakeResponse(200, health_body("x", served_sha=None)),
        "abbreviated_sha": FakeResponse(200, health_body("abc123")),
        "over_limit_body": FakeResponse(200, padded("a" * 40, BODY_LIMIT + 1)),
        "url_error": urllib.error.URLError("unreachable"),
        "http_error": urllib.error.HTTPError(
            "http://health.invalid", 500, "boom", http.client.HTTPMessage(), None
        ),
        "os_error": ConnectionResetError("reset"),
        "timeout_error": TimeoutError("slow"),
        "incomplete_read": http.client.IncompleteRead(b"{"),
        "bad_status_line": http.client.BadStatusLine("garbage"),
        "value_error": ValueError("bad url"),
    }


@pytest.mark.parametrize("name", list(unhealthy_answers("t" * 40)))
def test_an_unhealthy_answer_never_verifies_a_restart(scenario: ApplyScenario, name: str) -> None:
    scenario.health.respond_with(unhealthy_answers(scenario.target)[name])
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_TIMEOUT)
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    assert scenario.clock.slept <= 60.0
    assert len(scenario.health.urls) > 1


def test_a_body_exactly_at_the_size_limit_is_still_accepted(scenario: ApplyScenario) -> None:
    body = padded(scenario.target, BODY_LIMIT)
    assert len(body) == BODY_LIMIT
    scenario.health.respond_with(FakeResponse(200, body))
    assert scenario.apply().verdict is Verdict.APPLIED


def test_the_health_body_is_read_no_further_than_one_byte_past_the_limit(
    scenario: ApplyScenario,
) -> None:
    sizes: list[int] = []

    class Spy(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            sizes.append(size)
            return super().read(size)

    scenario.health.respond_with(Spy(200, health_body(scenario.target)))
    scenario.apply()
    assert sizes == [BODY_LIMIT + 1]
