from __future__ import annotations

import datetime as dt_mod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alan import usage_limit
from alan import workspace as workspace_mod
from alan.claude_cli import ClaudeCliError, UsageLimitError, run_agent, run_agent_streaming
from alan.dispatcher import Dispatcher
from alan.queue import JobStatus, Queue
from alan.teams import _forge_errors
from alan.usage_limit import FALLBACK_PAUSE_SECONDS, SAFETY_BUFFER_SECONDS
from tests.test_claude_cli_streaming import _lines, _patch_popen

NOW = dt_mod.datetime(2026, 6, 1, 10, 0, tzinfo=dt_mod.UTC).timestamp()

_SPEND_PHRASE = "You have reached your specified API usage limits."
_API_429 = (
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":'
    '"This request would exceed the rate limit for your organization '
    'of 30,000 input tokens per minute."}}'
)
_CREDIT_BALANCE = (
    "Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits."
)
_GITHUB_RATE_LIMIT_STDERR = "GraphQL: API rate limit already exceeded for user ID 24228929.\n"

Outcome = tuple[type[BaseException] | None, float | None]


class _FrozenDatetime:
    @staticmethod
    def now(tz: dt_mod.tzinfo | None = None) -> dt_mod.datetime:
        instant = dt_mod.datetime.fromtimestamp(NOW, tz=dt_mod.UTC)
        return instant.astimezone(tz)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS", raising=False)
    frozen_time = SimpleNamespace(time=lambda: NOW)
    monkeypatch.setattr(usage_limit, "time", frozen_time)
    monkeypatch.setattr(usage_limit, "datetime", _FrozenDatetime)
    monkeypatch.setattr(_forge_errors, "time", frozen_time)


@dataclass
class _Sites:
    monkeypatch: pytest.MonkeyPatch
    patch_subprocess: Callable[..., None]
    claude_json: Callable[..., str]

    def json_mode(self, text: str, *, is_error: bool) -> str:
        self.patch_subprocess(
            stdout=self.claude_json(result=text, is_error=True if is_error else None)
        )
        return run_agent("sys", "user")

    def stream(self, events: list[dict[str, Any]], *, returncode: int) -> str:
        _patch_popen(self.monkeypatch, stdout_lines=_lines(*events), returncode=returncode)
        return run_agent_streaming("sys", "user").result_text

    def stream_text(self, text: str, *, is_error: bool, returncode: int) -> str:
        event: dict[str, Any] = {"type": "result", "subtype": "success", "result": text}
        if is_error:
            event["is_error"] = True
        return self.stream([event], returncode=returncode)


Scenario = Callable[[_Sites], list[Callable[[], object]]]


def _error_text(text: str) -> Scenario:
    def build(sites: _Sites) -> list[Callable[[], object]]:
        return [
            partial(sites.json_mode, text, is_error=True),
            partial(sites.stream_text, text, is_error=True, returncode=0),
            partial(sites.stream_text, text, is_error=True, returncode=1),
        ]

    return build


def _success_text(text: str) -> Scenario:
    def build(sites: _Sites) -> list[Callable[[], object]]:
        return [
            partial(sites.json_mode, text, is_error=False),
            partial(sites.stream_text, text, is_error=False, returncode=0),
        ]

    return build


def _rejected_event_then_text(text: str) -> Scenario:
    events: list[dict[str, Any]] = [
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": NOW + 5000.0},
        },
        {"type": "result", "subtype": "success", "is_error": True, "result": text},
    ]

    def build(sites: _Sites) -> list[Callable[[], object]]:
        return [
            partial(sites.stream, events, returncode=0),
            partial(sites.stream, events, returncode=1),
        ]

    return build


def _github_rate_limit() -> Scenario:
    def build(_sites: _Sites) -> list[Callable[[], object]]:
        return [
            partial(
                _forge_errors.raise_for_infra,
                "gh pr view",
                returncode=1,
                stderr=_GITHUB_RATE_LIMIT_STDERR,
            )
        ]

    return build


def _outcome(call: Callable[[], object]) -> Outcome:
    try:
        call()
    except UsageLimitError as exc:
        return type(exc), exc.reset_at - NOW
    except Exception as exc:  # noqa: BLE001
        return type(exc), None
    return None, None


_ordinary = (UsageLimitError, FALLBACK_PAUSE_SECONDS)

NEGATIVE_CORPUS = [
    pytest.param(
        _error_text("You've hit your limit · resets 1pm (Europe/London)"),
        (UsageLimitError, 7200.0 + SAFETY_BUFFER_SECONDS),
        id="hit-your-limit-with-london-reset",
    ),
    pytest.param(
        _error_text("You've hit your session limit · resets 3:50pm (Europe/London)"),
        (UsageLimitError, 17400.0 + SAFETY_BUFFER_SECONDS),
        id="session-limit-with-london-reset",
    ),
    pytest.param(
        _error_text("Usage limit exceeded for this API key"),
        _ordinary,
        id="usage-limit-exceeded-for-api-key",
    ),
    pytest.param(
        _error_text("Rate limit exceeded, try again later"),
        _ordinary,
        id="rate-limit-exceeded-try-later",
    ),
    pytest.param(
        _error_text("Rate limit exceeded, resets in 45 minutes"),
        (UsageLimitError, 2700.0 + SAFETY_BUFFER_SECONDS),
        id="rate-limit-exceeded-resets-in-45-minutes",
    ),
    pytest.param(
        _error_text("Claude AI usage limit reached|1780000000"),
        _ordinary,
        id="claude-ai-usage-limit-reached-epoch",
    ),
    pytest.param(
        _rejected_event_then_text(_SPEND_PHRASE),
        (UsageLimitError, 5000.0 + SAFETY_BUFFER_SECONDS),
        id="rejected-rate-limit-event-then-spend-cap-text",
    ),
    pytest.param(
        _error_text(_API_429),
        (ClaudeCliError, None),
        id="ordinary-api-429-would-exceed-the-rate-limit",
    ),
    pytest.param(
        _github_rate_limit(),
        (_forge_errors.ForgeRateLimited, _forge_errors.FORGE_RATE_LIMIT_PAUSE_S),
        id="github-rate-limit-pauses-900-seconds",
    ),
    pytest.param(
        _error_text("Please check your API usage limits"),
        _ordinary,
        id="near-miss-check-your-api-usage-limits",
    ),
    pytest.param(
        _error_text("You have reached your limit"),
        (ClaudeCliError, None),
        id="near-miss-you-have-reached-your-limit",
    ),
    pytest.param(
        _error_text("Character limit for this field is 500"),
        (ClaudeCliError, None),
        id="near-miss-character-limit",
    ),
    pytest.param(
        _success_text(_SPEND_PHRASE),
        (None, None),
        id="successful-result-quoting-the-spend-phrase",
    ),
    pytest.param(
        _error_text(_CREDIT_BALANCE),
        (ClaudeCliError, None),
        id="credit-balance-too-low",
    ),
]


@pytest.fixture
def sites(
    monkeypatch: pytest.MonkeyPatch,
    patch_subprocess: Callable[..., None],
    claude_json: Callable[..., str],
) -> _Sites:
    return _Sites(monkeypatch, patch_subprocess, claude_json)


@pytest.mark.parametrize(("scenario", "expected"), NEGATIVE_CORPUS)
def test_negative_corpus_input_is_classified_exactly_as_before(
    sites: _Sites, scenario: Scenario, expected: Outcome
) -> None:
    calls = scenario(sites)
    assert [_outcome(call) for call in calls] == [expected] * len(calls)


class _RaisingTeam:
    name = "research"
    supports_resume = False

    def __init__(self, text: str) -> None:
        self._text = text

    def plan_workflow(self, *_args: object) -> list[object]:
        return []

    def run(self, *_args: object, **_kwargs: object) -> object:
        raise ClaudeCliError(self._text)


@pytest.fixture
def owner_queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        q.close()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_API_429, id="ordinary-api-429"),
        pytest.param(_CREDIT_BALANCE, id="credit-balance-too-low"),
    ],
)
def test_ordinary_failures_still_fail_the_job_without_pausing(
    owner_queue: Queue, ws_root: Path, text: str
) -> None:
    ws = workspace_mod.create("t1", "d", root=ws_root)
    owner_queue.enqueue(ws.task_id, "research")
    dispatcher = Dispatcher(owner_queue, {"research": _RaisingTeam(text)}, workspaces_root=ws_root)  # type: ignore[dict-item]
    dispatcher.run_once()
    (job,) = owner_queue.list_jobs()
    assert job.status is JobStatus.FAILED
    assert owner_queue.paused_until() is None
