from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import ArtifactRef, Handover, TestScenario
from alan.teams import _fix_forward
from alan.teams._pr_body import BodyAmendResult
from alan.workspace import Workspace, create


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("fix-forward-task", "iterate on the open PR", root=tmp_path / "ws")


def _prior_handover(
    workspace: Workspace, *, summary: str = "## Verdict\nVERDICT: REQUEST_CHANGES\n",
) -> Handover:
    return Handover(
        handover_id="handover_prior_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_prior",
        to_team_suggestions=["implementation_fix"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=summary,
        pr_number=42,
    )


def _scn_yaml(scn_id: str) -> str:
    return (
        f"- id: {scn_id}\n"
        '  description: "valid scenario"\n'
        '  given: "state"\n'
        '  when: "action"\n'
        '  then: "result"\n'
    )


def _fence(tag: str, body: str) -> str:
    return f"```{tag}\n{body}```\n"


def test_default_forbidden_actions_contains_only_scope_bullet() -> None:
    assert _fix_forward.DEFAULT_FORBIDDEN_ACTIONS == (
        "do not modify files outside the scope of the review findings",
    )


def test_absolute_forbidden_actions_contains_mechanical_and_safety_bullets() -> None:
    joined = " ".join(_fix_forward.ABSOLUTE_FORBIDDEN_ACTIONS)
    assert "editable install" in joined
    assert "amend prior commits" in joined
    assert "push or publish" in joined


def test_absolute_forbidden_actions_forbids_widening_a_ci_duration_budget() -> None:
    joined = " ".join(_fix_forward.ABSOLUTE_FORBIDDEN_ACTIONS)
    assert "a CI duration budget" in joined
    assert "do not widen a tightening-only ratchet baseline" in joined


def test_parse_fix_scenarios_returns_empty_when_no_block() -> None:
    assert _fix_forward.parse_fix_scenarios("## Summary\nNo scenarios here.") == []


def test_parse_fix_scenarios_returns_empty_on_invalid_yaml() -> None:
    synthesis = "```yaml test_scenarios.yaml\n: broken: yaml: {{\n```"
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_returns_empty_for_non_list() -> None:
    synthesis = "```yaml test_scenarios.yaml\nkey: value\n```"
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_skips_invalid_items_and_returns_valid() -> None:
    synthesis = (
        "```yaml test_scenarios.yaml\n"
        "- not_a_dict_string\n"
        "- id: bad_no_fields\n"
        "- id: s1\n"
        "  description: \"valid scenario\"\n"
        "  given: \"state\"\n"
        "  when: \"action\"\n"
        "  then: \"result\"\n"
        "```"
    )
    result = _fix_forward.parse_fix_scenarios(synthesis)
    assert len(result) == 1
    assert isinstance(result[0], TestScenario)
    assert result[0].id == "s1"


def test_parse_fix_scenarios_parses_bare_yaml_list() -> None:
    synthesis = "## Summary\nRe-emitting.\n\n" + _fence("yaml", _scn_yaml("bare_a"))
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["bare_a"]


def test_parse_fix_scenarios_parses_bare_yml_alias_list() -> None:
    synthesis = _fence("yml", _scn_yaml("bare_yml"))
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["bare_yml"]


def test_parse_fix_scenarios_parses_test_scenarios_heading_leniently() -> None:
    synthesis = (
        "## TEST SCENARIOS\n"
        + _fence("yaml", "- id: head_bad_no_fields\n" + _scn_yaml("head_ok"))
    )
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["head_ok"]


def test_parse_fix_scenarios_parses_bare_test_scenarios_mapping() -> None:
    body = "test_scenarios:\n" + "".join(
        "  " + line + "\n" for line in _scn_yaml("map_a").splitlines()
    )
    synthesis = _fence("yaml", body)
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["map_a"]


def test_parse_fix_scenarios_scoped_block_mapping_fallback() -> None:
    body = "test_scenarios:\n" + "".join(
        "  " + line + "\n" for line in _scn_yaml("scoped_map").splitlines()
    )
    synthesis = _fence("yaml test_scenarios.yaml", body)
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["scoped_map"]


def test_parse_fix_scenarios_rejects_bare_list_with_non_dict_item() -> None:
    synthesis = _fence("yaml", "- just_a_string\n" + _scn_yaml("would_be_valid"))
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_rejects_bare_list_with_incomplete_item() -> None:
    synthesis = _fence("yaml", "- id: missing_fields\n" + _scn_yaml("would_be_valid"))
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_rejects_inline_findings_shape() -> None:
    synthesis = _fence("yaml", "- path: alan/foo.py\n  line: 42\n  body: fix this\n")
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_rejects_non_actionable_info_string_block() -> None:
    synthesis = _fence(
        "yaml non_actionable_feedback.yaml", "reason: reviewer feedback is a flake\n"
    )
    assert _fix_forward.parse_fix_scenarios(synthesis) == []


def test_parse_fix_scenarios_skips_first_non_scenario_bare_block() -> None:
    synthesis = (
        _fence("yaml", "unrelated: config\nvalue: 1\n")
        + "\nsome prose\n\n"
        + _fence("yaml", _scn_yaml("second_block"))
    )
    assert [s.id for s in _fix_forward.parse_fix_scenarios(synthesis)] == ["second_block"]


def test_inbound_scenarios_missing_is_false_for_none() -> None:
    assert _fix_forward._inbound_scenarios_missing(None) is False  # noqa: SLF001


def test_inbound_scenarios_missing_true_when_marker_present(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(
        workspace, summary="VERDICT: REQUEST_CHANGES\nSCENARIOS_MISSING",
    )
    assert _fix_forward._inbound_scenarios_missing(prior) is True  # noqa: SLF001


def test_inbound_scenarios_missing_false_when_marker_absent(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    assert _fix_forward._inbound_scenarios_missing(prior) is False  # noqa: SLF001


def test_resolve_review_text_returns_full_synthesis_when_no_artifact_matches(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, summary="plain synthesis").model_copy(
        update={
            "artifacts_produced": [ArtifactRef(path="alan/x.py:1", description="impl change")],
        }
    )
    assert _fix_forward.resolve_review_text(prior) == "plain synthesis"


def test_resolve_review_text_reads_review_artifact_file(
    workspace: Workspace, tmp_path: Path,
) -> None:
    review_path = tmp_path / "review.md"
    review_path.write_text("SCENARIOS_MISSING\nfull review body", encoding="utf-8")
    prior = _prior_handover(workspace, summary="spiral:scenarios_missing: evidence").model_copy(
        update={
            "artifacts_produced": [
                ArtifactRef(path=str(review_path), description="full judge review (inv x)")
            ],
        }
    )
    assert _fix_forward.resolve_review_text(prior) == "SCENARIOS_MISSING\nfull review body"


def test_resolve_review_text_falls_back_when_artifact_unreadable(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, summary="fallback text").model_copy(
        update={
            "artifacts_produced": [
                ArtifactRef(path="/nonexistent/review.md", description="full judge review (inv x)")
            ],
        }
    )
    assert _fix_forward.resolve_review_text(prior) == "fallback text"


def test_inbound_scenarios_missing_true_when_marker_only_in_review_artifact(
    workspace: Workspace, tmp_path: Path,
) -> None:
    review_path = tmp_path / "review.md"
    review_path.write_text("SCENARIOS_MISSING", encoding="utf-8")
    prior = _prior_handover(workspace, summary="spiral:scenarios_missing: evidence").model_copy(
        update={
            "artifacts_produced": [
                ArtifactRef(path=str(review_path), description="full judge review (inv x)")
            ],
        }
    )
    assert _fix_forward._inbound_scenarios_missing(prior) is True  # noqa: SLF001


def test_scenarios_only_forward_handover_shape(workspace: Workspace) -> None:
    prior = _prior_handover(workspace)
    scenario = TestScenario(
        id="s1", description="d", given="g", when="w", then="t",
    )
    handover = _fix_forward.scenarios_only_forward_handover(
        workspace, prior, "inv_002", "synthesis text", 42, [scenario],
    )
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert handover.iteration == 3
    assert handover.revisit_of == prior.handover_id
    assert handover.pr_number == 42
    assert handover.test_scenarios == [scenario]
    assert handover.decisions[0].id == "fix-scenarios"


def test_scenarios_only_forward_handover_preserves_full_synthesis(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    handover = _fix_forward.scenarios_only_forward_handover(
        workspace, prior, "inv_002", "the worker's full synthesis text", 42, [scenario],
    )
    assert handover.full_synthesis == "the worker's full synthesis text"


def test_scenarios_only_forward_handover_no_prior(workspace: Workspace) -> None:
    handover = _fix_forward.scenarios_only_forward_handover(
        workspace, None, "inv_002", "synthesis text", None, [],
    )
    assert handover.iteration == 1
    assert handover.revisit_of is None
    assert handover.pr_number is None


def test_body_only_forward_handover_shape(workspace: Workspace) -> None:
    prior = _prior_handover(workspace)
    handover = _fix_forward.body_only_forward_handover(
        workspace, prior, "inv_002", "synthesis text", 42,
    )
    assert handover.handover_type == "forward"
    assert handover.to_team_suggestions == ["verification"]
    assert handover.pr_number == 42
    assert handover.decisions[0].id == "fix-body"
    assert handover.test_scenarios == list(prior.test_scenarios)


def test_body_only_forward_handover_preserves_full_synthesis(workspace: Workspace) -> None:
    prior = _prior_handover(workspace)
    handover = _fix_forward.body_only_forward_handover(
        workspace, prior, "inv_002", "the worker's full synthesis text", 42,
    )
    assert handover.full_synthesis == "the worker's full synthesis text"


def test_body_only_forward_handover_no_prior_carries_no_scenarios() -> None:
    ws = create("fix-forward-no-prior", "d")
    handover = _fix_forward.body_only_forward_handover(
        ws, None, "inv_002", "synthesis text", None,
    )
    assert handover.iteration == 1
    assert handover.test_scenarios == []


def test_no_code_forward_handover_prefers_body_amend_over_scenarios(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    amend = BodyAmendResult(ok=True, previous_body="old", new_body="new")
    handover = _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[scenario], amend=amend,
    )
    assert handover is not None
    assert handover.decisions[0].id == "fix-body"


def test_no_code_forward_handover_falls_back_to_scenarios_when_no_amend(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    handover = _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[scenario], amend=None,
    )
    assert handover is not None
    assert handover.decisions[0].id == "fix-scenarios"


def test_no_code_forward_handover_returns_none_when_nothing_to_forward(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    handover = _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[], amend=None,
    )
    assert handover is None


def test_no_code_forward_handover_body_path_writes_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    prior = _prior_handover(workspace)
    amend = BodyAmendResult(ok=True, previous_body="old", new_body="new")
    _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[], amend=amend,
    )
    entries = [
        json.loads(line)
        for line in (tmp_path / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "pr_body_amend_forward")
    assert row["pr_number"] == 42
    assert row["amend_ok"] is True
    assert row["team"] == "implementation_fix"


def test_no_code_forward_handover_returns_none_on_failed_amend_with_no_scenarios(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    amend = BodyAmendResult(ok=False, previous_body="old", new_body="new", error="404")
    handover = _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[], amend=amend,
    )
    assert handover is None


def test_no_code_forward_handover_falls_back_to_scenarios_when_amend_failed(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace)
    scenario = TestScenario(id="s1", description="d", given="g", when="w", then="t")
    amend = BodyAmendResult(ok=False, previous_body="old", new_body="new", error="404")
    handover = _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[scenario], amend=amend,
    )
    assert handover is not None
    assert handover.decisions[0].id == "fix-scenarios"


def test_no_code_forward_handover_failed_amend_still_writes_audit_row(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path))
    prior = _prior_handover(workspace)
    amend = BodyAmendResult(ok=False, previous_body="old", new_body="new", error="404")
    _fix_forward.no_code_forward_handover(
        workspace, prior, "inv_002", "synthesis", 42, scenarios=[], amend=amend,
    )
    entries = [
        json.loads(line)
        for line in (tmp_path / "impl_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "pr_body_amend_forward")
    assert row["amend_ok"] is False
