from __future__ import annotations

from pathlib import Path

import pytest

from alan import self_update
from alan.self_update import ApplyPath, Reason, Verdict, _journal, _rollback
from tests.self_update_apply_fakes import (
    HEALTH_URL,
    ApplyScenario,
    build_apply_scenario,
    exit_with,
    reply,
)

_RESTART_FAULTS: dict[str, tuple[tuple[str, ...], Reason]] = {
    "render": (("bash",), Reason.RENDER_FAILED),
    "sync": (("uv", "sync"), Reason.DEPENDENCY_SYNC_FAILED),
    "web_build": (("npm", "ci"), Reason.WEB_BUILD_FAILED),
    "teardown": (("launchctl", "bootout"), Reason.TEARDOWN_FAILED),
    "bootstrap": (("launchctl", "bootstrap"), Reason.BOOTSTRAP_FAILED),
    "kickstart": (("launchctl", "kickstart"), Reason.KICKSTART_FAILED),
}


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def journal_from(record: self_update.StatusRecord) -> _journal.Journal:
    if record.target_sha is None or record.previous_sha is None:
        raise AssertionError("a failed record must carry both commits")
    return _journal.Journal(
        _journal.Phase.ROLLING_BACK,
        record.target_sha,
        record.previous_sha,
        record.previous_slot,
        record.path is ApplyPath.RESTART,
    )


def assert_rolled_back(scenario: ApplyScenario, record: self_update.StatusRecord) -> None:
    journal = journal_from(record)
    scenario.commands.injections.clear()
    scenario.health.up = lambda: True
    scenario.health.answer = None
    scenario.health.sha = scenario.previous
    ok = _rollback.perform(
        scenario.repo, journal, plist_path=scenario.plist_path, health_url=HEALTH_URL
    )
    assert ok
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"
    if journal.restarted:
        assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.ROLLED_BACK
    assert scenario.target in self_update.read_rejected()


@pytest.mark.parametrize(("name", "expected"), _RESTART_FAULTS.values(), ids=_RESTART_FAULTS.keys())
def test_each_apply_failure_reason_triggers_a_rollback(
    scenario: ApplyScenario, name: tuple[str, ...], expected: Reason
) -> None:
    scenario.commands.inject(name, exit_with(1))
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, expected)
    assert_rolled_back(scenario, record)


def test_a_verification_timeout_on_a_restart_apply_triggers_a_rollback(
    scenario: ApplyScenario,
) -> None:
    scenario.health.up = lambda: False
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_TIMEOUT)
    assert_rolled_back(scenario, record)


def test_a_verification_mismatch_on_a_restart_free_apply_triggers_a_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    quiet = build_apply_scenario(monkeypatch, tmp_path, changed="docs/note.md")
    quiet.commands.inject(("activate",), reply())
    record = quiet.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.VERIFICATION_MISMATCH)
    assert record.path is ApplyPath.RESTART_FREE
    assert_rolled_back(quiet, record)


def test_a_failure_before_the_fast_forward_never_rolls_back_or_rejects(
    scenario: ApplyScenario,
) -> None:
    scenario.commands.inject(("git", "merge"), exit_with(1))
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.FAILED, Reason.FAST_FORWARD_FAILED)
    assert scenario.head() == scenario.previous
    assert self_update.read_rejected() == frozenset()
    assert _journal.read() is None
