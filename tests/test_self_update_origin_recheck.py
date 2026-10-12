from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from alan import process_runner, self_update
from alan.self_update import ApplyPath, Reason, Verdict, _journal, _run
from tests.self_update_apply_fakes import HEALTH_URL, ApplyScenario, build_apply_scenario
from tests.self_update_fixtures import git

UNUSABLE_ORIGINS = {
    "other-host": "https://gitlab.example.com/example-owner/example-repo.git",
    "uppercase-host": "https://GitHub.com/example-owner/example-repo.git",
    "explicit-port": "https://github.com:443/example-owner/example-repo.git",
    "trailing-slash": "https://github.com/example-owner/example-repo/",
}
OTHER_REPOSITORIES = {
    "other-owner": "https://github.com/other-owner/example-repo.git",
    "other-repo": "git@github.com:example-owner/other-repo.git",
}
USABLE_REWRITE = "https://github.com/example-owner/example-repo"
CHECKED_SLOT_URL = "https://github.com/example-owner/example-repo.git"


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApplyScenario:
    return build_apply_scenario(monkeypatch, tmp_path)


def edit_origin_on_first_idle_probe(scenario: ApplyScenario, url: str) -> None:
    def edit(call: int) -> None:
        git(scenario.repo, "remote", "set-url", "origin", url)

    scenario.idle.on_call = edit


def assert_nothing_changed(scenario: ApplyScenario) -> None:
    assert scenario.commands.mutations() == []
    assert scenario.commands.launchctl_mutations() == []
    assert not scenario.commands.rolled_back()
    assert scenario.head() == scenario.previous
    assert scenario.dist_link() == "dist.a"
    assert scenario.marker_bytes() == f"{scenario.previous}\n".encode()


def perform(scenario: ApplyScenario) -> self_update.StatusRecord | None:
    return _run.perform(
        scenario.repo,
        scheduled=False,
        plist_path=scenario.plist_path,
        health_url=HEALTH_URL,
        idle_probe=scenario.idle,
        wait_bound_s=30.0,
        detached=False,
    )


@pytest.mark.parametrize("url", UNUSABLE_ORIGINS.values(), ids=UNUSABLE_ORIGINS.keys())
def test_an_unusable_origin_is_blocked_before_anything_changes(
    scenario: ApplyScenario, url: str
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", url)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.fetched_sha is None
    assert scenario.base.fetch_calls == []
    assert scenario.stored() == record.as_json()
    assert_nothing_changed(scenario)


@pytest.mark.parametrize("url", UNUSABLE_ORIGINS.values(), ids=UNUSABLE_ORIGINS.keys())
def test_an_origin_edited_between_the_check_and_the_apply_is_blocked_by_the_recheck(
    scenario: ApplyScenario, url: str
) -> None:
    edit_origin_on_first_idle_probe(scenario, url)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.path is ApplyPath.RESTART
    assert record.target_sha == scenario.target
    assert record.previous_sha == scenario.previous
    assert scenario.base.fetch_calls != []
    assert scenario.stored() == record.as_json()
    assert_nothing_changed(scenario)


@pytest.mark.parametrize("url", OTHER_REPOSITORIES.values(), ids=OTHER_REPOSITORIES.keys())
def test_an_origin_switched_to_another_repository_between_check_and_apply_is_blocked(
    scenario: ApplyScenario, url: str
) -> None:
    edit_origin_on_first_idle_probe(scenario, url)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.target_sha == scenario.target
    assert scenario.stored() == record.as_json()
    assert_nothing_changed(scenario)


def render_environments(
    monkeypatch: pytest.MonkeyPatch, *, before_render: str | None = None, repo: Path | None = None
) -> list[Mapping[str, str | None] | None]:
    seen: list[Mapping[str, str | None] | None] = []
    inner = process_runner.run_subprocess

    def spy(
        cmd: Sequence[str],
        *,
        cwd: str | None,
        timeout: float,
        env: Mapping[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "bash":
            seen.append(env)
            if before_render is not None and repo is not None:
                git(repo, "remote", "set-url", "origin", before_render)
        return inner(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", spy)
    return seen


def test_the_apply_renders_with_the_origin_pinned_to_the_checked_repository(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = render_environments(monkeypatch)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.APPLIED, Reason.TARGET_VERIFIED)
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0]["GIT_CONFIG_KEY_1"] == "remote.origin.url"
    assert seen[0]["GIT_CONFIG_VALUE_1"] == CHECKED_SLOT_URL


@pytest.mark.parametrize("url", OTHER_REPOSITORIES.values(), ids=OTHER_REPOSITORIES.keys())
def test_an_origin_switched_after_the_recheck_cannot_redirect_the_render(
    scenario: ApplyScenario, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    seen = render_environments(monkeypatch, before_render=url, repo=scenario.repo)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.APPLIED, Reason.TARGET_VERIFIED)
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0]["GIT_CONFIG_VALUE_1"] == CHECKED_SLOT_URL


def test_an_origin_rewritten_to_another_usable_form_between_check_and_apply_still_applies(
    scenario: ApplyScenario,
) -> None:
    edit_origin_on_first_idle_probe(scenario, USABLE_REWRITE)
    record = scenario.apply()
    assert (record.verdict, record.reason) == (Verdict.APPLIED, Reason.TARGET_VERIFIED)
    assert scenario.head() == scenario.target


def test_a_blocked_recheck_ends_the_tick_without_a_rollback_a_journal_or_a_retry(
    scenario: ApplyScenario,
) -> None:
    edit_origin_on_first_idle_probe(scenario, UNUSABLE_ORIGINS["uppercase-host"])
    first = perform(scenario)
    assert first is not None
    assert (first.verdict, first.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert _journal.read() is None
    assert_nothing_changed(scenario)

    second = perform(scenario)
    assert second is not None
    assert (second.verdict, second.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert _journal.read() is None
    assert_nothing_changed(scenario)
