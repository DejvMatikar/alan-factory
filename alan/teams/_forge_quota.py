from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._pr_json import _nav_json_path  # pyright: ignore[reportPrivateUsage]

Resource = Literal["core", "graphql"]
QuotaOutcome = Literal["ok", "rate_limited", "transient", "error"]

PR_FEEDBACK_CALL_SITE = "pr_feedback_graphql"

_QUOTA_LOG_NAME = "gh_quota.jsonl"
_QUOTA_LOG_MAX_BYTES = 32 * 1024 * 1024
_LOW_HEADROOM_RATIO = 0.10

_RESET_AT_ISO_RE = re.compile(
    r'"resetAt"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"'
)
_RESET_EPOCH_RE = re.compile(r'"reset(?:At)?"\s*:\s*(\d{10,13})')


@dataclass(frozen=True)
class QuotaSample:
    resource: Resource
    call_site: str
    limit: int | None
    cost: int | None
    remaining: int | None
    reset_at: float | None
    outcome: QuotaOutcome


_LAST: dict[str, QuotaSample] = {}
# Guards _LAST and the rotate-then-append critical section below: the dispatcher
# runs concurrent merge polls as ThreadPoolExecutor threads inside a single
# process, so unguarded module state or an unsynchronized stat-then-rename here
# would race.
_LAST_LOCK = threading.Lock()


def _rotate_if_oversized(path: Path) -> None:
    try:
        oversized = path.stat().st_size > _QUOTA_LOG_MAX_BYTES
    except OSError:
        return
    if not oversized:
        return
    backup = path.with_name(path.name + ".1")
    try:
        path.replace(backup)
    except OSError:
        return


def record_quota_sample(sample: QuotaSample) -> None:
    path = logs_root() / _QUOTA_LOG_NAME
    with _LAST_LOCK:
        _rotate_if_oversized(path)
        append_jsonl_audit(path, asdict(sample), log_label="gh_quota")
        _LAST[sample.call_site] = sample


def last_sample(call_site: str) -> QuotaSample | None:
    with _LAST_LOCK:
        return _LAST.get(call_site)


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _iso_to_epoch(value: str) -> float | None:
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC).timestamp()


def _parse_iso_reset_at(text: str) -> float | None:
    match = _RESET_AT_ISO_RE.search(text)
    return _iso_to_epoch(match.group(1)) if match is not None else None


def _parse_epoch_reset_at(text: str) -> float | None:
    match = _RESET_EPOCH_RE.search(text)
    if match is None:
        return None
    raw = match.group(1)
    value = int(raw)
    return float(value // 1000) if len(raw) >= 13 else float(value)


def reset_at_from_text(text: str | None) -> float | None:
    if not text:
        return None
    iso = _parse_iso_reset_at(text)
    return iso if iso is not None else _parse_epoch_reset_at(text)


def low_headroom(remaining: int | None, limit: int | None) -> bool:
    if remaining is None or limit is None or limit <= 0:
        return False
    return (remaining / limit) < _LOW_HEADROOM_RATIO


def parse_graphql_rate_limit(
    stdout: str, *, call_site: str, outcome: QuotaOutcome = "ok",
) -> QuotaSample:
    rate_limit = _nav_json_path(stdout, "data", "rateLimit")
    if rate_limit is None:
        return QuotaSample(
            resource="graphql", call_site=call_site,
            limit=None, cost=None, remaining=None, reset_at=None, outcome=outcome,
        )
    reset_raw = rate_limit.get("resetAt")
    reset_at = _iso_to_epoch(reset_raw) if isinstance(reset_raw, str) else None
    return QuotaSample(
        resource="graphql",
        call_site=call_site,
        limit=_opt_int(rate_limit.get("limit")),
        cost=_opt_int(rate_limit.get("cost")),
        remaining=_opt_int(rate_limit.get("remaining")),
        reset_at=reset_at,
        outcome=outcome,
    )
