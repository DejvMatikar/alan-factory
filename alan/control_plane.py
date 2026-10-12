from __future__ import annotations

import os
from pathlib import Path

from alan import control_token_store

CONTROL_TOKEN_ENV = "ALAN_CONTROL_TOKEN"  # noqa: S105  # nosec B105
CONTROL_TOKEN_HEADER = "X-Alan-Control-Token"  # noqa: S105  # nosec B105


def expected_control_token() -> str:
    return os.environ.get(CONTROL_TOKEN_ENV, "")


def bootstrap_control_token(state_root: Path) -> str | None:
    value, _meta = control_token_store.load_or_generate(state_root)
    if value is not None:
        os.environ[CONTROL_TOKEN_ENV] = value
    else:
        os.environ.pop(CONTROL_TOKEN_ENV, None)
    return value


def clear_control_token() -> None:
    os.environ.pop(CONTROL_TOKEN_ENV, None)
    control_token_store.reset_cached_meta()
