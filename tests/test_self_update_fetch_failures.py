from __future__ import annotations

import base64
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from alan import self_update
from alan.self_update import Reason, Verdict, _journal, _run, _soak
from tests.self_update_apply_fakes import HEALTH_URL, ApplyScenario, build_apply_scenario
from tests.self_update_fixtures import (
    TRANSPORT_SENTINEL,
    UNUSABLE_RECORDS,
    Mutation,
    Scenario,
    build_scenario,
    completed,
    record_path,
    redirect_env,
    write_json,
)

ENCODED_SENTINEL = base64.b64encode(f"x-access-token:{TRANSPORT_SENTINEL}".encode()).decode()
LEAKY_TEXT = f"fatal: {TRANSPORT_SENTINEL} {ENCODED_SENTINEL}"
LONE_SURROGATE = "stored-\ud800-value"
FORBIDDEN_VERBS = frozenset({"fetch", "pull", "clone", "ls-remote"})
Failure = subprocess.CompletedProcess[str] | BaseException

LEAKY_FETCH_FAILURES: dict[str, Failure] = {
    "non_zero_exit_echoing_the_value": completed(
        TRANSPORT_SENTINEL, returncode=128, stderr=LEAKY_TEXT
    ),
    "timeout_carrying_the_value": subprocess.TimeoutExpired(
        ["git", "fetch"], 300.0, output=LEAKY_TEXT.encode(), stderr=LEAKY_TEXT.encode()
    ),
    "missing_binary_naming_the_value": FileNotFoundError(LEAKY_TEXT),
    "rejected_spawn_naming_the_value": PermissionError(LEAKY_TEXT),
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def inputs_for(tmp_path: Path) -> self_update.ApplyInputs:
    return self_update.ApplyInputs(
        detached=True,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        health_url=HEALTH_URL,
        plist_path=tmp_path / "ai.alan.plist",
    )


RUN_KINDS: dict[str, Callable[[Scenario], dict[str, Any]]] = {
    "normal": lambda scenario: {},
    "forced": lambda scenario: {"force": True},
    "pinned": lambda scenario: {"pin": scenario.fetched},
}


class SpawnRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.argvs: list[list[str]] = []
        real = subprocess.run

        def recording(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
            self.argvs.append(list(cmd))
            return real(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", recording)

    def fetch_class(self) -> list[list[str]]:
        return [argv for argv in self.argvs if FORBIDDEN_VERBS & set(argv)]

    def gh(self) -> list[list[str]]:
        return [argv for argv in self.argvs if argv[0] == "gh"]


def leaked(text: str) -> bool:
    return TRANSPORT_SENTINEL in text or ENCODED_SENTINEL in text


@pytest.mark.parametrize("kind", RUN_KINDS.keys())
@pytest.mark.parametrize("mutate", UNUSABLE_RECORDS.values(), ids=UNUSABLE_RECORDS.keys())
def test_every_unusable_record_fails_the_fetch_closed_without_any_fetch_or_cli_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: Scenario, mutate: Mutation, kind: str
) -> None:
    mutate(record_path())
    scenario.set_green()
    spawns = SpawnRecorder(monkeypatch)
    record = self_update.apply(scenario.repo, inputs_for(tmp_path), **RUN_KINDS[kind](scenario))
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert record.target_sha is None
    assert spawns.fetch_class() == []
    assert spawns.gh() == []
    assert scenario.fetch_calls == []
    assert scenario.router.calls == []


@pytest.mark.parametrize("kind", RUN_KINDS.keys())
def test_a_lone_surrogate_stored_value_fails_the_fetch_closed_without_a_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: Scenario, kind: str
) -> None:
    write_json({"value": LONE_SURROGATE})(record_path())
    spawns = SpawnRecorder(monkeypatch)
    record = self_update.apply(scenario.repo, inputs_for(tmp_path), **RUN_KINDS[kind](scenario))
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert spawns.fetch_class() == []
    assert spawns.gh() == []
    assert scenario.fetch_calls == []


@pytest.mark.parametrize(
    "failure", LEAKY_FETCH_FAILURES.values(), ids=LEAKY_FETCH_FAILURES.keys()
)
def test_a_failed_fetch_is_recorded_without_the_credential_or_an_escaping_exception(
    scenario: Scenario,
    failure: Failure,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    scenario.router.fail_fetch(failure)
    record = self_update.check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    ((argv, _),) = scenario.fetch_calls
    assert not any(leaked(part) for part in argv)
    assert not leaked(self_update.status_path().read_text(encoding="utf-8"))
    assert not leaked(repr(record))
    captured = capfd.readouterr()
    assert not leaked(captured.out + captured.err)
    assert not leaked(caplog.text)
    assert caplog.records == []
    assert scenario.router.calls == []


def test_an_unrepresentable_stored_value_is_recorded_without_the_credential_or_an_exception(
    scenario: Scenario,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    write_json({"value": LONE_SURROGATE})(record_path())
    record = self_update.check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert caplog.records == []
    assert "\ud800" not in self_update.status_path().read_text(encoding="utf-8")


def test_a_successful_apply_leaves_the_credential_in_no_command_line_record_journal_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    applying: ApplyScenario = build_apply_scenario(monkeypatch, tmp_path)
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    record = _run.perform(
        applying.repo,
        scheduled=False,
        plist_path=applying.plist_path,
        health_url=HEALTH_URL,
        idle_probe=lambda: True,
        wait_bound_s=0.0,
        detached=True,
    )
    assert record is not None
    assert record.verdict is Verdict.APPLIED
    assert applying.base.fetch_calls
    assert _journal.read() is not None
    captured = capfd.readouterr()
    texts = [
        self_update.status_path().read_text(encoding="utf-8"),
        _journal.journal_path().read_text(encoding="utf-8"),
        captured.out,
        captured.err,
        caplog.text,
        *(part for argv in applying.commands.argvs() for part in argv),
        *(part for argv, _ in applying.base.fetch_calls for part in argv),
    ]
    assert not any(leaked(text) for text in texts)
