from __future__ import annotations

import re

_MIN_DELIMITER_LEN = 3
_BACKTICK_RUN_RE = re.compile(r"`+")


def fence_delimiter(body: str) -> str:
    """Backtick delimiter guaranteed longer than every backtick run in
    `body`, so no substring of `body` can close the fence early."""
    longest_run = max(
        (len(m.group(0)) for m in _BACKTICK_RUN_RE.finditer(body)), default=0
    )
    return "`" * max(longest_run + 1, _MIN_DELIMITER_LEN)


def fence(body: str, *, info: str = "") -> str:
    """Wrap `body` in a break-proof backtick fence. `body` is never mutated."""
    delimiter = fence_delimiter(body)
    header = f"{delimiter}{info}" if info else delimiter
    return f"{header}\n{body}\n{delimiter}"


__all__ = ["fence", "fence_delimiter"]
