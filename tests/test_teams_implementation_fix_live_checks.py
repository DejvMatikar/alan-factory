from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from alan import handover as handover_mod
from alan import process_runner
from alan.e2e_failures import FailingE2ETest
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.forge import active_forge
from alan.handover import Handover
from alan.teams import (
    _fix_gate_checks,
    _fix_non_actionable,
    _pr_body,
    _pr_branch,
    _pr_ready,
    _pr_sonar,
    _pr_threads,
    _verification_gates,
)
from alan.teams import implementation_fix as fix_mod
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import always, api_result, gh_unreachable, pr_read
from tests.gh_guard import GH_STUB_RETURNCODE, GH_STUB_STDERR


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()
    always(
        cast(FakeForge, active_forge()), "commit_check_runs_page",
        api_result("", returncode=GH_STUB_RETURNCODE, stderr=GH_STUB_STDERR),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("live-fix-task", "iterate on the open PR", root=tmp_path / "ws")


def _prior(
    *,
    from_team: str = "verification",
    pr_number: int | None = 42,
    full_synthesis: str = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1",
) -> Handover:
    return Handover(
        handover_id="handover_live_001",
        handover_type="forward",
        task_id="live-fix-task",
        from_team=from_team,
        from_invocation_id="inv_1",
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=full_synthesis,
        pr_number=pr_number,
    )


def _no_threads_or_sonar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_threads, "list_unresolved_threads", lambda _pr, **_k: ())
    monkeypatch.setattr(_pr_sonar, "list_sonarcloud_annotations", lambda _pr, **_k: ())


def _stub_live_checks(
    monkeypatch: pytest.MonkeyPatch, result: _fix_gate_checks.FailingChecks,
) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def fake(
        prior: Handover | None, task_id: str, *, forge_target: ForgeTarget,
    ) -> _fix_gate_checks.FailingChecks:
        calls.append((prior, task_id, forge_target))
        return result

    monkeypatch.setattr(_fix_gate_checks, "live_failing_checks", fake)
    return calls


def _boom_live_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> _fix_gate_checks.FailingChecks:
        raise AssertionError("live_failing_checks must not be called here")

    monkeypatch.setattr(_fix_gate_checks, "live_failing_checks", boom)


def _boom_readiness_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("must not read gate readiness when the live lookup is ineligible")

    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", boom)


def test_live_lookup_fires_when_verification_left_no_gate_block(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    detail = _pr_branch.CheckFailureDetail(
        name="Backend tests + coverage", title="Tests failed",
        summary="3 failures", details_url="https://example.com/run/1",
    )
    calls = _stub_live_checks(
        monkeypatch, _fix_gate_checks.FailingChecks(details=(detail,))
    )
    prior = _prior()
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks on PR #42" in text
    assert "3 failures" in text
    assert len(calls) == 1
    assert calls[0][0] is prior
    assert calls[0][1] == workspace.task_id
    assert calls[0][2] == DEFAULT_FORGE_TARGET


def test_live_lookup_skipped_when_gate_block_present_and_actionable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_live_checks(monkeypatch)
    monkeypatch.setattr(
        _pr_branch, "fetch_failing_check_details",
        lambda _pr, _names, **_k: (
            _pr_branch.CheckFailureDetail(
                name="SonarCloud", title="t", summary="s", details_url="u",
            ),
        ),
    )
    prior = _prior(
        full_synthesis=(
            "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
            "## External Gate Status\nGATE_ACTIONABLE: yes\n"
            "GATE_ACTIONABLE_CHECKS: SonarCloud\n"
        )
    )
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" in text
    assert "SonarCloud" in text


def test_live_lookup_skipped_when_gate_block_present_non_actionable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_readiness_read(monkeypatch)
    prior = _prior(
        full_synthesis=(
            "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
            "## External Gate Status\nGATE_ACTIONABLE: no\n"
        )
    )
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" not in text


def test_no_live_lookup_for_merge_lane_prior(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_readiness_read(monkeypatch)
    prior = _prior(from_team="pr_merge")
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" not in text


def test_no_live_lookup_when_prior_is_none(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _boom_readiness_read(monkeypatch)
    text = fix_mod._build_user_text(workspace, None)
    assert "## Failing required checks" not in text


def test_no_live_lookup_when_prior_has_no_pr(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_readiness_read(monkeypatch)
    prior = _prior(pr_number=None)
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" not in text


def test_pending_only_readiness_never_presented_as_failure(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable",
        lambda *_a, **_k: _pr_ready.PrReadiness(
            ok=False, merge_state_status="BLOCKED", review_decision="",
            pending_checks=("Backend tests + coverage",),
            head_sha="deadbeef",
        ),
    )
    prior = _prior()
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" not in text


def test_forge_failure_during_live_lookup_never_raises(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, _active_fake_forge: FakeForge,
) -> None:
    _no_threads_or_sonar(monkeypatch)

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "gh: api rate limit exceeded"

    monkeypatch.setattr(process_runner, "run_subprocess", lambda *_a, **_k: _Failed())
    always(
        _active_fake_forge, "read_pr",
        pr_read(returncode=_Failed.returncode, stderr=_Failed.stderr),
    )
    prior = _prior()
    text = fix_mod._build_user_text(workspace, prior)
    assert "## Failing required checks" not in text


def test_live_lookup_leaves_prior_handover_untouched(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _stub_live_checks(
        monkeypatch,
        _fix_gate_checks.FailingChecks(
            details=(
                _pr_branch.CheckFailureDetail(
                    name="X", title="t", summary="s", details_url="u",
                ),
            )
        ),
    )
    prior = _prior()
    before = prior.model_copy(deep=True)
    fix_mod._build_user_text(workspace, prior)
    assert prior == before


def test_live_lookup_block_names_no_gh_commands(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _stub_live_checks(
        monkeypatch,
        _fix_gate_checks.FailingChecks(
            details=(
                _pr_branch.CheckFailureDetail(
                    name="X", title="t", summary="s", details_url="u",
                ),
            )
        ),
    )
    prior = _prior()
    text = fix_mod._build_user_text(workspace, prior)
    assert "gh run view" not in text
    assert "gh pr checks" not in text
    assert "Reproduce the failure locally before changing anything" in text


def test_live_lookup_names_failing_e2e_tests(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _stub_live_checks(
        monkeypatch,
        _fix_gate_checks.FailingChecks(
            details=(
                _pr_branch.CheckFailureDetail(
                    name="Frontend E2E (Playwright, blocking)", title="failed",
                    summary="", details_url="u",
                ),
            ),
            failing_tests=(FailingE2ETest(spec="a.spec.ts", title="does the thing"),),
        ),
    )
    prior = _prior()
    text = fix_mod._build_user_text(workspace, prior)
    assert "### Failing E2E tests" in text
    assert "a.spec.ts — does the thing" in text


_CHECK = "Lint and typecheck"
_JOB_URL = "https://github.com/o/r/actions/runs/9/job/11"
_SOURCE_ANNOTATION: dict[str, object] = {
    "annotation_level": "failure", "path": "alan/teams/x.py", "start_line": 7,
    "title": "AssertionError", "message": "expected 1 got 0",
}
_CONFIG_ANNOTATION: dict[str, object] = {
    "annotation_level": "failure", "path": ".github", "start_line": 1,
    "title": "", "message": "Process completed with exit code 1.",
}


def _detail(*, name: str = _CHECK, check_run_id: int = 11) -> _pr_branch.CheckFailureDetail:
    return _pr_branch.CheckFailureDetail(
        name=name, title="", summary="", details_url=_JOB_URL, check_run_id=check_run_id,
    )


def _answered_fix_prior(pr_number: int | None = 42) -> Handover:
    return Handover.model_validate({
        "handover_id": "handover_live_002",
        "handover_type": "revisit_amend",
        "task_id": "live-fix-task",
        "from_team": "implementation_fix",
        "from_invocation_id": "inv_2",
        "iteration": 3,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "Recorded answer for question 'q1': re-dispatch",
        "pr_number": pr_number,
        "open_questions": [
            {
                "id": "q1", "question": "why", "resolver_role": "human",
                "blocking": True, "answer": "re-dispatch",
            }
        ],
    })


def _red_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="",
        failing_checks=(_CHECK,), head_sha="0123456789abcdef",
    )


def _stub_red_pull_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    annotations: list[dict[str, object]] | None,
    log: str = "",
    details: tuple[_pr_branch.CheckFailureDetail, ...] | None = None,
) -> dict[str, list[int]]:
    calls: dict[str, list[int]] = {"annotations": [], "logs": []}

    def fake_annotations(check_run_id: int, **_k: object) -> list[dict[str, object]] | None:
        calls["annotations"].append(check_run_id)
        return annotations

    def fake_log(job_id: int, **_k: object) -> str:
        calls["logs"].append(job_id)
        return log

    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_k: None)
    monkeypatch.setattr(
        _pr_ready, "readiness_or_unavailable", lambda *_a, **_k: _red_readiness()
    )
    monkeypatch.setattr(
        _verification_gates, "_resolve_actionable_gate_checks",  # noqa: SLF001
        lambda *_a, **_k: ((_CHECK,), ()),
    )
    monkeypatch.setattr(
        _pr_branch, "fetch_failing_check_details",
        lambda *_a, **_k: details if details is not None else (_detail(),),
    )
    monkeypatch.setattr(_pr_branch, "fetch_check_annotations", fake_annotations)
    monkeypatch.setattr(_pr_branch, "fetch_failed_log", fake_log)
    return calls


def test_answered_fix_park_round_renders_failing_checks_with_evidence(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    calls = _stub_red_pull_request(monkeypatch, annotations=[_SOURCE_ANNOTATION])
    text = fix_mod._build_user_text(workspace, _answered_fix_prior())
    assert "## Failing required checks on PR #42" in text
    assert f"### Check 1 — {_CHECK}" in text
    assert "alan/teams/x.py:7: AssertionError expected 1 got 0" in text
    assert "Failure evidence:" in text
    assert calls["logs"] == []


def test_answered_fix_park_round_falls_back_to_the_log_for_config_directory_annotations(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    calls = _stub_red_pull_request(
        monkeypatch, annotations=[_CONFIG_ANNOTATION], log="FAILED tests/test_x.py::test_y",
    )
    text = fix_mod._build_user_text(workspace, _answered_fix_prior())
    assert "FAILED tests/test_x.py::test_y" in text
    assert "Process completed with exit code 1." not in text
    assert calls["logs"] == [11]


def test_answered_fix_park_round_with_no_pr_reads_nothing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_readiness_read(monkeypatch)
    text = fix_mod._build_user_text(workspace, _answered_fix_prior(pr_number=None))
    assert "## Failing required checks" not in text


def test_evidence_reaches_a_gate_block_derived_failing_check(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _boom_live_checks(monkeypatch)
    calls = _stub_red_pull_request(monkeypatch, annotations=[_SOURCE_ANNOTATION])
    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", lambda *_a, **_k: _red_readiness())
    prior = _prior(
        full_synthesis=(
            "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
            "## External Gate Status\nGATE_ACTIONABLE: yes\n"
            f"GATE_ACTIONABLE_CHECKS: {_CHECK}\n"
        )
    )
    text = fix_mod._build_user_text(workspace, prior)
    assert "alan/teams/x.py:7: AssertionError expected 1 got 0" in text
    assert calls["annotations"] == [11]


def test_checks_beyond_the_inspection_cap_keep_name_and_link_only(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    details = tuple(_detail(name=f"check {i}", check_run_id=100 + i) for i in range(5))
    calls = _stub_red_pull_request(monkeypatch, annotations=[_SOURCE_ANNOTATION], details=details)
    text = fix_mod._build_user_text(workspace, _answered_fix_prior())
    assert calls["annotations"] == [100, 101, 102]
    assert text.count("Failure evidence:") == 3
    assert "### Check 4 — check 3" in text
    assert "### Check 5 — check 4" in text


def test_a_park_answered_then_re_dispatched_carries_the_contradiction_and_the_evidence(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _stub_red_pull_request(monkeypatch, annotations=[_SOURCE_ANNOTATION])
    park = _fix_non_actionable.check_non_actionable(
        workspace, _prior(), "inv_fix1234", "## Summary\nnothing", [],
        "CI is flaky and unrelated to this diff",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert park is not None
    [question] = park.open_questions
    assert question.recommended is not None
    handover_mod.save_handover(workspace, park)
    answered = handover_mod.record_answer(
        workspace, park, question.id, question.recommended, from_team=park.from_team,
    )
    text = fix_mod._build_user_text(workspace, answered)
    assert "contradicts the claim that nothing here is actionable" in text
    assert f"Recorded answer for question '{question.id}'" in text
    assert "## Failing required checks on PR #42" in text
    assert "alan/teams/x.py:7: AssertionError expected 1 got 0" in text


def test_a_park_answered_then_re_dispatched_differs_from_the_first_round_without_any_excerpt(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_threads_or_sonar(monkeypatch)
    _stub_red_pull_request(monkeypatch, annotations=None, log="")
    first_round = fix_mod._build_user_text(workspace, _prior())
    park = _fix_non_actionable.check_non_actionable(
        workspace, _prior(), "inv_fix1234", "## Summary\nnothing", [],
        "CI is flaky and unrelated to this diff",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert park is not None
    [question] = park.open_questions
    assert question.recommended is not None
    handover_mod.save_handover(workspace, park)
    answered = handover_mod.record_answer(
        workspace, park, question.id, question.recommended, from_team=park.from_team,
    )
    second_round = fix_mod._build_user_text(workspace, answered)
    assert "Failure evidence:" not in second_round
    assert f"### Check 1 — {_CHECK}" in second_round
    assert "contradicts the claim that nothing here is actionable" in second_round
    assert "contradicts the claim that nothing here is actionable" not in first_round
