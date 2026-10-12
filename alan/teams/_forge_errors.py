from __future__ import annotations

import time
from typing import Literal

from alan.claude_cli import ServerErrorRetryable, UsageLimitError
from alan.teams import _forge_quota

ForgeFailureClass = Literal["domain_absent", "rate_limited", "transient", "auth", "unknown"]

RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "secondary rate limit",
    "api rate limit",
    "rate limit exceeded",
    "abuse detection mechanism",
    "429",
)

DOMAIN_ABSENT_MARKERS: tuple[str, ...] = (
    "could not resolve to a pullrequest",
    "could not resolve to an issue",
    "no pull requests found",
    "not found (http 404)",
)

AUTH_MARKERS: tuple[str, ...] = (
    "bad credentials",
    "401",
    "403",
    "unauthorized",
    "requires authentication",
    "authentication failed",
    "invalid token",
    "token expired",
    "revoked",
)

NETWORK_TRANSIENT_MARKERS: tuple[str, ...] = (
    "dial tcp",
    "connection refused",
    "connection reset",
    "i/o timeout",
    "tls handshake timeout",
    "no such host",
    "could not resolve host",
    "network is unreachable",
    "http 502",
    "http 503",
    "http 504",
    "unexpected eof",
)

FORGE_RATE_LIMIT_PAUSE_S = 900.0
_MAX_FORGE_PAUSE_S = 3600.0


class ForgeRateLimited(UsageLimitError):
    pass


class ForgeTransientError(ServerErrorRetryable):
    pass


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_forge_failure(
    *, returncode: int | None, stderr: str | None
) -> ForgeFailureClass:
    if returncode is None:
        return "transient"
    text = (stderr or "").lower()
    if _has_marker(text, RATE_LIMIT_MARKERS):
        return "rate_limited"
    if _has_marker(text, DOMAIN_ABSENT_MARKERS):
        return "domain_absent"
    if _has_marker(text, AUTH_MARKERS):
        return "auth"
    if _has_marker(text, NETWORK_TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


def forge_rate_limit_reset_at(payload: str | None = None) -> float:
    now = time.time()
    parsed = _forge_quota.reset_at_from_text(payload) if payload is not None else None
    if parsed is None:
        return now + FORGE_RATE_LIMIT_PAUSE_S
    return max(now, min(parsed, now + _MAX_FORGE_PAUSE_S))


def infra_failure_detail(
    context: str, *, returncode: int | None, stderr: str | None
) -> str:
    return f"{context} failed (rc={returncode}): {(stderr or '').strip()[:200]}"


def raise_for_infra(
    context: str, *, returncode: int | None, stderr: str | None
) -> ForgeFailureClass:
    failure_class = classify_forge_failure(returncode=returncode, stderr=stderr)
    detail = infra_failure_detail(context, returncode=returncode, stderr=stderr)
    if failure_class == "rate_limited":
        raise ForgeRateLimited(detail, forge_rate_limit_reset_at(stderr))
    if failure_class == "transient":
        raise ForgeTransientError(detail)
    return failure_class
