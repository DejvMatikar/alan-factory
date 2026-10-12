from __future__ import annotations

from pathlib import Path

from alan.engine_presets import ios_preset
from alan.teams import _declared_blocker, _fix_prompt
from alan.teams import implementation_fix as fix_mod

GUIDANCE = _fix_prompt.INSTRUCTION_DECLINE_GUIDANCE


def _assembled_prompts(tmp_path: Path) -> list[str]:
    return [
        fix_mod._SYSTEM_PROMPT,  # noqa: SLF001
        fix_mod._GENERIC_FIX_STATIC_TAIL,  # noqa: SLF001
        fix_mod.render_system_prompt(ios_preset(), tmp_path),
    ]


def test_guidance_names_the_instructions_section_the_mark_and_the_open_questions_section() -> None:
    assert "## User dispatch instructions" in GUIDANCE
    assert _declared_blocker.BLOCKING_MARK in GUIDANCE
    assert "## Open questions" in GUIDANCE


def test_guidance_asks_for_the_check_and_the_reason() -> None:
    assert "naming the check and the reason" in GUIDANCE


def test_guidance_asserts_no_behavioural_promise() -> None:
    lowered = GUIDANCE.lower()
    for promise in (" will ", "always", "guarantee", "ensures"):
        assert promise not in lowered


def test_guidance_restricts_the_bullet_to_an_actual_decline() -> None:
    assert "only when you actually decline" in GUIDANCE


def test_guidance_adds_no_placeholder_bullet_for_the_host_to_park_on() -> None:
    assert "none" not in GUIDANCE.lower()
    assert "<" not in GUIDANCE


def test_every_assembled_fix_prompt_carries_the_guidance_exactly_once(tmp_path: Path) -> None:
    for prompt in _assembled_prompts(tmp_path):
        assert prompt.count(GUIDANCE) == 1


def test_the_guidance_follows_the_declared_blocker_guidance_and_precedes_the_non_actionable_one(
    tmp_path: Path,
) -> None:
    joined = (
        _declared_blocker.DECLARED_BLOCKER_GUIDANCE
        + GUIDANCE
        + _fix_prompt.NON_ACTIONABLE_GUIDANCE
    )
    for prompt in _assembled_prompts(tmp_path):
        assert joined in prompt


def test_the_guidance_is_exported_from_the_fix_prompt_module() -> None:
    assert "INSTRUCTION_DECLINE_GUIDANCE" in _fix_prompt.__all__


def test_the_scope_deference_notice_keeps_its_wording() -> None:
    assert _fix_prompt.SCOPE_DEFERENCE_NOTICE == (
        "If this run's user prompt carries a `## User dispatch instructions` "
        "section, read its own authority notice there: it can widen scope past "
        "the file-scope restriction above and past the `## Forbidden actions` "
        "section below, for this run only. It never overrides `## Absolute "
        "prohibitions`. Absent that section, the restriction above applies as "
        "written."
    )


def test_the_declared_blocker_parser_reads_a_bullet_the_guidance_describes() -> None:
    synthesis = (
        "## Summary\ndone\n\n## Open questions\n"
        f"- {_declared_blocker.BLOCKING_MARK} Declined the instruction to edit "
        "`ci.yml` line 41: the `lint` check fails for an unrelated reason.\n"
    )
    [question] = _declared_blocker.parse_declared_blocker(synthesis, "inv-decline-guidance")
    assert "`lint` check" in question.question
    assert question.blocking is True
    assert question.resolver_role == "human"


def test_a_pass_that_carries_the_instruction_out_and_adds_no_bullet_parks_nothing() -> None:
    synthesis = "## Summary\ndone\n\n## Open questions\n- none\n"
    assert _declared_blocker.parse_declared_blocker(synthesis, "inv-decline-guidance") == []
