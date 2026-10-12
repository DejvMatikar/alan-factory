from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

FALLBACK_PAUSE_SECONDS = 3600
SAFETY_BUFFER_SECONDS = 120
SPEND_CAP_HOLD_EPOCH = 4102444800.0

_LIMIT_PHRASES = [
    "hit your limit",
    "you've hit your limit",
    "usage limit",
    "session limit",
    "rate limit exceeded",
]

_RE_TIME_TZ = re.compile(
    r"resets\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_RE_RELATIVE = re.compile(
    r"resets\s+in\s+(\d+)\s*(minutes?|hours?)",
    re.IGNORECASE,
)
_RE_TIME_LOCAL = re.compile(
    r"resets\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.IGNORECASE,
)
_RE_SPEND_CAP = re.compile(
    r"you have reached your (?:specified )?(?:workspace )?api usage limits"
    r"|enforced_spend_limit_reached",
    re.IGNORECASE,
)
_RE_REGAIN = re.compile(
    r"regain access on (\d{4}-\d{2}-\d{2})(?: at (\d{2}:\d{2}))?",
    re.IGNORECASE,
)


def _safety_buffer() -> int:
    val = os.environ.get("ALAN_USAGE_LIMIT_SAFETY_BUFFER_SECONDS", "")
    try:
        n = int(val)
        return n if n > 0 else SAFETY_BUFFER_SECONDS
    except ValueError:
        return SAFETY_BUFFER_SECONDS


def is_usage_limit_message(text: str | None) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(phrase in lower for phrase in _LIMIT_PHRASES)


def is_spend_cap_message(text: str | None) -> bool:
    if not text:
        return False
    return _RE_SPEND_CAP.search(text) is not None


def parse_regain_at(text: str) -> str | None:
    m = _RE_REGAIN.search(text)
    if m is None:
        return None
    try:
        regain = datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00:00'}")
    except ValueError:
        return None
    return regain.replace(tzinfo=UTC).isoformat()


def _adjust_hour(hour: int, *, is_pm: bool, is_am: bool) -> int:
    if is_pm and hour != 12:
        return hour + 12
    if is_am and hour == 12:
        return 0
    return hour


def _parse_time_str(time_str: str) -> tuple[int, int]:
    s = time_str.strip().lower()
    is_pm = s.endswith("pm")
    is_am = s.endswith("am")
    s = s[:-2].strip()
    if ":" in s:
        parts = s.split(":")
        hour, minute = int(parts[0]), int(parts[1])
    else:
        hour, minute = int(s), 0
    return _adjust_hour(hour, is_pm=is_pm, is_am=is_am), minute


def _parse_tz_reset_time(text: str) -> float | None:
    m = _RE_TIME_TZ.search(text)
    if m is None:
        return None
    try:
        hour, minute = _parse_time_str(m.group(1))
        tz = ZoneInfo(m.group(2).strip())
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp() + _safety_buffer()
    except Exception:  # noqa: BLE001
        logger.warning("failed to parse tz reset time in: %s", text[:100])
    return None


def _parse_relative_reset_time(text: str) -> float | None:
    m = _RE_RELATIVE.search(text)
    if m is None:
        return None
    amount = int(m.group(1))
    unit = m.group(2).lower()
    seconds = amount * 3600 if unit.startswith("hour") else amount * 60
    return time.time() + seconds + _safety_buffer()


def _parse_local_reset_time(text: str) -> float | None:
    m = _RE_TIME_LOCAL.search(text)
    if m is None:
        return None
    try:
        hour, minute = _parse_time_str(m.group(1))
        now = datetime.now().astimezone()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp() + _safety_buffer()
    except Exception:  # noqa: BLE001
        logger.warning("failed to parse local reset time in: %s", text[:100])
    return None


def parse_reset_time(text: str) -> float:
    result = _parse_tz_reset_time(text)
    if result is not None:
        return result
    result = _parse_relative_reset_time(text)
    if result is not None:
        return result
    result = _parse_local_reset_time(text)
    if result is not None:
        return result
    return time.time() + FALLBACK_PAUSE_SECONDS


def reset_epoch_from_rate_limit_event(event: dict[str, Any]) -> float | None:
    if event.get("type") != "rate_limit_event":
        return None
    info_raw = event.get("rate_limit_info")
    if not isinstance(info_raw, dict):
        return None
    info = cast(dict[str, Any], info_raw)
    if info.get("status") != "rejected":
        return None
    resets_at = info.get("resetsAt")
    if isinstance(resets_at, bool) or not isinstance(resets_at, (int, float)):
        return None
    if resets_at <= 0:
        return None
    return float(resets_at) + _safety_buffer()
