from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from alan import control_plane, engine_registry_store, repo_scaffold, secrets_store, server
from alan import engine_config as engine_config_mod
from alan import engine_config_proposal as engine_config_proposal_mod
from alan import project as project_mod
from alan import project_doc as project_doc_mod
from alan import repo_status as repo_status_mod
from alan import workspace as workspace_mod
from alan.branch_protection_compat import Finding
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, ForgeTarget, RepoIdentity
from alan.engine_preflight import PreflightResult
from alan.queue import Queue
from alan.routes import engines as engines_route_mod
from alan.slot_pool import PoolDisposeIncompleteError, SlotPool
from alan.teams import dependency_sweep as dependency_sweep_mod
from alan.teams._base import TeamEvent

_FORGE_CREDENTIAL = "ACME_GH_TOKEN"  # noqa: S105


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    engines_root: Path, secrets_root: Path, workspaces_root: Path
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)


class _RecordingPoolFactory:
    def __init__(self, pools_root: Path) -> None:
        self._pools_root = pools_root
        self.calls: list[EngineConfig] = []

    def __call__(self, cfg: EngineConfig) -> SlotPool:
        self.calls.append(cfg)
        size = cfg.pool_size or 2
        return SlotPool(
            root=self._pools_root / cfg.repo.slug.replace("/", "_"),
            size=size,
            repo_root=str(self._pools_root),
        )


@pytest.fixture
def dispatcher_queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "dispatcher-queue.db")
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def pool_factory(tmp_path: Path) -> _RecordingPoolFactory:
    return _RecordingPoolFactory(tmp_path / "pools")


@pytest.fixture
def dispatcher(
    client: TestClient,
    dispatcher_queue: Queue,
    workspaces_root: Path,
    pool_factory: _RecordingPoolFactory,
) -> Dispatcher:
    _ = client
    return Dispatcher(
        dispatcher_queue,
        {},
        pubsub=server.app.state.pubsub,
        workspaces_root=workspaces_root,
        pool_factory=pool_factory,
    )


def _subscribe(client: TestClient) -> list[TeamEvent]:
    events: list[TeamEvent] = []
    server.app.state.pubsub.subscribe_all(events.append)
    return events


@pytest.fixture
def client_with_dispatcher(
    client: TestClient, dispatcher: Dispatcher
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._optional_dispatcher_dep] = lambda: dispatcher
    try:
        yield client
    finally:
        server.app.dependency_overrides.pop(server._optional_dispatcher_dep, None)


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _minimal_body(owner="acme", repo="widget"):
    return {"repo": {"owner": owner, "repo": repo}}


def _forge_body(owner="acme", repo="widget"):
    return {
        "repo": {"owner": owner, "repo": repo},
        "forge": {
            "repo": {"owner": owner, "repo": repo},
            "credential_env_name": _FORGE_CREDENTIAL,
            "authoritative_checks": [],
        },
    }


def _full_config_body(owner="acme", repo="widget"):
    return {
        "repo": {"owner": owner, "repo": repo},
        "code_quality": {
            "project_key": "acme_widget",
            "organization": "acme",
            "host_url": "https://sonarcloud.io",
            "token_env_var": "ACME_SONAR_TOKEN",
        },
        "forge": {
            "repo": {"owner": owner, "repo": repo},
            "credential_env_name": _FORGE_CREDENTIAL,
            "authoritative_checks": ["Check A", "Check B", "Check C"],
        },
        "ci": {"main_ci_workflow": "tests.yml", "deploy_workflow": "deploy.yml"},
        "roles": {
            "implementer": {"model": "sonnet", "effort": "xhigh"},
            "judge": {"model": "opus", "effort": None},
        },
        "gate_commands": [["uv", "run", "pytest"]],
        "warmup_commands": [["uv", "sync"]],
        "base_tools": ["Read", "Grep"],
        "team_tools": {"implementation": ["Edit", "Write"]},
        "transition_graph": {
            "impl": ["verifier"],
            "impl_fix": ["verifier"],
            "verifier": ["impl_fix", "merger"],
            "merger": ["done"],
            "done": [],
        },
        "flow_policy": {
            "verifier_teams": ["verifier"],
            "merge_teams": ["merger"],
            "terminal_teams": ["done"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
        "pool_size": 3,
        "preset": None,
    }


def _create(client, body=None, **kwargs):
    return client.post(
        "/api/engines", json=body or _minimal_body(**kwargs), headers=_control_headers()
    )


def test_create_engine_returns_201_with_config(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 201
    body = res.json()
    assert body["repo"] == {"owner": "acme", "repo": "widget"}
    assert body["posture"] == "observe"


def test_create_engine_persists_and_is_listed(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines")
    assert res.status_code == 200
    slugs = {f"{e['repo']['owner']}/{e['repo']['repo']}" for e in res.json()["engines"]}
    assert slugs == {"acme/widget"}


def test_create_engine_duplicate_returns_409(client: TestClient) -> None:
    _create(client)
    res = _create(client)
    assert res.status_code == 409


def test_create_engine_without_preset_defaults_to_none(client: TestClient) -> None:
    res = _create(client)
    assert res.json()["preset"] is None


def test_create_engine_with_preset_field_persists_and_is_returned(
    client: TestClient,
) -> None:
    res = _create(client, body={**_minimal_body(), "preset": "dotnet"})
    assert res.status_code == 201
    assert res.json()["preset"] == "dotnet"
    res2 = client.get("/api/engines/acme/widget")
    assert res2.json()["preset"] == "dotnet"


def test_create_engine_invalid_posture_returns_422(client: TestClient) -> None:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}, "posture": "not-a-posture"},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_engine_bad_flow_graph_returns_422(client: TestClient) -> None:
    body = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": {
            "verifier_teams": ["verification"],
            "merge_teams": ["pr_merge"],
            "terminal_teams": ["deploy_verify"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
    }
    res = _create(client, body=body)
    assert res.status_code == 422


def test_create_engine_ignores_prompt_overrides_in_body(client: TestClient) -> None:
    res = _create(
        client,
        body={
            **_minimal_body(),
            "prompt_overrides": {
                "verification/judge": {
                    "text": "evil override with no required markers",
                    "base_fingerprint": "x",
                },
            },
        },
    )
    assert res.status_code == 201
    assert res.json()["prompt_overrides"] == {}
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["prompt_overrides"] == {}


def test_create_engine_self_merge_implementation_override_not_persisted_without_confirm(
    client: TestClient,
) -> None:
    res = _create(
        client,
        body={
            **_minimal_body(),
            "posture": "self_merge",
            "prompt_overrides": {
                "implementation/implementer": {
                    "text": "x {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}",
                    "base_fingerprint": "x",
                },
            },
        },
    )
    assert res.status_code == 201
    assert res.json()["prompt_overrides"] == {}


def test_create_engine_extra_field_rejected(client: TestClient) -> None:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}, "bogus_field": "x"},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_create_engine_without_control_token_returns_401(client: TestClient) -> None:
    res = client.post("/api/engines", json=_minimal_body())
    assert res.status_code == 401


def test_create_engine_wrong_control_token_returns_403(client: TestClient) -> None:
    res = client.post(
        "/api/engines",
        json=_minimal_body(),
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_create_engine_with_correct_control_token_succeeds(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 201


def test_list_engines_does_not_require_control_token(client: TestClient) -> None:
    res = client.get("/api/engines")
    assert res.status_code == 200


def test_get_engine_detail_does_not_require_control_token(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines/acme/widget")
    assert res.status_code == 200


def test_list_engines_empty_when_none_created(client: TestClient) -> None:
    res = client.get("/api/engines")
    assert res.status_code == 200
    assert res.json()["engines"] == []


def test_get_engine_detail_returns_config(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines/acme/widget")
    assert res.status_code == 200
    assert res.json()["repo"] == {"owner": "acme", "repo": "widget"}


def test_get_engine_detail_missing_returns_404(client: TestClient) -> None:
    res = client.get("/api/engines/no/such")
    assert res.status_code == 404


def test_get_engine_repo_status_returns_404_for_unknown_repo(client: TestClient) -> None:
    res = client.get("/api/engines/no/such/status")
    assert res.status_code == 404


def test_get_engine_repo_status_returns_all_unknown_for_forge_less_engine(
    client: TestClient,
) -> None:
    _create(client)
    res = client.get("/api/engines/acme/widget/status")
    assert res.status_code == 200
    body = res.json()
    assert body["is_default"] is False
    for key in ("main_ci", "open_prs", "cost", "delivery_time", "last_deploy", "health"):
        assert body[key]["provenance"]["status"] == "unknown"
    assert body["delivery_time"]["by_size"] == []


def test_get_engine_repo_status_returns_live_data_for_non_default_engine_own_credential(
    client: TestClient, state_root: Path
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    assert _create(client, body=_forge_body()).status_code == 201
    forge = ForgeTarget(
        repo=RepoIdentity(owner="acme", repo="widget"),
        credential_env_name=_FORGE_CREDENTIAL,
        authoritative_checks=frozenset(),
    )
    status = repo_status_mod.ForgeStatus(
        fetched_at=datetime.now(UTC).isoformat(),
        main_ci=repo_status_mod.MainCiField(
            provenance=repo_status_mod.Provenance(status="fresh", detail="x"),
            green_count=3,
            red_count=1,
            counted_over="all_checks",
        ),
        open_prs=repo_status_mod.OpenPrsField(
            provenance=repo_status_mod.Provenance(status="fresh", detail="y"),
            total=4,
            authored_by_alan=2,
        ),
    )
    repo_status_mod._write_forge_cache(forge, status)  # noqa: SLF001
    res = client.get("/api/engines/acme/widget/status")
    assert res.status_code == 200
    body = res.json()
    assert body["is_default"] is False
    assert body["main_ci"]["green_count"] == 3
    assert body["open_prs"]["total"] == 4
    assert body["open_prs"]["authored_by_alan"] == 2


def test_get_engine_repo_status_does_not_require_control_token(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines/acme/widget/status")
    assert res.status_code == 200


def test_create_engine_with_forge_rejects_unknown_credential(client: TestClient) -> None:
    res = _create(client, body=_forge_body())
    assert res.status_code == 422


def test_create_engine_with_forge_succeeds_when_secret_record_exists(
    client: TestClient,
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    res = _create(client, body=_forge_body())
    assert res.status_code == 201


def test_create_engine_without_forge_skips_credential_check(client: TestClient) -> None:
    res = _create(client)
    assert res.status_code == 201


def test_create_engine_never_uses_os_environ_for_credential_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_FORGE_CREDENTIAL, "env-value-should-be-ignored")
    res = _create(client, body=_forge_body())
    assert res.status_code == 422


def test_create_engine_with_preset_config_body_requires_credential_record(
    client: TestClient,
) -> None:
    preset_res = client.get(
        "/api/engine-presets/python/config", params={"owner": "acme", "repo": "widget"}
    )
    assert preset_res.status_code == 200
    res = _create(client, body=preset_res.json())
    assert res.status_code == 422


def _update(client, owner="acme", repo="widget", body=None, headers=None):
    return client.patch(
        f"/api/engines/{owner}/{repo}",
        json=body if body is not None else _minimal_body(owner, repo),
        headers=headers if headers is not None else _control_headers(),
    )


def test_update_engine_returns_200_but_ignores_posture_delta(client: TestClient) -> None:
    _create(client)
    res = _update(client, body={**_minimal_body(), "posture": "propose"})
    assert res.status_code == 200
    assert res.json()["posture"] == "observe"


def test_update_engine_does_not_persist_posture_change(client: TestClient) -> None:
    _create(client)
    _update(client, body={**_minimal_body(), "posture": "propose"})
    res = client.get("/api/engines/acme/widget")
    assert res.json()["posture"] == "observe"


def test_update_engine_same_posture_is_a_true_no_op(client: TestClient) -> None:
    _create(client)
    res = _update(client, body={**_minimal_body(), "posture": "observe"})
    assert res.status_code == 200
    assert res.json()["posture"] == "observe"


def test_update_engine_missing_returns_404(client: TestClient) -> None:
    res = _update(client)
    assert res.status_code == 404


def test_update_engine_repo_mismatch_returns_422(client: TestClient) -> None:
    _create(client)
    res = _update(client, body=_minimal_body(owner="other", repo="engine"))
    assert res.status_code == 422


def test_update_engine_ignores_prompt_overrides_delta_in_body(client: TestClient) -> None:
    _create(client)
    res = _update(
        client,
        body={
            **_minimal_body(),
            "prompt_overrides": {
                "verification/judge": {
                    "text": "evil override with no required markers",
                    "base_fingerprint": "x",
                },
            },
        },
    )
    assert res.status_code == 200
    assert res.json()["prompt_overrides"] == {}


def test_update_engine_does_not_persist_prompt_overrides_delta(client: TestClient) -> None:
    _create(client)
    _update(
        client,
        body={
            **_minimal_body(),
            "prompt_overrides": {
                "verification/judge": {
                    "text": "evil override with no required markers",
                    "base_fingerprint": "x",
                },
            },
        },
    )
    res = client.get("/api/engines/acme/widget")
    assert res.json()["prompt_overrides"] == {}


def test_update_engine_self_merge_implementation_override_not_persisted_without_confirm(
    client: TestClient,
) -> None:
    _create(client)
    _set_posture(client, body={"posture": "self_merge", "confirm": True})
    res = _update(
        client,
        body={
            **_minimal_body(),
            "posture": "self_merge",
            "prompt_overrides": {
                "implementation/implementer": {
                    "text": "x {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}",
                    "base_fingerprint": "x",
                },
            },
        },
    )
    assert res.status_code == 200
    assert res.json()["prompt_overrides"] == {}
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["prompt_overrides"] == {}


def test_update_engine_bad_flow_graph_returns_422(client: TestClient) -> None:
    _create(client)
    body = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": {
            "verifier_teams": ["verification"],
            "merge_teams": ["pr_merge"],
            "terminal_teams": ["deploy_verify"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
    }
    res = _update(client, body=body)
    assert res.status_code == 422


def test_update_engine_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _update(client, headers={})
    assert res.status_code == 401


def test_update_engine_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _update(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_update_engine_unknown_credential_returns_422(client: TestClient) -> None:
    _create(client)
    res = _update(client, body=_forge_body())
    assert res.status_code == 422


def test_update_engine_with_forge_succeeds_when_secret_record_exists(
    client: TestClient,
) -> None:
    _create(client)
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    res = _update(client, body=_forge_body())
    assert res.status_code == 200


def test_update_engine_422_after_credential_record_deleted(client: TestClient) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    assert _create(client, body=_forge_body()).status_code == 201
    assert secrets_store.delete_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE)
    res = _update(client, body=_forge_body())
    assert res.status_code == 422


def test_update_engine_bad_flow_graph_rejects_with_validator_message_and_no_persist(
    client: TestClient,
) -> None:
    _create(client)
    body = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": {
            "verifier_teams": ["verification"],
            "merge_teams": ["pr_merge"],
            "terminal_teams": ["deploy_verify"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
    }
    res = _update(client, body=body)
    assert res.status_code == 422
    assert res.json()["detail"] == (
        "merge team 'pr_merge' reachable from non-verifier teams: frozenset({'implementation'})"
    )
    persisted = client.get("/api/engines/acme/widget").json()
    assert persisted["transition_graph"] == {}
    assert persisted["flow_policy"] is None


_ROSTER_FLOW_POLICY = {
    "verifier_teams": ["verifier"],
    "merge_teams": ["merger"],
    "terminal_teams": ["done"],
    "internal_only_teams": [],
    "restricted_predecessors": {},
    "revisit_cap": 3,
}
_ROSTER_BASE_GRAPH = {
    "impl": ["verifier"],
    "impl_fix": ["verifier"],
    "verifier": ["impl_fix", "merger"],
    "merger": ["done"],
    "done": [],
}


def test_update_engine_excludes_team_from_roster_and_default_engine_unaffected(
    client: TestClient,
) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)

    body_with_scout = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": {**_ROSTER_BASE_GRAPH, "scout": ["impl"]},
        "flow_policy": _ROSTER_FLOW_POLICY,
    }
    _create(client, body=body_with_scout)

    res = _update(client, body={**body_with_scout, "transition_graph": _ROSTER_BASE_GRAPH})

    assert res.status_code == 200
    assert "scout" not in res.json()["transition_graph"]
    persisted = client.get("/api/engines/acme/widget").json()
    assert "scout" not in persisted["transition_graph"]

    default_now = engine_config_mod.lookup_by_repo(default_cfg.repo)
    assert default_now is not None
    assert default_now.transition_graph == default_cfg.transition_graph


def test_update_engine_excluding_team_breaks_flow_graph_returns_422_naming_team(
    client: TestClient,
) -> None:
    body = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": _ROSTER_BASE_GRAPH,
        "flow_policy": _ROSTER_FLOW_POLICY,
    }
    _create(client, body=body)

    broken_graph = {k: v for k, v in _ROSTER_BASE_GRAPH.items() if k != "verifier"}
    res = _update(client, body={**body, "transition_graph": broken_graph})

    assert res.status_code == 422
    assert "merger" in res.json()["detail"]
    persisted = client.get("/api/engines/acme/widget").json()
    assert persisted["transition_graph"] == _ROSTER_BASE_GRAPH


def test_update_engine_ignores_body_posture_and_uses_live_registry_value(
    client: TestClient,
) -> None:
    _create(client)
    _set_posture(client, body={"posture": "self_merge", "confirm": True})
    res = _update(client, body={**_minimal_body(), "posture": "observe"})
    assert res.status_code == 200
    assert res.json()["posture"] == "self_merge"
    assert client.get("/api/engines/acme/widget").json()["posture"] == "self_merge"


def test_update_engine_single_field_edit_preserves_all_other_fields(
    client: TestClient,
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    body = _full_config_body()
    created = _create(client, body=body).json()
    assert created["pool_size"] == 3

    res = _update(client, body={**body, "pool_size": 5})
    assert res.status_code == 200
    updated = res.json()

    assert updated["pool_size"] == 5
    created_rest = {k: v for k, v in created.items() if k != "pool_size"}
    updated_rest = {k: v for k, v in updated.items() if k != "pool_size"}
    assert updated_rest == created_rest


def test_update_engine_empty_command_lists_persist_as_empty(client: TestClient) -> None:
    body = {
        **_minimal_body(),
        "gate_commands": [["make", "test"]],
        "warmup_commands": [["make", "setup"]],
    }
    _create(client, body=body)

    res = _update(
        client, body={**_minimal_body(), "gate_commands": [], "warmup_commands": []}
    )
    assert res.status_code == 200
    assert res.json()["gate_commands"] == []
    assert res.json()["warmup_commands"] == []

    persisted = client.get("/api/engines/acme/widget").json()
    assert persisted["gate_commands"] == []
    assert persisted["warmup_commands"] == []


def test_update_engine_noop_patch_roundtrips_authoritative_checks_stably(
    client: TestClient,
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    body = _forge_body()
    body["forge"]["authoritative_checks"] = ["Check A", "Check B", "Check C", "Check D"]
    created = _create(client, body=body).json()
    expected_checks = {"Check A", "Check B", "Check C", "Check D"}
    assert set(created["forge"]["authoritative_checks"]) == expected_checks

    fetched = client.get("/api/engines/acme/widget").json()
    res = _update(client, body=fetched)
    assert res.status_code == 200
    assert set(res.json()["forge"]["authoritative_checks"]) == expected_checks

    persisted = client.get("/api/engines/acme/widget").json()
    assert set(persisted["forge"]["authoritative_checks"]) == expected_checks


def _set_posture(client, owner="acme", repo="widget", body=None, headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/posture",
        json=body if body is not None else {"posture": "propose", "confirm": True},
        headers=headers if headers is not None else _control_headers(),
    )


def test_set_posture_valid_returns_200_with_updated_config(client: TestClient) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "propose", "confirm": True})
    assert res.status_code == 200
    assert res.json()["posture"] == "propose"


def test_set_posture_persists_change(client: TestClient) -> None:
    _create(client)
    _set_posture(client, body={"posture": "autonomous", "confirm": True})
    res = client.get("/api/engines/acme/widget")
    assert res.json()["posture"] == "autonomous"


def test_set_posture_missing_confirm_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "propose"})
    assert res.status_code == 422


def test_set_posture_confirm_false_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "propose", "confirm": False})
    assert res.status_code == 422


def test_set_posture_invalid_enum_returns_422(client: TestClient) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "god_mode", "confirm": True})
    assert res.status_code == 422


def test_set_posture_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _set_posture(client, headers={})
    assert res.status_code == 401


def test_set_posture_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _set_posture(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_set_posture_missing_engine_returns_404(client: TestClient) -> None:
    res = _set_posture(client)
    assert res.status_code == 404


def test_set_posture_raise_appends_exactly_one_audit_entry_with_source_api(
    client: TestClient, engines_root: Path
) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "propose", "confirm": True})
    assert res.status_code == 200
    audit_path = engines_root / "posture_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["repo"] == "acme/widget"
    assert entry["from"] == "observe"
    assert entry["to"] == "propose"
    assert entry["source"] == "api"


def test_set_posture_no_op_appends_no_audit_entry_and_still_returns_200(
    client: TestClient, engines_root: Path
) -> None:
    _create(client)
    res = _set_posture(client, body={"posture": "observe", "confirm": True})
    assert res.status_code == 200
    assert res.json()["posture"] == "observe"
    audit_path = engines_root / "posture_audit.jsonl"
    assert not audit_path.exists()


def _preflight(client, owner="acme", repo="widget", headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/preflight",
        headers=headers if headers is not None else _control_headers(),
    )


def _fake_preflight(cfg: EngineConfig) -> PreflightResult:
    return PreflightResult(
        slug=cfg.repo.slug,
        checked_at="2026-07-21T00:00:00+00:00",
        overall="pass",
        findings=(Finding(check="author_token_valid", status="pass", message="ok"),),
    )


def test_preflight_returns_200_with_result_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "run_preflight", _fake_preflight)
    res = _preflight(client)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["overall"] == "pass"
    assert body["findings"] == [
        {
            "check": "author_token_valid", "status": "pass", "message": "ok",
            "remedy": "", "remedy_url": "",
        }
    ]


def test_preflight_missing_engine_returns_404(client: TestClient) -> None:
    res = _preflight(client)
    assert res.status_code == 404


def test_preflight_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _preflight(client, headers={})
    assert res.status_code == 401


def test_preflight_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _preflight(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_preflight_emits_preflight_completed_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "run_preflight", _fake_preflight)
    events = _subscribe(client)
    res = _preflight(client)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "preflight_completed"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def _get_preflight(client, owner="acme", repo="widget", headers=None):
    return client.get(
        f"/api/engines/{owner}/{repo}/preflight",
        headers=headers if headers is not None else {},
    )


def test_get_engine_preflight_returns_persisted_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    fake_result = _fake_preflight(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    monkeypatch.setattr(engines_route_mod, "load_last_preflight", lambda forge: fake_result)
    res = _get_preflight(client)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["overall"] == "pass"
    assert body["findings"] == [
        {
            "check": "author_token_valid", "status": "pass", "message": "ok",
            "remedy": "", "remedy_url": "",
        }
    ]


def test_get_engine_preflight_returns_null_when_never_preflighted(
    client: TestClient,
) -> None:
    _create(client)
    res = _get_preflight(client)
    assert res.status_code == 200
    assert res.json() is None


def test_get_engine_preflight_missing_engine_returns_404(client: TestClient) -> None:
    res = _get_preflight(client)
    assert res.status_code == 404


def test_get_engine_preflight_does_not_require_control_token(client: TestClient) -> None:
    _create(client)
    res = _get_preflight(client, headers={})
    assert res.status_code == 200


def test_get_engine_preflight_ignores_wrong_control_token(client: TestClient) -> None:
    _create(client)
    res = _get_preflight(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 200


_DOC_ANCHORS = (
    "## Vision\n\nShip it\n\n## Scope/Anti-scope\n\n\n"
    "## Decision Log\n\n\n## Discovered Facts\n\n"
)


def test_project_context_for_returns_empty_when_no_project_bound(
    projects_root: Path,
) -> None:
    title, doc = engines_route_mod._project_context_for(  # noqa: SLF001
        RepoIdentity(owner="acme", repo="widget")
    )
    assert (title, doc) == ("", "")


def test_project_context_for_returns_title_and_doc_text(projects_root: Path) -> None:
    project = project_mod.create("Widget Co")
    project = project_mod.update(project, {"engine_key": "acme/widget"})
    project, doc_path = project_doc_mod.ensure_doc(project)
    doc_path.write_text(_DOC_ANCHORS)

    title, doc = engines_route_mod._project_context_for(  # noqa: SLF001
        RepoIdentity(owner="acme", repo="widget")
    )
    assert title == "Widget Co"
    assert "Ship it" in doc


def test_project_context_for_handles_missing_doc_file(projects_root: Path) -> None:
    project = project_mod.create("Widget Co")
    project_mod.update(project, {"engine_key": "acme/widget"})

    title, doc = engines_route_mod._project_context_for(  # noqa: SLF001
        RepoIdentity(owner="acme", repo="widget")
    )
    assert title == "Widget Co"
    assert doc == ""


def test_project_context_for_handles_invalid_doc_ref(projects_root: Path) -> None:
    project = project_mod.create("Widget Co")
    project_mod.update(project, {"engine_key": "acme/widget", "doc_ref": "../evil.md"})

    title, doc = engines_route_mod._project_context_for(  # noqa: SLF001
        RepoIdentity(owner="acme", repo="widget")
    )
    assert title == "Widget Co"
    assert doc == ""


def test_project_for_engine_ignores_projects_bound_to_other_engines(
    projects_root: Path,
) -> None:
    other = project_mod.create("Alpha Other")
    project_mod.update(other, {"engine_key": "acme/other"})
    mine = project_mod.create("Zulu Mine")
    project_mod.update(mine, {"engine_key": "acme/widget"})

    found = engines_route_mod._project_for_engine(  # noqa: SLF001
        RepoIdentity(owner="acme", repo="widget")
    )
    assert found is not None
    assert found.title == "Zulu Mine"


def _scaffold(client, owner="acme", repo="widget", headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/scaffold",
        headers=headers if headers is not None else _control_headers(),
    )


def _onboarding_state(**overrides: Any) -> repo_scaffold.OnboardingState:
    base: dict[str, Any] = {"slug": "acme/widget"}
    base.update(overrides)
    return repo_scaffold.OnboardingState(**base)


def test_scaffold_engine_returns_200_with_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    state = _onboarding_state(scaffold_pr_number=7, stack="python")
    monkeypatch.setattr(repo_scaffold, "scaffold", lambda cfg, **kw: state)  # noqa: ARG005
    res = _scaffold(client)
    assert res.status_code == 200
    assert res.json()["scaffold_pr_number"] == 7
    assert res.json()["stack"] == "python"


def test_scaffold_engine_missing_engine_returns_404(client: TestClient) -> None:
    res = _scaffold(client)
    assert res.status_code == 404


def test_scaffold_engine_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _scaffold(client, headers={})
    assert res.status_code == 401


def test_scaffold_engine_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _scaffold(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_scaffold_engine_scaffold_error_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)

    def _raise(cfg, **kw):
        del cfg, kw
        raise repo_scaffold.ScaffoldError("no transport credential for 'acme/widget'")

    monkeypatch.setattr(repo_scaffold, "scaffold", _raise)
    res = _scaffold(client)
    assert res.status_code == 422
    assert "no transport credential" in res.json()["detail"]


def test_scaffold_engine_emits_engine_scaffolded_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(
        repo_scaffold, "scaffold", lambda cfg, **kw: _onboarding_state()  # noqa: ARG005
    )
    events = _subscribe(client)
    res = _scaffold(client)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_scaffolded"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_scaffold_engine_passes_project_title_and_doc(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, projects_root: Path
) -> None:
    _create(client)
    project = project_mod.create("Widget Co")
    project = project_mod.update(project, {"engine_key": "acme/widget"})
    project, doc_path = project_doc_mod.ensure_doc(project)
    doc_path.write_text(_DOC_ANCHORS)

    captured: dict[str, str] = {}

    def _fake_scaffold(cfg, *, project_title, project_doc):
        del cfg
        captured["title"] = project_title
        captured["doc"] = project_doc
        return _onboarding_state()

    monkeypatch.setattr(repo_scaffold, "scaffold", _fake_scaffold)
    res = _scaffold(client)
    assert res.status_code == 200
    assert captured["title"] == "Widget Co"
    assert "Ship it" in captured["doc"]


def _protect(client, owner="acme", repo="widget", headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/protect",
        headers=headers if headers is not None else _control_headers(),
    )


def test_protect_engine_returns_200_with_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    state = _onboarding_state(protection_applied_keys=["squash_merge_enabled"])
    monkeypatch.setattr(repo_scaffold, "protect", lambda cfg: state)
    res = _protect(client)
    assert res.status_code == 200
    assert res.json()["protection_applied_keys"] == ["squash_merge_enabled"]


def test_protect_engine_missing_engine_returns_404(client: TestClient) -> None:
    res = _protect(client)
    assert res.status_code == 404


def test_protect_engine_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _protect(client, headers={})
    assert res.status_code == 401


def test_protect_engine_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _protect(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_protect_engine_scaffold_error_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)

    def _raise(cfg):
        del cfg
        raise repo_scaffold.ScaffoldError("engine 'acme/widget' has no forge configured")

    monkeypatch.setattr(repo_scaffold, "protect", _raise)
    res = _protect(client)
    assert res.status_code == 422
    assert "no forge configured" in res.json()["detail"]


def test_protect_engine_emits_engine_protected_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(repo_scaffold, "protect", lambda cfg: _onboarding_state())
    events = _subscribe(client)
    res = _protect(client)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_protected"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def _get_onboarding(client, owner="acme", repo="widget", headers=None):
    return client.get(
        f"/api/engines/{owner}/{repo}/onboarding",
        headers=headers if headers is not None else {},
    )


def test_get_engine_onboarding_missing_engine_returns_404(client: TestClient) -> None:
    res = _get_onboarding(client)
    assert res.status_code == 404


def test_get_engine_onboarding_does_not_require_control_token(
    client: TestClient, state_root: Path
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    _create(client, body=_forge_body())
    res = _get_onboarding(client, headers={})
    assert res.status_code == 200


def test_get_engine_onboarding_default_state_checklist_never_shorter_than_floor(
    client: TestClient, state_root: Path
) -> None:
    secrets_store.write_secret("acme/widget", secrets_store.TRANSPORT_PURPOSE, "ghp_abc")
    _create(client, body=_forge_body())
    res = _get_onboarding(client)
    assert res.status_code == 200
    body = res.json()
    assert body["state"]["slug"] == "acme/widget"
    checks = {f["check"] for f in body["checklist"]}
    assert checks == {
        "push_repository", "mint_credentials", "workflow_write",
        "grant_reviewer_collaborator_access", "review_and_merge_scaffold_pr",
    }
    assert body["overall"] in {"pass", "fail", "unknown"}


def test_get_engine_onboarding_no_forge_returns_422(client: TestClient) -> None:
    _create(client)
    res = _get_onboarding(client)
    assert res.status_code == 422
    assert "no forge configured" in res.json()["detail"]


def test_get_engine_onboarding_reflects_persisted_state_and_checklist(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    state = _onboarding_state(scaffold_pr_number=3)
    checklist = (Finding(check="push_repository", status="pass", message="ok"),)
    monkeypatch.setattr(repo_scaffold, "onboarding_status", lambda cfg: state)
    monkeypatch.setattr(repo_scaffold, "build_checklist", lambda cfg, st: checklist)
    res = _get_onboarding(client)
    assert res.status_code == 200
    body = res.json()
    assert body["state"]["scaffold_pr_number"] == 3
    assert body["checklist"] == [
        {"check": "push_repository", "status": "pass", "message": "ok",
         "remedy": "", "remedy_url": ""}
    ]
    assert body["overall"] == "pass"


def _dep_sweep(client, owner="acme", repo="widget", enabled=True, headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/dependency-sweep",
        json={"enabled": enabled},
        headers=headers if headers is not None else _control_headers(),
    )


def test_dep_sweep_enable_persists_when_no_blocking_finding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "check_dependency_sweep_enable", lambda cfg: None)
    res = _dep_sweep(client, enabled=True)
    assert res.status_code == 200
    body = res.json()
    assert body["dependency_sweep"]["enabled"] is True
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["dependency_sweep"]["enabled"] is True


def test_dep_sweep_enable_refused_with_finding_when_check_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    finding = Finding(
        check="dependabot_config_present",
        status="fail",
        message=".github/dependabot.yml not found in acme/widget",
        remedy="add a .github/dependabot.yml file to the repository",
    )
    monkeypatch.setattr(
        engines_route_mod, "check_dependency_sweep_enable", lambda cfg: finding
    )
    res = _dep_sweep(client, enabled=True)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["check"] == "dependabot_config_present"
    assert "dependabot.yml" in detail["message"]
    assert "dependabot.yml" in detail["remedy"]
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["dependency_sweep"]["enabled"] is False


def test_dep_sweep_disable_never_runs_enable_checks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)

    def _boom(cfg: EngineConfig) -> None:
        raise AssertionError("disabling must never run the enable-gate checks")

    monkeypatch.setattr(engines_route_mod, "check_dependency_sweep_enable", _boom)
    res = _dep_sweep(client, enabled=False)
    assert res.status_code == 200
    assert res.json()["dependency_sweep"]["enabled"] is False


def test_dep_sweep_missing_engine_returns_404(client: TestClient) -> None:
    res = _dep_sweep(client)
    assert res.status_code == 404


def test_dep_sweep_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _dep_sweep(client, headers={})
    assert res.status_code == 401


def test_dep_sweep_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _dep_sweep(client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"})
    assert res.status_code == 403


def test_dep_sweep_enable_emits_dep_sweep_changed_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "check_dependency_sweep_enable", lambda cfg: None)
    events = _subscribe(client)
    res = _dep_sweep(client, enabled=True)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "dep_sweep_changed"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def _get_dep_sweep(client, owner="acme", repo="widget", headers=None):
    return client.get(
        f"/api/engines/{owner}/{repo}/dependency-sweep",
        headers=headers if headers is not None else {},
    )


def test_get_dep_sweep_status_returns_disabled_never_run_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "load_dependency_sweep_status", lambda forge: None)
    res = _get_dep_sweep(client)
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "slug": "acme/widget",
        "enabled": False,
        "last_run_at": None,
        "next_run_at": None,
    }


def test_get_dep_sweep_status_reflects_enabled_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engines_route_mod, "check_dependency_sweep_enable", lambda cfg: None)
    _dep_sweep(client, enabled=True)
    res = _get_dep_sweep(client)
    assert res.status_code == 200
    assert res.json()["enabled"] is True


def test_get_dep_sweep_status_returns_persisted_last_and_next_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    fake_status = dependency_sweep_mod.DependencySweepStatus(
        slug="acme/widget",
        enabled=True,
        last_run_at="2026-07-20T00:00:00+00:00",
        next_run_at="2026-07-27T00:00:00+00:00",
    )
    monkeypatch.setattr(
        engines_route_mod, "load_dependency_sweep_status", lambda forge: fake_status
    )
    res = _get_dep_sweep(client)
    assert res.status_code == 200
    body = res.json()
    assert body["last_run_at"] == "2026-07-20T00:00:00+00:00"
    assert body["next_run_at"] == "2026-07-27T00:00:00+00:00"


def test_get_dep_sweep_status_missing_engine_returns_404(client: TestClient) -> None:
    res = _get_dep_sweep(client)
    assert res.status_code == 404


def test_get_dep_sweep_status_does_not_require_control_token(client: TestClient) -> None:
    _create(client)
    res = _get_dep_sweep(client, headers={})
    assert res.status_code == 200


def test_dep_sweep_opt_out_then_opt_in_stays_green_for_alan_default_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)
    monkeypatch.setattr(secrets_store, "resolve_secret", lambda *a, **kw: "tok")  # noqa: ARG005
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),  # noqa: ARG005
    )
    owner, repo = default_cfg.repo.owner, default_cfg.repo.repo

    res_out = client.post(
        f"/api/engines/{owner}/{repo}/dependency-sweep",
        json={"enabled": False},
        headers=_control_headers(),
    )
    assert res_out.status_code == 200
    assert res_out.json()["dependency_sweep"]["enabled"] is False

    res_in = client.post(
        f"/api/engines/{owner}/{repo}/dependency-sweep",
        json={"enabled": True},
        headers=_control_headers(),
    )
    assert res_in.status_code == 200
    assert res_in.json()["dependency_sweep"]["enabled"] is True


def test_delete_engine_returns_204_and_removes_it(client: TestClient) -> None:
    _create(client)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 204
    assert client.get("/api/engines/acme/widget").status_code == 404


def test_delete_engine_missing_returns_404(client: TestClient) -> None:
    res = client.delete("/api/engines/no/such", headers=_control_headers())
    assert res.status_code == 404


def test_delete_engine_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = client.delete("/api/engines/acme/widget")
    assert res.status_code == 401


def test_delete_engine_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = client.delete(
        "/api/engines/acme/widget",
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def _make_ws(
    workspaces_root: Path,
    slug: str,
    status: workspace_mod.Status,
    *,
    engine_key: str | None = "acme/widget",
) -> workspace_mod.Workspace:
    ws = workspace_mod.create(slug, "d", root=workspaces_root, engine_key=engine_key)
    workspace_mod.transition(ws, status)
    return workspace_mod.read(ws.task_id, root=workspaces_root)


def test_delete_engine_blocked_by_active_workspace_returns_409(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    ws = _make_ws(workspaces_root, "active-ws", workspace_mod.Status.ACTIVE)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409
    assert res.json()["detail"]["blocking_workspace_ids"] == [ws.task_id]
    assert client.get("/api/engines/acme/widget").status_code == 200


def test_delete_engine_blocked_by_awaiting_input_workspace(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "waiting-ws", workspace_mod.Status.AWAITING_INPUT)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_blocked_by_paused_workspace(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "paused-ws", workspace_mod.Status.PAUSED)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_blocked_by_escalated_workspace(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "escalated-ws", workspace_mod.Status.ESCALATED)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_blocked_by_failed_workspace(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "failed-ws", workspace_mod.Status.FAILED)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_blocked_by_partial_workspace(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "partial-ws", workspace_mod.Status.PARTIAL)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_archived_workspace_does_not_block(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "archived-ws", workspace_mod.Status.ARCHIVED)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 204


def test_delete_engine_terminal_workspace_does_not_block(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "done-ws", workspace_mod.Status.COMPLETED)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 204


def test_delete_engine_force_bypasses_workspace_block(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "active-ws", workspace_mod.Status.ACTIVE)
    res = client.delete("/api/engines/acme/widget?force=true", headers=_control_headers())
    assert res.status_code == 204


def test_delete_engine_force_busy_still_409(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher, workspaces_root: Path
) -> None:
    _create(client_with_dispatcher)
    _make_ws(workspaces_root, "active-ws", workspace_mod.Status.ACTIVE)
    pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001
    idx, token = pool._claim()  # noqa: SLF001
    try:
        res = client_with_dispatcher.delete(
            "/api/engines/acme/widget?force=true", headers=_control_headers()
        )
        assert res.status_code == 409
    finally:
        pool._release(idx, token)  # noqa: SLF001
    assert client_with_dispatcher.get("/api/engines/acme/widget").status_code == 200
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_delete_engine_blocked_via_engine_snapshot_reference(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    cfg = EngineConfig.model_validate(client.get("/api/engines/acme/widget").json())
    ws = workspace_mod.create("snap-ws", "d", root=workspaces_root)
    snapshot = workspace_mod.EngineSnapshot(
        posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)
    workspace_mod.transition(ws, workspace_mod.Status.ACTIVE)

    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 409


def test_delete_engine_concurrent_removal_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(engine_config_mod, "unregister_and_persist", lambda repo: False)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 404


def test_delete_engine_refuses_current_default_even_with_force(
    client: TestClient,
) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)

    res = client.delete(
        f"/api/engines/{default_cfg.repo.owner}/{default_cfg.repo.repo}?force=true",
        headers=_control_headers(),
    )
    assert res.status_code == 409
    assert engine_config_mod.lookup_by_repo(default_cfg.repo) is not None


def test_create_engine_registers_pool_when_dispatcher_present(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    res = _create(client_with_dispatcher)
    assert res.status_code == 201
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_update_engine_same_pool_size_does_not_touch_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    existing_pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001

    res = _update(client_with_dispatcher, body={**_minimal_body(), "posture": "propose"})

    assert res.status_code == 200
    assert dispatcher._slot_pools_by_engine["acme/widget"] is existing_pool  # noqa: SLF001


def test_update_engine_invalid_flow_graph_does_not_dispose_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    old_pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001

    body = {
        **_minimal_body(),
        "pool_size": 4,
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": {
            "verifier_teams": ["verification"],
            "merge_teams": ["pr_merge"],
            "terminal_teams": ["deploy_verify"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
    }
    res = _update(client_with_dispatcher, body=body)

    assert res.status_code == 422
    assert dispatcher._slot_pools_by_engine["acme/widget"] is old_pool  # noqa: SLF001


def test_update_engine_pool_size_change_disposes_and_recreates_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    old_pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001

    res = _update(client_with_dispatcher, body={**_minimal_body(), "pool_size": 4})

    assert res.status_code == 200
    assert res.json()["pool_size"] == 4
    new_pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001
    assert new_pool is not old_pool
    assert new_pool._size == 4  # noqa: SLF001


def test_update_engine_pool_resize_busy_returns_409_and_config_unchanged(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001
    idx, token = pool._claim()  # noqa: SLF001
    try:
        res = _update(client_with_dispatcher, body={**_minimal_body(), "pool_size": 4})
        assert res.status_code == 409
    finally:
        pool._release(idx, token)  # noqa: SLF001
    assert client_with_dispatcher.get("/api/engines/acme/widget").json()["pool_size"] is None


def test_update_engine_pool_resize_dispose_incomplete_returns_500(
    client_with_dispatcher: TestClient,
    dispatcher: Dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client_with_dispatcher)

    def _boom(slug: str) -> None:
        raise PoolDisposeIncompleteError(f"could not remove pool for {slug}")

    monkeypatch.setattr(dispatcher, "dispose_pool", _boom)
    res = _update(client_with_dispatcher, body={**_minimal_body(), "pool_size": 4})
    assert res.status_code == 500


def test_patch_pool_resize_does_not_revert_concurrent_posture_downgrade(
    client_with_dispatcher: TestClient,
    dispatcher: Dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client_with_dispatcher, body={**_minimal_body(), "pool_size": 2})
    assert _set_posture(
        client_with_dispatcher, body={"posture": "self_merge", "confirm": True}
    ).status_code == 200

    entered_resize = threading.Event()
    release_resize = threading.Event()
    real_dispose_pool = dispatcher.dispose_pool

    def _blocking_dispose_pool(slug: str) -> None:
        entered_resize.set()
        assert release_resize.wait(timeout=5)
        real_dispose_pool(slug)

    monkeypatch.setattr(dispatcher, "dispose_pool", _blocking_dispose_pool)

    patch_response: Response | None = None

    def _do_patch() -> None:
        nonlocal patch_response
        patch_response = _update(
            client_with_dispatcher, body={**_minimal_body(), "pool_size": 4}
        )

    patch_thread = threading.Thread(target=_do_patch)
    patch_thread.start()
    try:
        assert entered_resize.wait(timeout=5)

        downgrade_res = _set_posture(
            client_with_dispatcher, body={"posture": "observe", "confirm": True}
        )
        assert downgrade_res.status_code == 200
    finally:
        release_resize.set()
        patch_thread.join(timeout=5)

    assert patch_response is not None
    assert patch_response.status_code == 200
    assert patch_response.json()["posture"] == "observe"
    assert client_with_dispatcher.get("/api/engines/acme/widget").json()["posture"] == "observe"


def test_update_engine_default_engine_pool_size_change_does_not_hijack_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher, workspaces_root: Path
) -> None:
    default_cfg = engine_config_mod.default_engine_config()
    engine_config_mod.register(default_cfg, default=True, validate_env=False)

    res = _update(
        client_with_dispatcher,
        owner=default_cfg.repo.owner,
        repo=default_cfg.repo.repo,
        body={
            "repo": {"owner": default_cfg.repo.owner, "repo": default_cfg.repo.repo},
            "pool_size": 4,
        },
    )

    assert res.status_code == 200
    assert res.json()["pool_size"] == 4
    assert default_cfg.repo.slug not in dispatcher._slot_pools_by_engine  # noqa: SLF001
    ws = workspace_mod.create("default-engine-ws", "d", root=workspaces_root)
    assert dispatcher._pool_for_workspace(ws) is dispatcher._slot_pool  # noqa: SLF001


def test_delete_engine_disposes_pool(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001

    res = client_with_dispatcher.delete(
        "/api/engines/acme/widget", headers=_control_headers()
    )

    assert res.status_code == 204
    assert "acme/widget" not in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_delete_engine_pool_busy_returns_409_and_registry_survives(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    pool = dispatcher._slot_pools_by_engine["acme/widget"]  # noqa: SLF001
    idx, token = pool._claim()  # noqa: SLF001
    try:
        res = client_with_dispatcher.delete(
            "/api/engines/acme/widget", headers=_control_headers()
        )
        assert res.status_code == 409
    finally:
        pool._release(idx, token)  # noqa: SLF001
    assert client_with_dispatcher.get("/api/engines/acme/widget").status_code == 200
    assert "acme/widget" in dispatcher._slot_pools_by_engine  # noqa: SLF001


def test_delete_engine_pool_dispose_incomplete_returns_500_and_registry_survives(
    client_with_dispatcher: TestClient,
    dispatcher: Dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client_with_dispatcher)

    def _boom(slug: str) -> None:
        raise PoolDisposeIncompleteError(f"could not remove pool for {slug}")

    monkeypatch.setattr(dispatcher, "dispose_pool", _boom)
    res = client_with_dispatcher.delete(
        "/api/engines/acme/widget", headers=_control_headers()
    )
    assert res.status_code == 500
    assert client_with_dispatcher.get("/api/engines/acme/widget").status_code == 200


def test_get_engine_pool_404_when_no_dispatcher(client: TestClient) -> None:
    _create(client)
    res = client.get("/api/engines/acme/widget/pool")
    assert res.status_code == 404


def test_get_engine_pool_404_when_engine_not_registered(
    client_with_dispatcher: TestClient,
) -> None:
    res = client_with_dispatcher.get("/api/engines/no/such/pool")
    assert res.status_code == 404


def test_get_engine_pool_404_when_no_pool_active_for_engine(
    client_with_dispatcher: TestClient,
) -> None:
    engine_config_mod.register_and_persist(
        EngineConfig(repo=RepoIdentity(owner="acme", repo="nopool"))
    )
    res = client_with_dispatcher.get("/api/engines/acme/nopool/pool")
    assert res.status_code == 404


def test_get_engine_pool_returns_state(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)

    res = client_with_dispatcher.get("/api/engines/acme/widget/pool")

    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["size"] == 2
    assert body["leased_count"] == 0
    assert body["free_count"] == 2
    assert isinstance(body["warm"], bool)
    assert body["quiescing"] is False
    assert isinstance(body["disk_usage_bytes"], int)
    assert "root" not in body


def test_get_engine_pool_returns_null_warmup_seconds_when_never_measured(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    res = client_with_dispatcher.get("/api/engines/acme/widget/pool")
    assert res.status_code == 200
    assert res.json()["warmup_seconds"] is None


def test_get_engine_pool_returns_persisted_warmup_seconds(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    engine_registry_store.write_warmup_seconds("acme/widget", 12.5)

    res = client_with_dispatcher.get("/api/engines/acme/widget/pool")

    assert res.status_code == 200
    assert res.json()["warmup_seconds"] == 12.5


def test_get_engine_pool_warmup_seconds_survives_pool_resize(
    client_with_dispatcher: TestClient, dispatcher: Dispatcher
) -> None:
    _create(client_with_dispatcher)
    engine_registry_store.write_warmup_seconds("acme/widget", 8.0)
    _update(client_with_dispatcher, body={**_minimal_body(), "pool_size": 4})

    res = client_with_dispatcher.get("/api/engines/acme/widget/pool")

    assert res.status_code == 200
    assert res.json()["warmup_seconds"] == 8.0


def _pinned_work(client, owner="acme", repo="widget"):
    return client.get(f"/api/engines/{owner}/{repo}/pinned-work")


def test_pinned_work_missing_engine_returns_404(client: TestClient) -> None:
    res = _pinned_work(client)
    assert res.status_code == 404


def test_pinned_work_does_not_require_control_token(client: TestClient) -> None:
    _create(client)
    res = _pinned_work(client)
    assert res.status_code == 200


def test_pinned_work_empty_when_no_workspaces(client: TestClient) -> None:
    _create(client)
    res = _pinned_work(client)
    assert res.status_code == 200
    assert res.json() == {"count": 0, "workspace_ids": []}


def test_pinned_work_counts_engine_key_referenced_non_terminal_workspaces(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    ws = _make_ws(workspaces_root, "active-ws", workspace_mod.Status.ACTIVE)
    res = _pinned_work(client)
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["workspace_ids"] == [ws.task_id]


def test_pinned_work_counts_snapshot_referenced_workspaces_without_undercounting(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    cfg = EngineConfig.model_validate(client.get("/api/engines/acme/widget").json())
    ws = workspace_mod.create(
        "snap-ws", "d", root=workspaces_root, engine_key="other/engine"
    )
    snapshot = workspace_mod.EngineSnapshot(
        posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
    )
    ws = workspace_mod.stamp_engine_snapshot(ws, snapshot)
    workspace_mod.transition(ws, workspace_mod.Status.ACTIVE)

    res = _pinned_work(client)
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["workspace_ids"] == [ws.task_id]


def test_pinned_work_excludes_terminal_workspaces(
    client: TestClient, workspaces_root: Path
) -> None:
    _create(client)
    _make_ws(workspaces_root, "done-ws", workspace_mod.Status.COMPLETED)
    res = _pinned_work(client)
    assert res.status_code == 200
    assert res.json() == {"count": 0, "workspace_ids": []}


def test_pinned_work_ignores_wrong_control_token(client: TestClient) -> None:
    _create(client)
    res = client.get(
        "/api/engines/acme/widget/pinned-work",
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 200


def test_create_engine_emits_engine_created_event(client: TestClient) -> None:
    events = _subscribe(client)
    res = _create(client)
    assert res.status_code == 201
    matches = [e for e in events if e.kind == "engine_created"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_update_engine_emits_engine_updated_event(client: TestClient) -> None:
    _create(client)
    events = _subscribe(client)
    res = _update(client, body={**_minimal_body(), "posture": "propose"})
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_updated"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_set_posture_emits_engine_posture_changed_event(client: TestClient) -> None:
    _create(client)
    events = _subscribe(client)
    res = _set_posture(client, body={"posture": "propose", "confirm": True})
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_posture_changed"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_delete_engine_emits_engine_deleted_event(client: TestClient) -> None:
    _create(client)
    events = _subscribe(client)
    res = client.delete("/api/engines/acme/widget", headers=_control_headers())
    assert res.status_code == 204
    matches = [e for e in events if e.kind == "engine_deleted"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_update_engine_pool_resize_emits_both_config_and_pool_events(
    client_with_dispatcher: TestClient,
) -> None:
    _create(client_with_dispatcher)
    events = _subscribe(client_with_dispatcher)
    res = _update(client_with_dispatcher, body={**_minimal_body(), "pool_size": 4})
    assert res.status_code == 200
    kinds = [e.kind for e in events]
    assert "engine_updated" in kinds
    assert "pool_lifecycle_changed" in kinds


def _noop_setup(client, _root):
    return None


def _setup_create_duplicate(client, _root):
    _create(client)


def _act_create_duplicate(client, _root):
    return _create(client)


def _act_create_invalid_posture(client, _root):
    return client.post(
        "/api/engines",
        json={"repo": {"owner": "acme", "repo": "widget"}, "posture": "not-a-posture"},
        headers=_control_headers(),
    )


def _act_create_no_token(client, _root):
    return client.post("/api/engines", json=_minimal_body())


def _act_create_wrong_token(client, _root):
    return client.post(
        "/api/engines",
        json=_minimal_body(),
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )


def _act_update_missing(client, _root):
    return _update(client)


def _setup_update_repo_mismatch(client, _root):
    _create(client)


def _act_update_repo_mismatch(client, _root):
    return _update(client, body=_minimal_body(owner="other", repo="engine"))


def _setup_set_posture(client, _root):
    _create(client)


def _act_set_posture_missing_confirm(client, _root):
    return _set_posture(client, body={"posture": "propose"})


def _act_set_posture_missing_engine(client, _root):
    return _set_posture(client)


def _act_delete_missing(client, _root):
    return client.delete("/api/engines/no/such", headers=_control_headers())


def _setup_delete_blocked(client, root):
    _create(client)
    _make_ws(root, "active-ws", workspace_mod.Status.ACTIVE)


def _act_delete_blocked(client, _root):
    return client.delete("/api/engines/acme/widget", headers=_control_headers())


def _act_preflight_missing(client, _root):
    return _preflight(client)


_NoPublishCase = tuple[
    str,
    Callable[[TestClient, Path], None],
    Callable[[TestClient, Path], Response],
    int,
]

_NO_PUBLISH_CASES: list[_NoPublishCase] = [
    ("create_duplicate_409", _setup_create_duplicate, _act_create_duplicate, 409),
    ("create_invalid_posture_422", _noop_setup, _act_create_invalid_posture, 422),
    ("create_no_token_401", _noop_setup, _act_create_no_token, 401),
    ("create_wrong_token_403", _noop_setup, _act_create_wrong_token, 403),
    ("update_missing_404", _noop_setup, _act_update_missing, 404),
    ("update_repo_mismatch_422", _setup_update_repo_mismatch, _act_update_repo_mismatch, 422),
    (
        "set_posture_missing_confirm_422",
        _setup_set_posture,
        _act_set_posture_missing_confirm,
        422,
    ),
    ("set_posture_missing_engine_404", _noop_setup, _act_set_posture_missing_engine, 404),
    ("delete_missing_404", _noop_setup, _act_delete_missing, 404),
    ("delete_blocked_by_workspace_409", _setup_delete_blocked, _act_delete_blocked, 409),
    ("preflight_missing_engine_404", _noop_setup, _act_preflight_missing, 404),
]


@pytest.mark.parametrize("case", _NO_PUBLISH_CASES, ids=[c[0] for c in _NO_PUBLISH_CASES])
def test_failure_paths_do_not_publish_engine_events(
    client: TestClient, workspaces_root: Path, case: _NoPublishCase
) -> None:
    _, setup, act, expected_status = case
    setup(client, workspaces_root)
    events = _subscribe(client)
    res = act(client, workspaces_root)
    assert res.status_code == expected_status
    assert events == []


_COMPOSED_OVERRIDE_TEXT = "custom prompt {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"
_WHOLESALE_OVERRIDE_TEXT = "custom judge prompt. VERDICT: line and DIFF_FINDINGS_CRITICAL: line."


def _composed_override_body(*, confirm: bool = True) -> dict[str, object]:
    return {
        "team": "implementation",
        "role": "implementer",
        "text": _COMPOSED_OVERRIDE_TEXT,
        "confirm": confirm,
    }


def _wholesale_override_body(*, confirm: bool = True) -> dict[str, object]:
    return {
        "team": "verification",
        "role": "judge",
        "text": _WHOLESALE_OVERRIDE_TEXT,
        "confirm": confirm,
    }


def _save_prompt_override(client, owner="acme", repo="widget", body=None, headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/prompt-overrides",
        json=body if body is not None else _composed_override_body(),
        headers=headers if headers is not None else _control_headers(),
    )


def test_save_composed_override_persists_and_is_returned(client: TestClient) -> None:
    _create(client)
    res = _save_prompt_override(client, body=_composed_override_body())
    assert res.status_code == 200
    entry = res.json()["prompt_overrides"]["implementation/implementer"]
    assert entry["text"] == _COMPOSED_OVERRIDE_TEXT
    assert isinstance(entry["base_fingerprint"], str)
    assert len(entry["base_fingerprint"]) == 64

    stored = client.get("/api/engines/acme/widget").json()
    stored_entry = stored["prompt_overrides"]["implementation/implementer"]
    assert stored_entry["text"] == _COMPOSED_OVERRIDE_TEXT


def test_save_composed_override_for_implementation_fix_persists(client: TestClient) -> None:
    _create(client)
    body = {
        "team": "implementation_fix",
        "role": "implementer",
        "text": _COMPOSED_OVERRIDE_TEXT,
        "confirm": True,
    }
    res = _save_prompt_override(client, body=body)
    assert res.status_code == 200
    assert "implementation_fix/implementer" in res.json()["prompt_overrides"]


def test_save_override_missing_placeholder_returns_422_naming_it(client: TestClient) -> None:
    _create(client)
    body = _composed_override_body()
    body["text"] = "custom prompt {{ENVIRONMENT}} {{REPO_GUIDANCE}}"
    res = _save_prompt_override(client, body=body)
    assert res.status_code == 422
    assert "{{SUCCESS}}" in res.text


def test_save_override_missing_marker_returns_422_naming_it(client: TestClient) -> None:
    _create(client)
    body = _wholesale_override_body()
    body["text"] = "custom judge prompt with only DIFF_FINDINGS_CRITICAL: line."
    res = _save_prompt_override(client, body=body)
    assert res.status_code == 422
    assert "VERDICT:" in res.text


def test_save_override_invalid_team_role_returns_422(client: TestClient) -> None:
    _create(client)
    body = {"team": "pr_merge", "role": "merger", "text": "anything", "confirm": True}
    res = _save_prompt_override(client, body=body)
    assert res.status_code == 422


def test_save_override_rejects_non_judge_verification_role(client: TestClient) -> None:
    _create(client)
    body = {
        "team": "verification",
        "role": "diff_reviewer",
        "text": _COMPOSED_OVERRIDE_TEXT,
        "confirm": True,
    }
    res = _save_prompt_override(client, body=body)
    assert res.status_code == 422


def test_save_override_missing_engine_returns_404(client: TestClient) -> None:
    res = _save_prompt_override(client)
    assert res.status_code == 404


def test_save_override_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _save_prompt_override(client, headers={})
    assert res.status_code == 401


def test_save_override_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _save_prompt_override(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_save_verification_override_without_confirm_returns_409_and_leaves_config_unchanged(
    client: TestClient,
) -> None:
    _create(client)
    res = _save_prompt_override(client, body=_wholesale_override_body(confirm=False))
    assert res.status_code == 409
    assert res.json()["detail"] == "requires_confirmation"
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["prompt_overrides"] == {}


def test_save_verification_override_with_confirm_persists(client: TestClient) -> None:
    _create(client)
    res = _save_prompt_override(client, body=_wholesale_override_body(confirm=True))
    assert res.status_code == 200
    assert "verification/judge" in res.json()["prompt_overrides"]


def test_save_composed_override_at_observe_posture_needs_no_confirmation(
    client: TestClient,
) -> None:
    _create(client)
    res = _save_prompt_override(client, body=_composed_override_body(confirm=False))
    assert res.status_code == 200


def test_save_composed_override_requires_confirmation_when_engine_can_self_merge(
    client: TestClient,
) -> None:
    _create(client)
    _set_posture(client, body={"posture": "self_merge", "confirm": True})
    res = _save_prompt_override(client, body=_composed_override_body(confirm=False))
    assert res.status_code == 409
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["prompt_overrides"] == {}


def test_save_composed_override_with_confirm_persists_when_engine_can_self_merge(
    client: TestClient,
) -> None:
    _create(client)
    _set_posture(client, body={"posture": "autonomous", "confirm": True})
    res = _save_prompt_override(client, body=_composed_override_body(confirm=True))
    assert res.status_code == 200
    assert "implementation/implementer" in res.json()["prompt_overrides"]


def test_save_override_emits_engine_updated_event(client: TestClient) -> None:
    _create(client)
    events = _subscribe(client)
    res = _save_prompt_override(client, body=_composed_override_body())
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_updated"]
    assert len(matches) == 1
    assert matches[0].payload == {"slug": "acme/widget"}


def test_save_override_never_mutates_prior_config_dict_in_place(client: TestClient) -> None:
    _create(client)
    identity = RepoIdentity(owner="acme", repo="widget")
    before_dict = engine_config_mod.lookup_by_repo(identity).prompt_overrides  # type: ignore[union-attr]
    assert before_dict == {}

    _save_prompt_override(client, body=_composed_override_body())

    after_dict = engine_config_mod.lookup_by_repo(identity).prompt_overrides  # type: ignore[union-attr]
    assert after_dict is not before_dict
    assert before_dict == {}


def test_save_second_override_builds_a_fresh_dict_each_time(client: TestClient) -> None:
    _create(client)
    _save_prompt_override(client, body=_composed_override_body())
    identity = RepoIdentity(owner="acme", repo="widget")
    mid_dict = engine_config_mod.lookup_by_repo(identity).prompt_overrides  # type: ignore[union-attr]

    _save_prompt_override(client, body=_wholesale_override_body())

    after_dict = engine_config_mod.lookup_by_repo(identity).prompt_overrides  # type: ignore[union-attr]
    assert after_dict is not mid_dict
    assert set(after_dict) == {"implementation/implementer", "verification/judge"}


def _config_proposal(client, owner="acme", repo="widget", body=None, headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/config-proposal",
        json=body if body is not None else {},
        headers=headers if headers is not None else _control_headers(),
    )


def _fake_proposal_result(**overrides: Any) -> engine_config_proposal_mod.ConfigProposalResult:
    base: dict[str, Any] = {
        "slug": "acme/widget",
        "base_preset": "python",
        "detected_stacks": ("python",),
        "additional_stacks": (),
        "proposed_config": engine_config_mod.default_engine_config().model_copy(
            update={"repo": RepoIdentity(owner="acme", repo="widget"), "forge": None}
        ),
        "diff": (),
        "validation": Finding(
            check="flow_graph_and_posture_valid", status="pass", message="ok"
        ),
    }
    base.update(overrides)
    return engine_config_proposal_mod.ConfigProposalResult(**base)


def test_config_proposal_returns_200_with_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    monkeypatch.setattr(
        engine_config_proposal_mod, "propose_config", lambda slug, **kw: _fake_proposal_result()
    )
    res = _config_proposal(client)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "acme/widget"
    assert body["base_preset"] == "python"
    assert body["validation"]["status"] == "pass"


def test_config_proposal_passes_base_preset_override_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    captured: dict[str, Any] = {}

    def _fake(slug: str, **kw: Any) -> engine_config_proposal_mod.ConfigProposalResult:
        captured["slug"] = slug
        captured.update(kw)
        return _fake_proposal_result(base_preset=kw.get("base_preset") or "python")

    monkeypatch.setattr(engine_config_proposal_mod, "propose_config", _fake)
    res = _config_proposal(client, body={"base_preset": "dotnet"})
    assert res.status_code == 200
    assert captured["slug"] == "acme/widget"
    assert captured["base_preset"] == "dotnet"
    assert res.json()["base_preset"] == "dotnet"


def test_config_proposal_invalid_base_preset_returns_422(client: TestClient) -> None:
    _create(client)
    res = _config_proposal(client, body={"base_preset": "ruby"})
    assert res.status_code == 422


def test_config_proposal_aborted_error_returns_422_with_manual_seed_finding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)

    def _raise(slug: str, **kw: Any) -> engine_config_proposal_mod.ConfigProposalResult:
        del slug, kw
        raise engine_config_proposal_mod.ProposalAbortedError("no forge configured")

    monkeypatch.setattr(engine_config_proposal_mod, "propose_config", _raise)
    res = _config_proposal(client)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["check"] == "config_proposal"
    assert detail["status"] == "fail"
    assert "no forge configured" in detail["message"]
    assert "manual" in detail["remedy"] or "preset" in detail["remedy"]


def test_config_proposal_unrepairable_error_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)

    def _raise(slug: str, **kw: Any) -> engine_config_proposal_mod.ConfigProposalResult:
        del slug, kw
        raise engine_config_proposal_mod.ProposalUnrepairableError("model output was garbage")

    monkeypatch.setattr(engine_config_proposal_mod, "propose_config", _raise)
    res = _config_proposal(client)
    assert res.status_code == 422
    assert "garbage" in res.json()["detail"]["message"]


def test_config_proposal_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    res = _config_proposal(client, headers={})
    assert res.status_code == 401


def test_config_proposal_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    res = _config_proposal(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def _config_draft_validate(client, owner="acme", repo="widget", body=None, headers=None):
    return client.post(
        f"/api/engines/{owner}/{repo}/config-draft-validate",
        json=body if body is not None else _minimal_body(owner, repo),
        headers=headers if headers is not None else _control_headers(),
    )


def test_config_draft_validate_returns_finding_for_valid_draft(client: TestClient) -> None:
    res = _config_draft_validate(client, body=_minimal_body())
    assert res.status_code == 200
    body = res.json()
    assert body["check"] == "flow_graph_and_posture_valid"
    assert body["status"] == "pass"


def test_config_draft_validate_returns_fail_finding_for_invalid_graph(
    client: TestClient,
) -> None:
    body = {
        "repo": {"owner": "acme", "repo": "widget"},
        "transition_graph": {"implementation": ["pr_merge"], "pr_merge": []},
        "flow_policy": {
            "verifier_teams": ["verification"],
            "merge_teams": ["pr_merge"],
            "terminal_teams": ["deploy_verify"],
            "internal_only_teams": [],
            "restricted_predecessors": {},
            "revisit_cap": 3,
        },
    }
    res = _config_draft_validate(client, body=body)
    assert res.status_code == 200
    assert res.json()["status"] == "fail"


def test_config_draft_validate_does_not_persist(client: TestClient) -> None:
    body = {**_minimal_body(), "posture": "autonomous"}
    res = _config_draft_validate(client, body=body)
    assert res.status_code == 200
    assert client.get("/api/engines/acme/widget").status_code == 404


def test_config_draft_validate_repo_mismatch_returns_422(client: TestClient) -> None:
    res = _config_draft_validate(
        client, owner="acme", repo="widget", body=_minimal_body(owner="other", repo="engine")
    )
    assert res.status_code == 422


def test_config_draft_validate_without_control_token_returns_401(client: TestClient) -> None:
    res = _config_draft_validate(client, headers={})
    assert res.status_code == 401


def test_config_draft_validate_wrong_control_token_returns_403(client: TestClient) -> None:
    res = _config_draft_validate(
        client, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403
