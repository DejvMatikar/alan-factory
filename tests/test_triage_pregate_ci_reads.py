from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    CiConfig,
    EngineConfig,
    ForgeConfig,
    RepoIdentity,
    register,
)
from alan.forge import JsonObject, RowsRead
from alan.forge_gh import GhForge
from alan.queue import Queue
from alan.teams.triage_pregate import (
    ClassifiedSurface,
    gather_all,
    gather_ci_surfaces_for,
)
from alan.workspace import create as create_workspace
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, rows_read

_DEFAULT_SLUG = "DejvMatikar/alan"


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> FakeForge:
    return _active_fake_forge


def _ci_engine_config(
    *,
    deploy_workflow: str | None = "ci-deploy.yml",
    owner: str = "acme",
    repo: str = "widget",
) -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner=owner, repo=repo),
        ci=CiConfig(main_ci_workflow="tests.yml", deploy_workflow=deploy_workflow),
    )


def _run(
    conclusion: str | None, run_id: int = 5, sha: str = "sha1", status: str = "completed",
) -> JsonObject:
    return {"databaseId": run_id, "status": status, "conclusion": conclusion, "headSha": sha}


def _read_kwargs(forge: FakeForge) -> list[dict[str, Any]]:
    return [kwargs for name, _args, kwargs in forge.calls if name == "latest_branch_run"]


def _read_slugs(forge: FakeForge) -> list[str]:
    return [kwargs["forge_target"].repo.slug for kwargs in _read_kwargs(forge)]


def _by_kind(surfaces: list[ClassifiedSurface], kind: str) -> ClassifiedSurface:
    return next(s for s in surfaces if s.kind == kind)


def test_gather_ci_surfaces_for_no_ci_reads_nothing(forge: FakeForge) -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="acme", repo="widget"))
    assert gather_ci_surfaces_for(cfg) == []
    assert forge.calls == []


@pytest.mark.parametrize("error", [OSError("no gh"), RuntimeError("boom")])
def test_gather_ci_surfaces_for_reports_unknown_surfaces_when_the_forge_call_raises(
    error: BaseException, forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", error)
    surfaces = gather_ci_surfaces_for(_ci_engine_config())
    assert [s.kind for s in surfaces] == ["main_ci", "deploy"]
    assert all(s.is_blocked is False and s.obs == {} for s in surfaces)


@pytest.mark.parametrize(
    "read",
    [
        rows_read(None, outcome="failed", returncode=1, stderr="err"),
        rows_read(None, outcome="raised", returncode=-1, stderr="gh vanished"),
        rows_read([]),
        rows_read(None),
    ],
    ids=["failed", "raised", "empty", "unparseable"],
)
def test_gather_ci_surfaces_for_reports_unknown_surfaces_when_no_run_was_read(
    read: RowsRead, forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", read)
    surfaces = gather_ci_surfaces_for(_ci_engine_config())
    assert [s.kind for s in surfaces] == ["main_ci", "deploy"]
    assert all(s.is_blocked is False and s.obs == {} for s in surfaces)


def test_gather_ci_surfaces_for_main_ci_failure_conclusion(forge: FakeForge) -> None:
    always(forge, "latest_branch_run", rows_read([_run("failure")]))
    main_ci = _by_kind(gather_ci_surfaces_for(_ci_engine_config()), "main_ci")
    assert main_ci.is_blocked is True
    assert main_ci.blocker_reason == "ci_failure"
    assert main_ci.surface_id == "main_ci:acme/widget"


def test_gather_ci_surfaces_for_main_ci_success_conclusion(forge: FakeForge) -> None:
    always(forge, "latest_branch_run", rows_read([_run("success", run_id=6, sha="sha2")]))
    main_ci = _by_kind(gather_ci_surfaces_for(_ci_engine_config()), "main_ci")
    assert main_ci.is_blocked is False
    assert main_ci.blocker_reason == ""


def test_gather_ci_surfaces_for_carries_the_run_id_and_head_sha_into_the_observation(
    forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", rows_read([_run("success", run_id=77, sha="abc123")]))
    main_ci = _by_kind(gather_ci_surfaces_for(_ci_engine_config()), "main_ci")
    assert main_ci.obs == {
        "run_id": 77, "status": "completed", "conclusion": "success",
        "sha": "abc123", "repo": "acme/widget",
    }


def test_gather_ci_surfaces_for_no_deploy_workflow_emits_only_main_ci(forge: FakeForge) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    surfaces = gather_ci_surfaces_for(_ci_engine_config(deploy_workflow=None))
    assert [s.kind for s in surfaces] == ["main_ci"]
    assert len(_read_kwargs(forge)) == 1


def test_gather_ci_surfaces_for_emits_deploy_surface_when_configured(forge: FakeForge) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    surfaces = gather_ci_surfaces_for(_ci_engine_config())
    assert [s.kind for s in surfaces] == ["main_ci", "deploy"]
    assert surfaces[1].surface_id == "deploy:acme/widget"


def test_gather_ci_surfaces_for_deploy_failure_conclusion(forge: FakeForge) -> None:
    always(forge, "latest_branch_run", rows_read([_run("failure", run_id=7, sha="sha3")]))
    deploy = _by_kind(gather_ci_surfaces_for(_ci_engine_config()), "deploy")
    assert deploy.is_blocked is True
    assert deploy.blocker_reason == "deploy_failure"
    assert deploy.obs["run_id"] == 7
    assert deploy.obs["sha"] == "sha3"


def test_gather_ci_surfaces_for_reads_each_workflow_on_main_with_the_thirty_second_bound(
    forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    gather_ci_surfaces_for(_ci_engine_config())
    assert [args[:2] for name, args, _kwargs in forge.calls if name == "latest_branch_run"] == [
        ("tests.yml", "main"), ("ci-deploy.yml", "main"),
    ]
    assert [kwargs["timeout"] for kwargs in _read_kwargs(forge)] == [30, 30]


def test_a_forge_less_engine_reads_main_ci_and_deploy_from_its_own_repository(
    forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    gather_ci_surfaces_for(_ci_engine_config(owner="acme", repo="widget"))
    assert _read_slugs(forge) == ["acme/widget", "acme/widget"]
    assert _DEFAULT_SLUG not in _read_slugs(forge)


def test_a_forge_less_engine_never_reads_through_the_default_forge_credential_target(
    forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    gather_ci_surfaces_for(_ci_engine_config(owner="acme", repo="widget"))
    targets = [kwargs["forge_target"] for kwargs in _read_kwargs(forge)]
    assert all(target.repo != DEFAULT_FORGE_TARGET.repo for target in targets)


def test_an_engine_whose_forge_block_names_another_repository_still_reads_its_own_repo(
    forge: FakeForge,
) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    config = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="other", repo="mirror"), credential_env_name="OTHER_GH_TOKEN",
        ),
        ci=CiConfig(main_ci_workflow="tests.yml", deploy_workflow="ci-deploy.yml"),
    )
    surfaces = gather_ci_surfaces_for(config)
    assert _read_slugs(forge) == ["acme/widget", "acme/widget"]
    assert [s.surface_id for s in surfaces] == ["main_ci:acme/widget", "deploy:acme/widget"]


def test_gather_all_reports_ci_surfaces_and_system_surfaces_when_the_reads_fail(
    queue: Queue, ws_root: Path, forge: FakeForge, tmp_path: Path
) -> None:
    always(forge, "latest_branch_run", rows_read(None, outcome="failed", returncode=1))
    register(
        _ci_engine_config(owner="DejvMatikar", repo="alan"), default=True, validate_env=False
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    ids = [s.surface_id for s in gather_all(ws_root, queue, logs, 2)]
    assert "main_ci:DejvMatikar/alan" in ids
    assert "deploy:DejvMatikar/alan" in ids
    assert "recurring_errors" in ids


def test_gather_all_includes_build_tasks(
    queue: Queue, ws_root: Path, forge: FakeForge, tmp_path: Path
) -> None:
    always(forge, "latest_branch_run", rows_read(None, outcome="failed", returncode=1))
    always(forge, "prs_by_head_prefix", rows_read(None, outcome="failed", returncode=1))
    register(
        _ci_engine_config(owner="DejvMatikar", repo="alan"), default=True, validate_env=False
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    create_workspace("impl-ws3", "d", root=ws_root, team="implementation")
    surfaces = gather_all(ws_root, queue, logs, 2)
    assert len([s for s in surfaces if s.kind == "build_task"]) >= 1


def test_gather_all_with_no_registered_engine_reads_no_ci(
    queue: Queue, ws_root: Path, forge: FakeForge, tmp_path: Path
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    surfaces = gather_all(ws_root, queue, logs, 2)
    assert not any(s.kind in ("main_ci", "deploy") for s in surfaces)
    assert _read_kwargs(forge) == []


def test_gather_all_reads_each_registered_engine_from_its_own_repository(
    queue: Queue, ws_root: Path, forge: FakeForge, tmp_path: Path
) -> None:
    always(forge, "latest_branch_run", rows_read([]))
    register(
        _ci_engine_config(owner="DejvMatikar", repo="alan"), default=True, validate_env=False
    )
    register(
        _ci_engine_config(owner="acme", repo="sprocket-two", deploy_workflow=None),
        validate_env=False,
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    ids = {s.surface_id for s in gather_all(ws_root, queue, logs, 2)}
    assert "main_ci:DejvMatikar/alan" in ids
    assert "deploy:DejvMatikar/alan" in ids
    assert "main_ci:acme/sprocket-two" in ids
    assert "deploy:acme/sprocket-two" not in ids
    assert _read_slugs(forge) == ["DejvMatikar/alan", "DejvMatikar/alan", "acme/sprocket-two"]


def _completed(stdout: str) -> Any:
    def _inner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return _inner


def test_a_non_object_first_row_is_dropped_by_the_adapter_before_the_surface_is_built(
    forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge.stub("latest_branch_run", GhForge().latest_branch_run)
    stdout = '["oops", {"databaseId": 9, "status": "completed", "conclusion": "failure"}]'
    monkeypatch.setattr(subprocess, "run", _completed(stdout))
    main_ci = _by_kind(gather_ci_surfaces_for(_ci_engine_config()), "main_ci")
    assert main_ci.is_blocked is True
    assert main_ci.obs["run_id"] == 9


@pytest.mark.parametrize("stdout", ['["oops", 3, null]', "[]", "not json", '{"a": 1}'])
def test_a_listing_without_an_object_row_gives_an_unknown_surface(
    stdout: str, forge: FakeForge, monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge.stub("latest_branch_run", GhForge().latest_branch_run)
    monkeypatch.setattr(subprocess, "run", _completed(stdout))
    surfaces = gather_ci_surfaces_for(_ci_engine_config())
    assert all(s.is_blocked is False and s.obs == {} for s in surfaces)
