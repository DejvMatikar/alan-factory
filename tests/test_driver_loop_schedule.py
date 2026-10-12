from __future__ import annotations

import pytest

from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome
from alan.driver_loop_schedule import (
    BACKOFF_BASE_S,
    BACKOFF_CAP_S,
    MID_RUN_WAKE_FLOOR_S,
    Decision,
    HoldReason,
    ScheduleContext,
    backoff_delay_s,
    backoff_failure_streak,
    decide,
)


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "command": "/alan-issue-driver",
        "cwd": "/opt/checkouts/alan-issue-driver",
        "self_paced": False,
        "interval_seconds": 1800,
        "timeout_seconds": 3600,
        "enabled": True,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


def _ctx(**overrides: object) -> ScheduleContext:
    fields: dict[str, object] = {
        "supervised": True,
        "entry": _entry(),
        "paused": False,
        "running": False,
        "usage_paused_until": None,
        "interactive_lock_present": False,
        "fast_forward_error": None,
        "recent_outcomes": (),
        "last_run_started_at": None,
        "last_run_ended_at": None,
        "self_paced_next_wake_at": None,
        "now": 1_000_000.0,
    }
    fields.update(overrides)
    return ScheduleContext(**fields)  # type: ignore[arg-type]


def test_decide_holds_when_not_supervised() -> None:
    decision = decide(_ctx(supervised=False))
    assert decision == Decision("hold", HoldReason.NOT_SUPERVISED)


def test_decide_holds_when_disabled_even_if_supervised() -> None:
    decision = decide(_ctx(entry=_entry(enabled=False)))
    assert decision == Decision("hold", HoldReason.DISABLED)


def test_decide_holds_when_running_even_if_paused() -> None:
    decision = decide(_ctx(running=True, paused=True))
    assert decision == Decision("hold", HoldReason.RUNNING)


def test_decide_holds_when_paused_even_if_usage_paused() -> None:
    decision = decide(_ctx(paused=True, usage_paused_until=2_000_000.0))
    assert decision == Decision("hold", HoldReason.PAUSED)


def test_decide_holds_for_usage_pause_over_interactive_lock() -> None:
    decision = decide(
        _ctx(usage_paused_until=2_000_000.0, interactive_lock_present=True)
    )
    assert decision.action == "hold"
    assert decision.reason == HoldReason.USAGE_PAUSE
    assert decision.next_wake_at == 2_000_000.0


def test_decide_ignores_expired_usage_pause() -> None:
    decision = decide(_ctx(usage_paused_until=500_000.0))
    assert decision.reason != HoldReason.USAGE_PAUSE


def test_decide_holds_for_interactive_lock_over_fast_forward_failure() -> None:
    decision = decide(
        _ctx(interactive_lock_present=True, fast_forward_error="boom")
    )
    assert decision == Decision("hold", HoldReason.INTERACTIVE_LOCK)


def test_decide_holds_for_fast_forward_failure_with_detail() -> None:
    decision = decide(_ctx(fast_forward_error="git fetch failed"))
    assert decision.action == "hold"
    assert decision.reason == HoldReason.FAST_FORWARD_FAILED
    assert decision.detail == "git fetch failed"


def test_decide_holds_for_backoff_over_idle() -> None:
    decision = decide(
        _ctx(
            recent_outcomes=(RunOutcome.FAILED,),
            last_run_started_at=1_000_000.0 - 5000,
            last_run_ended_at=1_000_000.0 - 10.0,
        )
    )
    assert decision.action == "hold"
    assert decision.reason == HoldReason.BACKOFF


def test_fixed_interval_due_at_raises_for_a_malformed_entry() -> None:
    entry = _entry(self_paced=False, interval_seconds=None)
    with pytest.raises(ValueError, match="interval_seconds"):
        decide(_ctx(entry=entry, last_run_started_at=1.0))


def test_decide_returns_idle_when_last_run_never_ended_but_not_yet_due() -> None:
    decision = decide(_ctx(last_run_started_at=1_000_000.0 - 10.0, last_run_ended_at=None))
    assert decision.action == "idle"
    assert decision.next_wake_at == 1_000_000.0 - 10.0 + 1800


def test_decide_returns_idle_when_not_yet_due() -> None:
    decision = decide(
        _ctx(last_run_started_at=1_000_000.0 - 10.0, last_run_ended_at=1_000_000.0 - 5.0)
    )
    assert decision.action == "idle"
    assert decision.next_wake_at == 1_000_000.0 - 10.0 + 1800


def test_decide_returns_spawn_when_never_run_before() -> None:
    decision = decide(_ctx())
    assert decision == Decision("spawn")


def test_decide_returns_spawn_when_interval_elapsed() -> None:
    decision = decide(
        _ctx(
            last_run_started_at=1_000_000.0 - 1800.0,
            last_run_ended_at=1_000_000.0 - 1000.0,
        )
    )
    assert decision == Decision("spawn")


def test_decide_applies_mid_run_wake_floor_after_long_run() -> None:
    entry = _entry(interval_seconds=100)
    ctx = _ctx(
        entry=entry,
        last_run_started_at=1_000_000.0 - 500.0,
        last_run_ended_at=1_000_000.0 - 10.0,
    )
    decision = decide(ctx)
    assert decision.action == "idle"
    assert decision.next_wake_at == 1_000_000.0 - 10.0 + MID_RUN_WAKE_FLOOR_S


def test_decide_self_paced_due_now_when_never_run() -> None:
    entry = _entry(self_paced=True, interval_seconds=None)
    decision = decide(_ctx(entry=entry))
    assert decision == Decision("spawn")


def test_decide_self_paced_uses_recorded_next_wake_at() -> None:
    entry = _entry(self_paced=True, interval_seconds=None)
    decision = decide(
        _ctx(entry=entry, self_paced_next_wake_at=1_000_500.0, last_run_ended_at=999_000.0)
    )
    assert decision.action == "idle"
    assert decision.next_wake_at == 1_000_500.0


def test_decide_self_paced_spawns_once_recorded_wake_passes() -> None:
    entry = _entry(self_paced=True, interval_seconds=None)
    decision = decide(
        _ctx(entry=entry, self_paced_next_wake_at=999_999.0, last_run_ended_at=999_000.0)
    )
    assert decision == Decision("spawn")


def test_backoff_failure_streak_counts_trailing_failures() -> None:
    assert backoff_failure_streak((RunOutcome.SUCCEEDED, RunOutcome.FAILED, RunOutcome.FAILED)) == 2


def test_backoff_failure_streak_stops_at_success() -> None:
    assert backoff_failure_streak((RunOutcome.FAILED, RunOutcome.SUCCEEDED)) == 0


def test_backoff_failure_streak_skips_usage_pause_without_breaking() -> None:
    outcomes = (RunOutcome.FAILED, RunOutcome.HELD_USAGE_PAUSE, RunOutcome.FAILED)
    assert backoff_failure_streak(outcomes) == 2


def test_backoff_failure_streak_skips_unknown_after_restart_without_breaking() -> None:
    outcomes = (RunOutcome.FAILED, RunOutcome.UNKNOWN_AFTER_RESTART)
    assert backoff_failure_streak(outcomes) == 1


def test_backoff_failure_streak_counts_timed_out() -> None:
    assert backoff_failure_streak((RunOutcome.TIMED_OUT,)) == 1


def test_backoff_failure_streak_empty_is_zero() -> None:
    assert backoff_failure_streak(()) == 0


def test_backoff_delay_s_is_zero_for_no_streak() -> None:
    assert backoff_delay_s(0) == 0.0


def test_backoff_delay_s_doubles_per_failure() -> None:
    assert backoff_delay_s(1) == BACKOFF_BASE_S
    assert backoff_delay_s(2) == BACKOFF_BASE_S * 2
    assert backoff_delay_s(3) == BACKOFF_BASE_S * 4


def test_backoff_delay_s_caps_at_ceiling() -> None:
    assert backoff_delay_s(20) == BACKOFF_CAP_S


def test_decide_spawns_again_once_backoff_window_passes() -> None:
    decision = decide(
        _ctx(
            recent_outcomes=(RunOutcome.FAILED,),
            last_run_started_at=1_000_000.0 - 10000,
            last_run_ended_at=1_000_000.0 - (BACKOFF_BASE_S + 1),
        )
    )
    assert decision == Decision("spawn")
