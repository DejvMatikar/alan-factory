from __future__ import annotations

from pathlib import Path

import pytest

from alan import driver_loop_status as status_mod
from alan.driver_loop_registry import QuarantinedDriverLoop
from alan.driver_loop_runs import RunOutcome
from alan.driver_loop_schedule import Decision, HoldReason
from alan.driver_loop_status import LoopStatusView
from alan.queue import Queue
from alan.teams import _triage_driver_loops as driver_loops_mod
from alan.teams._triage_driver_loops import (
    ATTENTION_KIND,
    CONFIG_QUARANTINE_KIND,
    UNHEALTHY_KIND,
    driver_loop_config_quarantine_surfaces,
    driver_loop_surfaces,
)


def _view(**overrides: object) -> LoopStatusView:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "enabled": True,
        "dedicated_clone": True,
        "decision": Decision("idle", next_wake_at=123.0),
        "last_outcome": None,
        "final_message": "",
        "denial_count": 0,
        "run_now_pending": False,
    }
    fields.update(overrides)
    return LoopStatusView(**fields)  # type: ignore[arg-type]


def test_healthy_idle_loop_raises_nothing(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [_view()])
    assert driver_loop_surfaces(queue=queue) == []


def test_disabled_loop_raises_nothing(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(enabled=False, decision=Decision("hold", HoldReason.DISABLED))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    assert driver_loop_surfaces(queue=queue) == []


def test_paused_loop_raises_nothing(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(decision=Decision("hold", HoldReason.PAUSED))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    assert driver_loop_surfaces(queue=queue) == []


def test_usage_pause_loop_raises_nothing(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(decision=Decision("hold", HoldReason.USAGE_PAUSE, next_wake_at=999.0))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    assert driver_loop_surfaces(queue=queue) == []


def test_overdue_loop_raises_unhealthy(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(decision=Decision("spawn"))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == UNHEALTHY_KIND
    assert surfaces[0].is_blocked is True
    assert surfaces[0].blocker_reason == "overdue"
    assert surfaces[0].obs["name"] == "alan-issue-driver"


def test_backing_off_loop_raises_unhealthy(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(decision=Decision("hold", HoldReason.BACKOFF, next_wake_at=555.0))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == UNHEALTHY_KIND
    assert surfaces[0].blocker_reason == "backing_off"


def test_last_failed_outcome_raises_unhealthy(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    view = _view(last_outcome=RunOutcome.FAILED)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert surfaces[0].kind == UNHEALTHY_KIND
    assert surfaces[0].blocker_reason == "failing"


def test_last_timed_out_outcome_raises_unhealthy(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    view = _view(last_outcome=RunOutcome.TIMED_OUT)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert surfaces[0].kind == UNHEALTHY_KIND
    assert surfaces[0].blocker_reason == "failing"


def test_ran_with_denials_raises_attention(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(last_outcome=RunOutcome.RAN_WITH_DENIALS, denial_count=2)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == ATTENTION_KIND
    assert surfaces[0].blocker_reason == "ran_with_denials"


def test_interactive_lock_raises_attention(monkeypatch: pytest.MonkeyPatch, queue: Queue) -> None:
    view = _view(decision=Decision("hold", HoldReason.INTERACTIVE_LOCK))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == ATTENTION_KIND
    assert surfaces[0].blocker_reason == "interactive_lock"


def test_unhealthy_takes_precedence_over_attention(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    view = _view(decision=Decision("spawn"), last_outcome=RunOutcome.RAN_WITH_DENIALS)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == UNHEALTHY_KIND


def test_surface_id_is_scoped_per_loop_name(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    view = _view(name="sample-issue-driver", decision=Decision("spawn"))
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert surfaces[0].surface_id == f"{UNHEALTHY_KIND}:sample-issue-driver"


def test_multiple_loops_each_get_their_own_surface(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    views = [
        _view(name="a", decision=Decision("spawn")),
        _view(name="b"),
        _view(name="c", last_outcome=RunOutcome.RAN_WITH_DENIALS),
    ]
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: views)
    surfaces = driver_loop_surfaces(queue=queue)
    assert {s.obs["name"] for s in surfaces} == {"a", "c"}


def test_fast_forward_failed_loop_raises_unhealthy(
    monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    view = _view(
        decision=Decision("hold", HoldReason.FAST_FORWARD_FAILED, detail="git fetch failed: boom")
    )
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    surfaces = driver_loop_surfaces(queue=queue)
    assert len(surfaces) == 1
    assert surfaces[0].kind == UNHEALTHY_KIND
    assert surfaces[0].blocker_reason == "fast_forward_failed"


@pytest.mark.parametrize(
    "reason", [HoldReason.NOT_SUPERVISED, HoldReason.DISABLED, HoldReason.PAUSED]
)
def test_failing_outcome_suppressed_while_held_disabled_paused_or_not_supervised(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, reason: HoldReason
) -> None:
    view = _view(decision=Decision("hold", reason), last_outcome=RunOutcome.FAILED)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    assert driver_loop_surfaces(queue=queue) == []


@pytest.mark.parametrize(
    "reason", [HoldReason.NOT_SUPERVISED, HoldReason.DISABLED, HoldReason.PAUSED]
)
def test_ran_with_denials_suppressed_while_held_disabled_paused_or_not_supervised(
    monkeypatch: pytest.MonkeyPatch, queue: Queue, reason: HoldReason
) -> None:
    view = _view(decision=Decision("hold", reason), last_outcome=RunOutcome.RAN_WITH_DENIALS)
    monkeypatch.setattr(status_mod, "snapshot", lambda queue: [view])
    assert driver_loop_surfaces(queue=queue) == []


def test_quarantined_driver_loop_raises_config_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quarantined = [
        QuarantinedDriverLoop(path=Path("config/driver_loops.d/bad.yaml"), error="boom")
    ]
    monkeypatch.setattr(driver_loops_mod, "load_registry", lambda: ([], quarantined))
    surfaces = driver_loop_config_quarantine_surfaces()
    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.kind == CONFIG_QUARANTINE_KIND
    assert surface.is_blocked is True
    assert surface.obs == {
        "path": "config/driver_loops.d/bad.yaml",
        "error": "boom",
        "repo": None,
    }


def test_no_quarantined_driver_loops_raises_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver_loops_mod, "load_registry", lambda: ([], []))
    assert driver_loop_config_quarantine_surfaces() == []
