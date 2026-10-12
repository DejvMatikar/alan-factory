from __future__ import annotations

from alan import agent_catalogue_teams_impl_slots as slots
from alan.claude_cli import format_user_text


def work_item_text() -> str:
    return format_user_text(slots.WORK_ITEM_TITLE, slots.WORK_ITEM_DESCRIPTION)


def implementation_user_message() -> str:
    return (
        work_item_text()
        + slots.SUBTASK_SECTION
        + slots.DISPATCH_INSTRUCTIONS
        + slots.IMPLEMENTATION_PLAN_SECTIONS
        + slots.IMPLEMENTATION_FORBIDDEN_ACTIONS
    )


def implementation_fix_user_message() -> str:
    return (
        work_item_text()
        + slots.SUBTASK_SECTION
        + slots.DISPATCH_INSTRUCTIONS
        + slots.FIX_REVIEW_SECTIONS
        + slots.FIX_UPSTREAM_SECTIONS
        + slots.FIX_FORBIDDEN_ACTIONS
    )


def verification_base_text() -> str:
    return (
        work_item_text()
        + slots.DISPATCH_INSTRUCTIONS
        + slots.IMPLEMENTATION_HANDOVER
        + slots.REVIEW_INPUTS
    )


__all__ = [
    "implementation_fix_user_message",
    "implementation_user_message",
    "verification_base_text",
    "work_item_text",
]
