from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget, RepoIdentity
from alan.teams import _pr_branch, _pr_state_paths
from tests import _pr_branch_support as support
from tests._pr_branch_support import _WIDGET_FORGE

_TARGET = DEFAULT_FORGE_TARGET

_DELETED_REEXPORTS = (
    "_engine_state_dir",
    "_is_default_forge",
    "_legacy_acked_comments_path",
    "_legacy_gate_rerun_count_path",
    "_legacy_remediation_retry_count_path",
    "_parse_acked_comment_ids",
    "_parse_nonneg_int",
    "_read_json_with_legacy_fallback",
    "_sanitize_path_component",
    "acked_comments_path",
    "bump_gate_rerun_count",
    "bump_remediation_retry_count",
    "gate_rerun_count_path",
    "load_acked_comment_ids",
    "load_gate_rerun_count",
    "load_remediation_retry_count",
    "remediation_retry_count_path",
    "save_acked_comment_id",
    "state_dir",
)


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


@pytest.mark.parametrize("name", _DELETED_REEXPORTS)
def test_pr_branch_no_longer_reexports_moved_names(name: str) -> None:
    assert not hasattr(_pr_branch, name)


def test_state_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    assert _pr_state_paths.state_dir() == Path.home() / ".alan" / "state"


def test_state_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "alt"))
    assert _pr_state_paths.state_dir() == tmp_path / "alt"


def test_acked_comments_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset()
    _pr_state_paths.save_acked_comment_id(42, 99, _TARGET)
    _pr_state_paths.save_acked_comment_id(42, 100, _TARGET)
    _pr_state_paths.save_acked_comment_id(42, 99, _TARGET)
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset({99, 100})


def test_save_acked_skips_write_when_id_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 99, _TARGET)
    p = _pr_state_paths.acked_comments_path(42, _TARGET)
    mtime_before = p.stat().st_mtime_ns
    _pr_state_paths.save_acked_comment_id(42, 99, _TARGET)
    assert p.stat().st_mtime_ns == mtime_before


def test_save_acked_persists_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 300, _TARGET)
    _pr_state_paths.save_acked_comment_id(42, 100, _TARGET)
    _pr_state_paths.save_acked_comment_id(42, 200, _TARGET)
    p = _pr_state_paths.acked_comments_path(42, _TARGET)
    on_disk = json.loads(p.read_text())
    assert on_disk == [100, 200, 300]


def test_load_acked_comment_ids_handles_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_acked_comment_ids(999, _TARGET) == frozenset()


def test_load_acked_comment_ids_handles_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_acked_comments.json").write_text("not json{")
    assert _pr_state_paths.load_acked_comment_ids(5, _TARGET) == frozenset()


def test_load_acked_comment_ids_handles_non_list_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_6_acked_comments.json").write_text('{"not": "a list"}')
    assert _pr_state_paths.load_acked_comment_ids(6, _TARGET) == frozenset()


def test_load_acked_comment_ids_filters_non_ints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_7_acked_comments.json").write_text('[1, "two", 3, null]')
    assert _pr_state_paths.load_acked_comment_ids(7, _TARGET) == frozenset({1, 3})


def test_load_acked_comment_ids_excludes_bools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_9_acked_comments.json").write_text("[true, false, 2]")
    assert _pr_state_paths.load_acked_comment_ids(9, _TARGET) == frozenset({2})


def test_gate_rerun_count_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_gate_rerun_count(42, _TARGET) == 0
    assert _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1") == 1
    assert _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1") == 2
    assert _pr_state_paths.load_gate_rerun_count(42, _TARGET) == 2
    on_disk = json.loads(_pr_state_paths.gate_rerun_count_path(42, _TARGET).read_text())
    assert on_disk == {"head_sha": "sha-1", "count": 2, "lifetime": 2}


def test_load_gate_rerun_count_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_gate_rerun_count(999, _TARGET) == 0


def test_load_gate_rerun_count_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_reruns.json").write_text("not json{")
    assert _pr_state_paths.load_gate_rerun_count(5, _TARGET) == 0


def test_load_gate_rerun_count_non_int_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_6_gate_reruns.json").write_text('"three"')
    assert _pr_state_paths.load_gate_rerun_count(6, _TARGET) == 0


def test_load_gate_rerun_count_negative_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_8_gate_reruns.json").write_text("-4")
    assert _pr_state_paths.load_gate_rerun_count(8, _TARGET) == 0


def test_load_gate_rerun_count_bool_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_10_gate_reruns.json").write_text("true")
    assert _pr_state_paths.load_gate_rerun_count(10, _TARGET) == 0


def test_gate_rerun_state_new_head_sha_resets_count_not_lifetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-2") == 1
    assert _pr_state_paths.load_gate_rerun_state(42, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-2", count=1, lifetime=3
    )


def test_bump_gate_rerun_count_legacy_bare_int_resets_on_first_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("2")
    assert _pr_state_paths.bump_gate_rerun_count(5, _TARGET, head_sha="sha-1") == 1
    on_disk = json.loads(p.read_text())
    assert on_disk == {"head_sha": "sha-1", "count": 1, "lifetime": 3}


def test_gate_rerun_state_sha_fetch_failure_does_not_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha=None) == 2
    assert _pr_state_paths.load_gate_rerun_state(42, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=2, lifetime=2
    )


def test_gate_rerun_state_sha_fetch_failure_preserves_anchor_for_later_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha=None)
    assert _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1") == 3
    assert _pr_state_paths.load_gate_rerun_state(42, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=3, lifetime=3
    )


def test_gate_rerun_count_for_sha_matches_reset_aware_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.gate_rerun_count_for_sha(42, _TARGET, head_sha="sha-1") == 1
    assert _pr_state_paths.gate_rerun_count_for_sha(42, _TARGET, head_sha="sha-2") == 0
    assert _pr_state_paths.gate_rerun_count_for_sha(42, _TARGET, head_sha=None) == 1


def test_load_gate_rerun_state_legacy_bare_int(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_reruns.json").write_text("3")
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha=None, count=3, lifetime=3
    )


def test_load_gate_rerun_state_dict_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-1", "count": 1, "lifetime": 4}))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4
    )


def test_load_gate_rerun_state_dict_payload_with_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4, "checks": ["ci/e2e"],
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",)
    )


def test_load_gate_rerun_state_non_list_checks_field_defaults_to_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4, "checks": "not-a-list",
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4
    )


def test_load_gate_rerun_state_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_gate_rerun_state(999, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha=None, count=0, lifetime=0
    )


def test_load_gate_rerun_state_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_reruns.json").write_text("not json{")
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha=None, count=0, lifetime=0
    )


@pytest.mark.parametrize(
    "payload",
    [
        '"not-a-dict-or-int"',
        "true",
        "-4",
        "[1, 2, 3]",
        json.dumps({"head_sha": 123, "count": 1, "lifetime": 1}),
        json.dumps({"head_sha": "sha-1", "count": True, "lifetime": 1}),
        json.dumps({"head_sha": "sha-1", "count": -1, "lifetime": 1}),
        json.dumps({"head_sha": "sha-1", "count": 1, "lifetime": True}),
        json.dumps({"head_sha": "sha-1", "count": 1, "lifetime": -1}),
        json.dumps({"head_sha": "sha-1", "count": "x", "lifetime": 1}),
        json.dumps({"head_sha": "sha-1", "count": 1, "lifetime": "x"}),
    ],
)
def test_load_gate_rerun_state_malformed_payloads_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_reruns.json").write_text(payload)
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha=None, count=0, lifetime=0
    )


def test_reconcile_attempt_count_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.reconcile_attempts_for_sha(42, _TARGET, head_sha="sha-1") == 0
    assert _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-1") == 1
    assert _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-1") == 2
    assert _pr_state_paths.reconcile_attempts_for_sha(42, _TARGET, head_sha="sha-1") == 2
    on_disk = json.loads(_pr_state_paths.reconcile_attempt_count_path(42, _TARGET).read_text())
    assert on_disk == {"head_sha": "sha-1", "count": 2, "lifetime": 2}


def test_reconcile_attempt_count_new_head_sha_resets_count_not_lifetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-2") == 1
    assert _pr_state_paths.load_reconcile_attempt_state(42, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha="sha-2", count=1, lifetime=3)
    )


def test_reconcile_attempt_count_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_reconcile_attempt_state(999, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha=None, count=0, lifetime=0)
    )


def test_reconcile_attempt_count_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_reconcile_attempts.json").write_text("not json{")
    assert _pr_state_paths.load_reconcile_attempt_state(5, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha=None, count=0, lifetime=0)
    )


def test_reconcile_attempt_count_sha_fetch_failure_does_not_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.bump_reconcile_attempt_count(42, _TARGET, head_sha=None) == 2
    assert _pr_state_paths.load_reconcile_attempt_state(42, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha="sha-1", count=2, lifetime=2)
    )


def test_reconcile_attempt_count_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_reconcile_attempt_count(5, _WIDGET_FORGE, head_sha="sha-1")
    assert _pr_state_paths.load_reconcile_attempt_state(5, _WIDGET_FORGE) == (
        _pr_state_paths.GateRerunState(head_sha="sha-1", count=1, lifetime=1)
    )
    assert _pr_state_paths.load_reconcile_attempt_state(5, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha=None, count=0, lifetime=0)
    )


def test_reconcile_attempt_count_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_reconcile_attempts.json").write_text("3")
    assert _pr_state_paths.load_reconcile_attempt_state(5, _TARGET) == (
        _pr_state_paths.GateRerunState(head_sha=None, count=3, lifetime=3)
    )


def test_reconcile_attempt_count_second_engine_does_not_fall_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_reconcile_attempts.json").write_text("3")
    assert _pr_state_paths.load_reconcile_attempt_state(5, _WIDGET_FORGE) == (
        _pr_state_paths.GateRerunState(head_sha=None, count=0, lifetime=0)
    )


def test_remediation_retry_count_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_remediation_retry_count(42, _TARGET) == 0
    assert _pr_state_paths.bump_remediation_retry_count(42, _TARGET) == 1
    assert _pr_state_paths.bump_remediation_retry_count(42, _TARGET) == 2
    assert _pr_state_paths.load_remediation_retry_count(42, _TARGET) == 2
    on_disk = json.loads(_pr_state_paths.remediation_retry_count_path(42, _TARGET).read_text())
    assert on_disk == 2


def test_load_remediation_retry_count_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_remediation_retry_count(999, _TARGET) == 0


def test_load_remediation_retry_count_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_remediation_retries.json").write_text("not json{")
    assert _pr_state_paths.load_remediation_retry_count(5, _TARGET) == 0


def test_load_remediation_retry_count_non_int_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_6_remediation_retries.json").write_text('"three"')
    assert _pr_state_paths.load_remediation_retry_count(6, _TARGET) == 0


def test_load_remediation_retry_count_negative_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_8_remediation_retries.json").write_text("-4")
    assert _pr_state_paths.load_remediation_retry_count(8, _TARGET) == 0


def test_load_remediation_retry_count_bool_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_10_remediation_retries.json").write_text("false")
    assert _pr_state_paths.load_remediation_retry_count(10, _TARGET) == 0


def test_engine_state_dir_explicit_default_forge_is_namespaced_alan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths._engine_state_dir(_TARGET) == tmp_path / "DejvMatikar" / "alan"  # noqa: SLF001


def test_engine_state_dir_second_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert (
        _pr_state_paths._engine_state_dir(_WIDGET_FORGE)  # noqa: SLF001
        == tmp_path / "acme-corp" / "widget-app"
    )


def test_engine_state_dir_handles_non_default_owner_repo_chars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    target = ForgeTarget(
        repo=RepoIdentity(owner="weird.owner-name", repo="repo_name.test"),
        credential_env_name="WEIRD_GH_TOKEN",  # noqa: S106  # nosec B106
    )
    assert (
        _pr_state_paths._engine_state_dir(target)  # noqa: SLF001
        == tmp_path / "weird.owner-name" / "repo_name.test"
    )


def test_engine_state_dir_sanitizes_path_traversal_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    target = ForgeTarget(
        repo=RepoIdentity(owner="../../etc", repo=".."),
        credential_env_name="EVIL_GH_TOKEN",  # noqa: S106  # nosec B106
    )
    result = _pr_state_paths._engine_state_dir(target)  # noqa: SLF001
    assert result.parent.parent == tmp_path
    assert ".." not in result.relative_to(tmp_path).parts


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "_"),
        (".", "_"),
        ("..", "_"),
        ("normal", "normal"),
        ("a/b", "a_b"),
        ("a\\b", "a_b"),
        ("a/b\\c", "a_b_c"),
    ],
)
def test_sanitize_path_component_direct(value: str, expected: str) -> None:
    assert _pr_state_paths._sanitize_path_component(value) == expected  # noqa: SLF001


def test_is_default_forge_true_for_default() -> None:
    assert _pr_state_paths._is_default_forge(DEFAULT_FORGE_TARGET) is True  # noqa: SLF001


def test_is_default_forge_false_for_second_engine() -> None:
    assert _pr_state_paths._is_default_forge(_WIDGET_FORGE) is False  # noqa: SLF001


def test_acked_comments_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.acked_comments_path(42, _TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_acked_comments.json"
    )


def test_acked_comments_path_namespaced_second_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.acked_comments_path(42, _WIDGET_FORGE) == (
        tmp_path / "acme-corp" / "widget-app" / "pr_42_acked_comments.json"
    )


def test_load_acked_comment_ids_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_42_acked_comments.json").write_text(json.dumps([1, 2, 3]))
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset({1, 2, 3})


def test_load_acked_comment_ids_second_engine_does_not_fall_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_42_acked_comments.json").write_text(json.dumps([1, 2, 3]))
    assert _pr_state_paths.load_acked_comment_ids(42, _WIDGET_FORGE) == frozenset()


def test_load_acked_comment_ids_namespaced_present_ignores_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_42_acked_comments.json").write_text(json.dumps([99]))
    namespaced = _pr_state_paths.acked_comments_path(42, _TARGET)
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text(json.dumps([1, 2]))
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset({1, 2})


def test_load_acked_comment_ids_corrupt_namespaced_does_not_fall_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_42_acked_comments.json").write_text(json.dumps([1, 2, 3]))
    namespaced = _pr_state_paths.acked_comments_path(42, _TARGET)
    namespaced.parent.mkdir(parents=True, exist_ok=True)
    namespaced.write_text("not json{")
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset()


def test_save_acked_comment_id_writes_namespaced_never_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 7, _TARGET)
    assert _pr_state_paths.acked_comments_path(42, _TARGET).exists()
    assert not (tmp_path / "pr_42_acked_comments.json").exists()


def test_save_acked_comment_id_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 7, _WIDGET_FORGE)
    assert _pr_state_paths.load_acked_comment_ids(42, _WIDGET_FORGE) == frozenset({7})
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset()


def test_release_acked_comment_id_removes_claimed_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 7, _TARGET)
    _pr_state_paths.release_acked_comment_id(42, 7, _TARGET)
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset()


def test_release_acked_comment_id_missing_id_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 7, _TARGET)
    _pr_state_paths.release_acked_comment_id(42, 99, _TARGET)
    assert _pr_state_paths.load_acked_comment_ids(42, _TARGET) == frozenset({7})


def test_release_acked_comment_id_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_acked_comment_id(42, 7, _WIDGET_FORGE)
    _pr_state_paths.release_acked_comment_id(42, 7, _TARGET)
    assert _pr_state_paths.load_acked_comment_ids(42, _WIDGET_FORGE) == frozenset({7})


def test_gate_rerun_count_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_5_gate_reruns.json").write_text("3")
    assert _pr_state_paths.load_gate_rerun_count(5, _TARGET) == 3


def test_gate_rerun_count_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(5, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.load_gate_rerun_count(5, _WIDGET_FORGE) == 0
    assert _pr_state_paths.load_gate_rerun_count(5, _TARGET) == 1


def test_gate_rerun_count_shared_across_verifier_and_merge_gate_lanes_per_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _WIDGET_FORGE, head_sha="sha-1")
    assert _pr_state_paths.load_gate_rerun_count(42, _WIDGET_FORGE) == 1
    _pr_state_paths.bump_gate_rerun_count(42, _WIDGET_FORGE, head_sha="sha-1")
    assert _pr_state_paths.load_gate_rerun_count(42, _WIDGET_FORGE) == 2
    assert _pr_state_paths.load_gate_rerun_count(42, _TARGET) == 0


def test_remediation_retry_count_reads_through_legacy_for_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    (tmp_path / "pr_9_remediation_retries.json").write_text("2")
    assert _pr_state_paths.load_remediation_retry_count(9, _TARGET) == 2


def test_remediation_retry_count_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_remediation_retry_count(9, _TARGET)
    assert _pr_state_paths.load_remediation_retry_count(9, _WIDGET_FORGE) == 0
    assert _pr_state_paths.load_remediation_retry_count(9, _TARGET) == 1


def _parse_int_or_default(raw: object) -> int:
    return raw if isinstance(raw, int) else -1


def test_read_json_with_legacy_fallback_namespaced_present_ignores_legacy(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    namespaced.write_text(json.dumps(1))
    legacy.write_text(json.dumps(2))
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, DEFAULT_FORGE_TARGET, -1, _parse_int_or_default,
    )
    assert result == 1


def test_read_json_with_legacy_fallback_namespaced_missing_legacy_present_default_forge(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(2))
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, DEFAULT_FORGE_TARGET, -1, _parse_int_or_default,
    )
    assert result == 2


def test_read_json_with_legacy_fallback_both_missing_returns_default(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, DEFAULT_FORGE_TARGET, -1, _parse_int_or_default,
    )
    assert result == -1


def test_read_json_with_legacy_fallback_malformed_namespaced_returns_default_no_legacy_read(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    namespaced.write_text("not json{")
    legacy.write_text(json.dumps(2))
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, DEFAULT_FORGE_TARGET, -1, _parse_int_or_default,
    )
    assert result == -1


def test_read_json_with_legacy_fallback_malformed_legacy_returns_default(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    legacy.write_text("not json{")
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, DEFAULT_FORGE_TARGET, -1, _parse_int_or_default,
    )
    assert result == -1


def test_read_json_with_legacy_fallback_non_default_forge_missing_namespaced_skips_legacy(
    tmp_path: Path,
) -> None:
    namespaced = tmp_path / "namespaced.json"
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(2))
    result = _pr_state_paths._read_json_with_legacy_fallback(  # noqa: SLF001
        namespaced, legacy, _WIDGET_FORGE, -1, _parse_int_or_default,
    )
    assert result == -1


def test_system_head_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.system_head_path(42, _TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_system_head.json"
    )


def test_load_system_head_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_system_head(42, _TARGET) is None


def test_save_and_load_system_head_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_system_head(42, "sha-1", "behind_plus_transient", _TARGET)
    marker = _pr_state_paths.load_system_head(42, _TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (
        "sha-1", "behind_plus_transient", False,
    )
    on_disk = json.loads(_pr_state_paths.system_head_path(42, _TARGET).read_text())
    assert on_disk.pop("written_at") == marker.written_at
    assert on_disk == {"head_sha": "sha-1", "rung": "behind_plus_transient", "forwarded": False}


def test_save_system_head_overwrites_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", _TARGET)
    _pr_state_paths.save_system_head(42, "sha-2", "stale_measured_baseline", _TARGET)
    marker = _pr_state_paths.load_system_head(42, _TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == (
        "sha-2", "stale_measured_baseline", False,
    )


def test_mark_system_head_forwarded_sets_flag_without_touching_other_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_system_head(42, "sha-1", "dirty", _TARGET)
    _pr_state_paths.mark_system_head_forwarded(42, _TARGET)
    marker = _pr_state_paths.load_system_head(42, _TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == ("sha-1", "dirty", True)


def test_mark_system_head_forwarded_noop_when_no_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.mark_system_head_forwarded(42, _TARGET)
    assert _pr_state_paths.load_system_head(42, _TARGET) is None
    assert not _pr_state_paths.system_head_path(42, _TARGET).exists()


def test_load_system_head_corrupt_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.system_head_path(42, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    assert _pr_state_paths.load_system_head(42, _TARGET) is None


@pytest.mark.parametrize(
    "payload",
    [
        "[1, 2, 3]",
        '"not-a-dict"',
        "42",
        json.dumps({"head_sha": 1, "rung": "dirty", "forwarded": False}),
        json.dumps({"head_sha": "", "rung": "dirty", "forwarded": False}),
        json.dumps({"head_sha": "sha-1", "rung": 1, "forwarded": False}),
        json.dumps({"head_sha": "sha-1", "rung": "", "forwarded": False}),
        json.dumps({"head_sha": "sha-1", "rung": "dirty", "forwarded": "yes"}),
        json.dumps({"rung": "dirty", "forwarded": False}),
    ],
)
def test_load_system_head_malformed_payloads_default_to_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.system_head_path(42, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload)
    assert _pr_state_paths.load_system_head(42, _TARGET) is None


def test_load_system_head_missing_forwarded_defaults_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.system_head_path(42, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-1", "rung": "dirty"}))
    marker = _pr_state_paths.load_system_head(42, _TARGET)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == ("sha-1", "dirty", False)


def test_system_head_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.save_system_head(5, "sha-1", "dirty", _WIDGET_FORGE)
    marker = _pr_state_paths.load_system_head(5, _WIDGET_FORGE)
    assert marker is not None
    assert (marker.head_sha, marker.rung, marker.forwarded) == ("sha-1", "dirty", False)
    assert _pr_state_paths.load_system_head(5, _TARGET) is None


def test_gate_rerun_state_specs_defaults_empty_and_carries_spec_list() -> None:
    state = _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",)
    )
    assert state.checks == ("ci/e2e",)
    assert state.specs == ()
    full = _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",), specs=("e2e/a.spec.ts",)
    )
    assert full.checks == ("ci/e2e",)
    assert full.specs == ("e2e/a.spec.ts",)


def test_load_gate_rerun_state_dict_payload_no_checks_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-1", "count": 1, "lifetime": 4}))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4,
    )


def test_load_gate_rerun_state_dict_payload_checks_present_no_specs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4, "checks": ["ci/e2e"],
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",),
    )


def test_load_gate_rerun_state_dict_payload_checks_and_specs_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4,
        "checks": ["ci/e2e"], "specs": ["e2e/a.spec.ts"],
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",), specs=("e2e/a.spec.ts",),
    )


def test_load_gate_rerun_state_malformed_specs_defaults_empty_but_keeps_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4,
        "checks": ["ci/e2e"], "specs": "not-a-list",
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",),
    )


def test_load_gate_rerun_state_non_string_spec_entries_default_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4,
        "checks": ["ci/e2e"], "specs": ["e2e/a.spec.ts", 1],
    }))
    assert _pr_state_paths.load_gate_rerun_state(5, _TARGET) == _pr_state_paths.GateRerunState(
        head_sha="sha-1", count=1, lifetime=4, checks=("ci/e2e",),
    )


def test_bump_gate_rerun_count_omits_specs_key_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1")
    on_disk = json.loads(_pr_state_paths.gate_rerun_count_path(42, _TARGET).read_text())
    assert "specs" not in on_disk


def test_bump_gate_rerun_count_writes_specs_sorted_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(
        42, _TARGET, head_sha="sha-1", specs=("e2e/b.spec.ts", "e2e/a.spec.ts"),
    )
    on_disk = json.loads(_pr_state_paths.gate_rerun_count_path(42, _TARGET).read_text())
    assert on_disk["specs"] == ["e2e/a.spec.ts", "e2e/b.spec.ts"]


def test_gate_rerun_specs_for_sha_matches_and_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_gate_rerun_count(42, _TARGET, head_sha="sha-1", specs=("e2e/a.spec.ts",))
    assert _pr_state_paths.gate_rerun_specs_for_sha(42, _TARGET, head_sha="sha-1") == (
        "e2e/a.spec.ts",
    )
    assert _pr_state_paths.gate_rerun_specs_for_sha(42, _TARGET, head_sha="sha-2") == ()
    assert _pr_state_paths.gate_rerun_specs_for_sha(42, _TARGET, head_sha=None) == ()


def test_load_reconcile_attempt_state_inert_to_stray_specs_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.reconcile_attempt_count_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "head_sha": "sha-1", "count": 1, "lifetime": 4, "specs": ["e2e/a.spec.ts"],
    }))
    state = _pr_state_paths.load_reconcile_attempt_state(5, _TARGET)
    assert (state.head_sha, state.count, state.lifetime) == ("sha-1", 1, 4)
    assert _pr_state_paths.reconcile_attempts_for_sha(5, _TARGET, head_sha="sha-1") == 1


def test_bump_reconcile_attempt_count_never_writes_specs_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_reconcile_attempt_count(5, _TARGET, head_sha="sha-1")
    on_disk = json.loads(_pr_state_paths.reconcile_attempt_count_path(5, _TARGET).read_text())
    assert "specs" not in on_disk
    assert "checks" not in on_disk


def test_acked_threads_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.acked_threads_path(42, _TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_acked_threads.json"
    )


def test_acked_threads_path_namespaced_second_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.acked_threads_path(42, _WIDGET_FORGE) == (
        tmp_path / "acme-corp" / "widget-app" / "pr_42_acked_threads.json"
    )


def test_claim_acked_thread_first_claim_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is True
    assert _pr_state_paths.acked_threads_path(42, _TARGET).exists()


def test_claim_acked_thread_second_claim_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is True
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is False


def test_claim_acked_thread_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _WIDGET_FORGE) is True
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is True


def test_claim_acked_thread_writes_sorted_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.claim_acked_thread(42, "PRTI_b", _TARGET)
    _pr_state_paths.claim_acked_thread(42, "PRTI_a", _TARGET)
    on_disk = json.loads(_pr_state_paths.acked_threads_path(42, _TARGET).read_text())
    assert on_disk == ["PRTI_a", "PRTI_b"]


def test_claim_acked_thread_missing_file_treated_as_unclaimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.claim_acked_thread(99, "PRTI_new", _TARGET) is True


def test_claim_acked_thread_corrupt_json_treated_as_unclaimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.acked_threads_path(5, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{")
    assert _pr_state_paths.claim_acked_thread(5, "PRTI_x", _TARGET) is True


def test_claim_acked_thread_non_list_payload_treated_as_unclaimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.acked_threads_path(6, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"not": "a list"}))
    assert _pr_state_paths.claim_acked_thread(6, "PRTI_x", _TARGET) is True


def test_claim_acked_thread_filters_non_string_and_empty_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.acked_threads_path(7, _TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(["PRTI_real", 5, "", None]))
    assert _pr_state_paths.claim_acked_thread(7, "PRTI_real", _TARGET) is False
    assert _pr_state_paths.claim_acked_thread(7, "PRTI_other", _TARGET) is True


def test_release_acked_thread_removes_claimed_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET)
    _pr_state_paths.release_acked_thread(42, "PRTI_1", _TARGET)
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is True


def test_release_acked_thread_missing_id_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET)
    _pr_state_paths.release_acked_thread(42, "PRTI_other", _TARGET)
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _TARGET) is False


def test_release_acked_thread_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.claim_acked_thread(42, "PRTI_1", _WIDGET_FORGE)
    _pr_state_paths.release_acked_thread(42, "PRTI_1", _TARGET)
    assert _pr_state_paths.claim_acked_thread(42, "PRTI_1", _WIDGET_FORGE) is False


def test_ack_budget_path_namespaced_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.ack_budget_path(42, _TARGET) == (
        tmp_path / "DejvMatikar" / "alan" / "pr_42_ack_budget.json"
    )


def test_load_ack_budget_missing_file_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 0


def test_bump_ack_budget_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1") == 1
    assert _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1") == 2
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 2
    on_disk = json.loads(_pr_state_paths.ack_budget_path(42, _TARGET).read_text())
    assert on_disk == {"head_sha": "sha-1", "count": 2, "lifetime": 2}


def test_ack_budget_resets_on_new_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-2") == 1
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-2") == 1


def test_ack_budget_fails_closed_to_lifetime_when_head_sha_unresolvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha=None) == 2
    assert _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha=None) == 3
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha=None) == 3


def test_ack_budget_second_engine_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_ack_budget(42, _WIDGET_FORGE, head_sha="sha-1")
    assert _pr_state_paths.load_ack_budget(42, _WIDGET_FORGE, head_sha="sha-1") == 1
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 0


def test_release_ack_budget_decrements_count_and_lifetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.release_ack_budget(42, _TARGET, head_sha="sha-1") == 1
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 1
    on_disk = json.loads(_pr_state_paths.ack_budget_path(42, _TARGET).read_text())
    assert on_disk == {"head_sha": "sha-1", "count": 1, "lifetime": 1}


def test_release_ack_budget_floors_at_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    assert _pr_state_paths.release_ack_budget(42, _TARGET, head_sha="sha-1") == 0
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 0


def test_release_ack_budget_with_unresolved_head_sha_uses_prior_anchor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    _pr_state_paths.bump_ack_budget(42, _TARGET, head_sha="sha-1")
    assert _pr_state_paths.release_ack_budget(42, _TARGET, head_sha=None) == 0
    assert _pr_state_paths.load_ack_budget(42, _TARGET, head_sha="sha-1") == 0
    on_disk = json.loads(_pr_state_paths.ack_budget_path(42, _TARGET).read_text())
    assert on_disk["head_sha"] == "sha-1"
