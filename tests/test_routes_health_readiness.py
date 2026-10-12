from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import engine_preflight as ep
from alan import reconciler as _reconciler_mod
from alan import server
from alan.health import HEALTH_META_KEY
from tests.fakes.forge import FakeForge
from tests.readiness_support import (
    CODE_QUALITY,
    CONNECTED_SLUG,
    DEFAULT_SLUG,
    NOT_READY_CASES,
    REVIEWER,
    Install,
)

_CASES = list(NOT_READY_CASES)


@pytest.fixture(autouse=True)
def _stub_seed_triage_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    return Install.isolated(tmp_path, monkeypatch)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        yield c


def _readiness(client: TestClient, path: str = "/api/health") -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = client.get(path).json()["dispatch_readiness"]
    return report


def _on_a_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_the_owner_install_reports_an_empty_readiness_list(client: TestClient) -> None:
    assert _readiness(client) == []


def test_the_report_is_empty_when_every_engine_is_ready(
    client: TestClient, install: Install
) -> None:
    install.satisfy_everything()
    assert _readiness(client) == []


@pytest.mark.parametrize("case", _CASES)
def test_each_not_ready_case_is_reported_with_engine_requirement_state_and_fix(
    client: TestClient, install: Install, case: str
) -> None:
    arrange, requirement = NOT_READY_CASES[case]
    arrange(install)
    cfg = install.default_cfg()
    report = _readiness(client)
    (issue,) = [i for i in report if i["requirement"] == requirement]
    assert set(issue) == {"engine", "requirement", "state", "message", "fix"}
    assert issue["engine"] == cfg.repo.slug
    assert issue["state"] == "not_ready"
    assert issue["message"]
    assert issue["fix"] == ep.readiness_fix(requirement, cfg)


def test_an_unconfirmed_readiness_is_reported_as_unknown(
    client: TestClient, install: Install
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    (issue,) = _readiness(client)
    assert (issue["requirement"], issue["state"]) == ("reviewer_credential", "unknown")


def test_a_connected_repository_is_reported_with_its_own_fix(
    client: TestClient, install: Install
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    install.save_preflight("pass")
    install.connect()
    (issue,) = _readiness(client)
    assert issue["engine"] == CONNECTED_SLUG
    assert issue["fix"] == f"store a reviewer credential for {CONNECTED_SLUG}"


def test_the_report_does_not_depend_on_the_triage_sweep_or_a_health_state(
    client: TestClient, install: Install
) -> None:
    install.require(CODE_QUALITY)
    assert server.app.state.queue.get_meta(HEALTH_META_KEY) is None
    data = client.get("/api/health").json()
    assert data["health_state"] is None
    assert data["last_computed_at"] is None
    assert [i["requirement"] for i in data["dispatch_readiness"]] == ["code_quality"]


def test_the_report_is_recomputed_on_every_request(
    client: TestClient, install: Install
) -> None:
    install.require(CODE_QUALITY)
    first = _readiness(client)
    second = _readiness(client)
    assert first == second
    assert len(first) == 1
    install.record_latch()
    assert _readiness(client) == []
    ep.code_quality_latch_path().unlink()
    assert len(_readiness(client)) == 1


def test_the_report_ignores_the_engine_and_project_query_filters(
    client: TestClient, install: Install
) -> None:
    install.require(CODE_QUALITY)
    assert len(_readiness(client, "/api/health?engine=elsewhere/none&project_id=nope")) == 1


def test_the_report_is_computed_off_the_event_loop(
    client: TestClient, install: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(CODE_QUALITY)
    seen: list[bool] = []
    real = ep.evaluate_readiness

    def _spy(cfg: Any, **kwargs: Any) -> Any:
        seen.append(_on_a_loop())
        return real(cfg, **kwargs)

    monkeypatch.setattr(ep, "evaluate_readiness", _spy)
    assert len(_readiness(client)) == 1
    assert seen == [False]


def test_serving_health_reads_nothing_from_github(
    client: TestClient, install: Install, _active_fake_forge: FakeForge
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    install.store_reviewer()
    install.connect()
    assert len(_readiness(client)) == 4
    assert _active_fake_forge.calls == []


def test_the_report_carries_no_credential_value_or_token_prefix(
    client: TestClient, install: Install
) -> None:
    install.require(REVIEWER, CODE_QUALITY)
    install.store_reviewer()
    rendered = json.dumps(_readiness(client))
    assert "reviewer-fixture-value" not in rendered
    assert "ghp_" not in rendered
    assert "github_pat_" not in rendered


def test_the_report_keeps_serving_when_the_health_sweep_state_has_gone_stale(
    client: TestClient, install: Install
) -> None:
    install.require(CODE_QUALITY)
    stale = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    server.app.state.queue.set_meta(
        HEALTH_META_KEY,
        json.dumps(
            {
                "verdict": "healthy",
                "blocked_count": 0,
                "in_flight_count": 0,
                "findings": [],
                "computed_at": stale,
            }
        ),
    )
    data = client.get("/api/health").json()
    assert data["health_state"] is None
    assert len(data["dispatch_readiness"]) == 1


def test_generated_openapi_schema_marks_the_report_optional_and_types_each_issue(
    client: TestClient,
) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "dispatch_readiness" not in schemas["HealthResponse"].get("required", [])
    assert set(schemas["ReadinessIssue"]["required"]) == {
        "engine",
        "requirement",
        "state",
        "message",
        "fix",
    }
    assert set(schemas["ReadinessIssue"]["properties"]["state"]["enum"]) == {
        "not_ready",
        "unknown",
        "warning",
    }


def test_the_default_engine_slug_is_what_the_report_names(
    client: TestClient, install: Install
) -> None:
    install.require(CODE_QUALITY)
    assert [i["engine"] for i in _readiness(client)] == [DEFAULT_SLUG]
