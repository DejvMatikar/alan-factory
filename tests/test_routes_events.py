from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from alan.dispatcher import PubSub
from alan.routes import events as events_mod
from alan.routes.events import (
    _changed_frame,
    _drain_coalesced,
    _kinds_for,
    get_events,
    global_event_stream,
)
from alan.teams._base import TeamEvent


def test_kinds_for_classification_table() -> None:
    assert _kinds_for(TeamEvent("stream", {})) == frozenset()
    assert _kinds_for(TeamEvent("health_state_saved", {})) == frozenset({"health"})
    assert _kinds_for(TeamEvent("driver_loop_changed", {})) == frozenset({"health"})
    assert _kinds_for(TeamEvent("project_amendment_approved", {"fingerprint": "x"})) == (
        frozenset({"workspace_list", "health"})
    )
    assert _kinds_for(TeamEvent("engine_created", {"slug": "a/b"})) == frozenset({"engines"})
    assert _kinds_for(TeamEvent("engine_updated", {"slug": "a/b"})) == frozenset({"engines"})
    assert _kinds_for(TeamEvent("engine_deleted", {"slug": "a/b"})) == frozenset({"engines"})
    assert _kinds_for(TeamEvent("engine_posture_changed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("engine_accent_changed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("engine_hidden", {"slug": "a/b"})) == frozenset({"engines"})
    assert _kinds_for(TeamEvent("engine_unhidden", {"slug": "a/b"})) == frozenset({"engines"})
    assert _kinds_for(TeamEvent("pool_lifecycle_changed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("preflight_completed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("dep_sweep_changed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("engine_scaffolded", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("engine_protected", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("repo_status_refreshed", {"slug": "a/b"})) == frozenset(
        {"engines"}
    )
    assert _kinds_for(TeamEvent("secret_written", {"engine": "a/b", "purpose": "transport"})) == (
        frozenset({"engines"})
    )
    assert _kinds_for(TeamEvent("workspace_archived", {"task_id": "t"})) == frozenset(
        {"workspace_list", "health"}
    )
    assert _kinds_for(TeamEvent("job_completed", {"task_id": "t"})) == frozenset(
        {"workspace_list", "health"}
    )
    assert "engines" not in _kinds_for(TeamEvent("workspace_archived", {"task_id": "t"}))
    assert "engines" not in _kinds_for(TeamEvent("job_completed", {"task_id": "t"}))
    assert "workspace_list" not in _kinds_for(TeamEvent("driver_loop_changed", {}))


def test_kinds_for_default_fan_out_excludes_inbox() -> None:
    assert _kinds_for(TeamEvent("some_arbitrary_unclassified_kind", {})) == frozenset(
        {"workspace_list", "health"}
    )


def test_changed_frame_shape() -> None:
    frame = _changed_frame(frozenset({"health", "workspace_list"}))
    assert frame.startswith("event: changed\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["health", "workspace_list"]}


async def test_drain_coalesced_merges_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.05)
    queue: asyncio.Queue[frozenset[str]] = asyncio.Queue()
    queue.put_nowait(frozenset({"health"}))
    merged = await _drain_coalesced(queue, frozenset({"workspace_list"}))
    assert merged == frozenset({"workspace_list", "health"})


async def test_drain_coalesced_returns_first_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    queue: asyncio.Queue[frozenset[str]] = asyncio.Queue()
    merged = await _drain_coalesced(queue, frozenset({"health"}))
    assert merged == frozenset({"health"})


async def test_global_event_stream_emits_coalesced_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish("t1", TeamEvent("job_completed", {"task_id": "t1"}))
    bus.publish_global(TeamEvent("project_amendment_approved", {"fingerprint": "f"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["health", "workspace_list"]}


async def test_global_event_stream_emits_engines_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("engine_created", {"slug": "acme/widget"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["engines"]}


async def test_global_event_stream_emits_dep_sweep_changed_as_engines_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("dep_sweep_changed", {"slug": "acme/widget"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["engines"]}


async def test_global_event_stream_emits_repo_status_refreshed_as_engines_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("repo_status_refreshed", {"slug": "acme/widget"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["engines"]}


async def test_global_event_stream_engines_frame_carries_no_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("engine_updated", {"slug": "acme/widget"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert set(payload.keys()) == {"kinds"}
    assert "slug" not in chunk
    assert "acme/widget" not in chunk


async def test_global_event_stream_emits_driver_loop_changed_as_health_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("driver_loop_changed", {}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["health"]}


def test_operator_session_changed_routes_to_the_health_surface_only() -> None:
    assert _kinds_for(TeamEvent("operator_session_changed", {})) == frozenset({"health"})
    assert "operator_session_changed" in events_mod._HEALTH_KINDS  # noqa: SLF001


async def test_global_event_stream_emits_operator_session_changed_as_health_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish_global(TeamEvent("operator_session_changed", {}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"kinds": ["health"]}


async def test_global_event_stream_ignores_stream_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_HEARTBEAT_S", 0.05)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    bus.publish("t1", TeamEvent("stream", {"task_id": "t1"}))
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    assert chunk == events_mod._HEARTBEAT_FRAME


async def test_global_event_stream_heartbeat_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_HEARTBEAT_S", 0.02)
    bus = PubSub()
    gen = global_event_stream(bus)
    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    await gen.aclose()
    assert chunk == ": ping\n\n"


async def test_global_event_stream_unsubscribes_on_close() -> None:
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    assert len(bus._all_listeners) == 1  # noqa: SLF001
    collect.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await collect
    await gen.aclose()
    assert bus._all_listeners == []  # noqa: SLF001


async def test_global_event_stream_drops_events_when_queue_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_QUEUE_MAX", 1)
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    def flood() -> None:
        for _ in range(5):
            bus.publish_global(TeamEvent("workspace_archived", {"task_id": "t"}))

    asyncio.get_running_loop().call_soon(flood)
    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    assert "workspace_list" in chunk


async def test_get_events_returns_sse_response() -> None:
    bus = PubSub()
    response = await get_events(bus)
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"


async def test_drain_coalesced_zero_budget_returns_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.0)
    queue: asyncio.Queue[frozenset[str]] = asyncio.Queue()
    queue.put_nowait(frozenset({"inbox"}))
    merged = await _drain_coalesced(queue, frozenset({"health"}))
    assert merged == frozenset({"health"})


async def test_global_event_stream_emits_after_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_mod, "_HEARTBEAT_S", 0.02)
    monkeypatch.setattr(events_mod, "_COALESCE_S", 0.01)
    bus = PubSub()
    gen = global_event_stream(bus)
    first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert first == ": ping\n\n"
    collect = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    bus.publish_global(TeamEvent("workspace_archived", {"task_id": "t"}))
    chunk = await asyncio.wait_for(collect, timeout=1.0)
    await gen.aclose()
    assert "workspace_list" in chunk
