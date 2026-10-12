from __future__ import annotations

from dataclasses import dataclass

from alan.self_update import _ops

DURATION_S = 600.0
POLL_S = 15.0
SUSTAINED_FAILURE_S = 120.0
_CRASH_RUNS_DELTA = 2
_CRASH_NOT_RUNNING_POLLS = 2


def _crashed(baseline_runs: int | None, runs: int | None, not_running_streak: int) -> bool:
    if not_running_streak >= _CRASH_NOT_RUNNING_POLLS:
        return True
    if baseline_runs is None or runs is None:
        return False
    return runs - baseline_runs >= _CRASH_RUNS_DELTA


@dataclass
class _SoakState:
    baseline_runs: int | None
    not_running_streak: int = 0
    unhealthy_elapsed: float = 0.0


def _poll_once(state: _SoakState, target_sha: str, health_url: str) -> bool:
    loaded, runs = _ops.service_status()
    state.not_running_streak = 0 if loaded else state.not_running_streak + 1
    if _crashed(state.baseline_runs, runs, state.not_running_streak):
        return False
    if _ops.health_ok(health_url, target_sha):
        state.unhealthy_elapsed = 0.0
        return True
    state.unhealthy_elapsed += POLL_S
    return state.unhealthy_elapsed < SUSTAINED_FAILURE_S


def run(target_sha: str, health_url: str) -> bool:
    _, baseline_runs = _ops.service_status()
    state = _SoakState(baseline_runs)
    elapsed = 0.0
    while elapsed < DURATION_S:
        _ops.sleep(POLL_S)
        elapsed += POLL_S
        if not _poll_once(state, target_sha, health_url):
            return False
    return True
