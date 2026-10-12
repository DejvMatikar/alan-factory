from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan._dispatcher_base import (
    _governing_engine_config,  # pyright: ignore[reportPrivateUsage]
    _stamp_snapshot,  # pyright: ignore[reportPrivateUsage]
    enqueue_with_pending_workflow,
    governing_engine_config,
)
from alan.claude_cli import OnProcStarted
from alan.dispatcher import (
    PubSub,
    pinned_engine_config,
)
from alan.engine_config import (
    EngineConfig,
    Posture,
    PromptOverride,
    RepoIdentity,
    admitted_teams,
    default_engine_config,
    load_yaml,
    register,
)
from alan.engine_config_validation import Posture as PostureV
from alan.handover import Handover
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import EngineSnapshot, Status, Workspace

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _make_ws(root: Path, title: str = "test") -> Workspace:
    return workspace_mod.create(title, "desc", root=root)


def _make_handover(task_id: str) -> Handover:
    return Handover(
        handover_id=f"h_{task_id}",
        handover_type="forward",
        task_id=task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


class _NoopTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(self, _ws: Workspace, _prior: Handover | None) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _make_handover(ws.task_id)


def _noop_team() -> _NoopTeam:
    return _NoopTeam()


def _autonomous_config() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="test", repo="test"),
        posture=Posture.autonomous,
        transition_graph={"research": ["implementation_plan"]},
    )


def _propose_config() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="test", repo="test"),
        posture=Posture.propose,
        transition_graph={"research": ["implementation_plan"]},
    )


def test_engine_snapshot_stores_posture_and_graph() -> None:
    snap = EngineSnapshot(posture=Posture.propose, transition_graph={"a": ["b"]})
    assert snap.posture == Posture.propose
    assert snap.transition_graph == {"a": ["b"]}


def test_engine_snapshot_is_frozen() -> None:
    snap = EngineSnapshot(posture=Posture.observe, transition_graph={})
    with pytest.raises(Exception):  # noqa: B017 - frozen model raises on set
        snap.posture = Posture.autonomous  # type: ignore[misc]


def test_engine_snapshot_roundtrips_via_json() -> None:
    snap = EngineSnapshot(posture=Posture.self_merge, transition_graph={"x": ["y", "z"]})
    dumped = snap.model_dump(mode="json")
    restored = EngineSnapshot.model_validate(dumped)
    assert restored == snap


def test_stamp_engine_snapshot_persists(root: Path) -> None:
    ws = _make_ws(root)
    assert ws.meta.engine_snapshot is None

    snap = EngineSnapshot(posture=Posture.propose, transition_graph={"a": ["b"]})
    ws2 = workspace_mod.stamp_engine_snapshot(ws, snap)

    assert ws2.meta.engine_snapshot == snap
    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.engine_snapshot == snap


def test_stamp_engine_snapshot_preserves_other_fields(root: Path) -> None:
    ws = _make_ws(root, title="keep me")
    snap = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws2 = workspace_mod.stamp_engine_snapshot(ws, snap)

    assert ws2.meta.title == "keep me"
    assert ws2.meta.status == Status.ACTIVE


def test_stamp_engine_snapshot_can_overwrite(root: Path) -> None:
    ws = _make_ws(root)
    snap_a = EngineSnapshot(posture=Posture.propose, transition_graph={})
    snap_b = EngineSnapshot(posture=Posture.autonomous, transition_graph={"x": []})
    ws = workspace_mod.stamp_engine_snapshot(ws, snap_a)
    ws = workspace_mod.stamp_engine_snapshot(ws, snap_b)

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.engine_snapshot == snap_b


def test_stamp_snapshot_uses_registered_config(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _autonomous_config())
    ws = _make_ws(root)
    ws2 = _stamp_snapshot(ws)

    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.posture == Posture.autonomous


def test_stamp_snapshot_falls_back_to_default_when_none_registered(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = _make_ws(root)
    ws2 = _stamp_snapshot(ws)

    default_posture = default_engine_config().posture
    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.posture == default_posture


def test_enqueue_stamps_snapshot_on_first_call(
    root: Path,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)
    assert ws.meta.engine_snapshot is None

    pubsub = PubSub()
    enqueue_with_pending_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws,
        team=_noop_team(),
        prior=None,
    )

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.engine_snapshot is not None
    assert reloaded.meta.engine_snapshot.posture == Posture.propose


def test_enqueue_does_not_overwrite_existing_snapshot(
    root: Path,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)

    snap = EngineSnapshot(posture=Posture.observe, transition_graph={"orig": []})
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    pubsub = PubSub()
    enqueue_with_pending_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws,
        team=_noop_team(),
        prior=None,
    )

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.engine_snapshot == snap


def test_snapshot_inert_to_config_mutation(
    root: Path,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)

    pubsub = PubSub()
    enqueue_with_pending_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws,
        team=_noop_team(),
        prior=None,
    )

    reloaded_before = workspace_mod.read(ws.task_id, root=root)
    assert reloaded_before.meta.engine_snapshot is not None
    assert reloaded_before.meta.engine_snapshot.posture == Posture.propose

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _autonomous_config())

    reloaded_after = workspace_mod.read(ws.task_id, root=root)
    assert reloaded_after.meta.engine_snapshot is not None
    assert reloaded_after.meta.engine_snapshot.posture == Posture.propose


def test_new_workspace_after_config_mutation_gets_new_posture(
    root: Path,
    queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws_old = _make_ws(root, title="old")
    pubsub = PubSub()
    enqueue_with_pending_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws_old,
        team=_noop_team(),
        prior=None,
    )

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _autonomous_config())
    ws_new = _make_ws(root, title="new")
    enqueue_with_pending_workflow(
        queue=queue,
        pubsub=pubsub,
        workspace=ws_new,
        team=_noop_team(),
        prior=None,
    )

    old_snap = workspace_mod.read(ws_old.task_id, root=root).meta.engine_snapshot
    new_snap = workspace_mod.read(ws_new.task_id, root=root).meta.engine_snapshot
    assert old_snap is not None and old_snap.posture == Posture.propose
    assert new_snap is not None and new_snap.posture == Posture.autonomous


def test_prompt_override_save_never_mutates_pinned_snapshot_config(root: Path) -> None:
    base_cfg = EngineConfig(
        repo=RepoIdentity(owner="prompt-pin-co", repo="widget"),
        posture=Posture.propose,
    )
    register(base_cfg)

    ws = _make_ws(root)
    snap = EngineSnapshot(posture=Posture.propose, transition_graph={}, config=base_cfg)
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    new_overrides = dict(base_cfg.prompt_overrides)
    new_overrides["implementation/implementer"] = PromptOverride(
        text="custom {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}", base_fingerprint="fp"
    )
    updated_cfg = base_cfg.model_copy(update={"prompt_overrides": new_overrides})

    reloaded = workspace_mod.read(ws.task_id, root=root)
    assert reloaded.meta.engine_snapshot is not None
    assert reloaded.meta.engine_snapshot.config is not None
    assert reloaded.meta.engine_snapshot.config.prompt_overrides == {}
    assert reloaded.meta.engine_snapshot.config.prompt_overrides is not new_overrides
    assert updated_cfg.prompt_overrides != base_cfg.prompt_overrides


def test_pinned_engine_config_returns_snapshot_posture(root: Path) -> None:
    ws = _make_ws(root)
    snap = EngineSnapshot(posture=Posture.self_merge, transition_graph={"a": ["b"]})
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    posture, graph = pinned_engine_config(ws)
    assert posture == Posture.self_merge
    assert graph == {"a": ["b"]}


def test_pinned_engine_config_returns_snapshot_graph(root: Path) -> None:
    ws = _make_ws(root)
    snap = EngineSnapshot(
        posture=Posture.propose,
        transition_graph={"team_a": ["team_b", "team_c"]},
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    _, graph = pinned_engine_config(ws)
    assert graph == {"team_a": ["team_b", "team_c"]}


def test_pinned_engine_config_falls_back_to_registered_when_no_snapshot(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)

    posture, _ = pinned_engine_config(ws)
    assert posture == Posture.propose


def test_pinned_engine_config_falls_back_to_default_when_no_registered(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = _make_ws(root)

    posture, _ = pinned_engine_config(ws)
    assert posture == default_engine_config().posture


def test_pinned_engine_config_ignores_live_config_mutation_when_snapshot_set(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws(root)
    snap = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _autonomous_config())

    posture, _ = pinned_engine_config(ws)
    assert posture == Posture.observe


def test_pinned_engine_config_returns_copy_of_graph(root: Path) -> None:
    ws = _make_ws(root)
    original_graph: dict[str, list[str]] = {"a": ["b"]}
    snap = EngineSnapshot(posture=Posture.propose, transition_graph=original_graph)
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    _, returned_graph = pinned_engine_config(ws)
    returned_graph["mutated"] = ["x"]

    _, fresh_graph = pinned_engine_config(ws)
    assert "mutated" not in fresh_graph


def test_default_engine_snapshot_posture_is_autonomous(root: Path) -> None:
    ws = _make_ws(root)
    snap = EngineSnapshot(
        posture=default_engine_config().posture,
        transition_graph=default_engine_config().transition_graph,
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)
    posture, _ = pinned_engine_config(ws)
    assert posture == PostureV.autonomous


def test_observe_engine_snapshot_withholds_consequential_teams(root: Path) -> None:
    ws = _make_ws(root)
    snap = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = workspace_mod.stamp_engine_snapshot(ws, snap)

    posture, _ = pinned_engine_config(ws)
    consequential = {"pr_merge", "verification", "triage", "remediation", "dependency_sweep"}
    assert admitted_teams(consequential, posture) == set()


def _named_config(slug: str, posture: Posture = Posture.propose) -> EngineConfig:
    owner, _, repo = slug.partition("/")
    return EngineConfig(repo=RepoIdentity(owner=owner, repo=repo), posture=posture)


def test_engine_snapshot_config_defaults_none() -> None:
    snap = EngineSnapshot(posture=Posture.propose, transition_graph={})
    assert snap.config is None


def test_engine_snapshot_legacy_json_without_config_deserializes_none() -> None:
    legacy = {"posture": "propose", "transition_graph": {"a": ["b"]}}
    restored = EngineSnapshot.model_validate(legacy)
    assert restored.config is None


def test_engine_snapshot_roundtrips_config_via_json() -> None:
    cfg = _autonomous_config()
    snap = EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    dumped = snap.model_dump(mode="json")
    restored = EngineSnapshot.model_validate(dumped)
    assert restored == snap
    assert restored.config == cfg


def test_stamp_snapshot_resolves_named_engine_via_registry(root: Path) -> None:
    named = _named_config("acme/widget")
    register(named)
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")

    ws2 = _stamp_snapshot(ws)

    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.posture == Posture.propose
    assert ws2.meta.engine_snapshot.config == named


def test_stamp_snapshot_pins_full_config_fields(root: Path) -> None:
    named = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        posture=Posture.propose,
        transition_graph={"research": ["implementation_plan"]},
        gate_commands=[["echo", "hi"]],
    )
    register(named)
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")

    ws2 = _stamp_snapshot(ws)

    cfg = ws2.meta.engine_snapshot.config if ws2.meta.engine_snapshot else None
    assert cfg is not None
    assert cfg.repo.slug == "acme/widget"
    assert cfg.gate_commands == [["echo", "hi"]]
    assert cfg.transition_graph == {"research": ["implementation_plan"]}


def test_stamp_snapshot_unknown_engine_key_falls_back_to_default(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = workspace_mod.create("ghost", "desc", root=root, engine_key="ghost/repo")

    ws2 = _stamp_snapshot(ws)

    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.posture == default_engine_config().posture
    assert ws2.meta.engine_snapshot.config == default_engine_config()


def test_stamp_snapshot_none_engine_key_ignores_registry_lookup(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(_named_config("acme/widget"))
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)
    assert ws.meta.engine_key is None

    ws2 = _stamp_snapshot(ws)

    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.config == _propose_config()


def test_named_engine_snapshot_inert_to_later_registry_lookup_change(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = _named_config("acme/widget", posture=Posture.propose)
    monkeypatch.setattr("alan.engine_config.lookup_by_slug", lambda _slug: v1)
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")
    ws = _stamp_snapshot(ws)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.posture == Posture.propose

    v2 = _named_config("acme/widget", posture=Posture.autonomous)
    monkeypatch.setattr("alan.engine_config.lookup_by_slug", lambda _slug: v2)

    posture, _ = pinned_engine_config(ws)
    assert posture == Posture.propose


def test_pinned_engine_config_uses_named_engine_before_first_dispatch(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = _named_config("acme/widget", posture=Posture.propose)
    monkeypatch.setattr(
        "alan.engine_config.lookup_by_slug",
        lambda slug: named if slug == "acme/widget" else None,
    )
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")
    assert ws.meta.engine_snapshot is None

    posture, _ = pinned_engine_config(ws)
    assert posture == Posture.propose


def test_pinned_engine_config_unknown_named_engine_falls_back_to_default(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = workspace_mod.create("ghost", "desc", root=root, engine_key="ghost/repo")

    posture, _ = pinned_engine_config(ws)
    assert posture == default_engine_config().posture


def test_named_default_engine_slug_reproduces_unnamed_snapshot_byte_for_byte(
    root: Path,
) -> None:
    default_cfg = default_engine_config()
    register(default_cfg, default=True, validate_env=False)

    ws_named = workspace_mod.create(
        "named-default", "desc", root=root, engine_key=default_cfg.repo.slug
    )
    ws_unnamed = workspace_mod.create("unnamed-default", "desc", root=root)

    snap_named = _stamp_snapshot(ws_named).meta.engine_snapshot
    snap_unnamed = _stamp_snapshot(ws_unnamed).meta.engine_snapshot

    assert snap_named is not None
    assert snap_named == snap_unnamed


def test_second_engine_fixture_governs_and_pins_distinct_from_default(root: Path) -> None:
    secondary = load_yaml(_FIXTURES / "engine_config_secondary.yaml")
    ws = workspace_mod.create(
        "secondary-governed", "desc", root=root, engine_key=secondary.repo.slug
    )

    ws2 = _stamp_snapshot(ws)

    assert ws2.meta.engine_snapshot is not None
    assert ws2.meta.engine_snapshot.posture == Posture.observe
    assert ws2.meta.engine_snapshot.config == secondary
    assert ws2.meta.engine_snapshot.config != default_engine_config()


def test_governing_engine_config_returns_pinned_snapshot_config(root: Path) -> None:
    named = _named_config("acme/widget")
    register(named)
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")
    ws = _stamp_snapshot(ws)

    cfg = governing_engine_config(ws)

    assert cfg == named


def test_governing_engine_config_no_snapshot_falls_back_to_registry(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: _propose_config())
    ws = _make_ws(root)
    assert ws.meta.engine_snapshot is None

    cfg = governing_engine_config(ws)

    assert cfg == _propose_config()


def test_governing_engine_config_no_snapshot_falls_back_to_default_when_none_registered(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("alan.engine_config.get_registered", lambda: None)
    ws = _make_ws(root)

    cfg = governing_engine_config(ws)

    assert cfg == default_engine_config()


def test_governing_engine_config_legacy_snapshot_without_config_falls_through(
    root: Path,
) -> None:
    ws = _make_ws(root)
    legacy_snapshot = EngineSnapshot(posture=Posture.propose, transition_graph={"a": ["b"]})
    ws = workspace_mod.stamp_engine_snapshot(ws, legacy_snapshot)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.config is None

    cfg = governing_engine_config(ws)

    assert cfg == _governing_engine_config(ws.meta.engine_key)


def test_governing_engine_config_legacy_snapshot_with_engine_key_uses_registry(
    root: Path,
) -> None:
    named = _named_config("acme/widget")
    register(named)
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")
    legacy_snapshot = EngineSnapshot(posture=Posture.observe, transition_graph={})
    ws = workspace_mod.stamp_engine_snapshot(ws, legacy_snapshot)
    assert ws.meta.engine_snapshot is not None
    assert ws.meta.engine_snapshot.config is None

    cfg = governing_engine_config(ws)

    assert cfg == named


def test_governing_engine_config_json_roundtrip_legacy_blob_resolves(root: Path) -> None:
    ws = _make_ws(root)
    legacy_blob = {"posture": "autonomous", "transition_graph": {"x": ["y"]}}
    restored_snapshot = EngineSnapshot.model_validate(legacy_blob)
    assert restored_snapshot.config is None
    ws = workspace_mod.stamp_engine_snapshot(ws, restored_snapshot)

    cfg = governing_engine_config(ws)

    assert cfg == default_engine_config()


def test_governing_engine_config_never_returns_none(root: Path) -> None:
    scenarios = [
        _make_ws(root, title="no-snapshot"),
        workspace_mod.stamp_engine_snapshot(
            _make_ws(root, title="legacy"),
            EngineSnapshot(posture=Posture.observe, transition_graph={}),
        ),
        _stamp_snapshot(_make_ws(root, title="stamped")),
    ]
    for ws in scenarios:
        assert governing_engine_config(ws) is not None


def test_governing_engine_config_ignores_live_mutation_when_pinned(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    named_v1 = _named_config("acme/widget", posture=Posture.propose)
    monkeypatch.setattr(
        "alan.engine_config.lookup_by_slug",
        lambda slug: named_v1 if slug == "acme/widget" else None,
    )
    ws = workspace_mod.create("named", "desc", root=root, engine_key="acme/widget")
    ws = _stamp_snapshot(ws)

    named_v2 = _named_config("acme/widget", posture=Posture.autonomous)
    monkeypatch.setattr(
        "alan.engine_config.lookup_by_slug",
        lambda slug: named_v2 if slug == "acme/widget" else None,
    )

    cfg = governing_engine_config(ws)

    assert cfg.posture == Posture.propose
