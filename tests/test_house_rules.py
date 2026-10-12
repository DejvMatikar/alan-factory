from __future__ import annotations

from alan.house_rules import HOUSE_RULES, HOUSE_RULES_VERSION, HouseRule
from alan.obligation_direction import classify


def test_house_rules_version_is_positive_int() -> None:
    assert isinstance(HOUSE_RULES_VERSION, int)
    assert HOUSE_RULES_VERSION >= 1


def test_house_rules_non_empty() -> None:
    assert HOUSE_RULES


def test_house_rule_ids_are_unique() -> None:
    ids = [hr.id for hr in HOUSE_RULES]
    assert len(ids) == len(set(ids))


def test_house_rule_ids_are_non_empty_strings() -> None:
    for hr in HOUSE_RULES:
        assert isinstance(hr.id, str)
        assert hr.id


def test_house_rule_texts_are_non_empty() -> None:
    for hr in HOUSE_RULES:
        assert hr.text.strip()


def test_house_rule_direction_is_closed_vocabulary() -> None:
    for hr in HOUSE_RULES:
        assert hr.direction in ("must", "must_not")


def test_house_rule_direction_matches_its_own_text() -> None:
    for hr in HOUSE_RULES:
        assert classify(hr.text) == hr.direction


def test_house_rules_are_frozen() -> None:
    hr = HOUSE_RULES[0]
    frozen_error = False
    try:
        hr.id = "changed"  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        frozen_error = True
    assert frozen_error


def test_house_rule_model_requires_min_length_fields() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HouseRule(id="", text="x", direction="must")
    with pytest.raises(ValidationError):
        HouseRule(id="x", text="", direction="must")
