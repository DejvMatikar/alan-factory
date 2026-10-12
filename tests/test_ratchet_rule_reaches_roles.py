from __future__ import annotations

from alan import house_rules
from alan.teams import _fix_forward, _fix_prompt, implementation, implementation_fix


def test_house_rules_version_bumped() -> None:
    assert house_rules.HOUSE_RULES_VERSION == 2


def test_house_rules_carries_a_no_ratchet_widening_rule() -> None:
    rule = next(
        hr for hr in house_rules.HOUSE_RULES if hr.id == "house-no-ratchet-widening"
    )
    assert rule.direction == "must_not"
    assert "ratchet" in rule.text.lower()
    assert "pull request that ratchet is blocking" in rule.text


def test_implementation_default_forbidden_actions_carries_ratchet_rule() -> None:
    joined = " ".join(implementation._DEFAULT_FORBIDDEN_ACTIONS)  # noqa: SLF001
    assert "ratchet" in joined.lower()
    assert "flake-quarantine issue" in joined


def test_implementation_default_forbidden_actions_reach_the_shared_renderer() -> None:
    rendered = _fix_prompt.render_forbidden_block(
        implementation._DEFAULT_FORBIDDEN_ACTIONS, (),  # noqa: SLF001
    )
    assert "## Forbidden actions" in rendered
    assert "ratchet" in rendered.lower()


def test_implementation_environment_block_no_longer_scopes_to_diff_added_specs() -> None:
    block = implementation.ENVIRONMENT_BLOCK
    assert "ratchet" in block.lower()
    assert "never loosen a threshold or add the file" not in block
    assert "names a spec this diff adds or edits:" not in block
    assert "names any spec, not only one this diff adds or edits" in block


def test_implementation_environment_block_points_at_the_relocation_channel() -> None:
    assert "flake-quarantine issue" in implementation.ENVIRONMENT_BLOCK


def test_implementation_default_system_prompt_carries_the_env_block_prose() -> None:
    assert "ratchet" in implementation._SYSTEM_PROMPT.lower()  # noqa: SLF001


def test_fix_forward_absolute_forbidden_actions_carries_ratchet_rule() -> None:
    joined = " ".join(_fix_forward.ABSOLUTE_FORBIDDEN_ACTIONS)
    assert "ratchet" in joined.lower()
    assert "flake-quarantine issue" in joined


def test_fix_forward_absolute_prohibition_reaches_the_shared_renderer() -> None:
    rendered = _fix_prompt.render_forbidden_block(
        _fix_forward.DEFAULT_FORBIDDEN_ACTIONS, _fix_forward.ABSOLUTE_FORBIDDEN_ACTIONS,
    )
    assert "## Absolute prohibitions" in rendered
    absolute_section = rendered.split("## Absolute prohibitions", 1)[1]
    assert "ratchet" in absolute_section.lower()


def test_implementation_fix_default_system_prompt_bakes_in_absolute_prohibition() -> None:
    assert "ratchet" in implementation_fix._SYSTEM_PROMPT.lower()  # noqa: SLF001
    section = implementation_fix._SYSTEM_PROMPT.split(  # noqa: SLF001
        "## Absolute prohibitions", 1
    )[1]
    assert "ratchet" in section.lower()


def test_implementation_fix_generic_tail_bakes_in_absolute_prohibition() -> None:
    tail = implementation_fix._GENERIC_FIX_STATIC_TAIL  # noqa: SLF001
    assert "ratchet" in tail.lower()
    section = tail.split("## Absolute prohibitions", 1)[1]
    assert "ratchet" in section.lower()
