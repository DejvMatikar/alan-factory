from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import project as project_mod
from alan import store
from alan.project import (
    Project,
    ProjectError,
    ProjectStatus,
    create,
    delete,
    list_projects,
    read,
    update,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "projects"


def test_create_returns_active_project_with_slug_id(root: Path) -> None:
    project = create("My Project", root=root)
    assert project.id == "my-project"
    assert project.title == "My Project"
    assert project.status is ProjectStatus.ACTIVE
    assert project.doc_ref is None
    assert (root / "my-project" / "project.json").is_file()


def test_create_with_doc_ref_persists(root: Path) -> None:
    project = create("Docs", root=root, doc_ref="docs/foo.md")
    assert project.doc_ref == "docs/foo.md"


def test_create_slug_collision_appends_suffix(root: Path) -> None:
    a = create("Same Title", root=root)
    b = create("Same Title", root=root)
    c = create("Same Title", root=root)
    assert a.id == "same-title"
    assert b.id == "same-title-2"
    assert c.id == "same-title-3"


def test_create_empty_title_defaults_to_project(root: Path) -> None:
    project = create("!!!", root=root)
    assert project.id == "project"


def test_create_truncates_long_slug(root: Path) -> None:
    project = create("a" * 100, root=root)
    assert len(project.id) == 32
    assert project.id == "a" * 32


def test_create_exhausts_suffixes_raises(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_mod, "_MAX_SLUG_SUFFIX", 1)
    create("dup", root=root)
    with pytest.raises(ProjectError, match="no unique project_id"):
        create("dup", root=root)


def test_create_skips_existing_dir_made_outside_module(root: Path) -> None:
    root.mkdir()
    (root / "racy").mkdir()
    project = create("Racy", root=root)
    assert project.id == "racy-2"


def test_create_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "default"))
    project = create("Default Root")
    assert (tmp_path / "default" / project.id / "project.json").is_file()


def test_create_honors_projects_root_env_when_root_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "envroot"))
    project = create("Env Project")
    assert (tmp_path / "envroot" / project.id / "project.json").is_file()


def test_read_returns_existing_project(root: Path) -> None:
    created = create("Read Test", root=root)
    loaded = read(created.id, root=root)
    assert loaded == created


def test_read_missing_project_raises(root: Path) -> None:
    root.mkdir()
    with pytest.raises(ProjectError, match="project not found"):
        read("nope", root=root)


def test_read_missing_project_file_raises(root: Path) -> None:
    project = create("missing-file", root=root)
    (root / project.id / "project.json").unlink()
    with pytest.raises(ProjectError, match="project not found"):
        read(project.id, root=root)


def test_list_projects_empty_when_root_missing(root: Path) -> None:
    assert list_projects(root=root) == []


def test_list_projects_returns_sorted(root: Path) -> None:
    create("Bravo", root=root)
    create("Alpha", root=root)
    create("Charlie", root=root)
    ids = [p.id for p in list_projects(root=root)]
    assert ids == ["alpha", "bravo", "charlie"]


def test_list_projects_skips_non_directory_entries(root: Path) -> None:
    create("Only", root=root)
    (root / "stray.json").write_text("{}", encoding="utf-8")
    ids = [p.id for p in list_projects(root=root)]
    assert ids == ["only"]


def test_list_projects_skips_entries_without_project_file(root: Path) -> None:
    create("Real", root=root)
    (root / "stray-dir").mkdir()
    ids = [p.id for p in list_projects(root=root)]
    assert ids == ["real"]


def test_list_projects_skips_corrupt_file(root: Path) -> None:
    create("Good", root=root)
    bad = root / "broken"
    bad.mkdir()
    (bad / "project.json").write_text("not json", encoding="utf-8")
    ids = [p.id for p in list_projects(root=root)]
    assert ids == ["good"]


def test_update_title_only(root: Path) -> None:
    project = create("Old Title", root=root)
    updated = update(project, {"title": "New Title"}, root=root)
    assert updated.title == "New Title"
    assert updated.status is ProjectStatus.ACTIVE
    assert updated.doc_ref is None
    reloaded = read(project.id, root=root)
    assert reloaded.title == "New Title"


def test_update_status_only(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"status": ProjectStatus.ARCHIVED}, root=root)
    assert updated.status is ProjectStatus.ARCHIVED
    assert updated.title == "P"
    assert updated.doc_ref is None


def test_update_doc_ref_only(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"doc_ref": "docs/x.md"}, root=root)
    assert updated.doc_ref == "docs/x.md"
    assert updated.title == "P"
    assert updated.status is ProjectStatus.ACTIVE


def test_update_bumps_updated_at(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"title": "P2"}, root=root)
    assert updated.updated_at >= project.updated_at


def test_update_rejects_invalid_data(root: Path) -> None:
    project = create("P", root=root)
    with pytest.raises(ProjectError, match="invalid project update"):
        update(project, {"title": None}, root=root)
    reloaded = read(project.id, root=root)
    assert reloaded.title == "P"


def test_update_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "default"))
    project = create("Default Root Update")
    updated = update(project, {"title": "Renamed"})
    assert updated.title == "Renamed"
    reloaded = read(project.id)
    assert reloaded.title == "Renamed"


def test_write_json_delegates_to_store_write_text_atomic(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create("delegate", root=root)
    calls: list[tuple[Path, str]] = []

    def spy(path: Path, text: str, **_kw: object) -> None:
        calls.append((path, text))

    monkeypatch.setattr(store, "write_text_atomic", spy)
    update(project, {"title": "New"}, root=root)
    assert len(calls) == 1
    path, text = calls[0]
    assert path == root / project.id / "project.json"
    assert json.loads(text)["title"] == "New"


def test_project_model_defaults() -> None:
    now = datetime.now(UTC)
    project = Project(id="x", title="t", created_at=now, updated_at=now)
    assert project.status is ProjectStatus.ACTIVE
    assert project.doc_ref is None
    assert project.autonomy == "propose"
    assert project.budget_usd is None


def test_create_defaults_autonomy_and_budget(root: Path) -> None:
    project = create("Autonomy Defaults", root=root)
    assert project.autonomy == "propose"
    assert project.budget_usd is None


def test_update_autonomy_only(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"autonomy": "auto_continue"}, root=root)
    assert updated.autonomy == "auto_continue"
    assert updated.title == "P"
    assert updated.budget_usd is None


def test_update_budget_usd_only(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"budget_usd": 42.5}, root=root)
    assert updated.budget_usd == pytest.approx(42.5)
    assert updated.autonomy == "propose"


def test_update_rejects_negative_budget(root: Path) -> None:
    project = create("P", root=root)
    with pytest.raises(ProjectError, match="invalid project update"):
        update(project, {"budget_usd": -1.0}, root=root)
    reloaded = read(project.id, root=root)
    assert reloaded.budget_usd is None


def test_update_rejects_non_finite_budget(root: Path) -> None:
    project = create("P", root=root)
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ProjectError, match="invalid project update"):
            update(project, {"budget_usd": bad}, root=root)


def test_completed_is_a_valid_project_status() -> None:
    assert ProjectStatus.COMPLETED.value == "completed"


def test_update_status_to_completed(root: Path) -> None:
    project = create("P", root=root)
    updated = update(project, {"status": ProjectStatus.COMPLETED}, root=root)
    assert updated.status is ProjectStatus.COMPLETED
    reloaded = read(project.id, root=root)
    assert reloaded.status is ProjectStatus.COMPLETED


def test_provisioning_is_a_valid_project_status() -> None:
    assert ProjectStatus.PROVISIONING.value == "provisioning"


def test_update_status_to_provisioning_and_back_to_active(root: Path) -> None:
    project = create("P", root=root)
    provisioning = update(project, {"status": ProjectStatus.PROVISIONING}, root=root)
    assert provisioning.status is ProjectStatus.PROVISIONING
    activated = update(provisioning, {"status": ProjectStatus.ACTIVE}, root=root)
    assert activated.status is ProjectStatus.ACTIVE
    reloaded = read(project.id, root=root)
    assert reloaded.status is ProjectStatus.ACTIVE


def test_cached_read_returns_project_and_populates_cache(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    project = create("P", root=root)
    cache: dict[str, Project | None] = {}
    result = project_mod.cached_read(project.id, cache)
    assert result == project
    assert cache == {project.id: project}


def test_cached_read_reuses_cache_without_rereading(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    project = create("P", root=root)
    cache: dict[str, Project | None] = {}
    project_mod.cached_read(project.id, cache)

    def boom(*_a: object, **_kw: object) -> Project:
        raise AssertionError("read must not be called again for a cached id")

    monkeypatch.setattr(project_mod, "read", boom)
    second = project_mod.cached_read(project.id, cache)
    assert second == project


def test_cached_read_dangling_id_returns_none_and_caches_none(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    root.mkdir()
    cache: dict[str, Project | None] = {}
    result = project_mod.cached_read("nope", cache)
    assert result is None
    assert cache == {"nope": None}


def test_cached_read_os_error_returns_none_and_caches_none(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    project = create("P", root=root)

    def boom(*_a: object, **_kw: object) -> Project:
        raise OSError("simulated race: project.json vanished after the is_file() check")

    monkeypatch.setattr(project_mod, "read", boom)
    cache: dict[str, Project | None] = {}
    result = project_mod.cached_read(project.id, cache)
    assert result is None
    assert cache == {project.id: None}


def test_delete_removes_project_directory(root: Path) -> None:
    project = create("Doomed", root=root)
    assert (root / project.id).is_dir()
    delete(project.id, root=root)
    assert not (root / project.id).exists()


def test_delete_missing_project_raises(root: Path) -> None:
    root.mkdir()
    with pytest.raises(ProjectError, match="project not found: ghost"):
        delete("ghost", root=root)


def test_delete_uses_default_root_when_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(tmp_path / "default"))
    project = create("Default Root Delete")
    delete(project.id)
    assert not (tmp_path / "default" / project.id).exists()


def test_delete_translates_rmtree_filenotfound_to_project_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = create("racy-rmtree", root=root)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(project.id)

    monkeypatch.setattr("alan.project.shutil.rmtree", boom)
    with pytest.raises(ProjectError, match="project not found: racy-rmtree"):
        delete(project.id, root=root)


def test_pre_p4_4_project_json_loads_with_defaults(root: Path) -> None:
    root.mkdir(parents=True)
    project_dir = root / "legacy"
    project_dir.mkdir()
    now = datetime.now(UTC).isoformat()
    legacy_blob = {
        "id": "legacy",
        "title": "Legacy Project",
        "status": "active",
        "doc_ref": None,
        "created_at": now,
        "updated_at": now,
    }
    (project_dir / "project.json").write_text(
        json.dumps(legacy_blob), encoding="utf-8"
    )
    project = read("legacy", root=root)
    assert project.autonomy == "propose"
    assert project.budget_usd is None


def test_project_engine_key_defaults_to_none(root: Path) -> None:
    project = create("Engine Defaults", root=root)
    assert project.engine_key is None


def test_project_engine_key_round_trips_through_update(root: Path) -> None:
    project = create("Engine Bind", root=root)
    updated = update(project, {"engine_key": "acme/widget"}, root=root)
    assert updated.engine_key == "acme/widget"
    reloaded = read(project.id, root=root)
    assert reloaded.engine_key == "acme/widget"


def test_engine_key_for_returns_project_engine_key(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    project = create("Engine Bound", root=root)
    update(project, {"engine_key": "acme/widget"}, root=root)
    assert project_mod.engine_key_for(project.id) == "acme/widget"


def test_engine_key_for_returns_none_when_project_has_no_engine(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    project = create("No Engine", root=root)
    assert project_mod.engine_key_for(project.id) is None


def test_engine_key_for_dangling_project_id_returns_none(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    root.mkdir(parents=True, exist_ok=True)
    assert project_mod.engine_key_for("nope") is None


def test_project_intended_engine_key_defaults_to_none(root: Path) -> None:
    project = create("Intent Defaults", root=root)
    assert project.intended_engine_key is None


def test_create_with_intended_engine_key_persists(root: Path) -> None:
    project = create("Intent Stamped", root=root, intended_engine_key="acme/sprocket")
    assert project.intended_engine_key == "acme/sprocket"
    reloaded = read(project.id, root=root)
    assert reloaded.intended_engine_key == "acme/sprocket"


def test_create_without_intended_engine_key_kwarg_leaves_it_none(root: Path) -> None:
    project = create("No Intent Kwarg", root=root)
    assert project.intended_engine_key is None


def test_read_project_legacy_json_without_engine_key_defaults_to_none(root: Path) -> None:
    root.mkdir(parents=True)
    project_dir = root / "legacy-engine"
    project_dir.mkdir()
    now = datetime.now(UTC).isoformat()
    legacy_blob = {
        "id": "legacy-engine",
        "title": "Legacy Engine Project",
        "status": "active",
        "doc_ref": None,
        "autonomy": "propose",
        "budget_usd": None,
        "created_at": now,
        "updated_at": now,
    }
    (project_dir / "project.json").write_text(
        json.dumps(legacy_blob), encoding="utf-8"
    )
    project = read("legacy-engine", root=root)
    assert project.engine_key is None
    assert project.intended_engine_key is None
