from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from alan.deploy_verify import DeployObservation, DeployRun, GateRecord, is_live, waited_s
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _forge_errors
from alan.teams import deploy_verify as deploy_verify_mod
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read


def _dt(offset_seconds: float = 0.0) -> datetime:
    base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
    return base + timedelta(seconds=offset_seconds)


def test_deploy_run_is_frozen() -> None:
    run = DeployRun(run_id="x", created_at=_dt(), status="completed", conclusion="success")
    with pytest.raises(AttributeError):
        run.run_id = "y"  # type: ignore[misc]


def _observation(
    *,
    arm: str = "restart_required",
    checkout: bool = False,
    slot: bool = False,
    served: bool = False,
    health_ok: bool = False,
) -> DeployObservation:
    return DeployObservation(
        arm=arm,  # type: ignore[arg-type]
        checkout_contains_merge=checkout,
        slot_contains_merge=slot,
        served_contains_merge=served,
        health_ok=health_ok,
    )


def test_is_live_restart_required_true_when_served_and_healthy() -> None:
    obs = _observation(arm="restart_required", served=True, health_ok=True)
    assert is_live(obs) is True


def test_is_live_restart_required_false_when_not_served() -> None:
    obs = _observation(arm="restart_required", served=False, health_ok=True)
    assert is_live(obs) is False


def test_is_live_restart_required_false_when_unhealthy() -> None:
    obs = _observation(arm="restart_required", served=True, health_ok=False)
    assert is_live(obs) is False


def test_is_live_restart_required_ignores_checkout_and_slot() -> None:
    obs = _observation(
        arm="restart_required", checkout=False, slot=False, served=True, health_ok=True
    )
    assert is_live(obs) is True


def test_is_live_restart_free_true_when_checkout_and_slot_contain_merge() -> None:
    obs = _observation(arm="restart_free", checkout=True, slot=True)
    assert is_live(obs) is True


def test_is_live_restart_free_false_when_checkout_missing() -> None:
    obs = _observation(arm="restart_free", checkout=False, slot=True)
    assert is_live(obs) is False


def test_is_live_restart_free_false_when_slot_missing() -> None:
    obs = _observation(arm="restart_free", checkout=True, slot=False)
    assert is_live(obs) is False


def test_is_live_restart_free_ignores_health_ok() -> None:
    obs = _observation(arm="restart_free", checkout=True, slot=True, health_ok=False)
    assert is_live(obs) is True


def test_gate_record_round_trip_restart_free() -> None:
    record = GateRecord(merge_sha="a" * 40, path="restart_free", deferred_at=None)
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_round_trip_signal() -> None:
    record = GateRecord(merge_sha="b" * 40, path="signal", deferred_at=None)
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_round_trip_ceiling() -> None:
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=1000.0)
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_round_trip_awaiting_restart() -> None:
    record = GateRecord(
        merge_sha="d" * 40,
        path="awaiting_restart",
        deferred_at=1000.0,
        deadline=6000.0,
        race_deadline=None,
    )
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_round_trip_awaiting_live() -> None:
    record = GateRecord(
        merge_sha="c" * 40,
        path="awaiting_live",
        deferred_at=1000.0,
        deadline=6000.0,
        race_deadline=None,
        merged_at=1500.0,
    )
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_to_json_uses_awaiting_live_not_legacy_literal() -> None:
    record = GateRecord(merge_sha="c" * 40, path="awaiting_live", deferred_at=1000.0)
    assert json.loads(record.to_json())["path"] == "awaiting_live"


def test_gate_record_from_json_legacy_awaiting_deploy_run_aliases_to_awaiting_live() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "path": "awaiting_deploy_run",
        "merge_sha": "a" * 40,
        "deferred_at": 1000.0,
        "merged_at": 900.0,
    })
    record = GateRecord.from_json(payload)
    assert record is not None
    assert record.path == "awaiting_live"
    assert record.merged_at == 900.0


def test_gate_record_from_json_rejects_unknown_legacy_path() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "awaiting_deploy_run_v2", "merge_sha": None,
         "deferred_at": None}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_default_merged_at_is_none() -> None:
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=1000.0)
    assert record.merged_at is None


def test_gate_record_round_trip_with_race_deadline() -> None:
    record = GateRecord(
        merge_sha="e" * 40,
        path="awaiting_restart",
        deferred_at=1000.0,
        deadline=None,
        race_deadline=1300.0,
    )
    assert GateRecord.from_json(record.to_json()) == record


def test_gate_record_from_json_legacy_blob_without_deadline_keys_parses_with_none() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "merge_sha": None,
        "path": "ceiling",
        "deferred_at": 1000.0,
    })
    record = GateRecord.from_json(payload)
    assert record is not None
    assert record.deadline is None
    assert record.race_deadline is None


def test_gate_record_from_json_invalid_deadline_type_returns_none() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "path": "ceiling",
        "merge_sha": None,
        "deferred_at": None,
        "deadline": "soon",
    })
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_invalid_race_deadline_type_returns_none() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "path": "ceiling",
        "merge_sha": None,
        "deferred_at": None,
        "race_deadline": "soon",
    })
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_invalid_merged_at_type_returns_none() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "path": "awaiting_live",
        "merge_sha": None,
        "deferred_at": None,
        "merged_at": "soon",
    })
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_legacy_blob_without_merged_at_key_parses_with_none() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "merge_sha": None,
        "path": "ceiling",
        "deferred_at": 1000.0,
    })
    record = GateRecord.from_json(payload)
    assert record is not None
    assert record.merged_at is None


def test_gate_record_from_json_accepts_awaiting_live_path() -> None:
    payload = json.dumps({
        "kind": "deploy_gate_v1",
        "path": "awaiting_live",
        "merge_sha": "a" * 40,
        "deferred_at": 1000.0,
        "merged_at": 900.0,
    })
    record = GateRecord.from_json(payload)
    assert record is not None
    assert record.path == "awaiting_live"
    assert record.merged_at == 900.0


def test_gate_record_from_json_none_returns_none() -> None:
    assert GateRecord.from_json(None) is None


def test_gate_record_from_json_malformed_json_returns_none() -> None:
    assert GateRecord.from_json("not json") is None


def test_gate_record_from_json_non_dict_returns_none() -> None:
    assert GateRecord.from_json("[1, 2]") is None


def test_gate_record_from_json_rejects_user_authored_instructions() -> None:
    assert GateRecord.from_json("please fix the bug in the login flow") is None


def test_gate_record_from_json_missing_kind_returns_none() -> None:
    payload = json.dumps({"path": "ceiling", "merge_sha": None, "deferred_at": None})
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_wrong_kind_returns_none() -> None:
    payload = json.dumps(
        {"kind": "something_else", "path": "ceiling", "merge_sha": None, "deferred_at": None}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_invalid_path_returns_none() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "bogus", "merge_sha": None, "deferred_at": None}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_invalid_merge_sha_type_returns_none() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "ceiling", "merge_sha": 123, "deferred_at": None}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_invalid_repo_type_returns_none() -> None:
    payload = json.dumps(
        {
            "kind": "deploy_gate_v1",
            "path": "ceiling",
            "merge_sha": None,
            "deferred_at": None,
            "repo": 123,
        }
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_round_trip_with_repo() -> None:
    record = GateRecord(
        merge_sha="a" * 40,
        path="awaiting_live",
        deferred_at=1000.0,
        deadline=2000.0,
        race_deadline=None,
        merged_at=1500.0,
        repo="DejvMatikar/example-repo",
    )
    loaded = GateRecord.from_json(record.to_json())
    assert loaded == record
    assert loaded is not None
    assert loaded.repo == "DejvMatikar/example-repo"


def test_gate_record_default_repo_is_none() -> None:
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=1000.0)
    assert record.repo is None


def test_gate_record_from_json_invalid_deferred_at_type_returns_none() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "ceiling", "merge_sha": None, "deferred_at": "soon"}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_bool_deferred_at_returns_none() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "ceiling", "merge_sha": None, "deferred_at": True}
    )
    assert GateRecord.from_json(payload) is None


def test_gate_record_from_json_accepts_int_deferred_at() -> None:
    payload = json.dumps(
        {"kind": "deploy_gate_v1", "path": "ceiling", "merge_sha": None, "deferred_at": 1000}
    )
    record = GateRecord.from_json(payload)
    assert record is not None
    assert record.deferred_at == 1000.0


def test_waited_s_none_record_is_zero() -> None:
    assert waited_s(None, now=1000.0) == 0.0


def test_waited_s_no_deferred_at_is_zero() -> None:
    record = GateRecord(merge_sha=None, path="restart_free", deferred_at=None)
    assert waited_s(record, now=1000.0) == 0.0


def test_waited_s_computes_elapsed() -> None:
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=100.0)
    assert waited_s(record, now=175.0) == 75.0


def test_waited_s_clamps_negative_to_zero_on_clock_skew() -> None:
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=200.0)
    assert waited_s(record, now=100.0) == 0.0


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _merge_info() -> deploy_verify_mod._PrMergeInfo:
    return deploy_verify_mod._pr_merge_info(42, forge_target=DEFAULT_FORGE_TARGET)


def test_pr_merge_info_reads_the_three_fields_from_the_given_target(
    forge: ForgeScript,
) -> None:
    other = ForgeTarget(repo=RepoIdentity(owner="acme", repo="widget"), credential_env_name="X")
    forge.queue("read_pr", pr_read({"state": "OPEN"}))
    deploy_verify_mod._pr_merge_info(42, forge_target=other)
    args, kwargs = forge.calls("read_pr")[0]
    assert args == (42, ("state", "mergedAt", "mergeCommit"))
    assert kwargs["forge_target"] is other
    assert kwargs["timeout"] == deploy_verify_mod._GH_TIMEOUT_S


def test_pr_merge_info_rate_limited_raises(forge: ForgeScript) -> None:
    forge.queue(
        "read_pr",
        pr_read(returncode=1, stderr="You have exceeded a secondary rate limit (HTTP 403)"),
    )
    with pytest.raises(_forge_errors.ForgeRateLimited):
        _merge_info()


def test_pr_merge_info_transient_network_failure_raises(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="dial tcp: i/o timeout"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _merge_info()


def test_pr_merge_info_subprocess_missing_raises_transient(forge: ForgeScript) -> None:
    forge.queue("read_pr", FileNotFoundError("gh missing"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _merge_info()


def test_pr_merge_info_subprocess_timeout_raises_transient(forge: ForgeScript) -> None:
    forge.queue("read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=1.0))
    with pytest.raises(_forge_errors.ForgeTransientError):
        _merge_info()


def test_pr_merge_info_auth_failure_keeps_no_verdict_shape(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="Bad credentials"))
    info = _merge_info()
    assert info.gh_ok is False
    assert info.state is None


def test_pr_merge_info_unknown_failure_keeps_no_verdict_shape(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="Pull request is not mergeable"))
    info = _merge_info()
    assert info.gh_ok is False


def test_pr_merge_info_merged_yields_merged_at_and_merge_sha(forge: ForgeScript) -> None:
    forge.queue(
        "read_pr",
        pr_read({
            "state": "MERGED",
            "mergedAt": "2026-05-20T10:00:00Z",
            "mergeCommit": {"oid": "a" * 40},
        }),
    )
    info = _merge_info()
    assert info.gh_ok is True
    assert info.state == "MERGED"
    assert info.merged_at == datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC)
    assert info.merge_sha == "a" * 40


def test_pr_merge_info_empty_fields_keeps_no_verdict_shape(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({}))
    info = _merge_info()
    assert info.gh_ok is False
    assert info.state is None
    assert info.merged_at is None


def test_pr_merge_info_state_without_merged_at_is_a_verdict(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"state": "OPEN"}))
    info = _merge_info()
    assert info.gh_ok is True
    assert info.state == "OPEN"
    assert info.merged_at is None


def test_pr_merge_info_merge_sha_none_when_merge_commit_absent(forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read({"state": "OPEN", "mergedAt": None}))
    assert _merge_info().merge_sha is None


def test_pr_merge_info_merge_sha_none_when_merge_commit_not_a_mapping(
    forge: ForgeScript,
) -> None:
    forge.queue(
        "read_pr",
        pr_read({
            "state": "MERGED",
            "mergedAt": "2026-05-20T10:00:00Z",
            "mergeCommit": "a" * 40,
        }),
    )
    assert _merge_info().merge_sha is None


def test_pr_merge_info_merge_sha_none_when_oid_not_a_string(forge: ForgeScript) -> None:
    forge.queue(
        "read_pr",
        pr_read({
            "state": "MERGED",
            "mergedAt": "2026-05-20T10:00:00Z",
            "mergeCommit": {"oid": None},
        }),
    )
    assert _merge_info().merge_sha is None
