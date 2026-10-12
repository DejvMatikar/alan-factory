from __future__ import annotations

from alan.teams._brief_refine_block import (
    BRIEF_REFINE_BLOCK_GUIDANCE,
    ORIGIN_LABELS,
    parse_brief_refine,
)


def _fenced(body: str) -> str:
    return f"some prose\n\n```yaml brief_refine.yaml\n{body}\n```\n"


def test_missing_fence_returns_none() -> None:
    assert parse_brief_refine("no fence here at all", frozenset()) is None


def test_bad_yaml_returns_none() -> None:
    text = _fenced("requirements: [unterminated")
    assert parse_brief_refine(text, frozenset()) is None


def test_non_mapping_root_returns_none() -> None:
    text = _fenced("- a\n- b")
    assert parse_brief_refine(text, frozenset()) is None


def test_requirements_not_a_list_returns_none() -> None:
    text = _fenced("requirements: not-a-list\nassumptions: []")
    assert parse_brief_refine(text, frozenset()) is None


def test_requirement_item_not_a_mapping_returns_none() -> None:
    text = _fenced("requirements:\n  - just a string\nassumptions: []")
    assert parse_brief_refine(text, frozenset()) is None


def test_requirement_item_missing_text_returns_none() -> None:
    text = _fenced("requirements:\n  - id: req-1\nassumptions: []")
    assert parse_brief_refine(text, frozenset()) is None


def test_new_item_out_of_vocabulary_origin_discards_whole_payload() -> None:
    text = _fenced(
        "requirements:\n"
        '  - text: "a new clause"\n'
        "    origin: made_up_source\n"
        '    locator: "x"\n'
        "assumptions: []"
    )
    assert parse_brief_refine(text, frozenset()) is None


def test_new_item_missing_origin_discards_whole_payload() -> None:
    text = _fenced('requirements:\n  - text: "a new clause"\nassumptions: []')
    assert parse_brief_refine(text, frozenset()) is None


def test_new_item_missing_locator_discards_whole_payload() -> None:
    text = _fenced(
        "requirements:\n"
        '  - text: "a new clause"\n'
        "    origin: user_text\n"
        "assumptions: []"
    )
    assert parse_brief_refine(text, frozenset()) is None


def test_assumptions_not_a_list_returns_none() -> None:
    text = _fenced("requirements: []\nassumptions: not-a-list")
    assert parse_brief_refine(text, frozenset()) is None


def test_assumption_entry_not_a_string_returns_none() -> None:
    text = _fenced("requirements: []\nassumptions:\n  - 5")
    assert parse_brief_refine(text, frozenset()) is None


def test_null_requirements_and_assumptions_parse_as_empty() -> None:
    text = _fenced("requirements:\nassumptions:")
    payload = parse_brief_refine(text, frozenset())
    assert payload is not None
    assert payload.requirements == ()
    assert payload.assumptions == ()


def test_kept_item_needs_no_origin() -> None:
    text = _fenced('requirements:\n  - id: req-1\n    text: "kept text"\nassumptions: []')
    payload = parse_brief_refine(text, frozenset({"req-1"}))
    assert payload is not None
    assert payload.requirements[0].id == "req-1"
    assert payload.requirements[0].origin is None


def test_new_item_with_valid_origin_and_locator_parses() -> None:
    text = _fenced(
        "requirements:\n"
        '  - text: "a new clause"\n'
        "    origin: cited_source\n"
        '    locator: "#42"\n'
        "assumptions:\n"
        '  - "an inferred detail"'
    )
    payload = parse_brief_refine(text, frozenset())
    assert payload is not None
    assert payload.requirements[0].id is None
    assert payload.requirements[0].origin == "cited_source"
    assert payload.requirements[0].locator == "#42"
    assert payload.assumptions == ("an inferred detail",)


def test_guard_duplicate_requirement_id_discards_payload() -> None:
    text = _fenced(
        "requirements:\n"
        '  - id: req-1\n    text: "kept once"\n'
        '  - id: req-1\n    text: "kept twice"\n'
        "assumptions: []"
    )
    assert parse_brief_refine(text, frozenset({"req-1"})) is None


def test_guard_prior_id_absent_from_output_discards_payload() -> None:
    text = _fenced('requirements:\n  - id: req-2\n    text: "kept text"\nassumptions: []')
    assert parse_brief_refine(text, frozenset({"req-1", "req-2"})) is None


def test_guard_prior_nonempty_with_empty_output_requirements_discards_payload() -> None:
    text = _fenced('requirements: []\nassumptions:\n  - "a"\n  - "b"')
    assert parse_brief_refine(text, frozenset({"req-1"})) is None


def test_guard_output_may_add_new_items_beyond_prior_count() -> None:
    text = _fenced(
        "requirements:\n"
        '  - id: req-1\n    text: "kept"\n'
        '  - text: "new one"\n    origin: user_text\n    locator: "x"\n'
        "assumptions: []"
    )
    payload = parse_brief_refine(text, frozenset({"req-1"}))
    assert payload is not None
    assert len(payload.requirements) == 2


def test_guard_all_prior_moved_would_fail_without_assumption_ids() -> None:
    text = _fenced('requirements:\n  - id: req-1\n    text: "kept"\nassumptions:\n  - "extra"')
    payload = parse_brief_refine(text, frozenset({"req-1"}))
    assert payload is not None


def test_origin_labels_closed_vocabulary() -> None:
    assert ORIGIN_LABELS == frozenset({"user_text", "cited_source", "house_rule"})


def test_guidance_text_carries_the_canonical_fence() -> None:
    assert "```yaml brief_refine.yaml" in BRIEF_REFINE_BLOCK_GUIDANCE
