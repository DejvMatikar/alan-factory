from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_post, _pr_ready, _verification_gates
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, pr_read, readiness_fields

_SHA = "c" * 40
_TASK = "task-x"
_PR = 42
_READINESS_AUDIT_FILE = "impl_audit.jsonl"
_GATE_PATH_AUDIT_FILE = "verification_audit.jsonl"
_GREEN = [{"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}]
_RED_AND_RUNNING = [
    {"name": "lint", "conclusion": "FAILURE", "status": "COMPLETED"},
    {"name": "ci", "status": "IN_PROGRESS"},
]
_REQUEST_CHANGES_JUDGE = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
_CRITICAL_JUDGE = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture
def no_stale_review_dismissal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda *_a, **_k: False)


def _rows(logs: Path, file_name: str, action: str) -> list[dict[str, Any]]:
    path = logs / file_name
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [entry for entry in entries if entry["action"] == action]
    return [{k: v for k, v in row.items() if k != "timestamp_utc"} for row in rows]


def _readiness_rows(logs: Path) -> list[dict[str, Any]]:
    return _rows(logs, _READINESS_AUDIT_FILE, "pr_readiness_check")


def _gate_path_rows(logs: Path) -> list[dict[str, Any]]:
    return _rows(logs, _GATE_PATH_AUDIT_FILE, "gate_path")


def _unavailable_row(transient_reason: str) -> dict[str, Any]:
    return {
        "task_id": _TASK, "action": "pr_readiness_check", "pr_number": _PR,
        "ok": False, "gh_ok": False, "mergeStateStatus": "", "reviewDecision": "",
        "failing": [], "pending": [], "reasons": [],
        "transient_reason": transient_reason, "head_sha": "", "check_count": 0,
    }


def test_real_readiness_row_carries_every_check_field(
    logs: Path, forge: ForgeScript,
) -> None:
    fields = readiness_fields(
        "BLOCKED", "CHANGES_REQUESTED", rollup=_RED_AND_RUNNING, head_sha=_SHA,
    )
    forge.queue("read_pr", pr_read(fields))
    _pr_ready.check_pr_merge_readiness(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    assert _readiness_rows(logs) == [{
        "task_id": _TASK, "action": "pr_readiness_check", "pr_number": _PR,
        "ok": False, "gh_ok": True,
        "mergeStateStatus": "BLOCKED", "reviewDecision": "CHANGES_REQUESTED",
        "failing": ["lint"], "pending": ["ci"],
        "reasons": [
            "mergeStateStatus=BLOCKED",
            "review decision CHANGES_REQUESTED",
            "failing checks: lint",
            "pending checks: ci",
        ],
        "transient_reason": "", "head_sha": _SHA, "check_count": 2,
    }]


def test_green_readiness_row_is_ok_with_no_reasons(logs: Path, forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(readiness_fields("CLEAN", "APPROVED", rollup=_GREEN)))
    _pr_ready.check_pr_merge_readiness(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    assert _readiness_rows(logs) == [{
        "task_id": _TASK, "action": "pr_readiness_check", "pr_number": _PR,
        "ok": True, "gh_ok": True,
        "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
        "failing": [], "pending": [], "reasons": [],
        "transient_reason": "", "head_sha": "", "check_count": 1,
    }]


def test_gh_nonzero_exit_row_keeps_constant_defaults(logs: Path, forge: ForgeScript) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="boom"))
    _pr_ready.check_pr_merge_readiness(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    assert _readiness_rows(logs) == [_unavailable_row("gh returncode=1: boom")]


def test_unparseable_response_row_keeps_constant_defaults(
    logs: Path, forge: ForgeScript,
) -> None:
    forge.queue("read_pr", pr_read({}))
    _pr_ready.check_pr_merge_readiness(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    assert _readiness_rows(logs) == [
        _unavailable_row("gh pr view returned unparseable JSON")
    ]


def test_forge_exception_row_is_written_by_the_never_raising_wrapper(
    logs: Path, forge: ForgeScript,
) -> None:
    forge.queue("read_pr", FileNotFoundError("gh"))
    _pr_ready.readiness_or_unavailable(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    slug = DEFAULT_FORGE_TARGET.repo.slug
    assert _readiness_rows(logs) == [_unavailable_row(
        f"forge unavailable: gh pr view (readiness) PR #{_PR} [{slug}] raised gh"
    )]


def test_verifier_lane_failure_row_reports_the_dirty_block(
    logs: Path, forge: ForgeScript,
) -> None:
    fields = readiness_fields(
        "DIRTY", "CHANGES_REQUESTED", rollup=_GREEN, head_sha=_SHA, latestReviews=[],
    )
    forge.queue("read_pr", pr_read(fields))
    _pr_ready.check_pr_verifier_blockers(_PR, _TASK, forge_target=DEFAULT_FORGE_TARGET)
    assert _readiness_rows(logs) == [{
        "task_id": _TASK, "action": "pr_readiness_check", "pr_number": _PR,
        "ok": False, "gh_ok": True,
        "mergeStateStatus": "DIRTY", "reviewDecision": "",
        "failing": [], "pending": [], "reasons": ["mergeStateStatus=DIRTY"],
        "transient_reason": "", "head_sha": _SHA, "check_count": 1,
    }]


@pytest.mark.usefixtures("no_stale_review_dismissal")
def test_gate_path_row_with_readiness_carries_gh_ok_and_the_check_lists(
    logs: Path, forge: ForgeScript,
) -> None:
    fields = readiness_fields("DIRTY", "", rollup=_RED_AND_RUNNING, head_sha=_SHA)
    forge.queue("read_pr", pr_read(fields))
    forge.queue("commit_check_runs_page", api_result(json.dumps({"check_runs": []})))
    _verification_gates.resolve_external_gates(
        _REQUEST_CHANGES_JUDGE, _PR, _TASK, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert _gate_path_rows(logs) == [{
        "task_id": _TASK, "action": "gate_path", "pr_number": _PR, "mode": "rescue_only",
        "gh_ok": True, "failing_checks": ["lint"], "pending_checks": ["ci"],
    }]


@pytest.mark.usefixtures("no_stale_review_dismissal")
def test_gate_path_row_without_readiness_has_no_gh_ok_when_gh_is_unreachable(
    logs: Path, forge: ForgeScript,
) -> None:
    forge.queue("read_pr", pr_read(returncode=1, stderr="boom"))
    _verification_gates.resolve_external_gates(
        _REQUEST_CHANGES_JUDGE, _PR, _TASK, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert _gate_path_rows(logs) == [{
        "task_id": _TASK, "action": "gate_path", "pr_number": _PR, "mode": "rescue_only",
    }]


def test_gate_path_row_without_readiness_has_no_gh_ok_in_skip_mode(logs: Path) -> None:
    _verification_gates.resolve_external_gates(
        _CRITICAL_JUDGE, _PR, _TASK, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert _gate_path_rows(logs) == [{
        "task_id": _TASK, "action": "gate_path", "pr_number": _PR, "mode": "skip",
    }]
