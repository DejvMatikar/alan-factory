from __future__ import annotations

import pytest

from alan.teams import _declared_blocker
from alan.teams._declared_blocker import (
    BLOCKER_UNFOUNDED_OPTION,
    BLOCKING_MARK,
    HAND_RESOLVE_OPTION,
    WORKER_STEP_OPTION,
    parse_declared_blocker,
)

_INV_ID = "inv_0123456789"


def test_missing_open_questions_heading_yields_no_questions() -> None:
    synthesis = "## Summary\nAll good.\n\n## Decisions\n- did a thing\n"
    assert parse_declared_blocker(synthesis, _INV_ID) == []


def test_open_questions_section_without_marked_bullets_yields_no_questions() -> None:
    synthesis = (
        "## Summary\nDone.\n\n"
        "## Open questions\n"
        "- should we bump the timeout later?\n"
    )
    assert parse_declared_blocker(synthesis, _INV_ID) == []


def test_single_marked_bullet_produces_one_aggregated_question() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK} the base branch is protected and blocks force-push\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    question = questions[0]
    assert question.id == f"declared_blocker_{_INV_ID[:8]}"
    assert question.question == "the base branch is protected and blocks force-push"
    assert question.proposed_answer_space == [
        HAND_RESOLVE_OPTION,
        BLOCKER_UNFOUNDED_OPTION,
        WORKER_STEP_OPTION,
    ]
    assert question.recommended == HAND_RESOLVE_OPTION
    assert question.question_class == "fix_escalation"
    assert question.resolver_role == "human"
    assert question.blocking is True
    assert question.option_resolvers[HAND_RESOLVE_OPTION] == "implementation_fix"
    assert question.option_resolvers[BLOCKER_UNFOUNDED_OPTION] == "verification"
    assert question.option_effects == {}


def test_worker_step_option_text_is_the_agreed_single_sentence() -> None:
    assert WORKER_STEP_OPTION == (
        "The declared step is the worker's to do: widen scope to the files it needs and do it."
    )


def test_worker_step_option_resolves_to_the_fix_team() -> None:
    question = parse_declared_blocker(f"## Open questions\n- {BLOCKING_MARK} blocked\n", _INV_ID)[0]
    assert question.option_resolvers[WORKER_STEP_OPTION] == "implementation_fix"


def test_existing_option_resolvers_are_unchanged_by_the_worker_step_option() -> None:
    question = parse_declared_blocker(f"## Open questions\n- {BLOCKING_MARK} blocked\n", _INV_ID)[0]
    assert question.option_resolvers == {
        HAND_RESOLVE_OPTION: "implementation_fix",
        BLOCKER_UNFOUNDED_OPTION: "verification",
        WORKER_STEP_OPTION: "implementation_fix",
    }


def test_recommendation_stays_on_hand_resolve_with_the_worker_step_option_offered() -> None:
    question = parse_declared_blocker(f"## Open questions\n- {BLOCKING_MARK} blocked\n", _INV_ID)[0]
    assert question.recommended == HAND_RESOLVE_OPTION
    assert WORKER_STEP_OPTION in question.proposed_answer_space


def test_worker_step_option_is_exported() -> None:
    assert "WORKER_STEP_OPTION" in _declared_blocker.__all__


def test_multiple_marked_bullets_aggregate_into_one_question() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK} first blocker\n"
        f"- {BLOCKING_MARK} second blocker\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    assert questions[0].question == "- first blocker\n- second blocker"


def test_unmarked_bullets_in_open_questions_are_not_carried() -> None:
    synthesis = (
        "## Open questions\n"
        "- an FYI observation, not blocking\n"
        f"- {BLOCKING_MARK} the real blocker\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    assert questions[0].question == "the real blocker"


def test_marked_bullet_outside_open_questions_section_is_ignored() -> None:
    synthesis = (
        "## Decisions\n"
        f"- {BLOCKING_MARK} this looks like a mark but is in the wrong section\n"
        "\n## Open questions\n"
        "- nothing blocking here\n"
    )
    assert parse_declared_blocker(synthesis, _INV_ID) == []


def test_mark_with_no_remaining_text_is_dropped() -> None:
    synthesis = f"## Open questions\n- {BLOCKING_MARK}\n"
    assert parse_declared_blocker(synthesis, _INV_ID) == []


def test_mark_with_no_remaining_text_is_dropped_alongside_a_usable_bullet() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK}\n"
        f"- {BLOCKING_MARK} a usable blocker\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    assert questions[0].question == "a usable blocker"


def test_bullet_text_is_truncated_at_300_chars() -> None:
    long_text = "x" * 400
    synthesis = f"## Open questions\n- {BLOCKING_MARK} {long_text}\n"
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions[0].question) == 300
    assert questions[0].question == "x" * 300


def test_only_first_five_marked_bullets_are_kept() -> None:
    bullets = "\n".join(f"- {BLOCKING_MARK} blocker {i}" for i in range(7))
    synthesis = f"## Open questions\n{bullets}\n"
    questions = parse_declared_blocker(synthesis, _INV_ID)
    lines = questions[0].question.splitlines()
    assert len(lines) == 5
    assert lines == [f"- blocker {i}" for i in range(5)]


def test_next_h2_heading_ends_the_open_questions_section() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK} inside the section\n"
        "\n## Commits\n"
        f"- {BLOCKING_MARK} not actually an open question\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    assert questions[0].question == "inside the section"


def test_module_exports_guidance_constant_mentioning_the_mark() -> None:
    assert BLOCKING_MARK in _declared_blocker.DECLARED_BLOCKER_GUIDANCE


@pytest.mark.parametrize("token", ["none.", "N/A", "-", "no", "nothing", "n/a", "None!"])
def test_noop_blocker_token_yields_no_question(token: str) -> None:
    synthesis = f"## Open questions\n- {BLOCKING_MARK} {token}\n"
    assert parse_declared_blocker(synthesis, _INV_ID) == []


def test_real_blocker_text_still_produces_a_question_alongside_noop_ones() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK} none.\n"
        f"- {BLOCKING_MARK} the base branch is protected and blocks force-push\n"
    )
    questions = parse_declared_blocker(synthesis, _INV_ID)
    assert len(questions) == 1
    assert questions[0].question == "the base branch is protected and blocks force-push"


def test_all_noop_bullets_yield_no_question() -> None:
    synthesis = (
        "## Open questions\n"
        f"- {BLOCKING_MARK} none.\n"
        f"- {BLOCKING_MARK} N/A\n"
        f"- {BLOCKING_MARK} -\n"
    )
    assert parse_declared_blocker(synthesis, _INV_ID) == []
