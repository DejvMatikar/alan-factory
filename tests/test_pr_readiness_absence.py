from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_ready
from tests._pr_branch_support import _audit_entries, set_verifier_transport_token
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read, readiness_fields

_PASSING_CI = {"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    set_verifier_transport_token(monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.mark.parametrize(
    ("check_count", "failing", "pending", "passing", "expected"),
    [
        (0, (), (), (), True),
        (1, (), (), (), False),
        (0, ("x",), (), (), False),
        (0, (), ("x",), (), False),
        (0, (), (), ("x",), False),
        (3, ("a",), ("b",), ("c",), False),
    ],
)
def test_results_absent_truth_table(
    check_count: int,
    failing: tuple[str, ...],
    pending: tuple[str, ...],
    passing: tuple[str, ...],
    expected: bool,
) -> None:
    readiness = _pr_ready.PrReadiness(
        ok=True,
        merge_state_status="CLEAN",
        review_decision="APPROVED",
        check_count=check_count,
        failing_checks=failing,
        pending_checks=pending,
        passing_checks=passing,
    )
    assert _pr_ready.results_absent(readiness) is expected


def test_clean_empty_rollup_is_not_ready_with_no_check_results_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", head_sha="a" * 40)))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is False
    assert "no check results" in readiness.failed_reasons
    assert readiness.check_count == 0


def test_skipped_only_rollup_is_not_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    skipped = {"name": "docs-only", "conclusion": "SKIPPED", "status": "COMPLETED"}
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[skipped])))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.check_count == 1
    assert readiness.ok is True
    assert "no check results" not in readiness.failed_reasons


def test_populated_passing_rollup_regression_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI])))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.check_count == 1
    assert "no check results" not in readiness.failed_reasons


def test_head_sha_populated_from_head_ref_oid_on_merge_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI], head_sha="b" * 40)
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.head_sha == "b" * 40


def test_head_sha_populated_from_head_ref_oid_on_verifier_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI], head_sha="c" * 40)
    forge.queue("read_pr", pr_read(fields))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.head_sha == "c" * 40


def test_transient_readiness_has_empty_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(returncode=1))
    readiness = _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    assert isinstance(readiness, _pr_ready.ReadinessUnavailable)
    assert _pr_ready.observed_head_sha(readiness) == ""


def test_audit_readiness_row_contains_head_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fields = readiness_fields("CLEAN", "APPROVED", rollup=[_PASSING_CI], head_sha="d" * 40)
    forge.queue("read_pr", pr_read(fields))
    _pr_ready.check_pr_merge_readiness(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)
    row = _audit_entries(tmp_path)[-1]
    assert row["head_sha"] == "d" * 40
    assert row["check_count"] == 1


def test_verifier_blocker_reasons_unchanged_by_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED")))
    readiness = _pr_ready.check_pr_verifier_blockers(
        42, "task-x", forge_target=DEFAULT_FORGE_TARGET
    )
    assert isinstance(readiness, _pr_ready.PrReadiness)
    assert readiness.ok is True
    assert readiness.failed_reasons == ()
