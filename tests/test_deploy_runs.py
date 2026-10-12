from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from alan import deploy_runs
from alan.claude_cli import ServerErrorRetryable
from alan.deploy_verify import DeployRun
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge import JsonObject
from tests.fakes.forge import FakeForge
from tests.forge_stubs import rows_read

_TARGET = ForgeTarget(
    repo=RepoIdentity(owner="DejvMatikar", repo="alan"),
    credential_env_name="ALAN_GH_TOKEN",  # noqa: S106  # nosec B106
)
_WORKFLOW = "ci-deploy.yml"


def _dt(offset_seconds: float = 0.0) -> datetime:
    base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    return base + timedelta(seconds=offset_seconds)


def test_parse_iso_datetime_no_fractional() -> None:
    dt = deploy_runs.parse_iso_datetime("2024-01-15T10:05:30Z")
    assert dt is not None
    assert dt.second == 30
    assert dt.tzinfo is UTC


def test_parse_iso_datetime_fractional_seconds() -> None:
    dt = deploy_runs.parse_iso_datetime("2024-01-15T10:05:30.123456Z")
    assert dt is not None
    assert dt.tzinfo is UTC


def test_parse_iso_datetime_invalid_returns_none() -> None:
    assert deploy_runs.parse_iso_datetime("not-a-date") is None


def test_post_merge_runs_filters_by_created_at() -> None:
    merged_at = _dt(0.0)
    pre = DeployRun(run_id="old", created_at=_dt(-60.0), status="completed", conclusion="success")
    post = DeployRun(run_id="new", created_at=_dt(60.0), status="completed", conclusion="success")
    result = deploy_runs.post_merge_runs([pre, post], merged_at)
    assert result == [post]


def test_post_merge_runs_includes_exact_merged_at_boundary() -> None:
    merged_at = _dt(0.0)
    run = DeployRun(run_id="a", created_at=merged_at, status="completed", conclusion="success")
    assert deploy_runs.post_merge_runs([run], merged_at) == [run]


def test_fetch_deploy_runs_parses_successful_output(_active_fake_forge: FakeForge) -> None:
    row: JsonObject = {
        "databaseId": 1, "createdAt": "2024-01-15T10:05:00Z",
        "status": "completed", "conclusion": "success",
    }
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read((row,)))
    runs = deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)
    assert len(runs) == 1
    assert runs[0].run_id == "1"
    assert runs[0].conclusion == "success"


def test_fetch_deploy_runs_ok_with_empty_rows_returns_empty_list(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    assert deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW) == []


def test_fetch_deploy_runs_raised_outcome_raises_retryable(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub(
        "workflow_runs",
        lambda *_a, **_k: rows_read(outcome="raised", rows=None, stderr="gh missing"),
    )
    with pytest.raises(ServerErrorRetryable):
        deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)


def test_fetch_deploy_runs_failed_outcome_raises_retryable_with_rc_and_stderr(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub(
        "workflow_runs",
        lambda *_a, **_k: rows_read(outcome="failed", rows=None, returncode=1, stderr="boom"),
    )
    with pytest.raises(ServerErrorRetryable, match="rc=1"):
        deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)


def test_fetch_deploy_runs_unparseable_payload_raises_retryable(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(None))
    with pytest.raises(ServerErrorRetryable):
        deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)


def test_fetch_deploy_runs_uses_the_20s_timeout(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)
    (call,) = _active_fake_forge.calls
    assert call[2]["timeout"] == 20.0


def test_fetch_deploy_runs_scopes_the_call_to_the_given_forge_target(
    _active_fake_forge: FakeForge,
) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    deploy_runs.fetch_deploy_runs(_TARGET, _WORKFLOW)
    (call,) = _active_fake_forge.calls
    assert call[0] == "workflow_runs"
    assert call[1] == ("ci-deploy.yml",)
    assert call[2]["forge_target"] == _TARGET
    assert call[2]["limit"] == 15


def test_fetch_deploy_runs_lists_the_given_workflow(_active_fake_forge: FakeForge) -> None:
    _active_fake_forge.stub("workflow_runs", lambda *_a, **_k: rows_read(()))
    deploy_runs.fetch_deploy_runs(_TARGET, "ship-it.yml")
    (call,) = _active_fake_forge.calls
    assert call[1] == ("ship-it.yml",)


def test_fetch_deploy_runs_without_a_deploy_workflow_returns_no_runs_and_skips_the_forge(
    monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge
) -> None:
    def _forbidden() -> object:
        raise AssertionError("active_forge must not be touched")

    monkeypatch.setattr(deploy_runs, "active_forge", _forbidden)
    assert deploy_runs.fetch_deploy_runs(_TARGET, None) == []
    assert _active_fake_forge.calls == []


def test_parse_deploy_runs_payload_invalid_json_returns_none() -> None:
    assert deploy_runs.parse_deploy_runs_payload("not-json") is None


def test_parse_deploy_runs_payload_non_list_returns_none() -> None:
    assert deploy_runs.parse_deploy_runs_payload('{"key": "val"}') is None


def test_parse_deploy_runs_payload_skips_non_dict_items() -> None:
    assert deploy_runs.parse_deploy_runs_payload('["not-a-dict"]') == []


def test_parse_deploy_runs_payload_skips_dict_items_that_fail_to_parse() -> None:
    payload = json.dumps([{"databaseId": 1, "status": "completed", "conclusion": "success"}])
    assert deploy_runs.parse_deploy_runs_payload(payload) == []


def test_parse_deploy_run_missing_created_at_skipped() -> None:
    item = {"databaseId": 1, "status": "completed", "conclusion": "success"}
    assert deploy_runs._parse_deploy_run(item) is None  # noqa: SLF001


def test_parse_deploy_run_invalid_created_at_skipped() -> None:
    item = {
        "databaseId": 1,
        "createdAt": "not-a-date",
        "status": "completed",
        "conclusion": "success",
    }
    assert deploy_runs._parse_deploy_run(item) is None  # noqa: SLF001


def test_parse_deploy_run_null_run_id_becomes_question_mark() -> None:
    item = {
        "databaseId": None,
        "createdAt": "2024-01-15T10:05:00Z",
        "status": "completed",
        "conclusion": "success",
    }
    run = deploy_runs._parse_deploy_run(item)  # noqa: SLF001
    assert run is not None
    assert run.run_id == "?"


def test_parse_deploy_run_null_conclusion_preserved() -> None:
    item = {
        "databaseId": 2,
        "createdAt": "2024-01-15T10:05:00Z",
        "status": "in_progress",
        "conclusion": None,
    }
    run = deploy_runs._parse_deploy_run(item)  # noqa: SLF001
    assert run is not None
    assert run.conclusion is None


def test_parse_deploy_run_non_string_status_defaults_empty() -> None:
    item = {
        "databaseId": 3,
        "createdAt": "2024-01-15T10:05:00Z",
        "status": 123,
        "conclusion": "success",
    }
    run = deploy_runs._parse_deploy_run(item)  # noqa: SLF001
    assert run is not None
    assert run.status == ""
