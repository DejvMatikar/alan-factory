from __future__ import annotations

from datetime import UTC, datetime

from alan.handover import Handover
from alan.teams import _pr_handover
from alan.teams import implementation as impl_mod
from alan.teams import implementation_fix as fix_mod
from alan.teams._base import format_dispatch_instructions


def _team_config(**overrides: object) -> _pr_handover.TeamConfig:
    base: dict[str, object] = {
        "team_name": "t",
        "default_forbidden": ("do not do X",),
        "next_team": (),
        "decision_prefix": "t",
    }
    base.update(overrides)
    return _pr_handover.TeamConfig(**base)  # type: ignore[arg-type]


def _prior_handover(**overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": "h1",
        "handover_type": "forward",
        "task_id": "t1",
        "from_team": "verification",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "s",
    }
    base.update(overrides)
    return Handover(**base)  # type: ignore[arg-type]


def test_dispatch_addendum_empty_instructions_returns_empty_regardless_of_config() -> None:
    outrank = _team_config(instructions_outrank_scope=True)
    plain = _team_config()
    assert _pr_handover._dispatch_addendum(None, "", plain) == ""  # noqa: SLF001
    assert _pr_handover._dispatch_addendum("   ", "task block", outrank) == ""  # noqa: SLF001


def test_dispatch_addendum_outrank_scope_adds_authority_notice_even_without_task_block() -> None:
    config = _team_config(instructions_outrank_scope=True)
    result = _pr_handover._dispatch_addendum("do the thing", "", config)  # noqa: SLF001
    assert "## User dispatch instructions" in result
    assert "do the thing" in result
    assert "outranks" in result
    assert "## Absolute prohibitions" in result
    assert result != format_dispatch_instructions("do the thing")


def test_dispatch_addendum_outrank_scope_adds_authority_notice_with_task_block() -> None:
    config = _team_config(instructions_outrank_scope=True)
    result = _pr_handover._dispatch_addendum(  # noqa: SLF001
        "do the thing", "\n\n## Your task\nsomething", config
    )
    assert "outranks" in result
    assert _pr_handover._ADDENDUM_PRECEDENCE_NOTICE not in result  # noqa: SLF001


def test_dispatch_addendum_non_outranking_with_task_block_uses_precedence_notice() -> None:
    config = _team_config()
    result = _pr_handover._dispatch_addendum(  # noqa: SLF001
        "do the thing", "\n\n## Your task\nsomething", config
    )
    assert _pr_handover._ADDENDUM_PRECEDENCE_NOTICE in result  # noqa: SLF001
    assert "outranks" not in result


def test_dispatch_addendum_non_outranking_without_task_block_returns_block_only() -> None:
    config = _team_config()
    result = _pr_handover._dispatch_addendum("do the thing", "", config)  # noqa: SLF001
    assert result == format_dispatch_instructions("do the thing")


def test_implementation_team_config_does_not_grant_scope_override() -> None:
    assert impl_mod._TEAM_CONFIG.instructions_outrank_scope is False  # noqa: SLF001
    assert impl_mod._TEAM_CONFIG.absolute_forbidden == ()  # noqa: SLF001


def test_implementation_fix_team_config_grants_scope_override() -> None:
    assert fix_mod._TEAM_CONFIG.instructions_outrank_scope is True  # noqa: SLF001
    assert fix_mod._TEAM_CONFIG.absolute_forbidden == fix_mod._ABSOLUTE_FORBIDDEN_ACTIONS  # noqa: SLF001


def test_forbidden_actions_block_renders_two_sections_when_absolute_present() -> None:
    prior = _prior_handover(forbidden_actions=["reviewer-authored bullet"])
    block = _pr_handover._forbidden_actions_block(  # noqa: SLF001
        prior, ("do not do X",), ("do not amend prior commits",)
    )
    assert "## Forbidden actions" in block
    assert "## Absolute prohibitions" in block
    assert "- reviewer-authored bullet" in block
    assert "- do not amend prior commits" in block
    assert block.index("reviewer-authored bullet") < block.index("## Absolute prohibitions")


def test_forbidden_actions_block_empty_absolute_is_back_compat_single_section() -> None:
    prior = _prior_handover()
    block = _pr_handover._forbidden_actions_block(prior, ("do not do X",))  # noqa: SLF001
    assert "## Forbidden actions" in block
    assert "## Absolute prohibitions" not in block


def test_authority_notice_grant_is_scoped_and_never_extends_to_fenced_blocks() -> None:
    notice = _pr_handover._ADDENDUM_AUTHORITY_NOTICE  # noqa: SLF001
    assert "## Absolute prohibitions" in notice
    assert "never overrides" in notice
    assert "this section only" in notice
    assert "untrusted-content notice" in notice
