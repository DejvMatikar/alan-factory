from __future__ import annotations

from typing import cast

from alan.teams._forge_errors import (
    ForgeFailureClass,
    ForgeRateLimited,
    classify_forge_failure,
    forge_rate_limit_reset_at,
)
from alan.teams._pr_json import (
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _safe_list,  # pyright: ignore[reportPrivateUsage]
)


def classify_feedback_failure(
    *, returncode: int | None, stderr: str | None,
) -> ForgeFailureClass | None:
    if returncode == 0:
        return None
    return classify_forge_failure(returncode=returncode, stderr=stderr)


def _error_messages(entries: list[object]) -> list[str]:
    messages: list[str] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        message = cast(_JsonObject, raw).get("message")
        if isinstance(message, str):
            messages.append(message)
    return messages


def _has_rate_limited_type(entries: list[object]) -> bool:
    for raw in entries:
        if isinstance(raw, dict) and cast(_JsonObject, raw).get("type") == "RATE_LIMITED":
            return True
    return False


def raise_for_graphql_errors(errors_payload: object, *, context: str) -> None:
    entries = _safe_list(errors_payload)
    if not entries:
        return
    joined = " ".join(_error_messages(entries))
    rate_limited = _has_rate_limited_type(entries) or (
        classify_forge_failure(returncode=1, stderr=joined) == "rate_limited"
    )
    if not rate_limited:
        return
    detail = f"{context}: graphql errors: {joined[:200]}"
    raise ForgeRateLimited(detail, forge_rate_limit_reset_at(joined))
