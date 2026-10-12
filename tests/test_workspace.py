from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import store
from alan import workspace as ws_mod
from alan.workspace import (
    Intervention,
    InterventionAction,
    Meta,
    Status,
    Task,
    TaskDependency,
    TaskDoDClause,
    TaskStatus,
    Workspace,
    WorkspaceError,
    append_history,
    apply_dependencies,
    create,
    list_workspaces,
    pr_numbers_from_tasks,
    read,
    revisit_key,
    transition,
    validate_dag_inputs,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def test_create_writes_meta_history_and_handovers_dir(root: Path) -> None:
    ws = create("My Task", "desc", root=root)
    assert ws.task_id == "my-task"
    assert ws.path == root / "my-task" / "tasks" / "my-task"
    assert (root / "my-task").is_dir()
    assert (ws.path / "meta.json").is_file()
    assert (ws.path / "history.json").is_file()
    assert (ws.path / "handovers").is_dir()
    assert ws.meta.status is Status.ACTIVE
    assert ws.meta.revisit_counts == {}


def test_create_slug_collision_appends_suffix(root: Path) -> None:
    a = create("Same Title", "first", root=root)
    b = create("Same Title", "second", root=root)
    c = create("Same Title", "third", root=root)
    assert a.task_id == "same-title"
    assert b.task_id == "same-title-2"
    assert c.task_id == "same-title-3"


def test_create_empty_title_defaults_to_task(root: Path) -> None:
    ws = create("!!!", "d", root=root)
    assert ws.task_id == "task"


def test_create_truncates_long_slug(root: Path) -> None:
    ws = create("a" * 100, "d", root=root)
    assert len(ws.task_id) == 32
    assert ws.task_id == "a" * 32


def test_create_exhausts_suffixes_raises(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_mod, "_MAX_SLUG_SUFFIX", 2)
    create("t", "d", root=root)
    create("t", "d", root=root)
    with pytest.raises(WorkspaceError, match="no unique task_id"):
        create("t", "d", root=root)


def test_create_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "default"))
    ws = create("Default Root", "d")
    assert ws.path == tmp_path / "default" / "default-root" / "tasks" / "default-root"


def test_create_honors_workspaces_root_env_when_root_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "envroot"))
    ws = create("Env Session", "d")
    assert ws.path == tmp_path / "envroot" / "env-session" / "tasks" / "env-session"


def test_create_skips_existing_dir_made_outside_module(root: Path) -> None:
    root.mkdir()
    (root / "racy").mkdir()
    ws = create("Racy", "d", root=root)
    assert ws.task_id == "racy-2"


def test_read_returns_existing_workspace(root: Path) -> None:
    created = create("Read Test", "d", root=root)
    loaded = read(created.task_id, root=root)
    assert loaded.task_id == created.task_id
    assert loaded.meta.title == "Read Test"
    assert loaded.meta.status is Status.ACTIVE


def test_read_missing_workspace_dir_raises(root: Path) -> None:
    root.mkdir()
    with pytest.raises(WorkspaceError, match="workspace not found"):
        read("nope", root=root)


def test_read_missing_meta_file_raises(root: Path) -> None:
    ws = create("missing-meta", "d", root=root)
    (ws.path / "meta.json").unlink()
    with pytest.raises(WorkspaceError, match="workspace not found"):
        read(ws.task_id, root=root)


def test_read_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "default"))
    created = create("X", "d")
    loaded = read(created.task_id)
    assert loaded.task_id == created.task_id


def test_list_workspaces_empty_when_root_missing(root: Path) -> None:
    assert list_workspaces(root=root) == []


def test_list_workspaces_returns_sorted(root: Path) -> None:
    create("Bravo", "d", root=root)
    create("Alpha", "d", root=root)
    create("Charlie", "d", root=root)
    ids = [w.task_id for w in list_workspaces(root=root)]
    assert ids == ["alpha", "bravo", "charlie"]


def test_list_workspaces_skips_entries_without_meta(root: Path) -> None:
    create("Real", "d", root=root)
    (root / "stray-dir").mkdir()
    ids = [w.task_id for w in list_workspaces(root=root)]
    assert ids == ["real"]


def test_list_workspaces_skips_non_directory_entries(root: Path) -> None:
    create("Only", "d", root=root)
    (root / "stray.json").write_text("{}", encoding="utf-8")
    ids = [w.task_id for w in list_workspaces(root=root)]
    assert ids == ["only"]


def test_list_workspaces_skips_corrupt_meta(root: Path) -> None:
    create("Good", "d", root=root)
    bad = root / "broken"
    bad.mkdir()
    (bad / "meta.json").write_text("not json", encoding="utf-8")
    ids = [w.task_id for w in list_workspaces(root=root)]
    assert ids == ["good"]


def test_list_workspaces_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "default"))
    create("Only", "d")
    ids = [w.task_id for w in list_workspaces()]
    assert ids == ["only"]


def test_append_history_creates_entries(root: Path) -> None:
    ws = create("hist", "d", root=root)
    append_history(ws, {"event": "started", "team": "research"})
    append_history(ws, {"event": "completed", "team": "research"})
    loaded = read(ws.task_id, root=root)
    with (loaded.path / "history.json").open("r", encoding="utf-8") as h:
        history = json.load(h)
    assert history == [
        {"event": "started", "team": "research"},
        {"event": "completed", "team": "research"},
    ]


def test_append_history_malformed_raises(root: Path) -> None:
    ws = create("hist3", "d", root=root)
    (ws.path / "history.json").write_text('{"not": "a list"}\n', encoding="utf-8")
    with pytest.raises(WorkspaceError, match="history.json malformed"):
        append_history(ws, {"event": "x"})


def test_append_history_unparseable_json_raises(root: Path) -> None:
    ws = create("hist4", "d", root=root)
    (ws.path / "history.json").write_text("not json at all", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="history.json malformed"):
        append_history(ws, {"event": "x"})


def test_append_history_missing_file_raises(root: Path) -> None:
    ws = create("hist5", "d", root=root)
    (ws.path / "history.json").unlink()
    with pytest.raises(WorkspaceError, match="missing file: history.json"):
        append_history(ws, {"event": "x"})


def test_read_meta_unparseable_json_raises(root: Path) -> None:
    ws = create("corrupt", "d", root=root)
    (ws.path / "meta.json").write_text("not json", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="meta.json malformed"):
        read(ws.task_id, root=root)


def test_read_meta_wrong_shape_raises(root: Path) -> None:
    ws = create("corrupt2", "d", root=root)
    (ws.path / "meta.json").write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="meta.json malformed"):
        read(ws.task_id, root=root)


def test_transition_persists_and_bumps_updated_at(root: Path) -> None:
    ws = create("upd", "d", root=root)
    original_updated = ws.meta.updated_at
    updated = transition(ws, Status.AWAITING_INPUT)
    assert updated.meta.status is Status.AWAITING_INPUT
    assert updated.meta.updated_at >= original_updated
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_transition_sets_and_persists_merge_block_class(root: Path) -> None:
    ws = create("upd-bc", "d", root=root)
    updated = transition(ws, Status.AWAITING_INPUT, merge_block_class="stale_review")
    assert updated.meta.merge_block_class == "stale_review"
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.merge_block_class == "stale_review"


def test_transition_preserves_merge_block_class_when_not_provided(root: Path) -> None:
    ws = create("upd-bc-keep", "d", root=root)
    ws = transition(ws, Status.AWAITING_INPUT, merge_block_class="sonar_hotspot")
    later = transition(ws, Status.ACTIVE)
    assert later.meta.merge_block_class == "sonar_hotspot"


def test_meta_roundtrip_preserves_datetime(root: Path) -> None:
    ws = create("rt", "d", root=root)
    transition(ws, Status.ARCHIVED)
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ARCHIVED
    assert isinstance(reloaded.meta.created_at, datetime)
    assert reloaded.meta.created_at.tzinfo is not None


def test_write_json_delegates_to_store_write_text_atomic(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("delegate", "d", root=root)
    calls: list[tuple[Path, str]] = []

    def spy(path: Path, text: str, **_kw: object) -> None:
        calls.append((path, text))

    monkeypatch.setattr(store, "write_text_atomic", spy)
    append_history(ws, {"event": "x"})
    assert calls == [
        (ws.path / "history.json", json.dumps([{"event": "x"}], indent=2, sort_keys=False))
    ]


def test_workspace_model_accepts_path(tmp_path: Path) -> None:
    meta = Meta(
        task_id="x",
        title="t",
        description="d",
        status=Status.ACTIVE,
        revisit_counts={"research|chain-001": 1},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ws = Workspace(task_id="x", path=tmp_path, meta=meta)
    assert ws.path == tmp_path
    assert ws.meta.revisit_counts == {"research|chain-001": 1}


def test_status_enum_has_expected_values() -> None:
    assert {s.value for s in Status} == {
        "active",
        "awaiting_input",
        "completed",
        "archived",
        "escalated",
        "failed",
        "paused",
        "partial",
    }


def test_paused_status_round_trips_through_meta_json(root: Path) -> None:
    ws = create("pause-round-trip", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["status"] = "paused"
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.PAUSED


@pytest.mark.parametrize(
    "legacy",
    ["plan_needs_revision"],
)
def test_legacy_status_heals_to_awaiting_input(root: Path, legacy: str) -> None:
    ws = create("legacy", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["status"] = legacy
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_failed_status_reads_as_failed(root: Path) -> None:
    ws = create("fail-read", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["status"] = "failed"
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.FAILED


def test_legacy_escalated_now_rehydrates_as_escalated(root: Path) -> None:
    ws = create("esc", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["status"] = "escalated"
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.status is Status.ESCALATED


def test_meta_ignores_legacy_auto_dispatch_field(root: Path) -> None:
    ws = create("legacy-auto", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["auto_dispatch"] = True
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.title == "legacy-auto"
    assert not hasattr(reloaded.meta, "auto_dispatch")


def test_unknown_legacy_status_still_raises(root: Path) -> None:
    ws = create("bad-status", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["status"] = "totally_unknown"
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="meta.json malformed"):
        read(ws.task_id, root=root)


def test_increment_revisit_count_adds_new_team(root: Path) -> None:
    ws = create("inc1", "d", root=root)
    updated = ws_mod.increment_revisit_count(ws, "research", "chain-001")
    assert updated.meta.revisit_counts == {"inc1|inc1|research|chain-001": 1}


def test_increment_revisit_count_increments_existing(root: Path) -> None:
    ws = create("inc2", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-001")
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-001")
    ws = ws_mod.increment_revisit_count(ws, "implementation_plan", "chain-001")
    assert ws.meta.revisit_counts == {
        "inc2|inc2|research|chain-001": 2,
        "inc2|inc2|implementation_plan|chain-001": 1,
    }


def test_increment_revisit_count_persists(root: Path) -> None:
    ws = create("inc3", "d", root=root)
    ws_mod.increment_revisit_count(ws, "research", "chain-001")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {"inc3|inc3|research|chain-001": 1}


def test_decay_revisit_count_reduces_by_one(root: Path) -> None:
    ws = create("dec1", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-001")
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-001")
    ws = ws_mod.decay_revisit_count(ws, "verification", "chain-001")
    assert ws.meta.revisit_counts == {"dec1|dec1|verification|chain-001": 1}


def test_decay_revisit_count_floor_at_zero(root: Path) -> None:
    ws = create("dec2", "d", root=root)
    ws = ws_mod.decay_revisit_count(ws, "verification", "chain-001")
    assert ws.meta.revisit_counts.get("dec2|dec2|verification|chain-001") == 0


def test_decay_revisit_count_with_explicit_task_id(root: Path) -> None:
    ws = create("dec3", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-001", task_id="sub-task")
    ws = ws_mod.decay_revisit_count(ws, "verification", "chain-001", task_id="sub-task")
    assert ws.meta.revisit_counts.get("dec3|sub-task|verification|chain-001") == 0


def test_decay_revisit_count_persists(root: Path) -> None:
    ws = create("dec4", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-001")
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-001")
    ws_mod.decay_revisit_count(ws, "research", "chain-001")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {"dec4|dec4|research|chain-001": 1}


def test_reset_revisit_chain_clears_matching_keys(root: Path) -> None:
    ws = create("rst1", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-abc")
    ws = ws_mod.increment_revisit_count(ws, "implementation_fix", "chain-abc")
    ws = ws_mod.reset_revisit_chain(ws, "chain-abc")
    assert ws.meta.revisit_counts == {}


def test_reset_revisit_chain_preserves_other_chain(root: Path) -> None:
    ws = create("rst2", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-abc")
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-xyz")
    ws = ws_mod.reset_revisit_chain(ws, "chain-abc")
    assert ws.meta.revisit_counts == {"rst2|rst2|verification|chain-xyz": 1}


def test_reset_revisit_chain_preserves_other_task(root: Path) -> None:
    ws = create("rst3", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-abc", task_id="sub-1")
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-abc", task_id="sub-2")
    ws = ws_mod.reset_revisit_chain(ws, "chain-abc", task_id="sub-1")
    assert ws.meta.revisit_counts == {"rst3|sub-2|verification|chain-abc": 1}


def test_reset_revisit_chain_noop_when_empty(root: Path) -> None:
    ws = create("rst4", "d", root=root)
    ws = ws_mod.reset_revisit_chain(ws, "chain-abc")
    assert ws.meta.revisit_counts == {}


def test_reset_revisit_chain_with_explicit_task_id(root: Path) -> None:
    ws = create("rst5", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "verification", "chain-abc", task_id="sub-t")
    ws = ws_mod.reset_revisit_chain(ws, "chain-abc", task_id="sub-t")
    assert ws.meta.revisit_counts.get("rst5|sub-t|verification|chain-abc") is None


def test_prune_revisit_counts_for_team_drops_only_that_team(root: Path) -> None:
    ws = create("prt1", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "triage", "chain-a")
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-a")
    assert ws_mod.prune_revisit_counts_for_team(ws, "triage") == 1
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {"prt1|prt1|research|chain-a": 1}


def test_prune_revisit_counts_for_team_drops_legacy_two_part_keys(root: Path) -> None:
    ws = create("prt2", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["revisit_counts"] = {
        "triage|old": 3,
        "research|c": 1,
        "prt2|prt2|triage|c2": 2,
    }
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    ws = ws_mod.read(ws.task_id, root=root)
    assert ws_mod.prune_revisit_counts_for_team(ws, "triage") == 2
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {"research|c": 1}


def test_prune_revisit_counts_for_team_noop_returns_zero(root: Path) -> None:
    ws = create("prt3", "d", root=root)
    ws = ws_mod.increment_revisit_count(ws, "research", "chain-a")
    before = (ws.path / "meta.json").read_text(encoding="utf-8")
    assert ws_mod.prune_revisit_counts_for_team(ws, "triage") == 0
    assert (ws.path / "meta.json").read_text(encoding="utf-8") == before


def test_delete_removes_workspace_directory(root: Path) -> None:
    ws = create("doomed", "d", root=root)
    assert ws.path.is_dir()
    ws_mod.delete(ws.task_id, root=root)
    assert not ws.path.exists()


def test_delete_recursive_removes_nested_contents(root: Path) -> None:
    ws = create("nested", "d", root=root)
    (ws.path / "invocations" / "inv_001" / "agents").mkdir(parents=True)
    (ws.path / "invocations" / "inv_001" / "agents" / "architect.stream.jsonl").write_text(
        '{"type":"result"}\n', encoding="utf-8"
    )
    (ws.path / "handovers" / "handover_nested_001.yaml").write_text(
        "schema_version: '1.0'\n", encoding="utf-8"
    )
    ws_mod.delete(ws.task_id, root=root)
    assert not ws.path.exists()


def test_delete_removes_cleared_chat_cost_dir(root: Path) -> None:
    ws = create("doomed-cleared-cost", "d", root=root)
    cleared_dir = ws.path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True)
    (cleared_dir / "11111111-1111-1111-1111-111111111111.json").write_text(
        '{"discussion_usd": 0.5, "collision_check_usd": null}', encoding="utf-8"
    )
    ws_mod.delete(ws.task_id, root=root)
    assert not ws.path.exists()
    assert not cleared_dir.exists()


def test_delete_missing_task_id_raises_workspace_error(root: Path) -> None:
    root.mkdir()
    with pytest.raises(WorkspaceError, match="workspace not found: ghost"):
        ws_mod.delete("ghost", root=root)


def test_delete_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_WORKSPACES_ROOT", str(tmp_path / "default"))
    ws = create("Default Delete", "d")
    ws_mod.delete(ws.task_id)
    assert not ws.path.exists()


def test_delete_translates_rmtree_filenotfound_to_workspace_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("racy-rmtree", "d", root=root)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(ws.path)

    monkeypatch.setattr("alan.workspace.shutil.rmtree", boom)
    with pytest.raises(WorkspaceError, match="failed to delete workspace racy-rmtree"):
        ws_mod.delete(ws.task_id, root=root)


def test_meta_ignores_stale_chat_session_id_field(root: Path) -> None:
    ws = create("Chat Stale", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["chat_session_id"] = "sess-stale"
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.task_id == ws.task_id


def test_meta_drops_legacy_revisit_keys_on_load(root: Path) -> None:
    ws = create("legacy-rc", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["revisit_counts"] = {"research": 1, "research|chain-001": 2}
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {"research|chain-001": 2}


def test_meta_revisit_counts_validator_non_dict_passthrough(root: Path) -> None:
    ws = create("rc-non-dict", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["revisit_counts"] = None
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="meta.json malformed"):
        ws_mod.read(ws.task_id, root=root)


def test_create_with_team_label_persists(root: Path) -> None:
    ws = create("team-task", "d", root=root, team="triage")
    assert ws.meta.team == "triage"
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.team == "triage"


def test_create_default_team_is_none(root: Path) -> None:
    ws = create("no-team", "d", root=root)
    assert ws.meta.team is None


def test_meta_team_missing_field_loads_as_none(root: Path) -> None:
    import json as _json

    ws = create("legacy-no-team", "d", root=root)
    meta_path = ws.path / "meta.json"
    raw = _json.loads(meta_path.read_text(encoding="utf-8"))
    del raw["team"]
    meta_path.write_text(_json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.team is None


def test_meta_drops_non_string_revisit_keys_via_validator() -> None:
    now = datetime.now(UTC)
    meta = Meta.model_validate(
        {
            "task_id": "t",
            "title": "x",
            "description": "d",
            "status": "active",
            "budget": "medium",
            "revisit_counts": {42: 9, "research|chain-001": 2},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert meta.revisit_counts == {"research|chain-001": 2}


def test_task_status_enum_has_expected_values() -> None:
    assert {s.value for s in TaskStatus} == {
        "pending",
        "ready",
        "running",
        "awaiting_input",
        "completed",
        "failed",
    }


def test_task_model_valid() -> None:
    t = Task(id="my-task", status=TaskStatus.PENDING)
    assert t.id == "my-task"
    assert t.depends_on == []
    assert t.team is None
    assert t.status is TaskStatus.PENDING


def test_task_model_invalid_id_raises() -> None:
    with pytest.raises(ValueError):
        Task(id="BAD ID!", status=TaskStatus.PENDING)


def test_task_model_with_depends_on_and_team() -> None:
    t = Task(
        id="task-b",
        depends_on=["task-a"],
        team="implementation",
        status=TaskStatus.READY,
    )
    assert t.depends_on == ["task-a"]
    assert t.team == "implementation"


def test_meta_tasks_defaults_to_empty(root: Path) -> None:
    ws = create("tasks-default", "d", root=root)
    assert ws.meta.tasks == []


def test_workspace_tasks_property_delegates_to_meta(root: Path) -> None:
    ws = create("tasks-prop", "d", root=root)
    assert ws.tasks is ws.meta.tasks


def test_meta_tasks_round_trips_via_json(root: Path) -> None:
    ws = create("tasks-rt", "d", root=root)
    task = Task(id="rt-task", status=TaskStatus.PENDING)
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    ws_mod._write_meta(ws.path, new_meta)
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert len(reloaded.meta.tasks) == 1
    assert reloaded.meta.tasks[0].id == "rt-task"
    assert reloaded.meta.tasks[0].status is TaskStatus.PENDING


def test_legacy_meta_without_tasks_field_synthesizes_task(root: Path) -> None:
    ws = create("legacy-tasks", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw.pop("tasks", None)
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert len(reloaded.meta.tasks) == 1
    assert reloaded.meta.tasks[0].id == ws.task_id
    assert reloaded.meta.tasks[0].status is TaskStatus.READY
    assert reloaded.meta.tasks[0].team is None


def test_workspace_id_equals_task_id(root: Path) -> None:
    ws = create("workspace-id-test", "d", root=root)
    assert ws.workspace_id == ws.task_id
    assert ws.workspace_id == "workspace-id-test"


def test_meta_heal_revisit_counts_drops_two_pipe_keys(root: Path) -> None:
    ws = create("heal-rc", "d", root=root)
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    raw["revisit_counts"] = {
        "research": 1,
        "research|chain-001": 2,
        "ws|task|research|chain-001": 3,
        "ws|task|research": 4,
    }
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.revisit_counts == {
        "research|chain-001": 2,
        "ws|task|research|chain-001": 3,
    }


def test_create_nested_layout_outer_dir_exists(root: Path) -> None:
    ws = create("Nested Layout", "d", root=root)
    assert ws.path == root / "nested-layout" / "tasks" / "nested-layout"
    assert (root / "nested-layout").is_dir()
    assert ws.path.is_dir()


def test_read_new_layout_resolves_inner_path(root: Path) -> None:
    created = create("Read New", "d", root=root)
    loaded = read(created.task_id, root=root)
    assert loaded.path == root / "read-new" / "tasks" / "read-new"
    assert loaded.meta.title == "Read New"


def test_read_legacy_layout_falls_back_to_outer_dir(root: Path) -> None:
    task_id = "legacy-read"
    ws_path = root / task_id
    ws_path.mkdir(parents=True)
    now = datetime.now(UTC)
    (ws_path / "meta.json").write_text(
        json.dumps({
            "task_id": task_id,
            "title": "legacy",
            "description": "d",
            "status": "active",
            "budget": "medium",
            "revisit_counts": {},
            "tasks": [{"id": task_id, "effort": "medium", "status": "ready"}],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }),
        encoding="utf-8",
    )
    ws = read(task_id, root=root)
    assert ws.path == ws_path
    assert ws.meta.title == "legacy"


def test_list_workspaces_discovers_new_layout(root: Path) -> None:
    ws = create("New List", "d", root=root)
    workspaces = list_workspaces(root=root)
    assert len(workspaces) == 1
    assert workspaces[0].path == ws.path


def test_list_workspaces_discovers_legacy_layout(root: Path) -> None:
    task_id = "legacy-list"
    ws_path = root / task_id
    ws_path.mkdir(parents=True)
    now = datetime.now(UTC)
    (ws_path / "meta.json").write_text(
        json.dumps({
            "task_id": task_id,
            "title": "legacy-list",
            "description": "d",
            "status": "active",
            "budget": "medium",
            "revisit_counts": {},
            "tasks": [{"id": task_id, "effort": "medium", "status": "ready"}],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }),
        encoding="utf-8",
    )
    workspaces = list_workspaces(root=root)
    assert len(workspaces) == 1
    assert workspaces[0].path == ws_path
    assert workspaces[0].task_id == task_id


def test_list_workspaces_dedupes_mixed_layouts(root: Path) -> None:
    task_id = "both"
    ws = create("Both", "d", root=root)
    assert ws.task_id == task_id
    now = datetime.now(UTC)
    (root / task_id / "meta.json").write_text(
        json.dumps({
            "task_id": task_id,
            "title": "both-legacy",
            "description": "d",
            "status": "active",
            "budget": "medium",
            "revisit_counts": {},
            "tasks": [{"id": task_id, "effort": "medium", "status": "ready"}],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }),
        encoding="utf-8",
    )
    workspaces = list_workspaces(root=root)
    assert len(workspaces) == 1
    assert workspaces[0].path == root / task_id / "tasks" / task_id


def test_read_hollow_outer_dir_raises_workspace_not_found(root: Path) -> None:
    ws = create("hollow", "d", root=root)
    (ws.path / "meta.json").unlink()
    (ws.path / "history.json").unlink()
    (ws.path / "handovers").rmdir()
    ws.path.rmdir()
    with pytest.raises(WorkspaceError, match="workspace not found"):
        read(ws.task_id, root=root)


def _make_task(task_id: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(id=task_id, status=status)


def testvalidate_dag_inputs_ok_empty() -> None:
    validate_dag_inputs({"a", "b"}, [])


def testvalidate_dag_inputs_ok_valid_dep() -> None:
    deps = [TaskDependency(from_id="a", to_id="b")]
    validate_dag_inputs({"a", "b"}, deps)


def testvalidate_dag_inputs_dangling_from_id() -> None:
    deps = [TaskDependency(from_id="x", to_id="b")]
    with pytest.raises(ValueError, match="unknown task id"):
        validate_dag_inputs({"a", "b"}, deps)


def testvalidate_dag_inputs_dangling_to_id() -> None:
    deps = [TaskDependency(from_id="a", to_id="z")]
    with pytest.raises(ValueError, match="unknown task id"):
        validate_dag_inputs({"a", "b"}, deps)


def testvalidate_dag_inputs_cycle_raises() -> None:
    deps = [TaskDependency(from_id="a", to_id="b"), TaskDependency(from_id="b", to_id="a")]
    with pytest.raises(ValueError, match="cycle detected"):
        validate_dag_inputs({"a", "b"}, deps)


def test_apply_dependencies_no_deps_unchanged() -> None:
    tasks = [_make_task("a"), _make_task("b")]
    result = apply_dependencies(tasks, [])
    assert [t.id for t in result] == ["a", "b"]
    assert result[0].depends_on == []
    assert result[1].depends_on == []


def test_apply_dependencies_folds_dep_into_child() -> None:
    tasks = [_make_task("parent"), _make_task("child")]
    deps = [TaskDependency(from_id="parent", to_id="child")]
    result = apply_dependencies(tasks, deps)
    assert result[0].depends_on == []
    assert result[1].depends_on == ["parent"]


def test_apply_dependencies_dedupes_preserving_task_order_first() -> None:
    task_b = Task(
        id="b", depends_on=["a"], status=TaskStatus.PENDING
    )
    tasks = [_make_task("a"), task_b]
    deps = [TaskDependency(from_id="a", to_id="b")]
    result = apply_dependencies(tasks, deps)
    assert result[1].depends_on == ["a"]


def test_apply_dependencies_multiple_parents() -> None:
    tasks = [_make_task("p1"), _make_task("p2"), _make_task("child")]
    deps = [
        TaskDependency(from_id="p1", to_id="child"),
        TaskDependency(from_id="p2", to_id="child"),
    ]
    result = apply_dependencies(tasks, deps)
    assert set(result[2].depends_on) == {"p1", "p2"}


def test_create_with_tasks_persists_multi_task_dag(root: Path) -> None:
    tasks = [_make_task("plan"), _make_task("impl")]
    deps = [TaskDependency(from_id="plan", to_id="impl")]
    ws = create("Multi", "d", root=root, tasks=tasks, dependencies=deps)
    assert len(ws.meta.tasks) == 2
    plan_task = next(t for t in ws.meta.tasks if t.id == "plan")
    impl_task = next(t for t in ws.meta.tasks if t.id == "impl")
    assert plan_task.depends_on == []
    assert impl_task.depends_on == ["plan"]


def test_create_with_tasks_persists_to_disk(root: Path) -> None:
    tasks = [_make_task("a"), _make_task("b")]
    deps = [TaskDependency(from_id="a", to_id="b")]
    ws = create("Persist Multi", "d", root=root, tasks=tasks, dependencies=deps)
    reloaded = read(ws.task_id, root=root)
    assert len(reloaded.meta.tasks) == 2
    b_task = next(t for t in reloaded.meta.tasks if t.id == "b")
    assert b_task.depends_on == ["a"]


def test_create_empty_tasks_legacy_path(root: Path) -> None:
    ws = create("Legacy", "d", root=root)
    assert ws.meta.tasks == []


def test_create_none_tasks_is_legacy_path(root: Path) -> None:
    ws = create("Legacy None", "d", root=root, tasks=None)
    assert ws.meta.tasks == []


def test_create_empty_list_tasks_is_legacy_path(root: Path) -> None:
    ws = create("Legacy Empty", "d", root=root, tasks=[])
    assert ws.meta.tasks == []


def test_revisit_key_returns_pipe_delimited_string() -> None:
    key = revisit_key("ws-abc", "task-abc", "research", "chain-001")
    assert key == "ws-abc|task-abc|research|chain-001"


def test_revisit_key_used_consistently_by_increment(root: Path) -> None:
    ws = create("key-consistency", "d", root=root)
    updated = ws_mod.increment_revisit_count(ws, "research", "chain-xyz")
    expected = revisit_key(ws.workspace_id, ws.meta.task_id, "research", "chain-xyz")
    assert expected in updated.meta.revisit_counts
    assert updated.meta.revisit_counts[expected] == 1


def test_increment_revisit_count_explicit_task_id_uses_subtask(root: Path) -> None:
    ws = create("multi-task-ws", "d", root=root)
    subtask_id = "subtask-abc"
    updated = ws_mod.increment_revisit_count(ws, "implementation", "chain-sub", task_id=subtask_id)
    correct_key = revisit_key(ws.workspace_id, subtask_id, "implementation", "chain-sub")
    doubled_key = revisit_key(ws.workspace_id, ws.workspace_id, "implementation", "chain-sub")
    assert correct_key in updated.meta.revisit_counts
    assert doubled_key not in updated.meta.revisit_counts


@pytest.fixture(autouse=True)
def _clear_meta_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_mod, "_META_CACHE", {})


def test_read_meta_cached_hit_calls_read_once(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Cache Hit", "d", root=root)
    call_count = 0
    original = ws_mod._read_meta

    def spy(p: object) -> Meta:
        nonlocal call_count
        call_count += 1
        return original(p)  # type: ignore[arg-type]

    monkeypatch.setattr(ws_mod, "_read_meta", spy)
    meta1 = ws_mod._read_meta_cached(ws.path)
    meta2 = ws_mod._read_meta_cached(ws.path)
    assert call_count == 1
    assert meta1.title == meta2.title == "Cache Hit"


def test_read_meta_cached_miss_on_mtime_change(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("Cache Stale", "d", root=root)
    call_count = 0
    original = ws_mod._read_meta

    def spy(p: object) -> Meta:
        nonlocal call_count
        call_count += 1
        return original(p)  # type: ignore[arg-type]

    monkeypatch.setattr(ws_mod, "_read_meta", spy)
    ws_mod._read_meta_cached(ws.path)
    transition(ws, Status.COMPLETED)
    reads_before = call_count
    ws_mod._read_meta_cached(ws.path)
    assert call_count == reads_before + 1


def test_read_meta_cached_fallthrough_on_stat_oserror(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("OSError Path", "d", root=root)
    call_count = 0
    original = ws_mod._read_meta

    def spy(p: object) -> Meta:
        nonlocal call_count
        call_count += 1
        return original(p)  # type: ignore[arg-type]

    monkeypatch.setattr(ws_mod, "_read_meta", spy)
    (ws.path / ws_mod._META_FILE).unlink()
    meta_path = ws.path / ws_mod._META_FILE
    with pytest.raises(WorkspaceError, match="missing file"):
        ws_mod._read_meta_cached(ws.path)
    assert call_count == 1
    assert meta_path not in ws_mod._META_CACHE


def test_meta_scope_audit_defaults_empty(root: Path) -> None:
    ws = create("Task", "desc", root=root)
    assert ws.meta.scope_audit == []


def test_create_persists_scope_audit(root: Path) -> None:
    from alan.scope_audit import ScopeGapFinding

    finding = ScopeGapFinding(
        kind="in_flight_pr",
        summary="PR #1 overlaps",
        references=["PR #1"],
        suggested_action="needs_user_call",
    )
    ws = create("Task With Audit", "desc", root=root, scope_audit=[finding])
    assert len(ws.meta.scope_audit) == 1
    assert ws.meta.scope_audit[0].kind == "in_flight_pr"

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.scope_audit[0].kind == "in_flight_pr"


def test_meta_scope_audit_backfills_on_legacy_meta(root: Path) -> None:
    import json as _json

    ws = create("Legacy Task", "desc", root=root)
    raw = _json.loads((ws.path / "meta.json").read_text())
    del raw["scope_audit"]
    (ws.path / "meta.json").write_text(_json.dumps(raw))

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.scope_audit == []


def test_meta_park_reason_defaults_to_none(root: Path) -> None:
    ws = create("Task", "desc", root=root)
    assert ws.meta.park_reason is None


def test_meta_blockers_defaults_to_empty(root: Path) -> None:
    ws = create("Task", "desc", root=root)
    assert ws.meta.blockers == []


def test_meta_park_reason_backfills_on_legacy_meta(root: Path) -> None:
    ws = create("Legacy Park Task", "desc", root=root)
    raw = json.loads((ws.path / "meta.json").read_text())
    del raw["park_reason"]
    del raw["blockers"]
    (ws.path / "meta.json").write_text(json.dumps(raw))

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.park_reason is None
    assert reloaded.meta.blockers == []


def test_transition_sets_and_persists_park_reason_and_blockers(root: Path) -> None:
    from alan.park import Blocker, ParkReason

    ws = create("upd-park", "d", root=root)
    blockers = [Blocker(kind="failing_check", name="Tests", detail="check failing: Tests")]
    updated = transition(
        ws,
        Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        blockers=blockers,
    )
    assert updated.meta.park_reason is ParkReason.MERGE_BLOCK
    assert updated.meta.blockers == blockers
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    assert reloaded.meta.blockers == blockers


def test_transition_preserves_park_reason_and_blockers_when_not_provided(
    root: Path,
) -> None:
    from alan.park import Blocker, ParkReason

    ws = create("upd-park-keep", "d", root=root)
    blockers = [Blocker(kind="reason", name="reason", detail="stall")]
    ws = transition(
        ws, Status.AWAITING_INPUT, park_reason=ParkReason.STALL, blockers=blockers
    )
    later = transition(ws, Status.PAUSED)
    assert later.meta.park_reason is ParkReason.STALL
    assert later.meta.blockers == blockers


def test_meta_engine_key_defaults_to_none(root: Path) -> None:
    ws = create("Engine Default", "d", root=root)
    assert ws.meta.engine_key is None


def test_create_with_engine_key_persists(root: Path) -> None:
    ws = create("Engine WS", "d", root=root, engine_key="acme/widget")
    assert ws.meta.engine_key == "acme/widget"

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.engine_key == "acme/widget"


def test_create_default_roadmap_item_id_is_none(root: Path) -> None:
    ws = create("No Roadmap Stamp", "d", root=root)
    assert ws.meta.roadmap_item_id is None


def test_create_with_roadmap_item_id_persists(root: Path) -> None:
    ws = create("Roadmap WS", "d", root=root, roadmap_item_id="itm_abc123")
    assert ws.meta.roadmap_item_id == "itm_abc123"

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.roadmap_item_id == "itm_abc123"


def test_meta_roadmap_item_id_backfills_on_legacy_meta(root: Path) -> None:
    ws = create("Legacy Roadmap Task", "desc", root=root)
    raw = json.loads((ws.path / "meta.json").read_text())
    del raw["roadmap_item_id"]
    (ws.path / "meta.json").write_text(json.dumps(raw))

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.roadmap_item_id is None


def test_meta_engine_key_backfills_on_legacy_meta(root: Path) -> None:
    ws = create("Legacy Engine Task", "desc", root=root)
    raw = json.loads((ws.path / "meta.json").read_text())
    del raw["engine_key"]
    (ws.path / "meta.json").write_text(json.dumps(raw))

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.engine_key is None


def test_meta_requirements_defaults_to_none(root: Path) -> None:
    ws = create("Req Default", "d", root=root)
    assert ws.meta.requirements is None


def test_create_with_requirements_persists(root: Path) -> None:
    from alan.workspace import Requirement

    reqs = [Requirement(text="It works", test_ref="test_it_works")]
    ws = create("Req WS", "d", root=root, requirements=reqs)
    assert ws.meta.requirements is not None
    assert len(ws.meta.requirements) == 1
    assert ws.meta.requirements[0].text == "It works"
    assert ws.meta.requirements[0].test_ref == "test_it_works"
    assert ws.meta.requirements[0].status == "PENDING"
    assert ws.meta.requirements[0].merged_pr_url is None

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].test_ref == "test_it_works"


def test_create_sanitizes_requirement_status(root: Path) -> None:
    from alan.workspace import Requirement

    reqs = [Requirement(text="r", test_ref="t", status="PASS", merged_pr_url="https://x.com")]
    ws = create("Req Sanitize", "d", root=root, requirements=reqs)
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].status == "PENDING"
    assert ws.meta.requirements[0].merged_pr_url is None


def test_create_with_empty_requirements_list(root: Path) -> None:
    ws = create("Req Empty", "d", root=root, requirements=[])
    assert ws.meta.requirements == []


def test_update_requirements(root: Path) -> None:
    from alan.workspace import Requirement, update_requirements

    ws = create("Update Req", "d", root=root, requirements=[Requirement(text="r", test_ref="t")])
    updated_reqs = [Requirement(text="r", test_ref="t", status="PASS")]
    ws2 = update_requirements(ws, updated_reqs)
    assert ws2.meta.requirements is not None
    assert ws2.meta.requirements[0].status == "PASS"

    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].status == "PASS"


def test_requirements_none_roundtrip(root: Path) -> None:
    ws = create("Legacy Roundtrip", "d", root=root)
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.requirements is None


def test_task_injection_depth_defaults_to_zero() -> None:
    t = Task(id="my-task", status=TaskStatus.PENDING)
    assert t.injection_depth == 0


def test_task_instructions_defaults_to_none() -> None:
    t = Task(id="my-task", status=TaskStatus.PENDING)
    assert t.instructions is None


def test_task_instructions_round_trips(root: Path) -> None:
    ws = create("instr-rt", "d", root=root)
    task = Task(id="t1", status=TaskStatus.PENDING, instructions="do work")
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    ws_mod._write_meta(ws.path, new_meta)
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].instructions == "do work"


def test_task_injection_depth_round_trips(root: Path) -> None:
    ws = create("depth-rt", "d", root=root)
    task = Task(id="t1", status=TaskStatus.PENDING, injection_depth=2)
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    ws_mod._write_meta(ws.path, new_meta)
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].injection_depth == 2


def test_meta_injection_overflow_defaults_empty(root: Path) -> None:
    ws = create("overflow-default", "d", root=root)
    assert ws.meta.injection_overflow == []


def test_inject_tasks_adds_tasks_and_updates_overflow(root: Path) -> None:
    ws = create("inject-tasks", "d", root=root)
    task = Task(id="t1", status=TaskStatus.PENDING, team="research")
    ws2 = ws_mod.inject_tasks(ws, [task], ["dropped-a"])
    assert len(ws2.meta.tasks) == 1
    assert ws2.meta.tasks[0].id == "t1"
    assert ws2.meta.injection_overflow == ["dropped-a"]
    reloaded = read(ws.task_id, root=root)
    assert len(reloaded.meta.tasks) == 1
    assert reloaded.meta.injection_overflow == ["dropped-a"]


def test_inject_tasks_noop_when_both_empty(root: Path) -> None:
    ws = create("inject-noop", "d", root=root)
    ws2 = ws_mod.inject_tasks(ws, [], [])
    assert ws2 is ws


def test_inject_tasks_appends_to_existing(root: Path) -> None:
    ws = create("inject-append", "d", root=root)
    task_a = Task(id="ta", status=TaskStatus.PENDING, team="r")
    ws = ws_mod.inject_tasks(ws, [task_a], [])
    task_b = Task(id="tb", status=TaskStatus.PENDING, team="r")
    ws2 = ws_mod.inject_tasks(ws, [task_b], ["drop-1"])
    assert len(ws2.meta.tasks) == 2
    assert ws2.meta.injection_overflow == ["drop-1"]


def test_task_injection_depth_rejects_negative() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Task(id="t1", status=TaskStatus.PENDING, injection_depth=-1)


def test_meta_injection_overflow_backfills_on_legacy_meta(root: Path) -> None:
    import json as _json

    ws = create("Legacy Overflow", "d", root=root)
    raw = _json.loads((ws.path / "meta.json").read_text())
    del raw["injection_overflow"]
    (ws.path / "meta.json").write_text(_json.dumps(raw))
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.injection_overflow == []


def test_clause_id_deterministic_and_whitespace_insensitive() -> None:
    assert ws_mod._clause_id("req", "hello") == ws_mod._clause_id("req", "  hello  ")
    assert ws_mod._clause_id("req", "hello").startswith("req-")
    assert ws_mod._clause_id("dod", "hello").startswith("dod-")
    assert ws_mod._clause_id("req", "a") != ws_mod._clause_id("req", "b")
    assert len(ws_mod._clause_id("req", "a").split("-")[1]) == 12


def test_requirement_mints_id_from_text() -> None:
    from alan.workspace import Requirement

    r = Requirement(text="clause one")
    assert r.id == ws_mod._clause_id("req", "clause one")
    assert r.test_ref is None


def test_requirement_test_ref_optional_id_from_text_not_ref() -> None:
    from alan.workspace import Requirement

    r = Requirement(text="clause", test_ref="t1")
    assert r.id == ws_mod._clause_id("req", "clause")


def test_requirement_rejects_empty_test_ref() -> None:
    from pydantic import ValidationError

    from alan.workspace import Requirement

    with pytest.raises(ValidationError):
        Requirement(text="clause", test_ref="")


def test_requirement_keeps_explicit_id() -> None:
    from alan.workspace import Requirement

    assert Requirement(id="req-custom00000", text="clause").id == "req-custom00000"


def test_requirement_non_dict_input_rejected() -> None:
    from pydantic import ValidationError

    from alan.workspace import Requirement

    with pytest.raises(ValidationError):
        Requirement.model_validate("not a dict")


def test_requirement_missing_text_rejected() -> None:
    from pydantic import ValidationError

    from alan.workspace import Requirement

    with pytest.raises(ValidationError):
        Requirement.model_validate({"test_ref": "t"})


def test_requirement_empty_text_rejected() -> None:
    from pydantic import ValidationError

    from alan.workspace import Requirement

    with pytest.raises(ValidationError):
        Requirement.model_validate({"text": ""})


def test_legacy_requirement_without_id_mints_on_load(root: Path) -> None:
    import json as _json

    from alan.workspace import Requirement

    ws = create(
        "Legacy Req", "d", root=root, requirements=[Requirement(text="c", test_ref="t")]
    )
    raw = _json.loads((ws.path / "meta.json").read_text())
    for req in raw["requirements"]:
        del req["id"]
    (ws.path / "meta.json").write_text(_json.dumps(raw))
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].id == ws_mod._clause_id("req", "c")


def test_create_unbound_requirement_persists(root: Path) -> None:
    from alan.workspace import Requirement

    ws = create(
        "Unbound", "d", root=root, requirements=[Requirement(text="just a clause")]
    )
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].test_ref is None
    assert ws.meta.requirements[0].id == ws_mod._clause_id("req", "just a clause")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].test_ref is None


def test_sanitize_rederives_id_ignoring_client_id(root: Path) -> None:
    from alan.workspace import Requirement

    reqs = [Requirement(id="req-evil00000000", text="real text", status="PASS")]
    ws = create("Sanitize Id", "d", root=root, requirements=reqs)
    assert ws.meta.requirements is not None
    assert ws.meta.requirements[0].id == ws_mod._clause_id("req", "real text")
    assert ws.meta.requirements[0].status == "PENDING"


def test_task_dod_clause_mints_id_and_defaults() -> None:
    from alan.workspace import TaskDoDClause

    c = TaskDoDClause(text="dod clause")
    assert c.id == ws_mod._clause_id("dod", "dod clause")
    assert c.covers == []
    assert c.evidence is None


def test_task_dod_clause_keeps_explicit_id() -> None:
    from alan.workspace import TaskDoDClause

    c = TaskDoDClause(
        id="dod-aaaaaaaaaaaa", text="x", covers=["req-1"], evidence="PR #5"
    )
    assert c.id == "dod-aaaaaaaaaaaa"
    assert c.covers == ["req-1"]
    assert c.evidence == "PR #5"


def test_planning_state_values() -> None:
    from alan.workspace import PlanningState

    assert PlanningState.OUTLINE.value == "outline"
    assert PlanningState.DETAILED.value == "detailed"


def test_task_planning_lineage_defaults() -> None:
    from alan.workspace import PlanningState

    t = Task(id="t1", status=TaskStatus.PENDING)
    assert t.description is None
    assert t.dod_clauses == []
    assert t.planning_state == PlanningState.DETAILED
    assert t.split_rationale is None
    assert t.parent_task_id is None
    assert t.child_task_ids == []


def test_task_dod_and_lineage_round_trip(root: Path) -> None:
    from alan.workspace import PlanningState, TaskDoDClause

    ws = create("dod-rt", "d", root=root)
    task = Task(
        id="t1",
        status=TaskStatus.PENDING,
        description="sub work",
        dod_clauses=[TaskDoDClause(text="do x", covers=["req-abc"], evidence="PR #1")],
        planning_state=PlanningState.OUTLINE,
        split_rationale="too big",
        parent_task_id="root",
        child_task_ids=["c1", "c2"],
    )
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    ws_mod._write_meta(ws.path, new_meta)
    rt = ws_mod.read(ws.task_id, root=root).meta.tasks[0]
    assert rt.description == "sub work"
    assert rt.dod_clauses[0].covers == ["req-abc"]
    assert rt.dod_clauses[0].evidence == "PR #1"
    assert rt.planning_state == PlanningState.OUTLINE
    assert rt.split_rationale == "too big"
    assert rt.parent_task_id == "root"
    assert rt.child_task_ids == ["c1", "c2"]


def test_legacy_task_without_new_fields_loads_with_defaults(root: Path) -> None:
    import json as _json

    from alan.workspace import PlanningState

    ws = create("legacy-task", "d", root=root)
    new_meta = ws.meta.model_copy(
        update={"tasks": [Task(id="t1", status=TaskStatus.READY)]}
    )
    ws_mod._write_meta(ws.path, new_meta)
    raw = _json.loads((ws.path / "meta.json").read_text())
    for key in (
        "description",
        "dod_clauses",
        "planning_state",
        "split_rationale",
        "parent_task_id",
        "child_task_ids",
    ):
        raw["tasks"][0].pop(key, None)
    (ws.path / "meta.json").write_text(_json.dumps(raw))
    rt = read(ws.task_id, root=root).meta.tasks[0]
    assert rt.planning_state == PlanningState.DETAILED
    assert rt.dod_clauses == []
    assert rt.parent_task_id is None


def test_inject_tasks_backlinks_parent_child_ids(root: Path) -> None:
    ws = create("relink", "d", root=root)
    ws = ws_mod.inject_tasks(
        ws,
        [
            Task(id="parent", status=TaskStatus.COMPLETED, team="r"),
            Task(id="sibling", status=TaskStatus.COMPLETED, team="r"),
        ],
        [],
    )
    ws2 = ws_mod.inject_tasks(
        ws,
        [Task(id="child", status=TaskStatus.PENDING, team="r", parent_task_id="parent")],
        [],
    )
    by_id = {t.id: t for t in ws2.meta.tasks}
    assert by_id["parent"].child_task_ids == ["child"]
    assert by_id["sibling"].child_task_ids == []


def test_inject_tasks_orphan_parent_links_nothing(root: Path) -> None:
    ws = create("orphan", "d", root=root)
    ws = ws_mod.inject_tasks(
        ws, [Task(id="existing", status=TaskStatus.COMPLETED, team="r")], []
    )
    ws2 = ws_mod.inject_tasks(
        ws,
        [Task(id="child", status=TaskStatus.PENDING, team="r", parent_task_id="ghost")],
        [],
    )
    assert {t.id for t in ws2.meta.tasks} == {"existing", "child"}
    assert next(t for t in ws2.meta.tasks if t.id == "existing").child_task_ids == []


def test_record_task_evidence_stamps_matching_preserves_rest(root: Path) -> None:
    from alan.workspace import TaskDoDClause

    ws = create("rec-ev", "d", root=root)
    target = Task(
        id="t1",
        status=TaskStatus.COMPLETED,
        dod_clauses=[
            TaskDoDClause(text="fresh", covers=["req-a"]),
            TaskDoDClause(text="already", covers=["req-b"], evidence="PR #old"),
        ],
    )
    other = Task(
        id="t2",
        status=TaskStatus.PENDING,
        dod_clauses=[TaskDoDClause(text="z", covers=["req-c"])],
    )
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [target, other]}))
    ws = read(ws.task_id, root=root)
    ws2 = ws_mod.record_task_evidence(ws, "t1", "PR #new")
    by_id = {t.id: t for t in ws2.meta.tasks}
    assert by_id["t1"].dod_clauses[0].evidence == "PR #new"
    assert by_id["t1"].dod_clauses[1].evidence == "PR #old"
    assert by_id["t2"].dod_clauses[0].evidence is None


def test_record_task_evidence_noop_when_nothing_to_stamp(root: Path) -> None:
    from alan.workspace import TaskDoDClause

    ws = create("rec-noop", "d", root=root)
    target = Task(
        id="t1",
        status=TaskStatus.COMPLETED,
        dod_clauses=[TaskDoDClause(text="a", covers=["req-x"], evidence="PR #old")],
    )
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [target]}))
    ws = read(ws.task_id, root=root)
    assert ws_mod.record_task_evidence(ws, "nope", "PR #new") is ws
    assert ws_mod.record_task_evidence(ws, "t1", "PR #new") is ws


def test_update_task_files_stamps_matching_preserves_rest(root: Path) -> None:
    ws = create("upd-files", "d", root=root)
    target = Task(id="t1", status=TaskStatus.PENDING)
    other = Task(id="t2", status=TaskStatus.PENDING, files=["alan/other.py"])
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [target, other]}))
    ws = read(ws.task_id, root=root)
    ws2 = ws_mod.update_task_files(ws, "t1", ["alan/a.py", "alan/b.py"])
    by_id = {t.id: t for t in ws2.meta.tasks}
    assert by_id["t1"].files == ["alan/a.py", "alan/b.py"]
    assert by_id["t2"].files == ["alan/other.py"]
    reloaded = read(ws.task_id, root=root)
    assert next(t for t in reloaded.meta.tasks if t.id == "t1").files == [
        "alan/a.py",
        "alan/b.py",
    ]


def test_update_task_files_noop_when_unchanged(root: Path) -> None:
    ws = create("upd-files-noop", "d", root=root)
    target = Task(id="t1", status=TaskStatus.PENDING, files=["alan/a.py"])
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [target]}))
    ws = read(ws.task_id, root=root)
    assert ws_mod.update_task_files(ws, "nope", ["alan/a.py"]) is ws
    assert ws_mod.update_task_files(ws, "t1", ["alan/a.py"]) is ws


def _broken_scenario() -> ws_mod.TestScenario:
    return ws_mod.TestScenario(
        id="s1",
        description="unsatisfiable by construction",
        given="the config is wired",
        when="npm --prefix alan/web run lint",
        then="vitest reports 18/18 passing",
    )


def _ws_with_scenario_task(root: Path, title: str) -> Workspace:
    ws = create(title, "d", root=root)
    target = Task(id="t1", status=TaskStatus.PENDING, scenarios=[_broken_scenario()])
    other = Task(id="t2", status=TaskStatus.PENDING, scenarios=[_broken_scenario()])
    ws_mod._write_meta(ws.path, ws.meta.model_copy(update={"tasks": [target, other]}))
    return read(ws.task_id, root=root)


def test_update_task_scenarios_replaces_matching_preserves_rest(root: Path) -> None:
    ws = _ws_with_scenario_task(root, "upd-scenarios")
    repaired = _broken_scenario().model_copy(
        update={"when": "npm --prefix alan/web run lint && npx vitest run x"}
    )
    ws2 = ws_mod.update_task_scenarios(ws, "t1", scenarios=[repaired])
    by_id = {t.id: t for t in ws2.meta.tasks}
    assert by_id["t1"].scenarios[0].when == repaired.when
    assert by_id["t2"].scenarios[0].when == "npm --prefix alan/web run lint"
    reloaded = read(ws.task_id, root=root)
    assert next(t for t in reloaded.meta.tasks if t.id == "t1").scenarios[0].when == (
        repaired.when
    )


def test_update_task_scenarios_waives_without_touching_scenarios(root: Path) -> None:
    ws = _ws_with_scenario_task(root, "upd-scenarios-waive")
    ws2 = ws_mod.update_task_scenarios(ws, "t1", waived=True)
    by_id = {t.id: t for t in ws2.meta.tasks}
    assert by_id["t1"].scenarios_waived is True
    assert by_id["t1"].scenarios == [_broken_scenario()]
    assert by_id["t2"].scenarios_waived is False


def test_update_task_scenarios_sets_both_fields(root: Path) -> None:
    ws = _ws_with_scenario_task(root, "upd-scenarios-both")
    ws2 = ws_mod.update_task_scenarios(ws, "t1", scenarios=[], waived=True)
    target = next(t for t in ws2.meta.tasks if t.id == "t1")
    assert target.scenarios == []
    assert target.scenarios_waived is True


def test_update_task_scenarios_noop_when_unchanged(root: Path) -> None:
    ws = _ws_with_scenario_task(root, "upd-scenarios-noop")
    assert ws_mod.update_task_scenarios(ws, "nope", waived=True) is ws
    assert ws_mod.update_task_scenarios(ws, "t1", scenarios=[_broken_scenario()]) is ws
    assert ws_mod.update_task_scenarios(ws, "t1") is ws


def test_task_title_field_defaults_none() -> None:
    t = Task(id="my-task", status=TaskStatus.PENDING)
    assert t.title is None


def test_task_title_accepts_string() -> None:
    t = Task(id="my-task", status=TaskStatus.PENDING, title="Add login endpoint")
    assert t.title == "Add login endpoint"


def test_task_title_round_trips(root: Path) -> None:
    ws = create("title-rt", "d", root=root)
    task = Task(id="t1", status=TaskStatus.PENDING, title="Ship the feature")
    new_meta = ws.meta.model_copy(update={"tasks": [task]})
    ws_mod._write_meta(ws.path, new_meta)
    reloaded = ws_mod.read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].title == "Ship the feature"


def test_legacy_task_without_title_loads_with_none(root: Path) -> None:
    import json as _json

    ws = create("legacy-title", "d", root=root)
    new_meta = ws.meta.model_copy(
        update={"tasks": [Task(id="t1", status=TaskStatus.READY)]}
    )
    ws_mod._write_meta(ws.path, new_meta)
    raw = _json.loads((ws.path / "meta.json").read_text())
    raw["tasks"][0].pop("title", None)
    (ws.path / "meta.json").write_text(_json.dumps(raw))
    rt = read(ws.task_id, root=root).meta.tasks[0]
    assert rt.title is None


def test_stamp_root_titles_stamps_ws_title_onto_titleless_root_task(root: Path) -> None:
    task = Task(id="root-task", status=TaskStatus.PENDING)
    ws = create("My Workspace Title", "d", tasks=[task], root=root)
    assert ws.meta.tasks[0].title == "My Workspace Title"


def test_stamp_root_titles_clamps_long_ws_title_to_500(root: Path) -> None:
    long_title = "x" * 600
    task = Task(id="root-task", status=TaskStatus.PENDING)
    ws = create(long_title, "d", tasks=[task], root=root)
    assert ws.meta.tasks[0].title == "x" * 500


def test_stamp_root_titles_does_not_overwrite_existing_task_title(root: Path) -> None:
    task = Task(id="root-task", status=TaskStatus.PENDING, title="Explicit Title")
    ws = create("Workspace Title", "d", tasks=[task], root=root)
    assert ws.meta.tasks[0].title == "Explicit Title"


def test_stamp_root_titles_does_not_stamp_child_tasks(root: Path) -> None:
    root_task = Task(id="root-task", status=TaskStatus.PENDING)
    child_task = Task(id="child-task", status=TaskStatus.PENDING, parent_task_id="root-task")
    ws = create("Workspace Title", "d", tasks=[root_task, child_task], root=root)
    assert ws.meta.tasks[0].title == "Workspace Title"
    assert ws.meta.tasks[1].title is None


def test_create_stamps_title_on_root_tasks(root: Path) -> None:
    task_a = Task(id="task-a", status=TaskStatus.PENDING)
    ws = create("Ws Title", "d", root=root, tasks=[task_a])
    assert ws.meta.tasks[0].title == "Ws Title"


def test_create_does_not_overwrite_existing_task_title(root: Path) -> None:
    task_a = Task(id="task-a", status=TaskStatus.PENDING, title="Existing")
    ws = create("Ws Title", "d", root=root, tasks=[task_a])
    assert ws.meta.tasks[0].title == "Existing"


def test_create_does_not_stamp_title_on_dependent_tasks(root: Path) -> None:
    task_a = Task(id="task-a", status=TaskStatus.PENDING)
    task_b = Task(id="task-b", status=TaskStatus.PENDING, depends_on=["task-a"])
    ws = create("Ws Title", "d", root=root, tasks=[task_a, task_b])
    root_task = next(t for t in ws.meta.tasks if t.id == "task-a")
    dep_task = next(t for t in ws.meta.tasks if t.id == "task-b")
    assert root_task.title == "Ws Title"
    assert dep_task.title is None


def test_test_scenario_fields_stored(root: Path) -> None:
    from alan.workspace import TestScenario

    s = TestScenario(
        id="s1", description="desc", given="g", when="uv run pytest", then="1 passed"
    )
    assert s.id == "s1"
    assert s.test_ref is None


def test_test_scenario_test_ref_stored(root: Path) -> None:
    from alan.workspace import TestScenario

    s = TestScenario(
        id="s1", description="d", given="g", when="", then="", test_ref="tests/t.py"
    )
    assert s.test_ref == "tests/t.py"


def test_task_scenarios_defaults_empty(root: Path) -> None:
    t = Task(id="t1", status=TaskStatus.PENDING)
    assert t.scenarios == []
    assert t.scenarios_waived is False


def test_task_scenarios_stored(root: Path) -> None:
    from alan.workspace import TestScenario

    s = TestScenario(id="s1", description="d", given="g", when="uv run pytest", then="ok")
    t = Task(id="t1", status=TaskStatus.PENDING, scenarios=[s])
    assert t.scenarios[0].id == "s1"


def test_task_scenarios_waived_flag(root: Path) -> None:
    t = Task(id="t1", status=TaskStatus.PENDING, scenarios_waived=True)
    assert t.scenarios_waived is True


def test_task_files_defaults_empty(root: Path) -> None:
    t = Task(id="t1", status=TaskStatus.PENDING)
    assert t.files == []


def test_task_files_stored(root: Path) -> None:
    t = Task(id="t1", status=TaskStatus.PENDING, files=["alan/foo.py", "tests/test_foo.py"])
    assert t.files == ["alan/foo.py", "tests/test_foo.py"]


def test_meta_json_task_without_files_key_loads_with_empty_manifest(root: Path) -> None:
    ws = create(
        "legacy-no-files", "d", root=root, tasks=[Task(id="t1", status=TaskStatus.PENDING)]
    )
    raw = json.loads((ws.path / "meta.json").read_text(encoding="utf-8"))
    assert "files" in raw["tasks"][0]
    del raw["tasks"][0]["files"]
    (ws.path / "meta.json").write_text(json.dumps(raw), encoding="utf-8")
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.tasks[0].files == []


def test_pr_numbers_from_tasks_empty_when_no_tasks() -> None:
    assert pr_numbers_from_tasks([]) == []


def test_pr_numbers_from_tasks_skips_clause_without_evidence() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="not stamped yet")],
        )
    ]
    assert pr_numbers_from_tasks(tasks) == []


def test_pr_numbers_from_tasks_skips_evidence_without_pr_reference() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="c", evidence="merged manually")],
        )
    ]
    assert pr_numbers_from_tasks(tasks) == []


def test_pr_numbers_from_tasks_single_pr() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="c", evidence="PR #42")],
        )
    ]
    assert pr_numbers_from_tasks(tasks) == [42]


def test_pr_numbers_from_tasks_two_refs_in_one_clause() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[
                TaskDoDClause(text="c", evidence="superseded PR #1, landed as PR #2")
            ],
        )
    ]
    assert pr_numbers_from_tasks(tasks) == [1, 2]


def test_pr_numbers_from_tasks_zero_is_not_dropped() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="c", evidence="PR #0")],
        )
    ]
    assert pr_numbers_from_tasks(tasks) == [0]


def test_pr_numbers_from_tasks_preserves_task_and_clause_order() -> None:
    tasks = [
        Task(
            id="t1",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="a", evidence="PR #5")],
        ),
        Task(
            id="t2",
            status=TaskStatus.READY,
            dod_clauses=[TaskDoDClause(text="b", evidence="PR #9")],
        ),
    ]
    assert pr_numbers_from_tasks(tasks) == [5, 9]


def test_meta_delivered_fields_default_none(root: Path) -> None:
    ws = create("Delivery Default", "d", root=root)
    assert ws.meta.delivered_at is None
    assert ws.meta.settled_active_seconds is None


def test_record_intervention_appends_action_with_timestamp(root: Path) -> None:
    ws = create("Intervened", "d", root=root)
    ws2 = ws_mod.record_intervention(ws, InterventionAction.RESUME)
    assert [i.action for i in ws2.meta.interventions] == [InterventionAction.RESUME]
    assert ws2.meta.interventions[0].timestamp_utc
    assert ws2.meta.updated_at >= ws.meta.updated_at
    reloaded = read(ws.task_id, root=root)
    assert [i.action for i in reloaded.meta.interventions] == [InterventionAction.RESUME]


def test_record_intervention_appends_preserving_order(root: Path) -> None:
    ws = create("Intervened Twice", "d", root=root)
    ws = ws_mod.record_intervention(ws, InterventionAction.ANSWER)
    ws = ws_mod.record_intervention(ws, InterventionAction.REDISPATCH)
    reloaded = read(ws.task_id, root=root)
    assert [i.action for i in reloaded.meta.interventions] == [
        InterventionAction.ANSWER,
        InterventionAction.REDISPATCH,
    ]


def test_record_intervention_rereads_fresh_meta_from_disk(root: Path) -> None:
    ws = create("Intervened Stale", "d", root=root)
    transition(ws, Status.PAUSED)
    ws2 = ws_mod.record_intervention(ws, InterventionAction.FORCE_COMPLETE)
    assert ws2.meta.status == Status.PAUSED
    assert [i.action for i in ws2.meta.interventions] == [InterventionAction.FORCE_COMPLETE]


def test_meta_without_interventions_key_loads_empty_list(root: Path) -> None:
    ws = create("Legacy Meta", "d", root=root)
    meta_path = ws.path / "meta.json"
    raw = json.loads(meta_path.read_text())
    del raw["interventions"]
    meta_path.write_text(json.dumps(raw))
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.interventions == []


def test_intervention_model_serializes_action_as_plain_string() -> None:
    stamped = Intervention(action=InterventionAction.ANSWER, timestamp_utc="t")
    assert stamped.model_dump(mode="json") == {"action": "answer", "timestamp_utc": "t"}


def test_mark_delivered_persists_timestamp_and_settled_seconds(root: Path) -> None:
    ws = create("Delivery Set", "d", root=root)
    ws2 = ws_mod.mark_delivered(ws, settled_active_seconds=123.5)
    assert ws2.meta.delivered_at is not None
    assert ws2.meta.settled_active_seconds == pytest.approx(123.5)
    reloaded = read(ws.task_id, root=root)
    assert reloaded.meta.delivered_at is not None
    assert reloaded.meta.settled_active_seconds == pytest.approx(123.5)


def test_mark_delivered_accepts_none_settled_seconds(root: Path) -> None:
    ws = create("Delivery None Settled", "d", root=root)
    ws2 = ws_mod.mark_delivered(ws, settled_active_seconds=None)
    assert ws2.meta.delivered_at is not None
    assert ws2.meta.settled_active_seconds is None


def test_mark_delivered_is_idempotent_on_repeat_confirmation(root: Path) -> None:
    ws = create("Delivery Repeat", "d", root=root)
    first = ws_mod.mark_delivered(ws, settled_active_seconds=100.0)
    second = ws_mod.mark_delivered(first, settled_active_seconds=999.0)
    assert second.meta.delivered_at == first.meta.delivered_at
    assert second.meta.settled_active_seconds == pytest.approx(100.0)


def test_mark_delivered_rereads_meta_from_disk_before_checking(root: Path) -> None:
    ws = create("Delivery Reread", "d", root=root)
    first = ws_mod.mark_delivered(ws, settled_active_seconds=50.0)
    second = ws_mod.mark_delivered(ws, settled_active_seconds=999.0)
    assert second.meta.delivered_at == first.meta.delivered_at
    assert second.meta.settled_active_seconds == pytest.approx(50.0)
