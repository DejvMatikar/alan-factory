from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import alan.teams._pr_merge_post_update as _pr_merge_post_update
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_feedback, _pr_merge_checks, _pr_ready
from alan.teams import pr_merge as pr_merge_mod
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, merge_attempt

_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
    authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
)

_NO_AUTHORITATIVE_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme", repo="widget"),
    credential_env_name="ACME_GH_TOKEN",
)

_THREAD_ZERO = _pr_feedback.ThreadInfo(
    copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0
)
_COMMENT_ZERO = _pr_feedback.CommentInfo(
    copilot_items=(), human_items=0, other_bot_items=0, total=0
)

_ADVISORY_CHECK_NAME = "Coverage upload (changed lines, advisory)"

_TOLERABLE_LOCAL_CHECKS = [
    {"name": "SonarCloud Code Analysis", "state": "SUCCESS"},
    {"name": _ADVISORY_CHECK_NAME, "state": "IN_PROGRESS"},
]

_TOLERANT_READINESS = _pr_ready.PrReadiness(
    ok=False,
    merge_state_status="UNSTABLE",
    review_decision="",
    failing_checks=(),
    pending_checks=(_ADVISORY_CHECK_NAME,),
    passing_checks=("SonarCloud Code Analysis",),
    head_sha="deadbeef",
    check_count=2,
)

_PROCEED_READINESS = _pr_ready.PrReadiness(
    ok=True,
    merge_state_status="CLEAN",
    review_decision="",
    head_sha="deadbeef",
    check_count=1,
    passing_checks=("ci",),
)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("advisory-task", "gate the advisory merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
    raise AssertionError("readiness must not be fetched on this path")


def _audit_rows(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / _pr_merge_checks._ADVISORY_AUDIT_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _advisory_rows(tmp_path: Path) -> list[dict[str, object]]:
    return [r for r in _audit_rows(tmp_path) if r["action"] == "advisory_check_tolerated"]


def test_resolve_checks_kind_returns_readiness_marker_for_tolerated_ready(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _TOLERANT_READINESS
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("ready", "deadbeef", _TOLERANT_READINESS)
    assert _advisory_rows(tmp_path) == []


def test_resolve_checks_kind_success_path_calls_readiness_exactly_once(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def _record(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        calls.append(object())
        return _TOLERANT_READINESS

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _record)
    _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert len(calls) == 1


def test_resolve_checks_kind_never_tolerates_behind_merge_state(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    behind = replace(_TOLERANT_READINESS, merge_state_status="BEHIND")
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: behind)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_when_no_authoritative_checks_declared(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_NO_AUTHORITATIVE_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_when_authoritative_check_itself_pending(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sonar_still_pending = replace(
        _TOLERANT_READINESS,
        pending_checks=("SonarCloud Code Analysis", _ADVISORY_CHECK_NAME),
        passing_checks=(),
    )
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: sonar_still_pending
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_tolerant_path_skips_readiness_fetch_while_local_authoritative_check_pending(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    sonar_running_locally = [
        {"name": "SonarCloud Code Analysis", "state": "IN_PROGRESS"},
        {"name": _ADVISORY_CHECK_NAME, "state": "IN_PROGRESS"},
    ]
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, sonar_running_locally, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_pending_local_names_upper_cases_states_and_ignores_settled_checks() -> None:
    checks = [
        {"name": "SonarCloud Code Analysis", "state": "queued"},
        {"name": _ADVISORY_CHECK_NAME, "state": "IN_PROGRESS"},
        {"name": "Backend tests + coverage", "state": "SUCCESS"},
    ]
    assert _pr_merge_checks._pending_local_names(checks) == frozenset({  # noqa: SLF001
        "SonarCloud Code Analysis", _ADVISORY_CHECK_NAME,
    })


def test_resolve_checks_kind_refuses_when_readiness_gh_not_ok(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_ok = _pr_ready.ReadinessUnavailable("gh unavailable")
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: not_ok)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_when_check_results_absent(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    absent = _pr_ready.PrReadiness(
        ok=True, merge_state_status="UNSTABLE", review_decision="",
        check_count=0, head_sha="deadbeef",
    )
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: absent)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_when_head_sha_missing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_head = replace(_TOLERANT_READINESS, head_sha="")
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: no_head)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_on_changes_requested_review(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes_requested = replace(_TOLERANT_READINESS, review_decision="CHANGES_REQUESTED")
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: changes_requested
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_refuses_when_readiness_has_failing_check(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuinely_red = replace(_TOLERANT_READINESS, failing_checks=("Backend tests + coverage",))
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: genuinely_red
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_respects_unresolved_human_feedback_over_tolerated_checks(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _TOLERANT_READINESS
    )
    human_unresolved = _pr_feedback.ThreadInfo(
        copilot_threads=(), human_unresolved=1, other_bot_unresolved=0, total=1
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, human_unresolved, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("unresolved", "", None)


def test_resolve_checks_kind_respects_copilot_unresolved_feedback_over_tolerated_checks(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _TOLERANT_READINESS
    )
    copilot_thread = _pr_feedback.CopilotThread(thread_id="t1", body="fix this", database_id=9)
    copilot_pending = _pr_feedback.ThreadInfo(
        copilot_threads=(copilot_thread,), human_unresolved=0, other_bot_unresolved=0, total=1
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, copilot_pending, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("copilot_unresolved", "", None)


def test_resolve_checks_kind_respects_unresolved_ceiling_over_tolerated_checks(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _TOLERANT_READINESS
    )
    over_ceiling = _pr_feedback.ThreadInfo(
        copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=101
    )
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _TOLERABLE_LOCAL_CHECKS, over_ceiling, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("unresolved_ceiling", "", None)


def test_resolve_checks_kind_empty_local_checks_never_fetches_readiness(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, [], _THREAD_ZERO, _COMMENT_ZERO, forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_pending_fallback_sentinel_never_tolerated(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, _pr_merge_checks.PENDING_FALLBACK, _THREAD_ZERO, _COMMENT_ZERO,
        forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_local_check_with_blank_state_never_tolerated(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    malformed = [
        {"name": "", "state": ""},
        {"name": _ADVISORY_CHECK_NAME, "state": "PENDING"},
    ]
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, malformed, _THREAD_ZERO, _COMMENT_ZERO, forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_mixed_local_failure_and_pending_never_tolerated(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", _boom)
    mixed = [
        {"name": "Backend tests + coverage", "state": "FAILURE"},
        {"name": _ADVISORY_CHECK_NAME, "state": "PENDING"},
    ]
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, mixed, _THREAD_ZERO, _COMMENT_ZERO, forge_target=_FORGE,
    )
    assert result == ("pending", "", None)


def test_resolve_checks_kind_skips_tolerant_path_for_failed_kind(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []

    def unavailable(pr_number: int, *_a: object, **_kw: object) -> _pr_ready.ReadinessUnavailable:
        reads.append(pr_number)
        return _pr_ready.ReadinessUnavailable("gh unavailable")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", unavailable)
    failed_only = [{"name": "Backend tests + coverage", "state": "FAILURE"}]
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, failed_only, _THREAD_ZERO, _COMMENT_ZERO, forge_target=_FORGE,
    )
    assert result == ("failed", "", None)
    assert reads == [42]


def test_resolve_checks_kind_direct_ready_kind_still_confirmed_via_second_source(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = _pr_ready.PrReadiness(
        ok=True, merge_state_status="CLEAN", review_decision="",
        check_count=1, passing_checks=("ci",), head_sha="sha-clean",
    )
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: clean)
    all_green = [{"name": "ci", "state": "SUCCESS"}]
    result = _pr_merge_checks.resolve_checks_kind(
        workspace, 42, all_green, _THREAD_ZERO, _COMMENT_ZERO, forge_target=_FORGE,
    )
    assert result == ("ready", "sha-clean", None)


def test_execute_merge_writes_advisory_audit_only_after_merge_succeeds(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _PROCEED_READINESS
    )
    forge.queue("merge_pr", merge_attempt())
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "deadbeef", forge_target=_FORGE, advisory_readiness=_TOLERANT_READINESS,
    )
    assert outcome is not None
    assert outcome.kind == "ready"
    rows = _advisory_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["pr_number"] == 42
    assert rows[0]["merge_state_status"] == "UNSTABLE"
    assert rows[0]["declined_checks"] == [_ADVISORY_CHECK_NAME]
    assert rows[0]["authoritative_checks"] == ["SonarCloud Code Analysis"]
    assert rows[0]["task_id"] == workspace.task_id


def test_execute_merge_without_advisory_marker_never_writes_advisory_audit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _PROCEED_READINESS
    )
    forge.queue("merge_pr", merge_attempt())
    outcome = pr_merge_mod._execute_merge(workspace, 42, "deadbeef", forge_target=_FORGE)
    assert outcome is not None
    assert outcome.kind == "ready"
    assert _advisory_rows(tmp_path) == []


def test_execute_merge_head_mismatch_refusal_does_not_write_advisory_audit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _PROCEED_READINESS
    )
    forge.queue("merge_pr", merge_attempt(
        returncode=1, stderr="head branch was modified. Review and try the merge again.",
    ))
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "deadbeef", forge_target=_FORGE, advisory_readiness=_TOLERANT_READINESS,
    )
    assert outcome is None
    assert _advisory_rows(tmp_path) == []


def test_execute_merge_behind_during_merge_does_not_write_advisory_audit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, forge: ForgeScript,
) -> None:
    behind = replace(_PROCEED_READINESS, merge_state_status="BEHIND")
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: behind)
    forge.queue("request_branch_update", api_result())
    monkeypatch.setattr(
        _pr_merge_post_update, "record_merge_time_system_head_if_updated",
        lambda *_a, **_kw: None,
    )
    outcome = pr_merge_mod._execute_merge(
        workspace, 42, "deadbeef", forge_target=_FORGE, advisory_readiness=_TOLERANT_READINESS,
    )
    assert outcome is not None
    assert outcome.kind == "error"
    assert _advisory_rows(tmp_path) == []


def test_merge_or_dry_run_writes_advisory_audit_for_dry_run(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_PR_MERGE_DRY_RUN", "1")
    outcome = pr_merge_mod._merge_or_dry_run(
        workspace, 42, "deadbeef", forge_target=_FORGE, advisory_readiness=_TOLERANT_READINESS,
    )
    assert outcome is not None
    assert outcome.kind == "ready"
    rows = _advisory_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["pr_number"] == 42
