from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import RepoIdentity
from alan.teams import TEAMS
from alan.teams import _verification_prompts as vp_mod
from alan.teams import verification as verif_mod
from alan.teams._verdict import parse_critical_count, parse_verdict
from alan.teams.verification import (
    ROLES,
    TEAM,
    VerificationTeam,
)
from alan.workspace import Workspace, create
from tests import _teams_verification_support as support


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


def test_team_registration() -> None:
    assert TEAMS["verification"] is TEAM
    assert isinstance(TEAM, VerificationTeam)
    assert TEAM.name == "verification"


def test_verification_is_restart_rederivable() -> None:
    assert TEAM.restart_rederivable is True


def test_diff_reviewer_prompt_has_no_web_specific_content() -> None:
    prompt = verif_mod._DIFF_REVIEWER_PROMPT
    assert "DESIGN_SYSTEM.md" not in prompt
    assert "alan/web" not in prompt
    assert "capture:screens" not in prompt


def test_diff_reviewer_prompt_directs_reviewer_to_supplied_evidence() -> None:
    prompt = verif_mod._DIFF_REVIEWER_PROMPT
    assert "## PR EVIDENCE" in prompt
    assert "Do not attempt to fetch either yourself" in prompt
    assert "no `gh pr diff`, no `gh pr view`, no other forge read" in prompt


def test_ui_consistency_block_has_screen_capture_bullet() -> None:
    assert "capture:screens" in vp_mod.RENDERED_SCREEN_BLOCK
    assert "## Rendered-screen observation" in vp_mod.RENDERED_SCREEN_BLOCK
    assert "screen-captures" in vp_mod.RENDERED_SCREEN_BLOCK


def test_rendered_screen_block_frames_capture_as_best_effort() -> None:
    block = vp_mod.RENDERED_SCREEN_BLOCK
    assert "capture unavailable — reviewed from markup" in block
    assert "NEVER a reason to add a finding" in block


def test_ui_consistency_block_has_ui_consistency_pass() -> None:
    block = vp_mod.UI_CONSISTENCY_BLOCK
    assert "UI-CONSISTENCY PASS" in block
    assert "DESIGN_SYSTEM.md" in block
    assert "@docs/DESIGN_SYSTEM.md" not in block
    assert "WCAG" in block


def test_ui_consistency_block_ui_findings_ride_inline_path() -> None:
    block = vp_mod.UI_CONSISTENCY_BLOCK
    assert "rides the existing INLINE_FINDINGS_YAML path" in block
    assert "no new output section" in block


def test_ui_consistency_block_severity_is_calibrated() -> None:
    block = vp_mod.UI_CONSISTENCY_BLOCK
    assert "MEDIUM or LOW" in block
    assert (
        "CRITICAL is reserved for a genuine WCAG-AA accessibility regression"
        in block
    )


def test_diff_reviewer_prompt_has_inline_anchoring_rule() -> None:
    prompt = verif_mod._DIFF_REVIEWER_PROMPT
    assert (
        "Each entry's `path` and `line` must point at a line that "
        "was actually added or modified in this diff (the new/right-hand "
        "side of a diff hunk) — GitHub rejects an inline comment anchored "
        "outside the diff. When the CRITICAL finding is an omission rather "
        "than a bad line, anchor on the nearest changed line in the same "
        "file instead:"
    ) in prompt


def test_diff_reviewer_prompt_has_hunk_grounded_worked_example() -> None:
    prompt = verif_mod._DIFF_REVIEWER_PROMPT
    assert (
        "```yaml\n"
        "- path: alan/example.py\n"
        "  line: 88\n"
        "  body: |\n"
        "    Missing null check on `user` before calling .save() — line 88 "
        "    is the added line in this hunk (see the supplied ## PR "
        "    EVIDENCE diff) closest to the missing check.\n"
        "```"
    ) in prompt


def test_plan_workflow_default_matches_roles_constant(tmp_path: Path) -> None:
    ws = create("verif-default", "d", root=tmp_path / "ws")

    specs = TEAM.plan_workflow(ws, None)

    assert [s.id for s in specs] == [r.name for r in ROLES]
    assert [s.model for s in specs] == [r.model for r in ROLES]
    assert [s.depends_on for s in specs] == [
        [],
        [],
        ["diff_reviewer", "scenario_runner"],
    ]


def test_plan_workflow_resolves_models_from_pinned_engine(tmp_path: Path) -> None:
    from alan import workspace as workspace_mod
    from alan.engine_config import EngineConfig, Posture, RoleConfig
    from alan.workspace import EngineSnapshot

    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        roles={"judge": RoleConfig(model="haiku"), "diff_reviewer": RoleConfig(model="opus")},
        posture=Posture.autonomous,
    )
    ws = create("verif-pinned", "d", root=tmp_path / "ws")
    ws = workspace_mod.stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )

    specs = TEAM.plan_workflow(ws, None)

    by_id = {s.id: s.model for s in specs}
    assert by_id["diff_reviewer"] == "opus"
    assert by_id["scenario_runner"] == "opus"
    assert by_id["judge"] == "haiku"


def test_parse_verdict_approve() -> None:
    assert parse_verdict("VERDICT: APPROVE\nblah") == "APPROVE"


def test_parse_verdict_request_changes() -> None:
    assert parse_verdict("VERDICT: REQUEST_CHANGES\nblah") == "REQUEST_CHANGES"


def test_parse_verdict_missing() -> None:
    assert parse_verdict("no verdict line here") is None


def test_parse_critical_count_present() -> None:
    assert parse_critical_count("DIFF_FINDINGS_CRITICAL: 3\n") == 3


def test_parse_critical_count_missing_defaults_zero() -> None:
    assert parse_critical_count("no critical line") == 0


@pytest.mark.parametrize(
    "line",
    [
        "**VERDICT: APPROVE**",
        "`VERDICT: APPROVE`",
        "### VERDICT: APPROVE",
        "- VERDICT: APPROVE",
        "> VERDICT: APPROVE",
        "  VERDICT: APPROVE",
    ],
)
def test_parse_verdict_accepts_markdown_decoration(line: str) -> None:
    assert parse_verdict(f"## Verdict\n\n{line}\n") == "APPROVE"


def test_parse_verdict_request_changes_bold() -> None:
    assert parse_verdict("**VERDICT: REQUEST_CHANGES**") == "REQUEST_CHANGES"


def test_parse_verdict_ignores_inline_prose() -> None:
    assert parse_verdict("The VERDICT: APPROVE was posted earlier.") is None


def test_parse_verdict_rejects_trailing_prose_on_sentinel_line() -> None:
    assert parse_verdict("VERDICT: APPROVE was posted earlier") is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("`DIFF_FINDINGS_CRITICAL: 0`", 0),
        ("**DIFF_FINDINGS_CRITICAL: 3**", 3),
    ],
)
def test_parse_critical_count_accepts_markdown_decoration(
    line: str, expected: int
) -> None:
    assert parse_critical_count(line) == expected


def test_role_user_prefix_diff_reviewer_injects_populated_review_context() -> None:
    token = verif_mod._review_context_var.set("## Ratchet direction observation")  # noqa: SLF001
    try:
        prefix = TEAM._role_user_prefix("diff_reviewer")  # noqa: SLF001
    finally:
        verif_mod._review_context_var.reset(token)  # noqa: SLF001
    assert prefix == "## Ratchet direction observation"


def test_role_user_prefix_diff_reviewer_returns_empty_when_no_review_context() -> None:
    assert TEAM._role_user_prefix("diff_reviewer") == ""  # noqa: SLF001


def test_role_user_prefix_judge_ignores_populated_review_context() -> None:
    token = verif_mod._review_context_var.set("## Ratchet direction observation")  # noqa: SLF001
    try:
        prefix = TEAM._role_user_prefix("judge")  # noqa: SLF001
    finally:
        verif_mod._review_context_var.reset(token)  # noqa: SLF001
    assert prefix == ""


def test_role_user_prefix_scenario_runner_has_only_neutral_scope_when_no_results() -> None:
    assert TEAM._role_user_prefix("scenario_runner") == vp_mod.TARGETED_TEST_SCOPE_NEUTRAL  # noqa: SLF001


def test_targeted_test_scope_block_directs_diff_reviewer_and_scenario_runner() -> None:
    prefix = vp_mod.TARGETED_TEST_SCOPE_BLOCK
    assert "clause_auditor" in prefix
    assert "sole owner" in prefix
    assert "one full backend suite run" in prefix
    assert "one frontend coverage run" in prefix
    assert "targeted selection scoped to this diff" in prefix


def test_targeted_test_scope_neutral_names_no_commands() -> None:
    neutral = vp_mod.TARGETED_TEST_SCOPE_NEUTRAL
    assert "clause_auditor" in neutral
    assert "sole owner" in neutral
    assert "uv run" not in neutral
    assert "npm" not in neutral


def test_targeted_test_scope_prefix_not_appended_to_base_prompt_constants() -> None:
    assert "TEST SCOPE" not in verif_mod._DIFF_REVIEWER_PROMPT
    assert "TEST SCOPE" not in verif_mod._SCENARIO_RUNNER_PROMPT


def test_judge_role_prompt_unaffected_by_test_scope_guidance() -> None:
    role = next(r for r in ROLES if r.name == "judge")
    assert "TEST SCOPE" not in role.system_prompt


def test_capability_boundary_paragraph_mentions_clause_vacuity_trigger() -> None:
    prompt = verif_mod._JUDGE_PROMPT  # noqa: SLF001
    assert "Capability-boundary declaration" in prompt
    assert "vacuity grounds" in prompt
    assert "unreachable code" in prompt


def test_judge_prompt_forbids_running_suites_or_gates() -> None:
    prompt = verif_mod._JUDGE_PROMPT  # noqa: SLF001
    assert "You never run test suites, gate commands, or any `uv run`/`npm`" in prompt
    assert "test runs belong to the roles before you" in prompt
    assert "clause_auditor owns the full suites" in prompt
    assert "judge from the role outputs present here and from reading files" in prompt
    assert "if a suite result is absent from them, say so rather than assume it" in prompt
    assert "already ran each full suite" not in prompt
    assert "## TEST SCOPE" not in prompt
