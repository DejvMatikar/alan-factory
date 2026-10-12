from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import unblock_recipes as recipes_mod
from alan.handover import ArtifactRef, Handover, OpenQuestion
from alan.teams import _fix_escalation
from tests import _unblock_support as support

_E2E = "Frontend E2E (Playwright, blocking)"
_BACKEND = "Backend tests + coverage"
_SPEC_TEST = (
    "alan/web/e2e/home.spec.ts — [chromium] › e2e/home.spec.ts:52:3 › no layout jump"
)
_RATCHET_TEST = "alan/web/e2e/home.spec.ts — Flake ratchet: new flaky spec file"
_REASON = "The judge review reports zero blocking code findings."


def _gate_block(*, actionable: bool = False, with_tests: bool = True) -> str:
    lines = [
        "VERDICT: REQUEST_CHANGES",
        "DIFF_FINDINGS_CRITICAL: 0",
        "",
        "## External Gate Status",
        "Downgraded APPROVE → REQUEST_CHANGES because GitHub reports the PR is not "
        "currently mergeable (mergeStateStatus / reviewDecision / required checks):",
        f"- failing checks: {_E2E}, {_BACKEND}",
        "(mergeStateStatus=BLOCKED, reviewDecision=CHANGES_REQUESTED)",
        "GATE_ACTIONABLE: yes" if actionable else "GATE_ACTIONABLE: no",
        "No failing check is attributable to this diff — every blocker is merge state, "
        "a pending check, or a quarantined/known-flaky check.",
    ]
    if with_tests:
        lines.append(f"GATE_FAILING_TEST: {_SPEC_TEST}")
        lines.append(f"GATE_FAILING_TEST: {_RATCHET_TEST}")
    return "\n".join(lines) + "\n"


def _verification_handover(synthesis: str) -> Handover:
    return Handover(
        handover_id="handover_t_v1",
        handover_type="forward",
        task_id="t",
        from_team="verification",
        from_invocation_id="inv_008",
        iteration=4,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
        pr_number=1159,
    )


def test_parse_gate_cause_reads_failing_checks_tests_and_status_from_gate_block() -> None:
    cause = _fix_escalation.parse_gate_cause(_gate_block())
    assert cause == _fix_escalation.GateCause(
        failing_checks=(_E2E, _BACKEND),
        failing_tests=(_SPEC_TEST,),
        ratchet_verdicts=(_RATCHET_TEST,),
        merge_state_status="BLOCKED",
        review_decision="CHANGES_REQUESTED",
    )


def test_parse_gate_cause_keeps_commas_inside_parenthesised_check_names() -> None:
    cause = _fix_escalation.parse_gate_cause(_gate_block())
    assert cause is not None
    assert _E2E in cause.failing_checks


def test_parse_gate_cause_none_without_gate_block() -> None:
    assert _fix_escalation.parse_gate_cause("VERDICT: REQUEST_CHANGES\nrename the symbol") is None


def test_parse_gate_cause_none_when_gate_actionable_yes() -> None:
    assert _fix_escalation.parse_gate_cause(_gate_block(actionable=True)) is None


def test_parse_gate_cause_tolerates_a_block_without_reason_status_or_test_lines() -> None:
    synthesis = "VERDICT: REQUEST_CHANGES\n## External Gate Status\nGATE_ACTIONABLE: no\n"
    assert _fix_escalation.parse_gate_cause(synthesis) == _fix_escalation.GateCause(
        failing_checks=(),
        failing_tests=(),
        ratchet_verdicts=(),
        merge_state_status="",
        review_decision="",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a, , b", ("a", "b")),
        (
            "SonarCloud (blocking quality gate), x (y, z)",
            ("SonarCloud (blocking quality gate)", "x (y, z)"),
        ),
        ("stray ) paren, next", ("stray ) paren", "next")),
        ("   ", ()),
    ],
)
def test_split_check_names_splits_only_on_top_level_commas(
    raw: str, expected: tuple[str, ...],
) -> None:
    assert _fix_escalation._split_check_names(raw) == expected  # noqa: SLF001


def test_non_actionable_question_recommends_pr_merge_with_the_concrete_cause() -> None:
    question = _fix_escalation.non_actionable_question(
        _verification_handover(_gate_block()), "inv_009", _REASON,
    )
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch pr_merge")
    assert f"failing checks: {_E2E}, {_BACKEND}" in question.recommended
    assert f"failing tests: {_SPEC_TEST}" in question.recommended
    assert f"flake ratchet: {_RATCHET_TEST}" in question.recommended
    assert "mergeStateStatus=BLOCKED, reviewDecision=CHANGES_REQUESTED" in question.question
    assert question.question.startswith(_REASON)


def test_non_actionable_question_reads_gate_cause_from_review_artifact(
    tmp_path: Path,
) -> None:
    review_path = tmp_path / "review.md"
    review_path.write_text(_gate_block(), encoding="utf-8")
    handover = _verification_handover("spiral:non_actionable_finding: evidence").model_copy(
        update={
            "artifacts_produced": [
                ArtifactRef(path=str(review_path), description="full judge review (inv x)")
            ],
        }
    )
    question = _fix_escalation.non_actionable_question(handover, "inv_009", _REASON)
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch pr_merge")
    assert f"failing checks: {_E2E}, {_BACKEND}" in question.recommended


def test_non_actionable_question_maps_the_pr_merge_option_and_the_by_hand_option() -> None:
    question = _fix_escalation.non_actionable_question(
        _verification_handover(_gate_block()), "inv_009", _REASON,
    )
    assert question.recommended is not None
    assert question.option_resolvers == {
        question.recommended: "pr_merge",
        _fix_escalation.RESOLVE_BY_HAND: "verification",
    }
    assert question.proposed_answer_space == [question.recommended, _fix_escalation.RESOLVE_BY_HAND]


def test_non_actionable_question_names_absent_tests_and_ratchet_explicitly() -> None:
    question = _fix_escalation.non_actionable_question(
        _verification_handover(_gate_block(with_tests=False)), "inv_009", _REASON,
    )
    assert "failing tests: none named" in question.question
    assert "flake ratchet: not triggered" in question.question


def test_non_actionable_question_summary_marks_missing_checks_and_status() -> None:
    synthesis = "VERDICT: REQUEST_CHANGES\n## External Gate Status\nGATE_ACTIONABLE: no\n"
    question = _fix_escalation.non_actionable_question(
        _verification_handover(synthesis), "inv_009", _REASON,
    )
    assert "failing checks: none" in question.question
    assert "mergeStateStatus=?, reviewDecision=?" in question.question


@pytest.mark.parametrize(
    "prior",
    [None, "VERDICT: REQUEST_CHANGES\nrename the symbol", _gate_block(actionable=True)],
)
def test_non_actionable_question_falls_back_to_generic_options_without_a_gate_cause(
    prior: str | None,
) -> None:
    handover = _verification_handover(prior) if prior is not None else None
    question = _fix_escalation.non_actionable_question(handover, "inv_009", _REASON)
    assert question.question == _REASON
    assert question.proposed_answer_space == list(_fix_escalation.GENERIC_OPTIONS)
    assert question.recommended == _fix_escalation.RESOLVE_BY_HAND
    assert question.option_resolvers == {_fix_escalation.RESOLVE_BY_HAND: "verification"}


@pytest.mark.parametrize("synthesis", [_gate_block(), "no gate block here"])
def test_non_actionable_question_option_resolver_destinations_are_manually_dispatchable(
    synthesis: str,
) -> None:
    from alan.teams import MANUALLY_DISPATCHABLE_TEAMS, TEAMS

    question = _fix_escalation.non_actionable_question(
        _verification_handover(synthesis), "inv_009", _REASON,
    )
    assert question.option_resolvers
    for team_name in question.option_resolvers.values():
        assert team_name in TEAMS
        assert team_name in MANUALLY_DISPATCHABLE_TEAMS


@pytest.mark.parametrize("synthesis", [_gate_block(), "no gate block here"])
def test_non_actionable_question_keeps_the_fix_escalation_class_and_id_prefix(
    synthesis: str,
) -> None:
    question = _fix_escalation.non_actionable_question(
        _verification_handover(synthesis), "inv_009abc", _REASON,
    )
    assert question.id == "non_actionable_feedback_inv_009a"
    assert question.question_class == "fix_escalation"
    assert question.resolver_role == "human"
    assert question.blocking is True
    assert question.recommended in question.proposed_answer_space


_RED = "Lint and typecheck"
_HEAD = "0123456789abcdef0123"


def _contradicted(
    *,
    checks: tuple[str, ...] = (_RED,),
    ceiling_reached: bool = False,
    head_sha: str = _HEAD,
    prior: Handover | None = None,
) -> OpenQuestion:
    return _fix_escalation.non_actionable_question(
        prior, "inv_009abc", _REASON,
        contradicted=checks, ceiling_reached=ceiling_reached, head_sha=head_sha,
    )


def test_contradiction_question_names_the_check_the_head_and_the_original_claim() -> None:
    question = _contradicted()
    assert question.question.startswith(_REASON)
    assert _RED in question.question
    assert _HEAD[:12] in question.question
    assert _HEAD[:13] not in question.question
    assert "no flake evidence excuses them" in question.question


def test_contradiction_question_offers_a_fix_option_scoped_to_the_named_check() -> None:
    question = _contradicted()
    [fix_option, by_hand] = question.proposed_answer_space
    assert fix_option.startswith(f"Re-dispatch implementation_fix scoped to {_RED}")
    assert "contradicts the claim that nothing here is actionable" in fix_option
    assert by_hand == _fix_escalation.RESOLVE_BY_HAND
    assert question.recommended == fix_option
    assert question.option_resolvers == {
        fix_option: "implementation_fix",
        _fix_escalation.RESOLVE_BY_HAND: "verification",
    }


def test_contradiction_question_at_the_ceiling_drops_the_fix_resolver_and_recommends_it() -> None:
    question = _contradicted(ceiling_reached=True)
    [ceiling_option, by_hand] = question.proposed_answer_space
    assert "fix-cycle ceiling is reached" in ceiling_option
    assert _RED in ceiling_option
    assert not ceiling_option.startswith("Re-dispatch implementation_fix")
    assert by_hand == _fix_escalation.RESOLVE_BY_HAND
    assert question.recommended == _fix_escalation.RESOLVE_BY_HAND
    assert question.option_resolvers == {_fix_escalation.RESOLVE_BY_HAND: "verification"}


@pytest.mark.parametrize("ceiling_reached", [False, True])
def test_the_by_hand_option_is_the_identical_constant_in_every_contradiction_shape(
    ceiling_reached: bool,
) -> None:
    question = _contradicted(ceiling_reached=ceiling_reached)
    assert question.proposed_answer_space[1] is _fix_escalation.RESOLVE_BY_HAND
    assert _fix_escalation.RESOLVE_BY_HAND in question.proposed_answer_space


def test_contradiction_question_caps_the_named_checks_at_five_and_counts_the_rest() -> None:
    names = tuple(f"check {i}" for i in range(8))
    question = _contradicted(checks=names)
    assert "check 0, check 1, check 2, check 3, check 4 and 3 more" in question.question
    assert "check 5" not in question.question
    assert "check 5" not in question.proposed_answer_space[0]


def test_contradiction_question_does_not_count_the_rest_at_exactly_five_names() -> None:
    names = tuple(f"check {i}" for i in range(5))
    question = _contradicted(checks=names)
    assert "check 4 are red" in question.question
    assert "more" not in question.question


def test_contradiction_question_says_unknown_when_the_head_was_not_reported() -> None:
    assert "at head unknown" in _contradicted(head_sha="").question


def test_contradiction_beats_a_parsed_external_gate_cause() -> None:
    question = _contradicted(prior=_verification_handover(_gate_block()))
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch implementation_fix")
    assert "External gate cause" not in question.question
    assert "pr_merge" not in question.option_resolvers.values()


def test_no_contradiction_leaves_the_gate_cause_question_untouched() -> None:
    question = _fix_escalation.non_actionable_question(
        _verification_handover(_gate_block()), "inv_009", _REASON, contradicted=(),
    )
    assert question.recommended is not None
    assert question.recommended.startswith("Re-dispatch pr_merge")


@pytest.mark.parametrize("ceiling_reached", [False, True])
def test_contradiction_option_resolver_destinations_are_manually_dispatchable(
    ceiling_reached: bool,
) -> None:
    from alan.teams import MANUALLY_DISPATCHABLE_TEAMS, TEAMS

    question = _contradicted(ceiling_reached=ceiling_reached)
    for team_name in question.option_resolvers.values():
        assert team_name in TEAMS
        assert team_name in MANUALLY_DISPATCHABLE_TEAMS


@pytest.mark.parametrize("ceiling_reached", [False, True])
def test_the_unblocker_still_recognises_the_contradiction_park(
    tmp_path: Path, ceiling_reached: bool,
) -> None:
    question = _contradicted(ceiling_reached=ceiling_reached)
    ws = support.parked_ws(tmp_path)
    latest = support.handover(
        ws, from_team="implementation_fix", questions=[question], pr_number=123
    )
    ctx = support.context(ws, latest=latest, pr_number=123)
    assert recipes_mod._match_non_actionable(ctx) is True  # noqa: SLF001
