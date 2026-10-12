from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import loop_guard
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover, OpenQuestion
from alan.teams import (
    _fix_escalation,
    _fix_non_actionable,
    _pr_flake_ledger,
    _pr_ready,
    _pr_state_paths,
)
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("fix-non-actionable-task", "iterate on the open PR", root=tmp_path / "ws")


def _prior_handover(workspace: Workspace) -> Handover:
    return Handover(
        handover_id="handover_prior_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_prior",
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="## Verdict\nVERDICT: REQUEST_CHANGES\n",
        pr_number=42,
    )


def _audit_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / "impl_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_check_non_actionable_returns_none_when_authored_body_present(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", "## Summary\nno code", [], "CI flake",
        authored_body="## Why\nExplains it.", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is None


def test_check_non_actionable_returns_none_when_commits_present(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", "## Summary\nfixed", ["abc fix"], "CI flake",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is None


def test_check_non_actionable_returns_none_when_reason_is_none(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", "## Summary\nnothing", [], None,
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is None


def test_check_non_actionable_still_escalates_without_authored_body(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", "## Summary\nno code", [], "CI flake unrelated to this PR",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == []


def test_check_non_actionable_carries_full_synthesis_not_just_reason(
    workspace: Workspace,
) -> None:
    synthesis = (
        "## Summary\nNo code change.\n\n"
        "```yaml scenario_rebind.yaml\n"
        "s1: uv run pytest tests/test_renamed.py\n"
        "```"
    )
    handover = _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", synthesis, [], "CI flake unrelated to this PR",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    assert handover.full_synthesis == synthesis
    assert handover.open_questions[0].question == "CI flake unrelated to this PR"


def test_check_non_actionable_writes_non_actionable_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", "## Summary\nno code", [], "CI flake unrelated",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    rows = _audit_rows(tmp_path)
    row = next(r for r in rows if r["action"] == "non_actionable")
    assert row["reason"] == "CI flake unrelated"
    assert row["team"] == "implementation_fix"


def test_check_non_actionable_still_writes_malformed_rebind_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    synthesis = "```yaml scenario_rebind.yaml\n: broken: yaml: {{\n```"
    _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", synthesis, [], "CI flake",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    rows = _audit_rows(tmp_path)
    assert any(r["action"] == "scenario_rebind_unparsed" for r in rows)
    assert any(r["action"] == "non_actionable" for r in rows)


def test_check_non_actionable_still_writes_malformed_scenarios_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    synthesis = "```yaml test_scenarios.yaml\n: broken: yaml: {{\n```"
    _fix_non_actionable.check_non_actionable(
        workspace, None, "inv_001", synthesis, [], "CI flake",
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    rows = _audit_rows(tmp_path)
    assert any(r["action"] == "scenario_block_unparsed" for r in rows)


def test_emit_malformed_fence_audits_returns_parsed_scenarios(
    workspace: Workspace,
) -> None:
    synthesis = (
        "```yaml test_scenarios.yaml\n"
        "- id: s1\n"
        '  description: "d"\n'
        '  given: "g"\n'
        '  when: "w"\n'
        '  then: "t"\n'
        "```"
    )
    scenarios = _fix_non_actionable.emit_malformed_fence_audits(workspace, synthesis)
    assert [s.id for s in scenarios] == ["s1"]


def test_emit_malformed_fence_audits_no_op_without_any_block(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    scenarios = _fix_non_actionable.emit_malformed_fence_audits(
        workspace, "## Summary\nnothing here"
    )
    assert scenarios == []
    assert not (tmp_path / "impl_audit.jsonl").exists()


def test_non_actionable_handover_populates_options_class_and_recommendation(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable._non_actionable_handover(  # noqa: SLF001
        workspace, None, "inv_001", "## Summary\nno code", "CI flake unrelated to this PR"
    )
    [q] = handover.open_questions
    assert q.question_class == "fix_escalation"
    assert 2 <= len(q.proposed_answer_space) <= 4
    assert q.recommended in q.proposed_answer_space


def test_non_actionable_handover_recommended_option_routes_forward_to_verification(
    workspace: Workspace,
) -> None:
    handover = _fix_non_actionable._non_actionable_handover(  # noqa: SLF001
        workspace, None, "inv_001", "## Summary\nno code", "CI flake unrelated to this PR"
    )
    [q] = handover.open_questions
    assert q.option_resolvers == {_fix_escalation.RESOLVE_BY_HAND: "verification"}
    assert q.recommended == _fix_escalation.RESOLVE_BY_HAND
    assert q.option_resolvers[q.recommended] == "verification"


def test_non_actionable_handover_carries_synthesis_as_full_synthesis(
    workspace: Workspace,
) -> None:
    synthesis = "## Summary\nno code, see scenario_rebind block below\n"
    handover = _fix_non_actionable._non_actionable_handover(  # noqa: SLF001
        workspace, None, "inv_001", synthesis, "CI flake"
    )
    assert handover.full_synthesis == synthesis


def test_non_actionable_handover_with_prior_carries_iteration_and_pr(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    handover = _fix_non_actionable._non_actionable_handover(  # noqa: SLF001
        workspace, prior, "inv_002", "synthesis text", "reason text"
    )
    assert handover.iteration == prior.iteration + 1
    assert handover.revisit_of == prior.handover_id
    assert handover.pr_number == prior.pr_number


_RED = "Lint and typecheck"
_FLAKY = "Backend tests + coverage"
_HEAD = "0123456789abcdef0123"
_GATE_CAUSE = (
    "VERDICT: REQUEST_CHANGES\n## External Gate Status\nGATE_ACTIONABLE: no\n"
    f"- failing checks: {_RED}\n(mergeStateStatus=BLOCKED, reviewDecision=)\n"
)


def _readiness(
    *,
    failing: tuple[str, ...] = (_RED,),
    pending: tuple[str, ...] = (),
    quarantined: tuple[str, ...] = (),
    head_sha: str = _HEAD,
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="",
        failing_checks=failing, pending_checks=pending,
        quarantined_checks=quarantined, head_sha=head_sha,
    )


def _stub_readiness(
    monkeypatch: pytest.MonkeyPatch, readiness: _pr_ready.ReadinessResult,
) -> list[tuple[int, str, object]]:
    calls: list[tuple[int, str, object]] = []

    def fake(
        pr_number: int, task_id: str, *, forge_target: object,
    ) -> _pr_ready.ReadinessResult:
        calls.append((pr_number, task_id, forge_target))
        return readiness

    monkeypatch.setattr(_pr_ready, "readiness_or_unavailable", fake)
    return calls


def _stub_fix_cycles(monkeypatch: pytest.MonkeyPatch, *, count: int, allowance: int = 0) -> None:
    monkeypatch.setattr(
        _pr_state_paths, "load_fix_cycle_state",
        lambda *_a, **_k: _pr_state_paths.FixCycleState(count=count, allowance=allowance),
    )


def _park_question(
    workspace: Workspace, prior: Handover | None, *, reason: str = "CI is flaky",
) -> OpenQuestion:
    handover = _fix_non_actionable.check_non_actionable(
        workspace, prior, "inv_001abcd", "## Summary\nno code", [], reason,
        authored_body=None, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is not None
    [question] = handover.open_questions
    return question


def _assert_canned_pair(question: OpenQuestion, reason: str = "CI is flaky") -> None:
    assert question.question == reason
    assert question.proposed_answer_space == list(_fix_escalation.GENERIC_OPTIONS)
    assert question.recommended == _fix_escalation.RESOLVE_BY_HAND
    assert question.option_resolvers == {_fix_escalation.RESOLVE_BY_HAND: "verification"}


def test_unexcused_red_required_check_contradicts_the_claim_and_is_named(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=1)
    question = _park_question(workspace, _prior_handover(workspace))
    assert question.question.startswith("CI is flaky ")
    assert _RED in question.question
    assert _HEAD[:12] in question.question
    assert _HEAD[:13] not in question.question
    [fix_option, by_hand] = question.proposed_answer_space
    assert _RED in fix_option
    assert "contradicts the claim that nothing here is actionable" in fix_option
    assert by_hand == _fix_escalation.RESOLVE_BY_HAND
    assert question.recommended == fix_option
    assert question.option_resolvers == {
        fix_option: "implementation_fix",
        _fix_escalation.RESOLVE_BY_HAND: "verification",
    }
    assert question.question_class == "fix_escalation"
    assert question.id.startswith(_fix_escalation.QUESTION_ID_PREFIX)


def test_the_contradiction_park_still_records_the_non_actionable_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=0)
    _park_question(workspace, _prior_handover(workspace))
    assert any(r["action"] == "non_actionable" for r in _audit_rows(tmp_path))


def test_the_readiness_read_targets_the_prior_pr_task_and_forge(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=0)
    _park_question(workspace, _prior_handover(workspace))
    assert calls == [(42, workspace.task_id, DEFAULT_FORGE_TARGET)]


def test_an_allowlisted_red_check_without_survivor_evidence_keeps_the_canned_pair(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness(failing=(_FLAKY,)))
    monkeypatch.setattr(_pr_flake_ledger, "survived_at_head_checks", lambda *_a, **_k: frozenset())
    _assert_canned_pair(_park_question(workspace, _prior_handover(workspace)))


def test_an_allowlisted_red_check_that_survived_a_rerun_at_head_contradicts_the_claim(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness(failing=(_FLAKY,)))
    _stub_fix_cycles(monkeypatch, count=0)
    monkeypatch.setattr(
        _pr_flake_ledger, "survived_at_head_checks", lambda *_a, **_k: frozenset({_FLAKY})
    )
    question = _park_question(workspace, _prior_handover(workspace))
    assert _FLAKY in question.question
    assert question.recommended is not None
    assert question.option_resolvers[question.recommended] == "implementation_fix"


def test_a_quarantined_red_check_keeps_the_canned_pair(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness(quarantined=(_RED,)))
    _assert_canned_pair(_park_question(workspace, _prior_handover(workspace)))


def test_a_pending_only_pull_request_keeps_the_canned_pair(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness(failing=(), pending=(_RED,)))
    _assert_canned_pair(_park_question(workspace, _prior_handover(workspace)))


def test_a_green_pull_request_keeps_the_canned_pair(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness(failing=()))
    _assert_canned_pair(_park_question(workspace, _prior_handover(workspace)))


def test_an_unavailable_readiness_read_still_mints_the_canned_park(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _pr_ready.ReadinessUnavailable("gh unavailable"))
    _assert_canned_pair(_park_question(workspace, _prior_handover(workspace)))


def test_a_prior_without_a_pull_request_never_reads_readiness(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_readiness(monkeypatch, _readiness())
    prior = _prior_handover(workspace).model_copy(update={"pr_number": None})
    _assert_canned_pair(_park_question(workspace, prior))
    assert calls == []


def test_no_prior_never_reads_readiness(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_readiness(monkeypatch, _readiness())
    _assert_canned_pair(_park_question(workspace, None))
    assert calls == []


@pytest.mark.parametrize(
    ("commits", "reason", "authored_body"),
    [(["abc fix"], "CI is flaky", None), ([], None, None), ([], "CI is flaky", "## Why\nbody")],
)
def test_paths_that_do_not_park_never_read_readiness(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
    commits: list[str], reason: str | None, authored_body: str | None,
) -> None:
    calls = _stub_readiness(monkeypatch, _readiness())
    handover = _fix_non_actionable.check_non_actionable(
        workspace, _prior_handover(workspace), "inv_001abcd", "## Summary\nx", commits, reason,
        authored_body=authored_body, forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover is None
    assert calls == []


def test_the_fix_cycle_ceiling_replaces_the_fix_option_and_recommends_resolving_by_hand(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=_pr_state_paths.FIX_CYCLE_CEILING)
    question = _park_question(workspace, _prior_handover(workspace))
    [ceiling_option, by_hand] = question.proposed_answer_space
    assert "fix-cycle ceiling is reached" in ceiling_option
    assert _RED in ceiling_option
    assert by_hand == _fix_escalation.RESOLVE_BY_HAND
    assert question.recommended == _fix_escalation.RESOLVE_BY_HAND
    assert question.option_resolvers == {_fix_escalation.RESOLVE_BY_HAND: "verification"}


def test_one_cycle_below_the_ceiling_still_offers_the_fix_option(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=_pr_state_paths.FIX_CYCLE_CEILING - 1)
    question = _park_question(workspace, _prior_handover(workspace))
    assert question.recommended is not None
    assert question.option_resolvers[question.recommended] == "implementation_fix"


def test_a_granted_allowance_lifts_the_ceiling(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=_pr_state_paths.FIX_CYCLE_CEILING, allowance=1)
    question = _park_question(workspace, _prior_handover(workspace))
    assert question.recommended is not None
    assert question.option_resolvers[question.recommended] == "implementation_fix"


@pytest.mark.parametrize(("count", "allowance"), [(0, 0), (5, 0), (6, 0), (7, 0), (6, 1), (7, 1)])
def test_the_ceiling_test_agrees_with_the_loop_guard_definition(
    monkeypatch: pytest.MonkeyPatch, count: int, allowance: int,
) -> None:
    _stub_fix_cycles(monkeypatch, count=count, allowance=allowance)
    assert _fix_non_actionable._fix_cycle_ceiling_reached(  # noqa: SLF001
        42, DEFAULT_FORGE_TARGET
    ) is loop_guard.fix_cycle_ceiling_reached(42, DEFAULT_FORGE_TARGET)


def test_the_by_hand_option_is_the_identical_constant_in_every_composed_answer_space(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spaces: list[list[str]] = []
    for count in (0, _pr_state_paths.FIX_CYCLE_CEILING):
        _stub_readiness(monkeypatch, _readiness())
        _stub_fix_cycles(monkeypatch, count=count)
        spaces.append(_park_question(workspace, _prior_handover(workspace)).proposed_answer_space)
    _stub_readiness(monkeypatch, _pr_ready.ReadinessUnavailable("gh unavailable"))
    spaces.append(_park_question(workspace, _prior_handover(workspace)).proposed_answer_space)
    assert all(_fix_escalation.RESOLVE_BY_HAND in space for space in spaces)


def test_a_live_contradiction_beats_a_parsed_external_gate_cause(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _readiness())
    _stub_fix_cycles(monkeypatch, count=0)
    prior = _prior_handover(workspace).model_copy(update={"full_synthesis": _GATE_CAUSE})
    question = _park_question(workspace, prior)
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch implementation_fix")
    assert "pr_merge" not in question.option_resolvers.values()


def test_a_parsed_external_gate_cause_still_routes_to_pr_merge_without_a_contradiction(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_readiness(monkeypatch, _pr_ready.ReadinessUnavailable("gh unavailable"))
    prior = _prior_handover(workspace).model_copy(update={"full_synthesis": _GATE_CAUSE})
    question = _park_question(workspace, prior)
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch pr_merge")


def test_non_actionable_handover_forwards_the_blocking_state_to_the_question(
    workspace: Workspace,
) -> None:
    blocking = _fix_non_actionable._BlockingState(  # noqa: SLF001
        contradicted=(_RED,), ceiling_reached=False, head_sha=_HEAD,
    )
    handover = _fix_non_actionable._non_actionable_handover(  # noqa: SLF001
        workspace, None, "inv_001abcd", "## Summary\nno code", "reason", blocking
    )
    [question] = handover.open_questions
    assert _RED in question.question
    assert _HEAD[:12] in question.question
