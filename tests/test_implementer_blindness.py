from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.handover import Decision, Handover, TestScenario
from alan.teams import _plan_projection as pp
from alan.teams import _pr_handover as ph
from alan.teams.implementation import _TEAM_CONFIG as _IMPL_TEAM_CONFIG
from alan.teams.implementation import _build_user_text, _prior_plan_block
from alan.teams.implementation_fix import _TEAM_CONFIG as _FIX_TEAM_CONFIG
from alan.workspace import Workspace, create

_SECRET_ID = "leak_probe_scenario"  # noqa: S105  # nosec B105
_SECRET_WHEN = "uv run --no-active pytest tests/test_secret.py::test_x"  # noqa: S105  # nosec B105
_SECRET_THEN = "1 passed"  # noqa: S105  # nosec B105


def _scenario(**overrides: str) -> TestScenario:
    base = {
        "id": _SECRET_ID,
        "description": "probe for a scenario leak",
        "given": "a prior handover carrying this scenario",
        "when": _SECRET_WHEN,
        "then": _SECRET_THEN,
    }
    base.update(overrides)
    return TestScenario.model_validate(base)


def _decisions_yaml_fence(*, info: str = "yaml decisions.yaml") -> str:
    return (
        f"```{info}\n"
        "decisions:\n"
        "  - id: d1\n"
        '    statement: "do the thing"\n'
        '    rationale: "because"\n'
        "    confidence: 0.9\n"
        "    reversibility: medium\n"
        "files_to_edit:\n"
        "  - alan/x.py\n"
        "test_scenarios:\n"
        f"  - id: {_SECRET_ID}\n"
        '    description: "d"\n'
        '    given: "g"\n'
        f'    when: "{_SECRET_WHEN}"\n'
        f'    then: "{_SECRET_THEN}"\n'
        "```\n"
    )


def _handover(**overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": "handover_probe_001",
        "handover_type": "forward",
        "task_id": "probe-task",
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "decisions": [
            Decision(
                id="d1", statement="x", rationale="y",
                confidence=0.9, reversibility="medium",
            )
        ],
        "full_synthesis": "ready",
    }
    base.update(overrides)
    return Handover.model_validate(base)


def test_strip_scenario_fences_drops_the_real_synthesizer_shape() -> None:
    text = "## Context\nsome prose\n\n" + _decisions_yaml_fence()
    stripped = pp.strip_scenario_fences(text)
    assert "## Context\nsome prose" in stripped
    assert _SECRET_ID not in stripped
    assert _SECRET_WHEN not in stripped
    assert "decisions.yaml" not in stripped


def test_strip_scenario_fences_drops_bare_fence_by_body_key_alone() -> None:
    text = (
        "prose before\n\n"
        "```yaml\n"
        "test_scenarios:\n"
        f"  - id: {_SECRET_ID}\n"
        f'    when: "{_SECRET_WHEN}"\n'
        "```\n"
        "prose after\n"
    )
    stripped = pp.strip_scenario_fences(text)
    assert "prose before" in stripped
    assert "prose after" in stripped
    assert _SECRET_ID not in stripped
    assert _SECRET_WHEN not in stripped


def test_strip_scenario_fences_tolerates_yml_extension() -> None:
    text = "prose\n\n" + _decisions_yaml_fence(info="yaml decisions.yml")
    stripped = pp.strip_scenario_fences(text)
    assert _SECRET_WHEN not in stripped


def test_strip_scenario_fences_tolerates_trailing_whitespace_and_crlf() -> None:
    text = (
        "prose\r\n"
        "```yaml decisions.yaml  \r\n"
        "test_scenarios:\r\n"
        f"  - id: {_SECRET_ID}\r\n"
        f'    when: "{_SECRET_WHEN}"\r\n'
        "```   \r\n"
        "after\r\n"
    )
    stripped = pp.strip_scenario_fences(text)
    assert _SECRET_WHEN not in stripped
    assert "after" in stripped


def test_strip_scenario_fences_preserves_unrelated_preceding_fence() -> None:
    text = (
        "prose\n\n"
        "```python\n"
        "print('unrelated')\n"
        "```\n\n"
        + _decisions_yaml_fence()
    )
    stripped = pp.strip_scenario_fences(text)
    assert "print('unrelated')" in stripped
    assert "```python" in stripped
    assert _SECRET_WHEN not in stripped


def test_strip_scenario_fences_drops_unclosed_flagged_fence_to_end_of_text() -> None:
    text = (
        "## Implementation steps\n1. do x\n\n"
        "```yaml decisions.yaml\n"
        "test_scenarios:\n"
        f"  - id: {_SECRET_ID}\n"
        f'    when: "{_SECRET_WHEN}"\n'
    )
    stripped = pp.strip_scenario_fences(text)
    assert "## Implementation steps\n1. do x" in stripped
    assert _SECRET_ID not in stripped
    assert _SECRET_WHEN not in stripped


def test_strip_scenario_fences_preserves_unclosed_unrelated_fence() -> None:
    text = "prose\n\n```python\nprint('dangling, never closed')\n"
    stripped = pp.strip_scenario_fences(text)
    assert "print('dangling, never closed')" in stripped


def test_strip_scenario_fences_noop_on_plain_text() -> None:
    text = "y" * 3000
    assert pp.strip_scenario_fences(text) == text


def test_redact_scenarios_scrubs_id_and_when() -> None:
    scenario = _scenario()
    text = f"see scenario {scenario.id} and run `{scenario.when}` to check"
    redacted, count = pp.redact_scenarios(text, [scenario])
    assert count == 2
    assert scenario.id not in redacted
    assert scenario.when not in redacted
    assert pp.REDACTION_PLACEHOLDER in redacted


def test_redact_scenarios_never_scrubs_then() -> None:
    scenario = _scenario()
    text = f"expected output: {scenario.then}"
    redacted, count = pp.redact_scenarios(text, [scenario])
    assert count == 0
    assert redacted == text


def test_redact_scenarios_over_redacts_a_when_reused_in_retained_prose() -> None:
    scenario = _scenario()
    text = f"Implementation step 3: also run {scenario.when} manually first."
    redacted, count = pp.redact_scenarios(text, [scenario])
    assert count == 1
    assert scenario.when not in redacted
    assert "Implementation step 3: also run" in redacted


def test_redact_scenarios_noop_without_scenarios() -> None:
    text = "nothing to see here"
    redacted, count = pp.redact_scenarios(text, [])
    assert (redacted, count) == (text, 0)


def test_redact_scenarios_longest_needle_wins_on_substring_overlap() -> None:
    scenario = _scenario(id="s1", when="run test s1 now")
    text = "please run test s1 now before merging"
    redacted, count = pp.redact_scenarios(text, [scenario])
    assert count == 1
    assert "s1" not in redacted
    assert redacted.count(pp.REDACTION_PLACEHOLDER) == 1


def test_prior_plan_block_strips_fence_when_steps_header_present() -> None:
    plan_synthesis = (
        "## Context\nctx\n\n"
        "## Implementation steps\n1. do x\n\n"
        "## Risks and mitigations\nnone\n\n"
        + _decisions_yaml_fence()
    )
    prior = _handover(plan_synthesis=plan_synthesis)
    body = _prior_plan_block(prior)
    assert "1. do x" in body
    assert _SECRET_ID not in body
    assert _SECRET_WHEN not in body


def test_prior_plan_block_never_leaks_when_steps_header_missing() -> None:
    plan_synthesis = (
        "## Context\nno steps header emitted by this worker\n\n"
        + _decisions_yaml_fence()
    )
    prior = _handover(plan_synthesis=plan_synthesis)
    body = _prior_plan_block(prior)
    assert "no steps header emitted" in body
    assert _SECRET_ID not in body
    assert _SECRET_WHEN not in body
    assert "decisions.yaml" not in body


def test_prior_plan_block_never_leaks_via_full_synthesis_fallback_offset() -> None:
    from alan.compression import DEFAULT_MAX_CHARS

    padding = "context " * DEFAULT_MAX_CHARS
    full_synthesis = padding + "\n\n" + _decisions_yaml_fence()
    prior = _handover(plan_synthesis=None, full_synthesis=full_synthesis)
    body = _prior_plan_block(prior)
    assert _SECRET_ID not in body
    assert _SECRET_WHEN not in body


def test_team_config_scenario_blind_defaults_false() -> None:
    cfg = ph.TeamConfig(
        team_name="x", default_forbidden=(), next_team=(), decision_prefix="x"
    )
    assert cfg.scenario_blind is False


def test_implementation_team_config_is_scenario_blind() -> None:
    assert _IMPL_TEAM_CONFIG.scenario_blind is True


def test_implementation_fix_team_config_is_not_scenario_blind() -> None:
    assert _FIX_TEAM_CONFIG.scenario_blind is False


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("blindness-probe", "verify implementer blindness", root=tmp_path / "ws")


def test_build_user_text_scrubs_leak_from_free_text_dispatch_instructions(
    workspace: Workspace,
) -> None:
    scenario = _scenario()
    prior = _handover(
        task_id=workspace.task_id,
        full_synthesis="ready",
        test_scenarios=[scenario],
    )
    text = _build_user_text(
        workspace, prior,
        instructions=f"Double-check by running {scenario.when} before merging.",
    )
    assert scenario.when not in text
    assert pp.REDACTION_PLACEHOLDER in text


def test_build_user_text_audits_scenario_leak_redacted(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    scenario = _scenario()
    prior = _handover(
        task_id=workspace.task_id,
        full_synthesis=f"plan mentions {scenario.id} in prose",
        test_scenarios=[scenario],
    )
    _build_user_text(workspace, prior)

    audit_path = tmp_path / "logs" / "impl_audit.jsonl"
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    hits = [e for e in entries if e.get("action") == "scenario_leak_redacted"]
    assert hits
    assert hits[0]["task_id"] == workspace.task_id
    assert hits[0]["count"] >= 1


def test_implementation_fix_user_text_is_not_scenario_blind(
    workspace: Workspace,
) -> None:
    scenario = _scenario()
    prior = _handover(
        task_id=workspace.task_id,
        from_team="verification",
        full_synthesis=f"judge review references {scenario.when}",
        test_scenarios=[scenario],
    )
    text = ph.compose_user_text(
        workspace, prior,
        mid_blocks=lambda p: [f"\n\n## Judge review\n{p.full_synthesis}"],
        config=_FIX_TEAM_CONFIG,
    )
    assert scenario.when in text
