from __future__ import annotations

import datetime as dt_mod
import json
import math
from collections.abc import Callable
from typing import Any

import pytest

from alan.claude_cli import (
    ClaudeCliError,
    SpendCapReached,
    UsageLimitError,
    run_agent,
    run_agent_streaming,
    run_chat_turn,
)
from alan.usage_limit import (
    SPEND_CAP_HOLD_EPOCH,
    is_spend_cap_message,
    is_usage_limit_message,
    parse_regain_at,
)
from tests.test_claude_cli_streaming import _lines, _patch_popen

_REGAIN = "2026-10-01T00:00:00+00:00"
_TIER = "You have reached your API usage limits. You will regain access on 2026-10-01 at 00:00 UTC."
_ORG = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-10-01 at 00:00 UTC."
)
_WORKSPACE = (
    "You have reached your specified workspace API usage limits. "
    "You will regain access on 2026-10-01 at 00:00 UTC."
)


def _api_error(status: int, message: str, code: str | None = None) -> str:
    error: dict[str, str] = {"type": "invalid_request_error", "message": message}
    if code is not None:
        error["code"] = code
    return f"API Error: {status} " + json.dumps({"type": "error", "error": error})


POSITIVE_CORPUS = [
    pytest.param(_TIER, _REGAIN, id="tier-message-alone"),
    pytest.param(_ORG, _REGAIN, id="organisation-message-alone"),
    pytest.param(_WORKSPACE, _REGAIN, id="workspace-message-alone"),
    pytest.param(_api_error(429, _TIER), _REGAIN, id="tier-429-without-error-code"),
    pytest.param(
        _api_error(429, _TIER, "enforced_spend_limit_reached"),
        _REGAIN,
        id="tier-429-with-error-code",
    ),
    pytest.param(_api_error(400, _ORG), _REGAIN, id="organisation-400-wrapped"),
    pytest.param(_api_error(400, _WORKSPACE), _REGAIN, id="workspace-400-wrapped"),
    pytest.param(
        _api_error(400, "Spend limit hit", "enforced_spend_limit_reached"),
        None,
        id="error-code-only",
    ),
    pytest.param(
        "You have reached your specified API usage limits.", None, id="no-regain-date"
    ),
    pytest.param(_TIER.upper(), _REGAIN, id="upper-case-message"),
]


@pytest.fixture
def error_sites(
    monkeypatch: pytest.MonkeyPatch,
    patch_subprocess: Callable[..., None],
    claude_json: Callable[..., str],
) -> list[Callable[[str], object]]:
    def json_mode(text: str) -> object:
        patch_subprocess(stdout=claude_json(result=text, is_error=True))
        return run_agent("sys", "user")

    def stream(text: str, returncode: int) -> object:
        event: dict[str, Any] = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": text,
        }
        _patch_popen(monkeypatch, stdout_lines=_lines(event), returncode=returncode)
        return run_agent_streaming("sys", "user")

    return [
        json_mode,
        lambda text: stream(text, 0),
        lambda text: stream(text, 1),
    ]


@pytest.mark.parametrize(("text", "regain_at"), POSITIVE_CORPUS)
def test_positive_corpus_input_raises_spend_cap_at_every_failed_result_site(
    error_sites: list[Callable[[str], object]], text: str, regain_at: str | None
) -> None:
    for site in error_sites:
        with pytest.raises(SpendCapReached) as exc_info:
            site(text)
        assert exc_info.value.reset_at == SPEND_CAP_HOLD_EPOCH
        assert exc_info.value.regain_at == regain_at
        assert exc_info.value.raw_message == text


def test_spend_cap_reached_is_a_usage_limit_error() -> None:
    exc = SpendCapReached("capped", None)
    assert isinstance(exc, UsageLimitError)
    assert isinstance(exc, ClaudeCliError)


def test_spend_cap_hold_is_finite_valid_json_and_a_real_date_everywhere() -> None:
    hold = SpendCapReached("capped", None).reset_at
    assert math.isfinite(hold)
    assert json.loads(json.dumps(hold, allow_nan=False)) == hold
    assert dt_mod.datetime.fromtimestamp(hold, tz=dt_mod.UTC).year == 2100
    assert hold * 1000 < 8.64e15


def test_chat_turn_spend_cap_is_still_a_usage_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = {"type": "result", "subtype": "success", "is_error": True, "result": _ORG}
    _patch_popen(monkeypatch, stdout_lines=_lines(event))
    with pytest.raises(UsageLimitError) as exc_info:
        run_chat_turn(
            user_text="x",
            session_id="00000000-0000-0000-0000-000000000000",
            resume=False,
            system_prompt="sys",
        )
    assert isinstance(exc_info.value, SpendCapReached)


def test_spend_cap_wins_over_ordinary_usage_limit_wording_in_one_message(
    error_sites: list[Callable[[str], object]],
) -> None:
    text = f"{_ORG} You've hit your limit · resets 1pm (UTC)"
    assert is_usage_limit_message(text)
    with pytest.raises(SpendCapReached):
        error_sites[0](text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_TIER, id="tier"),
        pytest.param(_ORG, id="organisation"),
        pytest.param(_WORKSPACE, id="workspace"),
        pytest.param("YOU HAVE REACHED YOUR API USAGE LIMITS", id="upper-case"),
        pytest.param("code: ENFORCED_SPEND_LIMIT_REACHED", id="error-code-upper-case"),
    ],
)
def test_is_spend_cap_message_true(text: str) -> None:
    assert is_spend_cap_message(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(None, id="none"),
        pytest.param("", id="empty"),
        pytest.param("Please check your API usage limits", id="check-your-usage-limits"),
        pytest.param("You have reached your limit", id="reached-your-limit"),
        pytest.param("Character limit for this field is 500", id="character-limit"),
        pytest.param("You've hit your session limit · resets 3pm (UTC)", id="session-limit"),
        pytest.param("Rate limit exceeded, try again later", id="rate-limit"),
        pytest.param("You have reached your usage limits", id="missing-api"),
    ],
)
def test_is_spend_cap_message_false(text: str | None) -> None:
    assert not is_spend_cap_message(text)


def test_is_usage_limit_message_still_matches_spend_cap_wording() -> None:
    assert is_usage_limit_message(_TIER)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(_TIER, _REGAIN, id="date-and-time"),
        pytest.param("You will regain access on 2026-10-01.", _REGAIN, id="date-only"),
        pytest.param(
            "regain access on 2026-10-01 at 13:45 UTC",
            "2026-10-01T13:45:00+00:00",
            id="afternoon",
        ),
        pytest.param("no date here", None, id="no-date"),
        pytest.param("regain access on 2026-13-45 at 00:00 UTC", None, id="impossible-date"),
        pytest.param("regain access on 2026-10-01 at 25:99 UTC", None, id="impossible-time"),
    ],
)
def test_parse_regain_at(text: str, expected: str | None) -> None:
    assert parse_regain_at(text) == expected
