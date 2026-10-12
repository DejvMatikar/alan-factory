from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from alan.roots import SLOT_POOL_ROOT_ENV

logger = logging.getLogger("alan.detached_mode")

DIAL_ENV = "ALAN_DETACHED_WORKERS"

_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes"})
_OFF_VALUES: frozenset[str] = frozenset({"off", "0", "false", "no"})
_AUTO_VALUES: frozenset[str] = frozenset({"auto", ""})


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _pool_configured(env: Mapping[str, str]) -> bool:
    # Equivalent to `alan.roots.slot_pool_root() is not None`, expressed against
    # the caller's env mapping so the auto/on/off matrix is testable without
    # mutating process-global os.environ.
    return bool((env.get(SLOT_POOL_ROOT_ENV) or "").strip())


def _raw_dial(env: Mapping[str, str]) -> str:
    return (env.get(DIAL_ENV) or "").strip().lower()


def resolve_detached_mode(env: Mapping[str, str] | None = None) -> bool:
    e = _env(env)
    raw = _raw_dial(e)
    if raw in _ON_VALUES:
        return True
    if raw in _OFF_VALUES:
        return False
    if raw not in _AUTO_VALUES:
        logger.warning(
            "%s=%r not recognized (want auto/on/off); falling back to auto", DIAL_ENV, raw
        )
    return _pool_configured(e)


def _reason(env: Mapping[str, str]) -> str:
    raw = _raw_dial(env)
    if raw in _ON_VALUES or raw in _OFF_VALUES:
        return f"explicit {DIAL_ENV}={raw!r}"
    pool_state = "configured" if _pool_configured(env) else "empty"
    return f"auto: working-area pool {pool_state}"


def log_resolved_mode(env: Mapping[str, str] | None = None) -> bool:
    e = _env(env)
    mode = resolve_detached_mode(e)
    logger.info(
        "detached worker mode resolved: %s (%s)", "on" if mode else "off", _reason(e)
    )
    return mode


__all__ = ["DIAL_ENV", "log_resolved_mode", "resolve_detached_mode"]
