from __future__ import annotations

from alan import default_profile
from alan.teams._env import float_env

_BUDGET_ENV = "ALAN_DEPLOY_VERIFY_BUDGET_S"
_DEFAULT_BUDGET_S = 5400.0
IDLE_WAIT_MARGIN_S = 900.0
STALE_ALLOWANCE_S = 7200.0


def effective_budget_s() -> float:
    configured = default_profile.resolved().profile.never_live_budget_s
    return configured if configured is not None else float_env(_BUDGET_ENV, _DEFAULT_BUDGET_S)


def idle_wait_bound_s() -> float:
    return effective_budget_s() - IDLE_WAIT_MARGIN_S


def stale_threshold_s() -> float:
    return effective_budget_s() + STALE_ALLOWANCE_S
