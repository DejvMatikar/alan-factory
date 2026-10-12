from __future__ import annotations

import datetime as dt_mod
import time
from zoneinfo import ZoneInfo

import pytest

from alan.claude_cli import UsageLimitError
from alan.usage_limit import (
    FALLBACK_PAUSE_SECONDS,
    SAFETY_BUFFER_SECONDS,
    is_usage_limit_message,
    parse_reset_time,
    reset_epoch_from_rate_limit_event,
)


class TestIsUsageLimitMessage:
    def test_hit_your_limit(self):
        assert is_usage_limit_message("You've hit your limit · resets 1pm (Europe/London)")

    def test_usage_limit(self):
        assert is_usage_limit_message("Usage limit exceeded for this API key")

    def test_session_limit(self):
        assert is_usage_limit_message(
            "You've hit your session limit · resets 3:50pm (Europe/London)"
        )

    def test_rate_limit_exceeded(self):
        assert is_usage_limit_message("Rate limit exceeded, try again later")

    def test_case_insensitive(self):
        assert is_usage_limit_message("You've HIT YOUR LIMIT")

    def test_generic_error_negative(self):
        assert not is_usage_limit_message("Connection timeout after 30s")

    def test_generic_limit_word_negative(self):
        assert not is_usage_limit_message("Character limit for this field is 500")

    def test_empty_string(self):
        assert not is_usage_limit_message("")

    def test_none(self):
        assert not is_usage_limit_message(None)


class TestParseResetTime:
    def test_absolute_time_with_timezone(self):
        text = "You've hit your limit · resets 1pm (Europe/London)"
        result = parse_reset_time(text)
        assert result > time.time()
        assert result < time.time() + 86400 + SAFETY_BUFFER_SECONDS + 1

    def test_absolute_time_with_minutes_and_timezone(self):
        text = "resets 1:30pm (America/New_York)"
        result = parse_reset_time(text)
        assert result > time.time()

    def test_relative_minutes(self):
        text = "Rate limit exceeded, resets in 45 minutes"
        before = time.time()
        result = parse_reset_time(text)
        expected_min = before + 45 * 60 + SAFETY_BUFFER_SECONDS
        assert abs(result - expected_min) < 2

    def test_relative_hours(self):
        text = "resets in 2 hours"
        before = time.time()
        result = parse_reset_time(text)
        expected_min = before + 2 * 3600 + SAFETY_BUFFER_SECONDS
        assert abs(result - expected_min) < 2

    def test_absolute_time_local(self):
        text = "resets 3pm"
        result = parse_reset_time(text)
        assert result > time.time()

    def test_fallback_on_unparseable(self):
        text = "Something went wrong, try again later"
        before = time.time()
        result = parse_reset_time(text)
        expected = before + FALLBACK_PAUSE_SECONDS
        assert abs(result - expected) < 2

    def test_am_time(self):
        text = "resets 6am (UTC)"
        result = parse_reset_time(text)
        assert result > time.time()

    def test_12pm_noon(self):
        text = "resets 12pm (Europe/London)"
        result = parse_reset_time(text)
        assert result > time.time()

    def test_12am_midnight(self):
        text = "resets 12am (Europe/London)"
        result = parse_reset_time(text)
        assert result > time.time()

    def test_tz_reset_target_in_future_no_wrap(self, monkeypatch):
        UTC = ZoneInfo("UTC")
        _fixed = dt_mod.datetime(2024, 6, 1, 10, 0, 0, tzinfo=UTC)
        expected_target = dt_mod.datetime(2024, 6, 1, 13, 0, 0, tzinfo=UTC)

        class _FakeDT:
            @staticmethod
            def now(tz: object = None) -> dt_mod.datetime:
                return _fixed

        monkeypatch.setattr("alan.usage_limit.datetime", _FakeDT)
        result = parse_reset_time("resets 1pm (UTC)")
        expected = expected_target.timestamp() + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 1.0

    def test_tz_reset_target_in_past_wraps_next_day(self, monkeypatch):
        UTC = ZoneInfo("UTC")
        _fixed = dt_mod.datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
        expected_target = dt_mod.datetime(2024, 6, 2, 13, 0, 0, tzinfo=UTC)

        class _FakeDT:
            @staticmethod
            def now(tz: object = None) -> dt_mod.datetime:
                return _fixed

        monkeypatch.setattr("alan.usage_limit.datetime", _FakeDT)
        result = parse_reset_time("resets 1pm (UTC)")
        expected = expected_target.timestamp() + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 1.0

    def test_local_reset_target_in_future_no_wrap(self, monkeypatch):
        _fixed_naive = dt_mod.datetime(2024, 6, 1, 10, 0, 0)

        class _FakeDT:
            @staticmethod
            def now(tz: object = None) -> dt_mod.datetime:
                return _fixed_naive

        monkeypatch.setattr("alan.usage_limit.datetime", _FakeDT)
        result = parse_reset_time("resets 11pm")
        now_aware = _fixed_naive.astimezone()
        expected_target = now_aware.replace(hour=23, minute=0, second=0, microsecond=0)
        expected = expected_target.timestamp() + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 1.0

    def test_local_reset_target_in_past_wraps_next_day(self, monkeypatch):
        _fixed_naive = dt_mod.datetime(2024, 6, 1, 16, 0, 0)

        class _FakeDT:
            @staticmethod
            def now(tz: object = None) -> dt_mod.datetime:
                return _fixed_naive

        monkeypatch.setattr("alan.usage_limit.datetime", _FakeDT)
        result = parse_reset_time("resets 3pm")
        now_aware = _fixed_naive.astimezone()
        target = now_aware.replace(hour=15, minute=0, second=0, microsecond=0)
        expected_target = target + dt_mod.timedelta(days=1)
        expected = expected_target.timestamp() + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 1.0

    def test_tz_bad_timezone_falls_through_to_local(self):
        result = parse_reset_time("resets 1pm (NoSuchTimezone/Fake)")
        assert result > time.time()

    def test_local_bad_hour_falls_back_to_absolute(self):
        text = "resets 13pm"
        before = time.time()
        result = parse_reset_time(text)
        expected = before + FALLBACK_PAUSE_SECONDS
        assert abs(result - expected) < 2

    def test_session_limit_text_parses_tz_reset(self, monkeypatch):
        UTC = ZoneInfo("UTC")
        _fixed = dt_mod.datetime(2024, 6, 1, 10, 0, 0, tzinfo=UTC)
        expected_target = dt_mod.datetime(2024, 6, 1, 15, 50, 0, tzinfo=UTC)

        class _FakeDT:
            @staticmethod
            def now(tz: object = None) -> dt_mod.datetime:
                return _fixed

        monkeypatch.setattr("alan.usage_limit.datetime", _FakeDT)
        text = "You've hit your session limit · resets 3:50pm (UTC)"
        result = parse_reset_time(text)
        expected = expected_target.timestamp() + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 1.0


class TestResetEpochFromRateLimitEvent:
    def test_rejected_event_returns_epoch_with_buffer(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1779976200,
                "rateLimitType": "five_hour",
            },
        }
        result = reset_epoch_from_rate_limit_event(event)
        assert result == 1779976200.0 + SAFETY_BUFFER_SECONDS

    def test_allowed_warning_returns_none(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "resetsAt": 1779976200,
                "rateLimitType": "five_hour",
            },
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_wrong_event_type_returns_none(self):
        event = {
            "type": "assistant",
            "rate_limit_info": {"status": "rejected", "resetsAt": 1779976200},
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_missing_rate_limit_info_returns_none(self):
        assert reset_epoch_from_rate_limit_event({"type": "rate_limit_event"}) is None

    def test_non_dict_rate_limit_info_returns_none(self):
        event = {"type": "rate_limit_event", "rate_limit_info": "rejected"}
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_missing_resets_at_returns_none(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected"},
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_non_numeric_resets_at_returns_none(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": "2pm"},
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_bool_resets_at_returns_none(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": True},
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_zero_resets_at_returns_none(self):
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": 0},
        }
        assert reset_epoch_from_rate_limit_event(event) is None

    def test_safety_buffer_env_var_applied(self, monkeypatch):
        monkeypatch.setenv("ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS", "5")
        event = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": 1000},
        }
        assert reset_epoch_from_rate_limit_event(event) == 1005.0


class TestSafetyBuffer:
    def test_env_var_positive_int_overrides_default(self, monkeypatch):
        monkeypatch.setenv("ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS", "1")
        text = "Rate limit exceeded, resets in 1 minutes"
        before = time.time()
        result = parse_reset_time(text)
        expected = before + 60 + 1
        assert abs(result - expected) < 2

    def test_env_var_non_positive_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS", "0")
        text = "Rate limit exceeded, resets in 1 minutes"
        before = time.time()
        result = parse_reset_time(text)
        expected = before + 60 + SAFETY_BUFFER_SECONDS
        assert abs(result - expected) < 2


class TestUsageLimitError:
    def test_attributes(self):
        err = UsageLimitError("hit your limit", 1234567890.0)
        assert err.raw_message == "hit your limit"
        assert err.reset_at == 1234567890.0
        assert str(err) == "hit your limit"

    def test_is_exception(self):
        err = UsageLimitError("msg", 0.0)
        assert isinstance(err, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(UsageLimitError) as exc_info:
            raise UsageLimitError("test", 999.0)
        assert exc_info.value.reset_at == 999.0

    def test_is_subclass_of_claude_cli_error(self):
        from alan.claude_cli import ClaudeCliError

        assert issubclass(UsageLimitError, ClaudeCliError)
